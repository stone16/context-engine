from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine


def delete_article_policy_scenario(
    configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    """Remove one disposable Article-policy scenario and its Organization."""

    engine = create_database_engine(configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM context_resource WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": organization_id},
            )
    finally:
        engine.dispose()


def insert_organization(engine: Engine, organization_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (organization_id) VALUES (:organization_id)"
            ),
            {"organization_id": organization_id},
        )


def set_tenant_default(
    engine: Engine,
    organization_id: UUID,
    kind: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE organization_article_policy_default
                SET policy_kind = :kind,
                    group_refs = CAST(:group_refs AS text[]),
                    default_version = default_version + 1
                WHERE organization_id = :organization_id
                """
            ),
            {
                "organization_id": organization_id,
                "kind": kind,
                "group_refs": [],
            },
        )


def set_source_default(
    engine: Engine,
    organization_id: UUID,
    source_ref: str,
    kind: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_article_policy_default (
                    organization_id, source_ref, policy_kind, group_refs,
                    default_version
                ) VALUES (
                    :organization_id, :source_ref, :kind,
                    CAST(:group_refs AS text[]), 1
                )
                ON CONFLICT (organization_id, source_ref) DO UPDATE
                SET policy_kind = EXCLUDED.policy_kind,
                    group_refs = EXCLUDED.group_refs,
                    default_version =
                        source_article_policy_default.default_version + 1
                """
            ),
            {
                "organization_id": organization_id,
                "source_ref": source_ref,
                "kind": kind,
                "group_refs": [],
            },
        )


def observe_source_acl(
    engine: Engine,
    *,
    organization_id: UUID,
    source_ref: str,
    resource_ref: str,
    status: str = "resolved",
    policy_kind: str | None = "organization",
    group_refs: tuple[str, ...] = (),
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO article_source_acl_observation (
                    organization_id, source_ref, resource_ref, evidence_mode,
                    observation_status, policy_kind, group_refs,
                    observation_version, observed_at
                ) VALUES (
                    :organization_id, :source_ref, :resource_ref, 'mirrored',
                    :status, :policy_kind, CAST(:group_refs AS text[]), 1,
                    statement_timestamp()
                )
                """
            ),
            {
                "organization_id": organization_id,
                "source_ref": source_ref,
                "resource_ref": resource_ref,
                "status": status,
                "policy_kind": policy_kind,
                "group_refs": list(group_refs),
            },
        )


def ingest_article(
    engine: Engine,
    *,
    organization_id: UUID,
    source_ref: str,
    resource_ref: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO context_resource (
                    organization_id, resource_ref, source_ref,
                    active_revision_id, tombstoned
                ) VALUES (
                    :organization_id, :resource_ref, :source_ref, NULL, false
                )
                ON CONFLICT (organization_id, resource_ref) DO NOTHING
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
                "source_ref": source_ref,
            },
        )
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": str(organization_id)},
        )
        connection.execute(
            text(
                "SELECT public.context_fix_article_access_policy("
                ":organization_id, :resource_ref)"
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
            },
        )


def replace_revision_without_policy_mutation(
    engine: Engine,
    *,
    organization_id: UUID,
    resource_ref: str,
) -> UUID:
    revision_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO context_revision (
                    organization_id, resource_ref, revision_id
                ) VALUES (
                    :organization_id, :resource_ref, :revision_id
                )
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
                "revision_id": revision_id,
            },
        )
        connection.execute(
            text(
                """
                UPDATE context_resource
                SET active_revision_id = :revision_id
                WHERE organization_id = :organization_id
                  AND resource_ref = :resource_ref
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
                "revision_id": revision_id,
            },
        )
    return revision_id


def article_policy(
    engine: Engine,
    organization_id: UUID,
    resource_ref: str,
) -> tuple[str, int, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT policy_kind, policy_version, resolution_rung
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
    return row.policy_kind, row.policy_version, row.resolution_rung


def policy_epoch(engine: Engine, organization_id: UUID) -> int:
    with engine.connect() as connection:
        observed = connection.execute(
            text(
                "SELECT policy_epoch FROM organization_policy_epoch "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": organization_id},
        ).scalar_one()
    assert type(observed) is int
    return observed


def fixed_policy_epoch(
    engine: Engine,
    organization_id: UUID,
    resource_ref: str,
) -> int:
    with engine.connect() as connection:
        observed = connection.execute(
            text(
                "SELECT fixed_at_policy_epoch FROM article_access_policy "
                "WHERE organization_id = :organization_id "
                "AND resource_ref = :resource_ref"
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
            },
        ).scalar_one()
    assert type(observed) is int
    return observed


def unique_article_refs(prefix: str) -> tuple[str, str]:
    suffix = uuid4()
    return f"source:{prefix}:{suffix}", f"resource:{prefix}:{suffix}"
