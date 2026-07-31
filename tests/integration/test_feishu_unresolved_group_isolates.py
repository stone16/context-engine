from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from adapters.connectors.feishu import (
    FeishuAclVisibility,
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuPermissionKind,
    FeishuPermissionSubject,
)
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.feishu_integration import (
    accept_feishu_page,
    apply_feishu_page,
    cleanup_feishu_scenario,
    next_observation_time,
    seed_feishu_article,
)

pytestmark = pytest.mark.integration


def test_feishu_unresolved_group_isolates(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    observed_at = next_observation_time()
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=observed_at,
        document_ref="document:unresolved-group",
        visibility=FeishuAclVisibility.PRIVATE,
        subjects=(
            FeishuPermissionSubject(FeishuPermissionKind.GROUP, "group:root"),
        ),
        group_snapshot=FeishuGroupSnapshot(
            "groups:v1",
            (
                FeishuGroupNode(
                    "group:root",
                    "local-group:root",
                    (),
                    ("group:missing",),
                ),
            ),
            observed_at,
        ),
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(migration_configuration, accepted)
        applied = apply_feishu_page(guarded_control_engine, accepted)
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT observation.observation_status, observation.policy_kind,
                           policy.published, policy.policy_kind
                    FROM article_source_acl_observation AS observation
                    JOIN article_access_policy AS policy
                      ON policy.organization_id = observation.organization_id
                     AND policy.resource_ref = observation.resource_ref
                    WHERE observation.organization_id = :org
                      AND observation.resource_ref = :resource
                    """
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                },
            ).one()
        assert applied.published is False
        assert tuple(state) == ("unresolved_group", None, False, None)
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )
