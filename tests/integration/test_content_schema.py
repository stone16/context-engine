from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.configuration import MIGRATOR_ROLE, RUNTIME_ROLE, WORKER_ROLE

pytestmark = pytest.mark.integration
CHECKED_AT = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ContentFixture:
    organization_a: UUID
    organization_b: UUID
    user_a: UUID
    membership_a: UUID
    active_resource_ref: str
    active_revision_id: UUID
    active_fragment_ref: str
    denied_fragment_ref: str
    stale_revision_id: UUID
    stale_fragment_ref: str
    tombstoned_resource_ref: str
    tombstoned_revision_id: UUID
    tombstoned_fragment_ref: str
    hostile_resource_ref: str
    hostile_revision_id: UUID
    hostile_fragment_ref: str


@contextmanager
def user_actor_connection(
    engine: Engine,
    fixture: ContentFixture,
) -> Iterator[Connection]:
    settings = {
        "app.organization_id": str(fixture.organization_a),
        "app.actor_kind": "user",
        "app.user_id": str(fixture.user_a),
        "app.membership_id": str(fixture.membership_a),
        "app.membership_version": "1",
        "app.principal_ref": f"principal:{fixture.user_a}",
        "app.request_id": f"request:{uuid4()}",
        "app.authentication_binding_ref": f"binding:{uuid4()}",
        "app.checked_at": CHECKED_AT.isoformat().replace("+00:00", "Z"),
    }
    with engine.begin() as connection:
        for setting_name, setting_value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": setting_name, "value": setting_value},
            )
        yield connection


@pytest.fixture(scope="module")
def content_fixture(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[ContentFixture]:
    fixture = ContentFixture(
        organization_a=uuid4(),
        organization_b=uuid4(),
        user_a=uuid4(),
        membership_a=uuid4(),
        active_resource_ref=f"resource:{uuid4()}",
        active_revision_id=uuid4(),
        active_fragment_ref=f"fragment:{uuid4()}",
        denied_fragment_ref=f"fragment:{uuid4()}",
        stale_revision_id=uuid4(),
        stale_fragment_ref=f"fragment:{uuid4()}",
        tombstoned_resource_ref=f"resource:{uuid4()}",
        tombstoned_revision_id=uuid4(),
        tombstoned_fragment_ref=f"fragment:{uuid4()}",
        hostile_resource_ref=f"resource:{uuid4()}",
        hostile_revision_id=uuid4(),
        hostile_fragment_ref=f"fragment:{uuid4()}",
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_a), (:organization_b)
                    """
                ),
                {
                    "organization_a": fixture.organization_a,
                    "organization_b": fixture.organization_b,
                },
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_a)"),
                {"user_a": fixture.user_a},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_a, :membership_a, :user_a, 'active',
                        1, :valid_from, NULL
                    )
                    """
                ),
                {
                    "organization_a": fixture.organization_a,
                    "membership_a": fixture.membership_a,
                    "user_a": fixture.user_a,
                    "valid_from": CHECKED_AT - timedelta(days=1),
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES
                    (
                        :organization_a, :active_resource_ref, 'source:a',
                        :active_revision_id, false
                    ),
                    (
                        :organization_a, :tombstoned_resource_ref, 'source:a',
                        :tombstoned_revision_id, true
                    ),
                    (
                        :organization_b, :hostile_resource_ref, 'source:b',
                        :hostile_revision_id, false
                    )
                    """
                ),
                asdict(fixture),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES
                    (:organization_a, :active_resource_ref, :active_revision_id),
                    (:organization_a, :active_resource_ref, :stale_revision_id),
                    (
                        :organization_a, :tombstoned_resource_ref,
                        :tombstoned_revision_id
                    ),
                    (
                        :organization_b, :hostile_resource_ref,
                        :hostile_revision_id
                    )
                    """
                ),
                asdict(fixture),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content
                    ) VALUES
                    (
                        :organization_a, :active_resource_ref,
                        :active_revision_id, :active_fragment_ref,
                        0, 'AUTHORIZED-CONTENT'
                    ),
                    (
                        :organization_a, :active_resource_ref,
                        :active_revision_id, :denied_fragment_ref,
                        1, 'DENIED-SAME-ORGANIZATION'
                    ),
                    (
                        :organization_a, :active_resource_ref,
                        :stale_revision_id, :stale_fragment_ref,
                        0, 'STALE-CONTENT'
                    ),
                    (
                        :organization_a, :tombstoned_resource_ref,
                        :tombstoned_revision_id, :tombstoned_fragment_ref,
                        0, 'TOMBSTONED-CONTENT'
                    ),
                    (
                        :organization_b, :hostile_resource_ref,
                        :hostile_revision_id, :hostile_fragment_ref,
                        0, 'CROSS-ORGANIZATION-HOSTILE'
                    )
                    """
                ),
                asdict(fixture),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resource_access_policy (
                        organization_id, resource_ref, principal_ref,
                        access_version, access_state, revoked_at
                    ) VALUES
                    (
                        :organization_a, :active_resource_ref,
                        'principal:' || CAST(:user_a AS text),
                        1, 'allowed', NULL
                    ),
                    (
                        :organization_a, :tombstoned_resource_ref,
                        'principal:' || CAST(:user_a AS text),
                        1, 'allowed', NULL
                    ),
                    (
                        :organization_b, :hostile_resource_ref,
                        'principal:' || CAST(:user_a AS text),
                        1, 'allowed', NULL
                    )
                    """
                ),
                asdict(fixture),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES
                    (
                        :organization_a, :membership_a, 1,
                        :active_resource_ref, 'body'
                    ),
                    (
                        :organization_a, :membership_a, 1,
                        :tombstoned_resource_ref, 'body'
                    )
                    """
                ),
                asdict(fixture),
            )
        yield fixture
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment "
                    "DISABLE TRIGGER context_fragment_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_revision "
                    "DISABLE TRIGGER context_revision_reject_mutation"
                )
            )
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM membership_resource_field_right "
                        "WHERE organization_id IN "
                        "(:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM resource_access_policy "
                        "WHERE organization_id IN "
                        "(:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM context_fragment "
                        "WHERE organization_id IN (:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM context_revision "
                        "WHERE organization_id IN (:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM context_resource "
                        "WHERE organization_id IN (:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM membership WHERE organization_id = :organization_a"
                    ),
                    asdict(fixture),
                )
                connection.execute(
                    text("DELETE FROM user_account WHERE user_id = :user_a"),
                    asdict(fixture),
                )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id IN (:organization_a, :organization_b)"
                    ),
                    asdict(fixture),
                )
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_revision "
                        "ENABLE TRIGGER context_revision_reject_mutation"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment "
                        "ENABLE TRIGGER context_fragment_reject_mutation"
                    )
                )
        engine.dispose()


def test_runtime_rls_exposes_only_active_non_tombstoned_same_organization_rows(
    guarded_runtime_engine: Engine,
    content_fixture: ContentFixture,
) -> None:
    with user_actor_connection(guarded_runtime_engine, content_fixture) as connection:
        resource_rows = connection.execute(
            text(
                """
                SELECT resource_ref, source_ref, active_revision_id, tombstoned
                FROM context_resource
                ORDER BY resource_ref
                """
            )
        ).all()
        assert [row.resource_ref for row in resource_rows] == [
            content_fixture.active_resource_ref
        ]

        revision_rows = connection.execute(
            text(
                """
                SELECT resource_ref, revision_id
                FROM context_revision
                ORDER BY resource_ref, revision_id
                """
            )
        ).all()
        assert [(row.resource_ref, row.revision_id) for row in revision_rows] == [
            (
                content_fixture.active_resource_ref,
                content_fixture.active_revision_id,
            )
        ]

        fragments = connection.execute(
            text(
                """
                SELECT fragment_ref, content
                FROM context_fragment
                ORDER BY ordinal
                """
            )
        ).all()
        assert [(row.fragment_ref, row.content) for row in fragments] == [
            (content_fixture.active_fragment_ref, "AUTHORIZED-CONTENT"),
            (content_fixture.denied_fragment_ref, "DENIED-SAME-ORGANIZATION"),
        ]


def test_missing_actor_context_exposes_zero_content_rows(
    guarded_runtime_engine: Engine,
    content_fixture: ContentFixture,
) -> None:
    del content_fixture
    with guarded_runtime_engine.begin() as connection:
        for table_name in (
            "context_resource",
            "context_revision",
            "context_fragment",
        ):
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {table_name}")
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize("table_name", ["context_revision", "context_fragment"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_revision_and_fragment_rows_reject_in_place_mutation(
    migration_configuration: DatabaseConfiguration,
    content_fixture: ContentFixture,
    table_name: str,
    operation: str,
) -> None:
    identifier_column = (
        "revision_id" if table_name == "context_revision" else "fragment_ref"
    )
    identifier = (
        content_fixture.active_revision_id
        if table_name == "context_revision"
        else content_fixture.active_fragment_ref
    )
    statement = (
        f"UPDATE {table_name} SET {identifier_column} = {identifier_column} "
        f"WHERE organization_id = :organization_id AND {identifier_column} = :id"
        if operation == "UPDATE"
        else f"DELETE FROM {table_name} "
        f"WHERE organization_id = :organization_id AND {identifier_column} = :id"
    )
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(DBAPIError) as error, engine.begin() as connection:
            connection.execute(
                text(statement),
                {"organization_id": content_fixture.organization_a, "id": identifier},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "55000"
    finally:
        engine.dispose()


def test_content_schema_enforces_organization_inclusive_lineage(
    migration_configuration: DatabaseConfiguration,
    content_fixture: ContentFixture,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    UPDATE context_resource
                    SET active_revision_id = :hostile_revision_id
                    WHERE organization_id = :organization_a
                      AND resource_ref = :active_resource_ref
                    """
                ),
                {
                    "hostile_revision_id": content_fixture.hostile_revision_id,
                    "organization_a": content_fixture.organization_a,
                    "active_resource_ref": content_fixture.active_resource_ref,
                },
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        """
        INSERT INTO context_revision (
            organization_id, resource_ref, revision_id
        ) VALUES (
            :organization_a, :hostile_resource_ref, :new_id
        )
        """,
        """
        INSERT INTO context_fragment (
            organization_id, resource_ref, revision_id,
            fragment_ref, ordinal, content
        ) VALUES (
            :organization_a, :active_resource_ref,
            :hostile_revision_id, :new_ref, 99, 'must-not-persist'
        )
        """,
    ],
)
def test_revision_and_fragment_parents_cannot_cross_organization(
    migration_configuration: DatabaseConfiguration,
    content_fixture: ContentFixture,
    statement: str,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(statement),
                {
                    **asdict(content_fixture),
                    "new_id": uuid4(),
                    "new_ref": f"fragment:{uuid4()}",
                },
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="spaces"),
        pytest.param("\t\n\r", id="control-whitespace"),
        pytest.param("\x1c\x1d\x1e\x1f", id="python-control-whitespace"),
        pytest.param("\u0085", id="next-line"),
        pytest.param("\u00a0", id="no-break-space"),
        pytest.param("\u1680", id="ogham-space-mark"),
        pytest.param(
            "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a",
            id="unicode-quad-and-width-spaces",
        ),
        pytest.param("\u2028\u2029", id="unicode-line-separators"),
        pytest.param("\u202f\u205f\u3000", id="remaining-unicode-spaces"),
    ],
)
def test_context_fragment_rejects_blank_content(
    migration_configuration: DatabaseConfiguration,
    content_fixture: ContentFixture,
    content: str,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 9999, :content
                    )
                    """
                ),
                {
                    "organization_id": content_fixture.organization_a,
                    "resource_ref": content_fixture.active_resource_ref,
                    "revision_id": content_fixture.active_revision_id,
                    "fragment_ref": f"fragment:{uuid4()}",
                    "content": content,
                },
            )
        assert (
            getattr(getattr(error.value.orig, "diag", None), "constraint_name", None)
            == "ck_context_fragment_projection_payload"
        )
    finally:
        engine.dispose()


def test_content_tables_have_force_rls_and_least_privilege_grants(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            security = {
                row.relname: (
                    row.relrowsecurity,
                    row.relforcerowsecurity,
                    row.owner,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            relname,
                            relrowsecurity,
                            relforcerowsecurity,
                            pg_get_userbyid(relowner) AS owner
                        FROM pg_class
                        WHERE oid IN (
                            'public.context_resource'::regclass,
                            'public.context_revision'::regclass,
                            'public.context_fragment'::regclass
                        )
                        """
                    )
                )
            }
            grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.table_privileges
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'context_resource',
                              'context_revision',
                              'context_fragment'
                          )
                          AND grantee IN (
                              'PUBLIC', :runtime_role, :worker_role
                          )
                        """
                    ),
                    {"runtime_role": RUNTIME_ROLE, "worker_role": WORKER_ROLE},
                )
            }
            policies = {
                (row.tablename, row.policyname): (
                    row.cmd,
                    tuple(row.roles),
                    row.qual,
                    row.with_check,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT tablename, policyname, cmd, roles, qual, with_check
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename IN (
                              'context_resource',
                              'context_revision',
                              'context_fragment'
                          )
                        """
                    )
                )
            }
            columns: dict[str, set[str]] = {
                row.table_name: set()
                for row in connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'context_resource',
                              'context_revision',
                              'context_fragment'
                          )
                        """
                    )
                )
            }
            for row in connection.execute(
                text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN (
                          'context_resource',
                          'context_revision',
                          'context_fragment'
                      )
                    """
                )
            ):
                columns[row.table_name].add(row.column_name)
            constraints = {
                row.conname: row.definition
                for row in connection.execute(
                    text(
                        """
                        SELECT conname, pg_get_constraintdef(oid, true) AS definition
                        FROM pg_constraint
                        WHERE conrelid IN (
                            'public.context_resource'::regclass,
                            'public.context_revision'::regclass,
                            'public.context_fragment'::regclass
                        )
                        """
                    )
                )
            }
            immutability_function = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            function_record.prosecdef,
                            function_record.proconfig,
                            language_record.lanname,
                            pg_get_userbyid(function_record.proowner)
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        JOIN pg_language AS language_record
                          ON language_record.oid = function_record.prolang
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname =
                              'context_content_reject_mutation'
                        """
                    )
                ).one()
            )
            immutability_function_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.routine_privileges
                        WHERE routine_schema = 'public'
                          AND routine_name = 'context_content_reject_mutation'
                        """
                    )
                )
            }
            immutability_triggers = {
                row.relname: (row.tgname, row.tgenabled, row.tgisinternal)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            relation.relname,
                            trigger_record.tgname,
                            trigger_record.tgenabled,
                            trigger_record.tgisinternal
                        FROM pg_trigger AS trigger_record
                        JOIN pg_class AS relation
                          ON relation.oid = trigger_record.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND trigger_record.tgname IN (
                              'context_revision_reject_mutation',
                              'context_fragment_reject_mutation'
                          )
                        """
                    )
                )
            }
        assert security == {
            "context_resource": (True, True, MIGRATOR_ROLE),
            "context_revision": (True, True, MIGRATOR_ROLE),
            "context_fragment": (True, True, MIGRATOR_ROLE),
        }
        assert grants == {
            (RUNTIME_ROLE, "context_resource", "SELECT"),
            (RUNTIME_ROLE, "context_revision", "SELECT"),
            (RUNTIME_ROLE, "context_fragment", "SELECT"),
        }
        assert columns == {
            "context_resource": {
                "organization_id",
                "resource_ref",
                "source_ref",
                "active_revision_id",
                "tombstoned",
            },
            "context_revision": {
                "organization_id",
                "resource_ref",
                "revision_id",
            },
            "context_fragment": {
                "organization_id",
                "resource_ref",
                "revision_id",
                "fragment_ref",
                "ordinal",
                "projection_kind",
                "content",
            },
        }
        assert {
            "pk_context_resource",
            "fk_context_resource_organization",
            "fk_context_resource_active_revision_same_organization",
            "pk_context_revision",
            "fk_context_revision_organization",
            "fk_context_revision_resource_same_organization",
            "pk_context_fragment",
            "uq_context_fragment_revision_ordinal",
            "fk_context_fragment_organization",
            "fk_context_fragment_revision_same_organization",
            "ck_context_fragment_ordinal_nonnegative",
            "ck_context_fragment_projection_kind",
            "ck_context_fragment_projection_payload",
        } <= constraints.keys()
        content_constraint = constraints["ck_context_fragment_projection_payload"]
        assert "projection_kind = 'body'::text" in content_constraint
        assert "projection_kind = 'fields'::text" in content_constraint
        assert "content IS NULL" in content_constraint
        assert (
            "deferrable initially deferred"
            in constraints[
                "fk_context_resource_active_revision_same_organization"
            ].lower()
        )
        assert (
            constraints["fk_context_revision_resource_same_organization"]
            .lower()
            .startswith(
                "foreign key (organization_id, resource_ref) "
                "references context_resource(organization_id, resource_ref)"
            )
        )
        assert (
            constraints["fk_context_fragment_revision_same_organization"]
            .lower()
            .startswith(
                "foreign key (organization_id, resource_ref, revision_id) "
                "references context_revision(organization_id, resource_ref, "
                "revision_id)"
            )
        )
        assert immutability_function == (
            False,
            ["search_path=pg_catalog"],
            "plpgsql",
            MIGRATOR_ROLE,
        )
        assert immutability_function_grants == {(MIGRATOR_ROLE, "EXECUTE")}
        assert immutability_triggers == {
            "context_revision": (
                "context_revision_reject_mutation",
                "O",
                False,
            ),
            "context_fragment": (
                "context_fragment_reject_mutation",
                "O",
                False,
            ),
        }
        for table_name in security:
            runtime_policy = policies[(table_name, f"{table_name}_current_user_actor")]
            assert runtime_policy[0:2] == ("SELECT", (RUNTIME_ROLE,))
            assert runtime_policy[2] is not None
            assert runtime_policy[3] is None
            normalized_policy = str(runtime_policy[2]).lower()
            for required_fragment in (
                "app.organization_id",
                "app.actor_kind",
                "app.user_id",
                "app.membership_id",
                "app.membership_version",
                "app.principal_ref",
                "app.request_id",
                "app.authentication_binding_ref",
                "app.checked_at",
            ):
                assert required_fragment in normalized_policy
            assert "tombstoned" in normalized_policy
            if table_name != "context_resource":
                assert "active_revision_id" in normalized_policy
            if table_name == "context_fragment":
                assert "membership_resource_field_right" in normalized_policy
                assert "field_right.field_ref = 'body'" in normalized_policy
                assert "resource_access_policy" in normalized_policy
                assert "current_access.access_state = 'allowed'" in normalized_policy
            migrator_policy = policies[
                (table_name, f"{table_name}_migrator_administration")
            ]
            assert migrator_policy[0:2] == ("ALL", (MIGRATOR_ROLE,))
            assert str(migrator_policy[2]).lower() == "true"
            assert str(migrator_policy[3]).lower() == "true"
    finally:
        engine.dispose()
