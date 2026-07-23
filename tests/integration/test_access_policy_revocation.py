from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.access_policy import (
    MAX_ACCESS_VERSION,
    MAX_POLICY_EPOCH,
    AccessChangeRejected,
    AccessPolicyControlUnavailable,
    PostgreSQLAccessPolicyControl,
    ResourceAccessRevocation,
)
from engine.persistence.configuration import (
    ACCESS_POLICY_DEFINER_ROLE,
    CONTROL_ROLE,
    MIGRATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)

pytestmark = pytest.mark.integration
CHECKED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class AccessFixture:
    organization_a: UUID
    organization_b: UUID
    user_a: UUID
    user_b: UUID
    membership_a: UUID
    membership_b: UUID
    revision_a: UUID
    revision_b: UUID
    principal_a: str
    principal_b: str
    resource_a_one: str
    resource_a_two: str
    resource_b: str


def _resource_rows(fixture: AccessFixture) -> tuple[tuple[object, ...], ...]:
    return (
        (
            fixture.organization_a,
            fixture.resource_a_one,
            fixture.revision_a,
            fixture.principal_a,
        ),
        (
            fixture.organization_a,
            fixture.resource_a_two,
            fixture.revision_a,
            fixture.principal_a,
        ),
        (
            fixture.organization_b,
            fixture.resource_b,
            fixture.revision_b,
            fixture.principal_b,
        ),
    )


@pytest.fixture
def access_fixture(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[AccessFixture]:
    fixture = AccessFixture(
        organization_a=uuid4(),
        organization_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        membership_a=uuid4(),
        membership_b=uuid4(),
        revision_a=uuid4(),
        revision_b=uuid4(),
        principal_a=f"principal:{uuid4()}",
        principal_b=f"principal:{uuid4()}",
        resource_a_one=f"resource:{uuid4()}",
        resource_a_two=f"resource:{uuid4()}",
        resource_b=f"resource:{uuid4()}",
    )
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
                    "organization_a": fixture.organization_a,
                    "organization_b": fixture.organization_b,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_account (user_id)
                    VALUES (:user_a), (:user_b)
                    """
                ),
                {"user_a": fixture.user_a, "user_b": fixture.user_b},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES
                    (
                        :organization_a, :membership_a, :user_a, 'active',
                        1, :valid_from, NULL
                    ),
                    (
                        :organization_b, :membership_b, :user_b, 'active',
                        1, :valid_from, NULL
                    )
                    """
                ),
                {
                    "organization_a": fixture.organization_a,
                    "organization_b": fixture.organization_b,
                    "membership_a": fixture.membership_a,
                    "membership_b": fixture.membership_b,
                    "user_a": fixture.user_a,
                    "user_b": fixture.user_b,
                    "valid_from": CHECKED_AT - timedelta(days=1),
                },
            )
            for organization_id, resource_ref, revision_id, _ in _resource_rows(
                fixture
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO context_resource (
                            organization_id, resource_ref, source_ref,
                            active_revision_id, tombstoned
                        ) VALUES (
                            :organization_id, :resource_ref, :source_ref,
                            :revision_id, FALSE
                        )
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "resource_ref": resource_ref,
                        "source_ref": f"source:{organization_id}",
                        "revision_id": revision_id,
                    },
                )
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
            for organization_id, resource_ref, _, principal_ref in _resource_rows(
                fixture
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO resource_access_policy (
                            organization_id, resource_ref, principal_ref,
                            access_version, access_state, revoked_at
                        ) VALUES (
                            :organization_id, :resource_ref, :principal_ref,
                            1, 'allowed', NULL
                        )
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "resource_ref": resource_ref,
                        "principal_ref": principal_ref,
                    },
                )
        yield fixture
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_revision "
                    "DISABLE TRIGGER context_revision_reject_mutation"
                ),
            )
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        DELETE FROM resource_access_policy
                        WHERE organization_id IN (
                            :organization_a, :organization_b
                        )
                        """
                    ),
                    {
                        "organization_a": fixture.organization_a,
                        "organization_b": fixture.organization_b,
                    },
                )
                connection.execute(
                    text(
                        """
                        DELETE FROM context_revision
                        WHERE organization_id IN (
                            :organization_a, :organization_b
                        )
                        """
                    ),
                    {
                        "organization_a": fixture.organization_a,
                        "organization_b": fixture.organization_b,
                    },
                )
                connection.execute(
                    text(
                        """
                        DELETE FROM context_resource
                        WHERE organization_id IN (
                            :organization_a, :organization_b
                        )
                        """
                    ),
                    {
                        "organization_a": fixture.organization_a,
                        "organization_b": fixture.organization_b,
                    },
                )
                connection.execute(
                    text(
                        """
                        DELETE FROM membership
                        WHERE organization_id IN (
                            :organization_a, :organization_b
                        )
                        """
                    ),
                    {
                        "organization_a": fixture.organization_a,
                        "organization_b": fixture.organization_b,
                    },
                )
                connection.execute(
                    text(
                        "DELETE FROM user_account "
                        "WHERE user_id IN (:user_a, :user_b)"
                    ),
                    {"user_a": fixture.user_a, "user_b": fixture.user_b},
                )
                connection.execute(
                    text(
                        """
                        DELETE FROM organization
                        WHERE organization_id IN (
                            :organization_a, :organization_b
                        )
                        """
                    ),
                    {
                        "organization_a": fixture.organization_a,
                        "organization_b": fixture.organization_b,
                    },
                )
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_revision "
                        "ENABLE TRIGGER context_revision_reject_mutation"
                    )
                )
        migration_engine.dispose()


def _command(
    organization_id: UUID,
    resource_ref: str,
    principal_ref: str,
    *,
    expected_access_version: int = 1,
) -> ResourceAccessRevocation:
    return ResourceAccessRevocation(
        organization_id=organization_id,
        resource_ref=resource_ref,
        principal_ref=principal_ref,
        expected_access_version=expected_access_version,
    )


def _state(
    migration_engine: Engine,
    organization_id: UUID,
    resource_ref: str,
    principal_ref: str,
) -> tuple[int, str, int, bool]:
    with migration_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    epoch.policy_epoch,
                    access.access_state,
                    access.access_version,
                    access.revoked_at IS NOT NULL AS has_revoked_at
                FROM organization_policy_epoch AS epoch
                JOIN resource_access_policy AS access
                  ON access.organization_id = epoch.organization_id
                WHERE epoch.organization_id = :organization_id
                  AND access.resource_ref = :resource_ref
                  AND access.principal_ref = :principal_ref
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
                "principal_ref": principal_ref,
            },
        ).one()
    return (
        row.policy_epoch,
        row.access_state,
        row.access_version,
        row.has_revoked_at,
    )


@pytest.mark.security_evidence(id="PG-REVOCATION-006", layer="postgres")
def test_change_access_atomically_revokes_exact_grant_and_advances_epoch(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    try:
        control = PostgreSQLAccessPolicyControl(control_engine)

        epoch = control.change_access(
            _command(
                access_fixture.organization_a,
                access_fixture.resource_a_one,
                access_fixture.principal_a,
            )
        )

        assert epoch.organization_id == access_fixture.organization_a
        assert epoch.value == 2
        assert _state(
            migration_engine,
            access_fixture.organization_a,
            access_fixture.resource_a_one,
            access_fixture.principal_a,
        ) == (2, "revoked", 2, True)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


@pytest.mark.parametrize("failure", ["missing", "already-revoked", "access-overflow"])
def test_rejected_access_change_rolls_back_epoch_and_policy_together(
    failure: str,
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    control = PostgreSQLAccessPolicyControl(control_engine)
    resource_ref = access_fixture.resource_a_one
    try:
        if failure == "already-revoked":
            control.change_access(
                _command(
                    access_fixture.organization_a,
                    resource_ref,
                    access_fixture.principal_a,
                )
            )
            expected_state = (2, "revoked", 2, True)
            expected_version = 2
        elif failure == "access-overflow":
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE resource_access_policy
                        SET access_version = :maximum
                        WHERE organization_id = :organization_id
                          AND resource_ref = :resource_ref
                          AND principal_ref = :principal_ref
                        """
                    ),
                    {
                        "maximum": MAX_ACCESS_VERSION,
                        "organization_id": access_fixture.organization_a,
                        "resource_ref": resource_ref,
                        "principal_ref": access_fixture.principal_a,
                    },
                )
            expected_state = (1, "allowed", MAX_ACCESS_VERSION, False)
            expected_version = MAX_ACCESS_VERSION
        else:
            resource_ref = "resource:missing"
            expected_state = None
            expected_version = 1

        with pytest.raises(AccessChangeRejected):
            control.change_access(
                _command(
                    access_fixture.organization_a,
                    resource_ref,
                    access_fixture.principal_a,
                    expected_access_version=expected_version,
                )
            )

        with migration_engine.connect() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT policy_epoch
                    FROM organization_policy_epoch
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": access_fixture.organization_a},
            ).scalar_one() == (2 if failure == "already-revoked" else 1)
        if expected_state is not None:
            assert _state(
                migration_engine,
                access_fixture.organization_a,
                access_fixture.resource_a_one,
                access_fixture.principal_a,
            ) == expected_state
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_epoch_overflow_rejects_before_exposing_any_access_change(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE organization_policy_epoch
                    SET policy_epoch = :maximum
                    WHERE organization_id = :organization_id
                    """
                ),
                {
                    "maximum": MAX_POLICY_EPOCH,
                    "organization_id": access_fixture.organization_a,
                },
            )

        with pytest.raises(AccessChangeRejected):
            PostgreSQLAccessPolicyControl(control_engine).change_access(
                _command(
                    access_fixture.organization_a,
                    access_fixture.resource_a_one,
                    access_fixture.principal_a,
                )
            )

        assert _state(
            migration_engine,
            access_fixture.organization_a,
            access_fixture.resource_a_one,
            access_fixture.principal_a,
        ) == (MAX_POLICY_EPOCH, "allowed", 1, False)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_late_database_fault_rolls_back_completed_access_and_epoch_updates(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.test_reject_access_change()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    SECURITY INVOKER
                    SET search_path = pg_catalog
                    AS $function$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM public.resource_access_policy AS access
                            WHERE access.organization_id = NEW.organization_id
                              AND access.access_state = 'revoked'
                              AND access.access_version = 2
                        ) THEN
                            RAISE EXCEPTION USING
                                ERRCODE = 'P0001',
                                MESSAGE = 'access update was not completed';
                        END IF;
                        RAISE EXCEPTION USING
                            ERRCODE = '40001',
                            MESSAGE = 'injected late access-change failure';
                    END;
                    $function$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER test_reject_access_change
                    AFTER UPDATE ON public.organization_policy_epoch
                    FOR EACH ROW
                    EXECUTE FUNCTION public.test_reject_access_change()
                    """
                )
            )
        try:
            with pytest.raises(AccessPolicyControlUnavailable):
                PostgreSQLAccessPolicyControl(control_engine).change_access(
                    _command(
                        access_fixture.organization_a,
                        access_fixture.resource_a_one,
                        access_fixture.principal_a,
                    )
                )
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER test_reject_access_change "
                        "ON public.organization_policy_epoch"
                    )
                )
                connection.execute(
                    text("DROP FUNCTION public.test_reject_access_change()")
                )

        assert _state(
            migration_engine,
            access_fixture.organization_a,
            access_fixture.resource_a_one,
            access_fixture.principal_a,
        ) == (1, "allowed", 1, False)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_same_organization_concurrent_revocations_have_no_lost_epoch_bump(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    control = PostgreSQLAccessPolicyControl(control_engine)
    commands = (
        _command(
            access_fixture.organization_a,
            access_fixture.resource_a_one,
            access_fixture.principal_a,
        ),
        _command(
            access_fixture.organization_a,
            access_fixture.resource_a_two,
            access_fixture.principal_a,
        ),
    )
    try:
        with migration_engine.connect() as lock_connection, psycopg.connect(
            host="127.0.0.1",
            port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            autocommit=True,
        ) as observer_connection:
            lock_transaction = lock_connection.begin()
            try:
                lock_connection.execute(
                    text(
                        """
                        SELECT policy_epoch
                        FROM organization_policy_epoch
                        WHERE organization_id = :organization_id
                        FOR UPDATE
                        """
                    ),
                    {"organization_id": access_fixture.organization_a},
                ).scalar_one()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = tuple(
                        executor.submit(control.change_access, command)
                        for command in commands
                    )
                    deadline = monotonic() + 10
                    waiting_count = 0
                    while monotonic() < deadline:
                        observed = observer_connection.execute(
                            """
                            SELECT count(*)
                            FROM pg_stat_activity
                            WHERE usename = %s
                              AND state = 'active'
                              AND wait_event_type = 'Lock'
                            """,
                            (CONTROL_ROLE,),
                        ).fetchone()
                        assert observed is not None
                        waiting_count = observed[0]
                        if waiting_count == 2:
                            break
                        sleep(0.01)
                    lock_transaction.commit()
                    epochs = tuple(
                        future.result(timeout=10) for future in futures
                    )
            finally:
                if lock_transaction.is_active:
                    lock_transaction.rollback()

        assert waiting_count == 2
        assert {epoch.value for epoch in epochs} == {2, 3}
        for command in commands:
            assert _state(
                migration_engine,
                command.organization_id,
                command.resource_ref,
                command.principal_ref,
            ) == (3, "revoked", 2, True)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_revoking_org_a_does_not_change_org_b_epoch_or_access(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    try:
        PostgreSQLAccessPolicyControl(control_engine).change_access(
            _command(
                access_fixture.organization_a,
                access_fixture.resource_a_one,
                access_fixture.principal_a,
            )
        )

        assert _state(
            migration_engine,
            access_fixture.organization_b,
            access_fixture.resource_b,
            access_fixture.principal_b,
        ) == (1, "allowed", 1, False)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_runtime_can_read_own_epoch_and_grant_but_cannot_mutate_them(
    guarded_runtime_engine: Engine,
    access_fixture: AccessFixture,
) -> None:
    settings = {
        "app.organization_id": str(access_fixture.organization_a),
        "app.actor_kind": "user",
        "app.user_id": str(access_fixture.user_a),
        "app.membership_id": str(access_fixture.membership_a),
        "app.membership_version": "1",
        "app.principal_ref": access_fixture.principal_a,
        "app.request_id": f"request:{uuid4()}",
        "app.authentication_binding_ref": f"binding:{uuid4()}",
        "app.checked_at": CHECKED_AT.isoformat().replace("+00:00", "Z"),
    }
    with guarded_runtime_engine.begin() as connection:
        for name, value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        assert connection.execute(
            text("SELECT policy_epoch FROM organization_policy_epoch")
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT resource_ref FROM resource_access_policy "
                "ORDER BY resource_ref"
            )
        ).scalars().all() == sorted(
            [access_fixture.resource_a_one, access_fixture.resource_a_two]
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            connection.execute(
                text(
                    """
                    UPDATE organization_policy_epoch
                    SET policy_epoch = policy_epoch + 1
                    """
                )
            )


def test_control_role_cannot_bypass_atomic_operation_with_direct_dml(
    control_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    try:
        with control_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config("
                    "'app.organization_id', :organization_id, true"
                    ")"
                ),
                {"organization_id": str(access_fixture.organization_a)},
            )
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(
                    text(
                        """
                        UPDATE resource_access_policy
                        SET access_state = 'revoked',
                            access_version = access_version + 1,
                            revoked_at = statement_timestamp()
                        WHERE organization_id = :organization_id
                        """
                    ),
                    {"organization_id": access_fixture.organization_a},
                )
    finally:
        control_engine.dispose()


def test_control_operation_rejects_a_cross_organization_session_binding(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    control_engine = create_database_engine(control_configuration)
    migration_engine = create_database_engine(migration_configuration)
    try:
        with (
            control_engine.begin() as connection,
            pytest.raises(DBAPIError, match="access change was not accepted"),
        ):
            connection.execute(
                text(
                    "SELECT set_config("
                    "'app.organization_id', :organization_id, true"
                    ")"
                ),
                {"organization_id": str(access_fixture.organization_b)},
            )
            connection.execute(
                text(
                    """
                    SELECT public.context_control_revoke_resource_access(
                        :organization_id,
                        :resource_ref,
                        :principal_ref,
                        1
                    )
                    """
                ),
                {
                    "organization_id": access_fixture.organization_a,
                    "resource_ref": access_fixture.resource_a_one,
                    "principal_ref": access_fixture.principal_a,
                },
            )

        assert _state(
            migration_engine,
            access_fixture.organization_a,
            access_fixture.resource_a_one,
            access_fixture.principal_a,
        ) == (1, "allowed", 1, False)
    finally:
        control_engine.dispose()
        migration_engine.dispose()


def test_runtime_and_worker_cannot_execute_control_operation(
    guarded_runtime_engine: Engine,
    worker_configuration: DatabaseConfiguration,
    access_fixture: AccessFixture,
) -> None:
    engines = [
        guarded_runtime_engine,
        create_database_engine(worker_configuration),
    ]
    try:
        for engine in engines:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config("
                        "'app.organization_id', :organization_id, true"
                        ")"
                    ),
                    {"organization_id": str(access_fixture.organization_a)},
                )
                with pytest.raises(DBAPIError, match="permission denied"):
                    connection.execute(
                        text(
                            """
                            SELECT public.context_control_revoke_resource_access(
                                :organization_id,
                                :resource_ref,
                                :principal_ref,
                                1
                            )
                            """
                        ),
                        {
                            "organization_id": access_fixture.organization_a,
                            "resource_ref": access_fixture.resource_a_one,
                            "principal_ref": access_fixture.principal_a,
                        },
                    )
    finally:
        engines[1].dispose()


def test_control_function_and_table_grants_seal_the_only_mutation_path(
    migration_configuration: DatabaseConfiguration,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            function_security = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(function_record.proowner),
                            function_record.prosecdef,
                            function_record.proconfig
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname =
                              'context_control_revoke_resource_access'
                          AND pg_get_function_identity_arguments(
                              function_record.oid
                          ) = 'requested_organization_id uuid, '
                              'requested_resource_ref text, '
                              'requested_principal_ref text, '
                              'expected_access_version bigint'
                        """
                    )
                ).one()
            )
            function_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
                            privilege.privilege_type
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(
                                function_record.proacl,
                                acldefault('f', function_record.proowner)
                            )
                        ) AS privilege
                        LEFT JOIN pg_roles AS grantee
                          ON grantee.oid = privilege.grantee
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname =
                              'context_control_revoke_resource_access'
                        """
                    )
                )
            }
            relevant_table_grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
                            relation.relname AS table_name,
                            privilege.privilege_type
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(
                                relation.relacl,
                                acldefault('r', relation.relowner)
                            )
                        ) AS privilege
                        LEFT JOIN pg_roles AS grantee
                          ON grantee.oid = privilege.grantee
                        WHERE namespace.nspname = 'public'
                          AND relation.relname IN (
                              'organization_policy_epoch',
                              'resource_access_policy'
                          )
                          AND COALESCE(grantee.rolname, 'PUBLIC') IN (
                              'PUBLIC',
                              :definer_role,
                              :control_role,
                              :runtime_role,
                              :worker_role
                          )
                        """
                    ),
                    {
                        "definer_role": ACCESS_POLICY_DEFINER_ROLE,
                        "control_role": CONTROL_ROLE,
                        "runtime_role": RUNTIME_ROLE,
                        "worker_role": WORKER_ROLE,
                    },
                )
            }
            definer_role_security = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            role.rolcanlogin,
                            role.rolsuper,
                            role.rolbypassrls,
                            role.rolinherit,
                            role.rolcreaterole,
                            role.rolcreatedb,
                            role.rolreplication,
                            has_database_privilege(
                                role.oid, current_database(), 'CREATE'
                            ),
                            has_database_privilege(
                                role.oid, current_database(), 'TEMPORARY'
                            ),
                            has_schema_privilege(
                                role.oid, 'public', 'CREATE'
                            ),
                            has_schema_privilege(
                                role.oid, 'public', 'USAGE'
                            ),
                            pg_has_role(:control_role, role.oid, 'USAGE'),
                            pg_has_role(:runtime_role, role.oid, 'USAGE'),
                            pg_has_role(:worker_role, role.oid, 'USAGE')
                        FROM pg_roles AS role
                        WHERE role.rolname = :definer_role
                        """
                    ),
                    {
                        "definer_role": ACCESS_POLICY_DEFINER_ROLE,
                        "control_role": CONTROL_ROLE,
                        "runtime_role": RUNTIME_ROLE,
                        "worker_role": WORKER_ROLE,
                    },
                ).one()
            )
            definer_policies = {
                (row.tablename, row.cmd, tuple(row.roles), row.qual, row.with_check)
                for row in connection.execute(
                    text(
                        """
                        SELECT tablename, cmd, roles, qual, with_check
                        FROM pg_policies
                        WHERE schemaname = 'public'
                          AND policyname LIKE '%access_policy_definer_%'
                        """
                    )
                )
            }
            definer_memberships = {
                (
                    row.member_role_name,
                    row.admin_option,
                    row.inherit_option,
                    row.set_option,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            member_role.rolname AS member_role_name,
                            membership.admin_option,
                            membership.inherit_option,
                            membership.set_option
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS granted_role
                          ON granted_role.oid = membership.roleid
                        JOIN pg_roles AS member_role
                          ON member_role.oid = membership.member
                        WHERE granted_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }
            roles_granted_to_definer = {
                row.granted_role_name
                for row in connection.execute(
                    text(
                        """
                        SELECT granted_role.rolname AS granted_role_name
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS member_role
                          ON member_role.oid = membership.member
                        JOIN pg_roles AS granted_role
                          ON granted_role.oid = membership.roleid
                        WHERE member_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }
            definer_owned_relations = {
                (row.nspname, row.relname, row.relkind)
                for row in connection.execute(
                    text(
                        """
                        SELECT namespace.nspname, relation.relname, relation.relkind
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_roles AS owner_role
                          ON owner_role.oid = relation.relowner
                        WHERE owner_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }
            definer_owned_routines = {
                (row.nspname, row.proname, row.identity_arguments)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            namespace.nspname,
                            function_record.proname,
                            pg_get_function_identity_arguments(
                                function_record.oid
                            ) AS identity_arguments
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        JOIN pg_roles AS owner_role
                          ON owner_role.oid = function_record.proowner
                        WHERE owner_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }
            definer_owned_namespaces = {
                row.nspname
                for row in connection.execute(
                    text(
                        """
                        SELECT namespace.nspname
                        FROM pg_namespace AS namespace
                        JOIN pg_roles AS owner_role
                          ON owner_role.oid = namespace.nspowner
                        WHERE owner_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }
            definer_owned_databases = {
                row.datname
                for row in connection.execute(
                    text(
                        """
                        SELECT database_record.datname
                        FROM pg_database AS database_record
                        JOIN pg_roles AS owner_role
                          ON owner_role.oid = database_record.datdba
                        WHERE owner_role.rolname = :definer_role
                        """
                    ),
                    {"definer_role": ACCESS_POLICY_DEFINER_ROLE},
                )
            }

        assert function_security == (
            ACCESS_POLICY_DEFINER_ROLE,
            True,
            ["search_path=pg_catalog"],
        )
        assert function_grants == {
            (ACCESS_POLICY_DEFINER_ROLE, "EXECUTE"),
            (CONTROL_ROLE, "EXECUTE"),
        }
        assert relevant_table_grants == {
            (ACCESS_POLICY_DEFINER_ROLE, "organization_policy_epoch", "SELECT"),
            (ACCESS_POLICY_DEFINER_ROLE, "organization_policy_epoch", "UPDATE"),
            (ACCESS_POLICY_DEFINER_ROLE, "resource_access_policy", "SELECT"),
            (ACCESS_POLICY_DEFINER_ROLE, "resource_access_policy", "UPDATE"),
            (RUNTIME_ROLE, "organization_policy_epoch", "SELECT"),
            (RUNTIME_ROLE, "resource_access_policy", "SELECT"),
        }
        assert definer_role_security == (
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
        )
        assert definer_memberships == {
            (MIGRATOR_ROLE, False, False, True),
        }
        assert roles_granted_to_definer == set()
        assert definer_owned_relations == set()
        assert definer_owned_routines == {
            (
                "public",
                "context_control_revoke_resource_access",
                "requested_organization_id uuid, requested_resource_ref text, "
                "requested_principal_ref text, expected_access_version bigint",
            ),
            (
                "public",
                "context_control_tombstone_file_resource",
                "requested_organization_id uuid, requested_source_id uuid, "
                "requested_resource_ref text, requested_event_ref text, "
                "requested_event_sequence bigint, "
                "requested_cleanup_intent_id uuid",
            ),
            (
                "public",
                "context_control_offboard_file_source",
                "requested_organization_id uuid, requested_source_id uuid, "
                "requested_cleanup_intent_id uuid",
            ),
            (
                "public",
                "context_runtime_file_source_lifecycle_allows",
                "requested_organization_id uuid, requested_source_ref text",
            ),
        }
        assert definer_owned_namespaces == set()
        assert definer_owned_databases == set()
        assert {
            (table, command, roles)
            for table, command, roles, _, _ in definer_policies
        } == {
            (
                "organization_policy_epoch",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "resource_access_policy",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "organization_policy_epoch",
                "UPDATE",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "resource_access_policy",
                "UPDATE",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "context_resource",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "context_resource",
                "UPDATE",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_resource_cleanup_intent",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_resource_cleanup_intent",
                "INSERT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "context_source",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "context_source",
                "UPDATE",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_import_job",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_import_job",
                "UPDATE",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_source_cleanup_intent",
                "SELECT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
            (
                "file_source_cleanup_intent",
                "INSERT",
                (ACCESS_POLICY_DEFINER_ROLE,),
            ),
        }
        for _, command, _, using_expression, check_expression in definer_policies:
            if command == "SELECT":
                assert check_expression is None
                tenant_expression = using_expression
            elif command == "INSERT":
                assert using_expression is None
                tenant_expression = check_expression
            else:
                assert using_expression == check_expression
                tenant_expression = using_expression
            assert tenant_expression is not None
            assert "app.organization_id" in tenant_expression
    finally:
        migration_engine.dispose()
