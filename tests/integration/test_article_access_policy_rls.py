from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.article_access_policy import (
    article_policy,
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    set_source_default,
    set_tenant_default,
    unique_article_refs,
)

pytestmark = pytest.mark.integration

CHECKED_AT = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("explicit_kind", "source_kind", "tenant_kind", "expected_kind", "expected_rung"),
    (
        ("private", "organization", "organization", "private", "explicit_article"),
        (None, "private", "organization", "private", "source_default"),
        (None, None, "organization", "organization", "tenant_default"),
        (None, None, None, None, "isolation"),
    ),
)
def test_production_sql_uses_exact_visibility_cascade(
    explicit_kind: str | None,
    source_kind: str | None,
    tenant_kind: str | None,
    expected_kind: str | None,
    expected_rung: str,
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    source_ref, resource_ref = unique_article_refs("sql-cascade")
    try:
        insert_organization(engine, organization_id)
        set_tenant_default(engine, organization_id, tenant_kind)
        if source_kind is not None:
            set_source_default(engine, organization_id, source_ref, source_kind)
        if explicit_kind is not None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO article_explicit_policy_setting (
                            organization_id, source_ref, resource_ref,
                            policy_kind, group_refs
                        ) VALUES (:org, :source, :resource, :kind, ARRAY[]::text[])
                        """
                    ),
                    {
                        "org": organization_id,
                        "source": source_ref,
                        "resource": resource_ref,
                        "kind": explicit_kind,
                    },
                )
        observe_source_acl(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
        )
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
        )

        assert article_policy(engine, organization_id, resource_ref) == (
            expected_kind,
            1,
            expected_rung,
        )
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


@pytest.mark.parametrize(
    ("local_kind", "source_kind", "expected_kind"),
    (
        ("organization", "private", "private"),
        ("private", "organization", "private"),
    ),
)
def test_production_sql_source_acl_floor_only_narrows(
    local_kind: str,
    source_kind: str,
    expected_kind: str,
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    source_ref, resource_ref = unique_article_refs("sql-floor")
    try:
        insert_organization(engine, organization_id)
        set_tenant_default(engine, organization_id, local_kind)
        observe_source_acl(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
            policy_kind=source_kind,
        )
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
        )

        assert article_policy(engine, organization_id, resource_ref) == (
            expected_kind,
            1,
            "tenant_default",
        )
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


@contextmanager
def _user_actor(
    engine: Engine,
    *,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
) -> Iterator[Connection]:
    settings = {
        "app.organization_id": str(organization_id),
        "app.actor_kind": "user",
        "app.user_id": str(user_id),
        "app.membership_id": str(membership_id),
        "app.membership_version": "1",
        "app.principal_ref": f"principal:{user_id}",
        "app.request_id": f"request:{uuid4()}",
        "app.authentication_binding_ref": f"binding:{uuid4()}",
        "app.checked_at": CHECKED_AT.isoformat().replace("+00:00", "Z"),
    }
    with engine.begin() as connection:
        for name, value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        yield connection


@pytest.mark.security_evidence(id="PG-ARTICLE-ACCESS-RLS-141", layer="postgres")
def test_article_policy_tables_force_rls_and_groups_authorize_at_article_atom(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    membership_a, membership_b = uuid4(), uuid4()
    revision_a, revision_b = uuid4(), uuid4()
    resource_a = f"resource:article-rls:{uuid4()}"
    resource_b = f"resource:article-rls:{uuid4()}"
    fragment_a = f"fragment:article-rls:{uuid4()}"
    fragment_b = f"fragment:article-rls:{uuid4()}"
    source_a = f"source:article-rls:{uuid4()}"
    source_b = f"source:article-rls:{uuid4()}"
    group_ref = "group:article-rls"
    parameters = {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": user_a,
        "user_b": user_b,
        "membership_a": membership_a,
        "membership_b": membership_b,
        "revision_a": revision_a,
        "revision_b": revision_b,
        "resource_a": resource_a,
        "resource_b": resource_b,
        "fragment_a": fragment_a,
        "fragment_b": fragment_b,
        "source_a": source_a,
        "source_b": source_b,
        "group_ref": group_ref,
        "group_refs": [group_ref],
        "valid_from": CHECKED_AT - timedelta(days=1),
    }
    migration_engine = create_database_engine(migration_configuration)
    article_tables = {
        "article_access_group",
        "article_access_group_membership",
        "article_access_policy",
        "article_explicit_policy_setting",
        "article_source_acl_observation",
        "organization_article_policy_default",
        "source_article_policy_default",
    }
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:org_a), (:org_b)"
                ),
                parameters,
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_a), (:user_b)"),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES
                    (:org_a, :membership_a, :user_a, 'active', 1, :valid_from, NULL),
                    (:org_b, :membership_b, :user_b, 'active', 1, :valid_from, NULL)
                    """
                ),
                parameters,
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES
                    (:org_a, :resource_a, :source_a, :revision_a, false),
                    (:org_b, :resource_b, :source_b, :revision_b, false)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES
                    (:org_a, :resource_a, :revision_a),
                    (:org_b, :resource_b, :revision_b)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content
                    ) VALUES
                    (:org_a, :resource_a, :revision_a, :fragment_a, 0, 'ORG-A'),
                    (:org_b, :resource_b, :revision_b, :fragment_b, 0, 'ORG-B')
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES
                    (:org_a, :membership_a, 1, :resource_a, 'body'),
                    (:org_b, :membership_b, 1, :resource_b, 'body')
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO article_access_group (organization_id, group_ref) "
                    "VALUES (:org_a, :group_ref), (:org_b, :group_ref)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO article_access_group_membership (
                        organization_id, group_ref, membership_id,
                        membership_version
                    ) VALUES
                    (:org_a, :group_ref, :membership_a, 1),
                    (:org_b, :group_ref, :membership_b, 1)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO article_access_policy (
                        organization_id, resource_ref, policy_version,
                        local_policy_kind, local_group_refs, policy_kind, group_refs,
                        published, resolution_rung, source_evidence_mode,
                        source_observation_status, source_observation_version,
                        source_acl_as_of, source_declared_lag_seconds,
                        fixed_at_policy_epoch
                    ) VALUES
                    (:org_a, :resource_a, 1, 'groups', CAST(:group_refs AS text[]),
                     'groups', CAST(:group_refs AS text[]), true, 'explicit_article',
                     'mirrored', 'resolved', 1, :valid_from, 0, 1),
                    (:org_b, :resource_b, 1, 'groups', CAST(:group_refs AS text[]),
                     'groups', CAST(:group_refs AS text[]), true, 'explicit_article',
                     'mirrored', 'resolved', 1, :valid_from, 0, 1)
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO revision_publication_event (
                        organization_id, resource_ref, revision_id,
                        ordinal, state, recorded_at
                    ) VALUES
                    (:org_a, :resource_a, :revision_a, 0, 'prepared', :valid_from),
                    (:org_b, :resource_b, :revision_b, 0, 'prepared', :valid_from)
                    """
                ),
                parameters,
            )

        with _user_actor(
            guarded_runtime_engine,
            organization_id=org_a,
            user_id=user_a,
            membership_id=membership_a,
        ) as connection:
            # There is deliberately no resource_access_policy row. GROUPS is
            # the only Article grant and must expose only Org A's active body.
            assert connection.execute(
                text(
                    "SELECT content FROM context_fragment "
                    "ORDER BY organization_id, resource_ref"
                )
            ).scalars().all() == ["ORG-A"]
            assert connection.execute(
                text(
                    "SELECT group_ref FROM article_access_group_membership "
                    "ORDER BY organization_id, group_ref"
                )
            ).scalars().all() == [group_ref]
            assert connection.execute(
                text(
                    "SELECT resource_ref FROM article_access_policy "
                    "ORDER BY organization_id, resource_ref"
                )
            ).scalars().all() == [resource_a]
            assert connection.execute(
                text(
                    "SELECT state FROM revision_publication_event "
                    "ORDER BY organization_id, resource_ref, ordinal"
                )
            ).scalars().all() == ["prepared"]

            # The remaining policy carriers are administrative facts and have
            # neither a Runtime table grant nor a policy that could leak them.
            for table_name in sorted(
                article_tables
                - {"article_access_group_membership", "article_access_policy"}
            ):
                savepoint = connection.begin_nested()
                try:
                    with pytest.raises(DBAPIError, match="permission denied"):
                        connection.execute(text(f"SELECT * FROM {table_name}"))
                finally:
                    savepoint.rollback()

        with migration_engine.connect() as connection:
            security = {
                (row.relname, row.relrowsecurity, row.relforcerowsecurity)
                for row in connection.execute(
                    text(
                        """
                        SELECT relation.relname, relation.relrowsecurity,
                               relation.relforcerowsecurity
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relname = ANY(CAST(:tables AS text[]))
                        """
                    ),
                    {"tables": sorted(article_tables)},
                )
            }
        assert security == {(name, True, True) for name in article_tables}
    finally:
        with migration_engine.begin() as connection:
            for table_name, trigger_name in (
                (
                    "revision_publication_event",
                    "revision_publication_event_immutable",
                ),
                ("context_fragment", "context_fragment_reject_mutation"),
                ("context_revision", "context_revision_reject_mutation"),
            ):
                connection.execute(
                    text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
                )
        try:
            with migration_engine.begin() as connection:
                for statement in (
                    "DELETE FROM membership_resource_field_right WHERE "
                    "organization_id IN (:org_a, :org_b)",
                    "DELETE FROM revision_publication_event WHERE "
                    "organization_id IN (:org_a, :org_b)",
                    "DELETE FROM context_fragment WHERE "
                    "organization_id IN (:org_a, :org_b)",
                    "DELETE FROM context_revision WHERE "
                    "organization_id IN (:org_a, :org_b)",
                    "DELETE FROM context_resource WHERE "
                    "organization_id IN (:org_a, :org_b)",
                    "DELETE FROM membership WHERE organization_id IN (:org_a, :org_b)",
                    "DELETE FROM user_account WHERE user_id IN (:user_a, :user_b)",
                    "DELETE FROM organization WHERE "
                    "organization_id IN (:org_a, :org_b)",
                ):
                    connection.execute(text(statement), parameters)
        finally:
            with migration_engine.begin() as connection:
                for table_name, trigger_name in (
                    ("context_revision", "context_revision_reject_mutation"),
                    ("context_fragment", "context_fragment_reject_mutation"),
                    (
                        "revision_publication_event",
                        "revision_publication_event_immutable",
                    ),
                ):
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                    )
            migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-ARTICLE-GROUP-INTERSECTION-141", layer="postgres")
def test_groups_floor_persists_exact_intersection_and_disjoint_groups_isolate(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    shared = "group:shared"
    local_only = "group:local-only"
    source_only = "group:source-only"
    source_ref = f"source:group-floor:{uuid4()}"
    overlap_resource = f"resource:group-overlap:{uuid4()}"
    disjoint_resource = f"resource:group-disjoint:{uuid4()}"
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    "INSERT INTO article_access_group (organization_id, group_ref) "
                    "VALUES (:org, :shared), (:org, :local_only), (:org, :source_only)"
                ),
                {
                    "org": organization_id,
                    "shared": shared,
                    "local_only": local_only,
                    "source_only": source_only,
                },
            )
            connection.execute(
                text(
                    "UPDATE organization_article_policy_default SET "
                    "policy_kind = 'groups', group_refs = CAST(:groups AS text[]) "
                    "WHERE organization_id = :org"
                ),
                {"org": organization_id, "groups": [shared, local_only]},
            )
            for resource_ref, source_groups in (
                (overlap_resource, [shared, source_only]),
                (disjoint_resource, [source_only]),
            ):
                connection.execute(
                    text(
                        "INSERT INTO context_resource (organization_id, resource_ref, "
                        "source_ref, active_revision_id, tombstoned) "
                        "VALUES (:org, :resource, :source, NULL, false)"
                    ),
                    {
                        "org": organization_id,
                        "resource": resource_ref,
                        "source": source_ref,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO article_source_acl_observation ("
                        "organization_id, source_ref, resource_ref, evidence_mode, "
                        "observation_status, policy_kind, group_refs, "
                        "observation_version, observed_at) VALUES ("
                        ":org, :source, :resource, 'mirrored', 'resolved', "
                        "'groups', CAST(:groups AS text[]), 1, statement_timestamp())"
                    ),
                    {
                        "org": organization_id,
                        "source": source_ref,
                        "resource": resource_ref,
                        "groups": source_groups,
                    },
                )
                connection.execute(
                    text("SELECT set_config('app.organization_id', :org, true)"),
                    {"org": str(organization_id)},
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT public.context_fix_article_access_policy("
                            ":org, :resource)"
                        ),
                        {"org": organization_id, "resource": resource_ref},
                    ).scalar_one()
                    is True
                )

            overlap = connection.execute(
                text(
                    "SELECT policy_kind, group_refs, published, resolution_rung "
                    "FROM article_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {"org": organization_id, "resource": overlap_resource},
            ).one()
            disjoint = connection.execute(
                text(
                    "SELECT policy_kind, group_refs, published, resolution_rung "
                    "FROM article_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {"org": organization_id, "resource": disjoint_resource},
            ).one()
        assert tuple(overlap) == (
            "groups",
            [shared],
            True,
            "tenant_default",
        )
        assert tuple(disjoint) == (
            None,
            [],
            False,
            "tenant_default",
        )
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)
