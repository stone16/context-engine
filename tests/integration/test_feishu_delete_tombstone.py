from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from adapters.connectors.feishu import FeishuAclVisibility
from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.feishu_integration import (
    accept_feishu_page,
    apply_feishu_page,
    cleanup_feishu_scenario,
    next_observation_time,
    seed_feishu_article,
)

pytestmark = pytest.mark.integration


def test_feishu_delete_tombstones_article_without_removing_content(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=next_observation_time(),
        document_ref="document:deleted",
        visibility=FeishuAclVisibility.PRIVATE,
        deleted=True,
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(migration_configuration, accepted)
        with engine.begin() as connection:
            revision_id = connection.execute(
                text("SELECT gen_random_uuid()")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO context_revision "
                    "(organization_id, resource_ref, revision_id) "
                    "VALUES (:org, :resource, :revision)"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "revision": revision_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO context_fragment "
                    "(organization_id, resource_ref, revision_id, "
                    "fragment_ref, ordinal, content) VALUES "
                    "(:org, :resource, :revision, 'fragment:deleted', 0, 'retained')"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "revision": revision_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE context_resource SET active_revision_id = :revision "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                    "revision": revision_id,
                },
            )
        applied = apply_feishu_page(guarded_control_engine, accepted)
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, count(fragment.fragment_ref)
                    FROM context_resource AS resource
                    JOIN context_fragment AS fragment
                      ON fragment.organization_id = resource.organization_id
                     AND fragment.resource_ref = resource.resource_ref
                    WHERE resource.organization_id = :org
                      AND resource.resource_ref = :resource
                    GROUP BY resource.tombstoned
                    """
                ),
                {
                    "org": accepted.scenario.organization_id,
                    "resource": accepted.document_ref,
                },
            ).one()
        assert applied.tombstoned is True
        assert tuple(state) == (True, 1)
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )
