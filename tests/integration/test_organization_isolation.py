from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from engine.persistence import (
    DatabaseConfiguration,
    create_database_engine,
    organization_transaction,
)
from engine.persistence.configuration import MIGRATOR_ROLE, RUNTIME_ROLE, WORKER_ROLE

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "engine/persistence/schema_security_manifest.yaml"


@dataclass(frozen=True, slots=True)
class OrganizationPair:
    organization_a: UUID
    organization_b: UUID


class PostgreSQLError(Protocol):
    sqlstate: str | None


def assert_sqlstate(error: DBAPIError, expected: str) -> None:
    assert cast(PostgreSQLError, error.orig).sqlstate == expected


@pytest.fixture
def organizations(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> Iterator[OrganizationPair]:
    """Create isolated security roots without using bootstrap credentials."""

    pair = OrganizationPair(uuid4(), uuid4())
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_a), (:organization_b)
                    """
                ),
                {
                    "organization_a": pair.organization_a,
                    "organization_b": pair.organization_b,
                },
            )

        yield pair
    finally:
        for organization_id in (
            pair.organization_a,
            pair.organization_b,
        ):
            with organization_transaction(
                guarded_runtime_engine, organization_id
            ) as connection:
                connection.execute(
                    text(
                        """
                        DELETE FROM organization_record
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": organization_id},
                )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM organization
                    WHERE organization_id IN (
                        :organization_a,
                        :organization_b
                    )
                    """
                ),
                {
                    "organization_a": pair.organization_a,
                    "organization_b": pair.organization_b,
                },
            )
        migration_engine.dispose()


def insert_record(
    engine: Engine,
    organization_id: UUID,
    record_id: UUID,
    payload: str,
    *,
    parent_record_id: UUID | None = None,
) -> None:
    with organization_transaction(engine, organization_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO organization_record (
                    organization_id,
                    record_id,
                    parent_record_id,
                    payload
                )
                VALUES (
                    :organization_id,
                    :record_id,
                    :parent_record_id,
                    :payload
                )
                """
            ),
            {
                "organization_id": organization_id,
                "record_id": record_id,
                "parent_record_id": parent_record_id,
                "payload": payload,
            },
        )


def visible_records(
    connection: Connection,
) -> list[tuple[UUID, UUID, UUID | None, str]]:
    return [
        (
            row.organization_id,
            row.record_id,
            row.parent_record_id,
            row.payload,
        )
        for row in connection.execute(
            text(
                """
                SELECT
                    organization_id,
                    record_id,
                    parent_record_id,
                    payload
                FROM organization_record
                ORDER BY record_id
                """
            )
        )
    ]


def test_bidirectional_rls_hides_and_blocks_wrong_organization_effects(
    guarded_runtime_engine: Engine,
    organizations: OrganizationPair,
) -> None:
    """RLS-FAIL-CLOSED-003/DB-006: the two-Organization matrix is symmetric."""

    shared_record_id = uuid4()
    records = {
        organizations.organization_a: "organization-a",
        organizations.organization_b: "organization-b",
    }
    for organization_id, payload in records.items():
        insert_record(
            guarded_runtime_engine,
            organization_id,
            shared_record_id,
            payload,
        )

    wrong_organization_effect_count = 0
    directions = (
        (organizations.organization_a, organizations.organization_b),
        (organizations.organization_b, organizations.organization_a),
    )
    for current_organization, other_organization in directions:
        with organization_transaction(
            guarded_runtime_engine, current_organization
        ) as connection:
            assert visible_records(connection) == [
                (
                    current_organization,
                    shared_record_id,
                    None,
                    records[current_organization],
                )
            ]

        with (
            pytest.raises(DBAPIError, match="row-level security"),
            organization_transaction(
                guarded_runtime_engine, current_organization
            ) as connection,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id,
                        record_id,
                        parent_record_id,
                        payload
                    )
                    VALUES (:other_organization, :record_id, NULL, :payload)
                    """
                ),
                {
                    "other_organization": other_organization,
                    "record_id": uuid4(),
                    "payload": "wrong-organization-insert",
                },
            )

        with (
            pytest.raises(DBAPIError, match="row-level security"),
            organization_transaction(
                guarded_runtime_engine, current_organization
            ) as connection,
        ):
            connection.execute(
                text(
                    """
                    UPDATE organization_record
                    SET organization_id = :other_organization,
                        record_id = :moved_record_id
                    WHERE organization_id = :current_organization
                      AND record_id = :record_id
                    """
                ),
                {
                    "current_organization": current_organization,
                    "other_organization": other_organization,
                    "record_id": shared_record_id,
                    "moved_record_id": uuid4(),
                },
            )

        with organization_transaction(
            guarded_runtime_engine, current_organization
        ) as connection:
            update_result = connection.execute(
                text(
                    """
                    UPDATE organization_record
                    SET payload = 'wrong-organization-update'
                    WHERE organization_id = :other_organization
                      AND record_id = :record_id
                    """
                ),
                {
                    "other_organization": other_organization,
                    "record_id": shared_record_id,
                },
            )
            wrong_organization_effect_count += update_result.rowcount
            assert update_result.rowcount == 0

        with organization_transaction(
            guarded_runtime_engine, current_organization
        ) as connection:
            delete_result = connection.execute(
                text(
                    """
                    DELETE FROM organization_record
                    WHERE organization_id = :other_organization
                      AND record_id = :record_id
                    """
                ),
                {
                    "other_organization": other_organization,
                    "record_id": shared_record_id,
                },
            )
            wrong_organization_effect_count += delete_result.rowcount
            assert delete_result.rowcount == 0

    assert wrong_organization_effect_count == 0
    for organization_id, payload in records.items():
        with organization_transaction(
            guarded_runtime_engine, organization_id
        ) as connection:
            assert visible_records(connection) == [
                (organization_id, shared_record_id, None, payload)
            ]


def test_composite_ownership_accepts_same_org_and_rejects_cross_org_parent(
    guarded_runtime_engine: Engine,
    organizations: OrganizationPair,
) -> None:
    """TENANT-FK-002/DB-003: parent ownership is enforced in PostgreSQL."""

    parent_a = uuid4()
    parent_b = uuid4()
    child_a = uuid4()
    insert_record(
        guarded_runtime_engine,
        organizations.organization_a,
        parent_a,
        "parent-a",
    )
    insert_record(
        guarded_runtime_engine,
        organizations.organization_b,
        parent_b,
        "parent-b",
    )

    insert_record(
        guarded_runtime_engine,
        organizations.organization_a,
        child_a,
        "same-organization-child",
        parent_record_id=parent_a,
    )

    with pytest.raises(
        IntegrityError,
        match="fk_organization_record_parent_same_organization",
    ):
        insert_record(
            guarded_runtime_engine,
            organizations.organization_a,
            uuid4(),
            "cross-organization-child",
            parent_record_id=parent_b,
        )

    with organization_transaction(
        guarded_runtime_engine, organizations.organization_a
    ) as connection:
        assert set(visible_records(connection)) == {
            (organizations.organization_a, parent_a, None, "parent-a"),
            (
                organizations.organization_a,
                child_a,
                parent_a,
                "same-organization-child",
            ),
        }


def test_organization_foreign_key_rejects_orphan_record(
    guarded_runtime_engine: Engine,
) -> None:
    """TENANT-OWNERSHIP-001: no representative row can lack a real owner."""

    nonexistent_organization = uuid4()
    with pytest.raises(
        IntegrityError,
        match="fk_organization_record_organization",
    ):
        insert_record(
            guarded_runtime_engine,
            nonexistent_organization,
            uuid4(),
            "orphan",
        )


def test_missing_tenant_context_is_fail_closed_for_every_operation(
    guarded_runtime_engine: Engine,
    organizations: OrganizationPair,
) -> None:
    """DB-001: missing reads are empty and every write errors before effects."""

    record_a = uuid4()
    record_b = uuid4()
    insert_record(
        guarded_runtime_engine,
        organizations.organization_a,
        record_a,
        "organization-a",
    )
    insert_record(
        guarded_runtime_engine,
        organizations.organization_b,
        record_b,
        "organization-b",
    )

    missing_context_fallback_count = 0
    with guarded_runtime_engine.connect() as connection:
        assert connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one_or_none() in {None, ""}
        rows = visible_records(connection)
        missing_context_fallback_count += len(rows)
        assert rows == []

    with (
        pytest.raises(DBAPIError) as insert_error,
        guarded_runtime_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                INSERT INTO organization_record (
                    organization_id,
                    record_id,
                    parent_record_id,
                    payload
                )
                VALUES (:organization_id, :record_id, NULL, :payload)
                """
            ),
            {
                "organization_id": organizations.organization_a,
                "record_id": uuid4(),
                "payload": "missing-context-insert",
            },
        )
    assert_sqlstate(insert_error.value, "42501")

    with (
        pytest.raises(DBAPIError) as update_error,
        guarded_runtime_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                UPDATE organization_record
                SET payload = 'missing-context-update'
                WHERE organization_id = :organization_id
                  AND record_id = :record_id
                """
            ),
            {
                "organization_id": organizations.organization_a,
                "record_id": record_a,
            },
        )
    assert_sqlstate(update_error.value, "42501")

    with (
        pytest.raises(DBAPIError) as delete_error,
        guarded_runtime_engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                DELETE FROM organization_record
                WHERE organization_id = :organization_id
                  AND record_id = :record_id
                """
            ),
            {
                "organization_id": organizations.organization_b,
                "record_id": record_b,
            },
        )
    assert_sqlstate(delete_error.value, "42501")

    assert missing_context_fallback_count == 0
    for organization_id, record_id, payload in (
        (organizations.organization_a, record_a, "organization-a"),
        (organizations.organization_b, record_b, "organization-b"),
    ):
        with organization_transaction(
            guarded_runtime_engine, organization_id
        ) as connection:
            assert visible_records(connection) == [
                (organization_id, record_id, None, payload)
            ]


def test_single_connection_pool_reuse_never_leaks_organization_context(
    runtime_configuration: DatabaseConfiguration,
    organizations: OrganizationPair,
) -> None:
    """DB-002: repeated pooled reuse preserves missing-context fallback = 0."""

    engine = create_database_engine(
        runtime_configuration,
        pool_size=1,
        max_overflow=0,
    )
    records = {
        organizations.organization_a: (uuid4(), "organization-a"),
        organizations.organization_b: (uuid4(), "organization-b"),
    }
    missing_context_fallback_count = 0
    backend_pids: set[int] = set()
    try:
        for organization_id, (record_id, payload) in records.items():
            insert_record(engine, organization_id, record_id, payload)

        ordered_organizations = tuple(records)
        for iteration in range(12):
            organization_id = ordered_organizations[iteration % 2]
            record_id, payload = records[organization_id]
            with organization_transaction(engine, organization_id) as connection:
                backend_pids.add(
                    connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                )
                assert visible_records(connection) == [
                    (organization_id, record_id, None, payload)
                ]

            with engine.connect() as connection:
                backend_pids.add(
                    connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                )
                assert connection.execute(
                    text("SELECT current_setting('app.organization_id', true)")
                ).scalar_one_or_none() in {None, ""}
                rows = visible_records(connection)
                missing_context_fallback_count += len(rows)
                assert rows == []

            with (
                pytest.raises(DBAPIError) as write_error,
                engine.begin() as connection,
            ):
                backend_pids.add(
                    connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                )
                assert connection.execute(
                    text("SELECT current_setting('app.organization_id', true)")
                ).scalar_one_or_none() in {None, ""}
                connection.execute(
                    text(
                        """
                        UPDATE organization_record
                        SET payload = 'unreachable-after-pool-reuse'
                        WHERE false
                        """
                    )
                )
            assert_sqlstate(write_error.value, "42501")

        assert backend_pids and len(backend_pids) == 1
        assert missing_context_fallback_count == 0
    finally:
        engine.dispose()


def test_failed_organization_transaction_rolls_back_row_and_local_context(
    runtime_configuration: DatabaseConfiguration,
    organizations: OrganizationPair,
) -> None:
    """DB-002: exception cleanup leaves neither durable data nor tenant GUC."""

    engine = create_database_engine(
        runtime_configuration,
        pool_size=1,
        max_overflow=0,
    )
    rolled_back_record = uuid4()
    backend_pid: int
    try:
        with (
            pytest.raises(LookupError, match="cancel operation"),
            organization_transaction(
                engine, organizations.organization_a
            ) as connection,
        ):
            backend_pid = connection.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id,
                        record_id,
                        parent_record_id,
                        payload
                    )
                    VALUES (:organization_id, :record_id, NULL, :payload)
                    """
                ),
                {
                    "organization_id": organizations.organization_a,
                    "record_id": rolled_back_record,
                    "payload": "must-roll-back",
                },
            )
            raise LookupError("cancel operation")

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                == backend_pid
            )
            assert connection.execute(
                text("SELECT current_setting('app.organization_id', true)")
            ).scalar_one_or_none() in {None, ""}
            assert visible_records(connection) == []

        with organization_transaction(
            engine, organizations.organization_a
        ) as connection:
            assert (
                connection.execute(
                    text(
                        """
                    SELECT count(*)
                    FROM organization_record
                    WHERE record_id = :record_id
                    """
                    ),
                    {"record_id": rolled_back_record},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_force_rls_subjects_the_table_owner_without_tenant_context(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    organizations: OrganizationPair,
) -> None:
    """DB-004: FORCE is behavioral—the owning migrator cannot bypass RLS."""

    record_id = uuid4()
    insert_record(
        guarded_runtime_engine,
        organizations.organization_a,
        record_id,
        "force-owner-oracle",
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert connection.execute(
                text("SELECT current_setting('app.organization_id', true)")
            ).scalar_one_or_none() in {None, ""}
            assert visible_records(connection) == []

        with (
            pytest.raises(DBAPIError) as update_error,
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    UPDATE organization_record
                    SET payload = 'owner-bypass-update'
                    WHERE organization_id = :organization_id
                      AND record_id = :record_id
                    """
                ),
                {
                    "organization_id": organizations.organization_a,
                    "record_id": record_id,
                },
            )
        assert_sqlstate(update_error.value, "42501")

        with (
            pytest.raises(DBAPIError) as delete_error,
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    DELETE FROM organization_record
                    WHERE organization_id = :organization_id
                      AND record_id = :record_id
                    """
                ),
                {
                    "organization_id": organizations.organization_a,
                    "record_id": record_id,
                },
            )
        assert_sqlstate(delete_error.value, "42501")

        with (
            pytest.raises(DBAPIError) as insert_error,
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO organization_record (
                        organization_id,
                        record_id,
                        parent_record_id,
                        payload
                    )
                    VALUES (:organization_id, :record_id, NULL, :payload)
                    """
                ),
                {
                    "organization_id": organizations.organization_a,
                    "record_id": uuid4(),
                    "payload": "owner-bypass-insert",
                },
            )
        assert_sqlstate(insert_error.value, "42501")
    finally:
        migration_engine.dispose()

    with organization_transaction(
        guarded_runtime_engine, organizations.organization_a
    ) as connection:
        assert visible_records(connection) == [
            (
                organizations.organization_a,
                record_id,
                None,
                "force-owner-oracle",
            )
        ]


@pytest.mark.parametrize(
    "statement",
    [
        """
        INSERT INTO organization_record (
            organization_id,
            record_id,
            parent_record_id,
            payload
        )
        SELECT :organization_id, :record_id, NULL, 'unreachable'
        WHERE false
        """,
        """
        UPDATE organization_record
        SET payload = 'unreachable'
        WHERE false
        """,
        """
        DELETE FROM organization_record
        WHERE false
        """,
    ],
)
def test_missing_context_write_guard_runs_when_no_row_reaches_rls(
    guarded_runtime_engine: Engine,
    statement: str,
) -> None:
    """DB-001: a statement-level guard rejects even zero-candidate writes."""

    with (
        pytest.raises(DBAPIError) as error,
        guarded_runtime_engine.begin() as connection,
    ):
        connection.execute(
            text(statement),
            {
                "organization_id": uuid4(),
                "record_id": uuid4(),
            },
        )
    assert_sqlstate(error.value, "42501")


def test_catalog_proves_force_rls_policy_ownership_constraints_and_grants(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    """DB-004/DB-008: catalog evidence keeps every enforcement layer active."""

    with guarded_runtime_engine.connect() as connection:
        runtime_role_facts = tuple(
            connection.execute(
                text(
                    """
                    SELECT
                        current_user,
                        pg_get_userbyid(relation.relowner),
                        role.rolsuper,
                        role.rolbypassrls,
                        pg_has_role(
                            current_user,
                            pg_get_userbyid(relation.relowner),
                            'MEMBER'
                        )
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    JOIN pg_roles AS role
                      ON role.rolname = current_user
                    WHERE namespace.nspname = 'public'
                      AND relation.relname = 'organization_record'
                    """
                )
            ).one()
        )
    assert runtime_role_facts == (
        RUNTIME_ROLE,
        MIGRATOR_ROLE,
        False,
        False,
        False,
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            catalog_tables = set(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        """
                    )
                ).scalars()
            )
            relation_owners = {
                row.relname: row.owner
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            relation.relname,
                            pg_get_userbyid(relation.relowner) AS owner
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relname IN (
                              'organization',
                              'organization_record'
                          )
                        """
                    )
                )
            }
            relation_security = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            relation.relrowsecurity,
                            relation.relforcerowsecurity,
                            pg_get_userbyid(relation.relowner)
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relname = 'organization_record'
                        """
                    )
                ).one()
            )
            policy = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            policyname,
                            permissive,
                            roles,
                            cmd,
                            qual,
                            with_check
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = 'organization_record'
                        """
                    )
                ).one()
            )
            write_guard = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            trigger_record.tgname,
                            trigger_record.tgenabled,
                            trigger_record.tgisinternal,
                            pg_get_triggerdef(trigger_record.oid, true),
                            function_record.proname,
                            pg_get_userbyid(function_record.proowner),
                            function_record.prosecdef,
                            language_record.lanname,
                            function_record.proconfig,
                            function_record.prosrc
                        FROM pg_trigger AS trigger_record
                        JOIN pg_class AS relation
                          ON relation.oid = trigger_record.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_proc AS function_record
                          ON function_record.oid = trigger_record.tgfoid
                        JOIN pg_language AS language_record
                          ON language_record.oid = function_record.prolang
                        WHERE namespace.nspname = 'public'
                          AND relation.relname = 'organization_record'
                          AND trigger_record.tgname =
                              'organization_record_write_context_guard'
                        """
                    )
                ).one()
            )
            write_guard_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.routine_privileges
                        WHERE routine_schema = 'public'
                          AND routine_name =
                              'organization_record_require_write_context'
                        """
                    )
                )
            }
            runtime_grants = {
                (row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT table_name, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND grantee = :runtime_role
                          AND table_name IN (
                              'organization',
                              'organization_record'
                          )
                        """
                    ),
                    {"runtime_role": RUNTIME_ROLE},
                )
            }
            prohibited_grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.table_privileges
                        WHERE table_schema = 'public'
                          AND grantee IN ('PUBLIC', :worker_role)
                          AND table_name IN (
                              'organization',
                              'organization_record'
                          )
                        """
                    ),
                    {"worker_role": WORKER_ROLE},
                )
            }
            column_nullability = {
                row.attname: row.attnotnull
                for row in connection.execute(
                    text(
                        """
                        SELECT attribute.attname, attribute.attnotnull
                        FROM pg_attribute AS attribute
                        JOIN pg_class AS relation
                          ON relation.oid = attribute.attrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relname = 'organization_record'
                          AND attribute.attnum > 0
                          AND NOT attribute.attisdropped
                        """
                    )
                )
            }
            constraints = {
                row.conname: row.definition
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            constraint_record.conname,
                            pg_get_constraintdef(constraint_record.oid, true)
                                AS definition
                        FROM pg_constraint AS constraint_record
                        JOIN pg_class AS relation
                          ON relation.oid = constraint_record.conrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND relation.relname IN (
                              'organization',
                              'organization_record'
                          )
                        """
                    )
                )
            }
    finally:
        migration_engine.dispose()

    manifest_document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_tables = {table["name"] for table in manifest_document["tables"]}
    assert manifest_tables == catalog_tables
    assert relation_owners == {
        "organization": MIGRATOR_ROLE,
        "organization_record": MIGRATOR_ROLE,
    }
    assert relation_security == (True, True, MIGRATOR_ROLE)
    assert policy[:4] == (
        "organization_record_organization_isolation",
        "PERMISSIVE",
        [RUNTIME_ROLE],
        "ALL",
    )
    assert policy[4] == policy[5]
    assert policy[4] is not None
    normalized_policy = str(policy[4]).lower()
    assert "organization_id" in normalized_policy
    assert "current_setting" in normalized_policy
    assert "app.organization_id" in normalized_policy
    assert "nullif" in normalized_policy

    assert write_guard[:3] == (
        "organization_record_write_context_guard",
        "O",
        False,
    )
    normalized_trigger = str(write_guard[3]).lower()
    assert "before insert or delete or update" in normalized_trigger
    assert "for each statement" in normalized_trigger
    assert "organization_record_require_write_context()" in normalized_trigger
    assert write_guard[4:9] == (
        "organization_record_require_write_context",
        MIGRATOR_ROLE,
        False,
        "plpgsql",
        ["search_path=pg_catalog"],
    )
    normalized_guard_body = str(write_guard[9]).lower()
    assert "current_setting('app.organization_id', true)" in normalized_guard_body
    assert "nullif" in normalized_guard_body
    assert "errcode = '42501'" in normalized_guard_body
    assert write_guard_grants == {
        (MIGRATOR_ROLE, "EXECUTE"),
        (RUNTIME_ROLE, "EXECUTE"),
    }

    assert runtime_grants == {
        ("organization_record", "SELECT"),
        ("organization_record", "INSERT"),
        ("organization_record", "UPDATE"),
        ("organization_record", "DELETE"),
    }
    assert prohibited_grants == set()
    assert column_nullability == {
        "organization_id": True,
        "record_id": True,
        "parent_record_id": False,
        "payload": True,
    }

    assert set(constraints) == {
        "pk_organization",
        "pk_organization_record",
        "fk_organization_record_organization",
        "fk_organization_record_parent_same_organization",
    }
    assert constraints["pk_organization_record"].lower() == (
        "primary key (organization_id, record_id)"
    )
    assert (
        constraints["fk_organization_record_organization"]
        .lower()
        .startswith(
            "foreign key (organization_id) references organization(organization_id)"
        )
    )
    assert (
        constraints["fk_organization_record_parent_same_organization"]
        .lower()
        .startswith(
            "foreign key (organization_id, parent_record_id) references "
            "organization_record(organization_id, record_id)"
        )
    )
