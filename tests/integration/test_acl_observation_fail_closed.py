from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.article_access_policy import (
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    set_tenant_default,
    unique_article_refs,
)

pytestmark = pytest.mark.integration


def _is_isolated(engine: Engine, organization_id: object, resource_ref: str) -> bool:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT policy_kind, group_refs, published,
                       source_observation_status
                FROM article_access_policy
                WHERE organization_id = :organization_id
                  AND resource_ref = :resource_ref
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
            },
        ).one()
    return (
        row.policy_kind is None
        and row.group_refs == []
        and row.published is False
        and row.source_observation_status in {"missing", "failed", "unresolved_group"}
    )


@pytest.mark.parametrize("failure", ("missing", "failed", "unresolved_group"))
def test_missing_failed_or_unresolved_source_acl_observation_isolates(
    failure: str,
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    source_ref, resource_ref = unique_article_refs(f"acl-{failure}")
    try:
        insert_organization(engine, organization_id)
        set_tenant_default(engine, organization_id, "organization")
        if failure != "missing":
            observe_source_acl(
                engine,
                organization_id=organization_id,
                source_ref=source_ref,
                resource_ref=resource_ref,
                status=failure,
                policy_kind=None,
            )

        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
        )

        assert _is_isolated(engine, organization_id, resource_ref)
        with engine.connect() as connection:
            observed_mode = connection.execute(
                text(
                    """
                    SELECT evidence_mode
                    FROM article_source_acl_observation
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": organization_id,
                    "resource_ref": resource_ref,
                },
            ).scalar_one_or_none()
        assert observed_mode in {None, "mirrored"}
        assert observed_mode != "weak"
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_no_public_control_operation_accepts_caller_asserted_source_acl() -> None:
    from engine.control import ContextControl, ControlOperation

    assert not hasattr(ContextControl, "observe_article_source_acl")
    assert "observe_article_source_acl" not in {item.value for item in ControlOperation}
