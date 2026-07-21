from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from engine.persistence import (
    DatabaseConfiguration,
    assert_runtime_role,
    create_database_engine,
)
from engine.persistence.configuration import (
    ACCESS_POLICY_DEFINER_ROLE,
    CONTROL_ROLE,
    MIGRATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)
from scripts.provision_database_roles import (
    RoleProvisioningContract,
    provision_security_roles,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]


def role_attributes(engine: Engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    """
                    SELECT
                        current_user,
                        role.rolsuper,
                        role.rolcreaterole,
                        role.rolcreatedb,
                        role.rolcanlogin,
                        role.rolreplication,
                        role.rolbypassrls,
                        role.rolinherit
                    FROM pg_roles AS role
                    WHERE role.rolname = current_user
                    """
                )
            ).one()
        )


def test_server_is_postgresql_17_with_pgvector_0_8_5(
    guarded_runtime_engine: Engine,
) -> None:
    with guarded_runtime_engine.connect() as connection:
        version_number = connection.execute(
            text("SELECT current_setting('server_version_num')::integer")
        ).scalar_one()
        extension_version = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()

    assert version_number // 10_000 == 17
    assert extension_version == "0.8.5"


def test_migration_control_runtime_and_worker_roles_have_reviewed_capabilities(
    migration_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> None:
    configurations = (
        migration_configuration,
        control_configuration,
        runtime_configuration,
        worker_configuration,
    )
    results: dict[str, tuple[object, ...]] = {}
    for configuration in configurations:
        engine = create_database_engine(configuration)
        try:
            results[configuration.expected_role] = role_attributes(engine)
        finally:
            engine.dispose()

    assert set(results) == {
        MIGRATOR_ROLE,
        CONTROL_ROLE,
        RUNTIME_ROLE,
        WORKER_ROLE,
    }
    for role_name, attributes in results.items():
        assert attributes == (
            role_name,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
        )


def test_post_init_role_provisioning_repairs_a_legacy_volume_idempotently(
    control_configuration: DatabaseConfiguration,
) -> None:
    contract = RoleProvisioningContract(
        database_name=os.environ["POSTGRES_DB"],
        bootstrap_role=os.environ["POSTGRES_USER"],
        bootstrap_password=os.environ["POSTGRES_PASSWORD"],
        postgres_port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
        migrator_role=MIGRATOR_ROLE,
        control_role=CONTROL_ROLE,
        control_password=os.environ["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        definer_role=ACCESS_POLICY_DEFINER_ROLE,
    )
    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        command.downgrade(alembic_configuration, "20260721_0004")
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            bootstrap_connection.execute(
                f"REVOKE {ACCESS_POLICY_DEFINER_ROLE} FROM {MIGRATOR_ROLE}"
            )
            for role_name in (ACCESS_POLICY_DEFINER_ROLE, CONTROL_ROLE):
                bootstrap_connection.execute(f"DROP OWNED BY {role_name}")
                bootstrap_connection.execute(f"DROP ROLE {role_name}")
            bootstrap_connection.commit()
            missing_roles = bootstrap_connection.execute(
                """
                SELECT count(*)
                FROM pg_roles
                WHERE rolname IN (%s, %s)
                """,
                (CONTROL_ROLE, ACCESS_POLICY_DEFINER_ROLE),
            ).fetchone()
            assert missing_roles == (0,)

            provision_security_roles(bootstrap_connection, contract)
            bootstrap_connection.commit()
            provision_security_roles(bootstrap_connection, contract)
            bootstrap_connection.commit()
            facts = bootstrap_connection.execute(
                """
                SELECT
                    control.rolcanlogin,
                    control.rolsuper,
                    control.rolinherit,
                    definer.rolcanlogin,
                    definer.rolsuper,
                    definer.rolinherit,
                    membership.admin_option,
                    membership.inherit_option,
                    membership.set_option
                FROM pg_roles AS control
                CROSS JOIN pg_roles AS definer
                JOIN pg_auth_members AS membership
                  ON membership.roleid = definer.oid
                JOIN pg_roles AS migrator
                  ON migrator.oid = membership.member
                WHERE control.rolname = %s
                  AND definer.rolname = %s
                  AND migrator.rolname = %s
                """,
                (CONTROL_ROLE, ACCESS_POLICY_DEFINER_ROLE, MIGRATOR_ROLE),
            ).fetchone()
            assert facts == (
                True,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                True,
            )

        command.upgrade(alembic_configuration, "head")
        control_engine = create_database_engine(control_configuration)
        try:
            assert role_attributes(control_engine)[0] == CONTROL_ROLE
        finally:
            control_engine.dispose()
    finally:
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            provision_security_roles(bootstrap_connection, contract)
        command.upgrade(alembic_configuration, "head")


def test_control_runtime_and_worker_are_not_owners_or_migrator_members(
    guarded_runtime_engine: Engine,
    control_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    control_engine = create_database_engine(control_configuration)
    try:
        engines = (control_engine, guarded_runtime_engine, worker_engine)
        for engine in engines:
            with engine.connect() as connection:
                facts = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT
                                pg_get_userbyid(database.datdba) = current_user,
                                pg_get_userbyid(namespace.nspowner) = current_user,
                                pg_has_role(current_user, :migrator, 'MEMBER'),
                                pg_has_role(current_user, :migrator, 'USAGE'),
                                has_database_privilege(
                                    current_user, current_database(), 'CREATE'
                                ),
                                has_schema_privilege(
                                    current_user, 'public', 'CREATE'
                                )
                            FROM pg_database AS database
                            JOIN pg_namespace AS namespace
                              ON namespace.nspname = 'public'
                            WHERE database.datname = current_database()
                            """
                        ),
                        {"migrator": MIGRATOR_ROLE},
                    ).one()
                )
            assert facts == (False, False, False, False, False, False)
    finally:
        control_engine.dispose()
        worker_engine.dispose()


def test_control_runtime_and_worker_have_no_create_or_temporary_table_privilege(
    guarded_runtime_engine: Engine,
    control_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    control_engine = create_database_engine(control_configuration)
    try:
        for engine in (control_engine, guarded_runtime_engine, worker_engine):
            with engine.connect() as connection:
                privileges = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT
                                has_database_privilege(
                                    current_user, current_database(), 'CREATE'
                                ),
                                has_database_privilege(
                                    current_user, current_database(), 'TEMPORARY'
                                ),
                                has_schema_privilege(
                                    current_user, 'public', 'CREATE'
                                )
                            """
                        )
                    ).one()
                )
            assert privileges == (False, False, False)
    finally:
        control_engine.dispose()
        worker_engine.dispose()


def test_migrator_owns_database_schema_and_alembic_metadata(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            owners = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            pg_get_userbyid(database.datdba),
                            pg_get_userbyid(namespace.nspowner),
                            pg_get_userbyid(relation.relowner)
                        FROM pg_database AS database
                        JOIN pg_namespace AS namespace
                          ON namespace.nspname = 'public'
                        JOIN pg_class AS relation
                          ON relation.relnamespace = namespace.oid
                         AND relation.relname = 'alembic_version'
                        WHERE database.datname = current_database()
                        """
                    )
                ).one()
            )
        assert owners == (MIGRATOR_ROLE, MIGRATOR_ROLE, MIGRATOR_ROLE)
    finally:
        engine.dispose()


def test_role_guard_passes_runtime_and_rejects_owner_credentials(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    with guarded_runtime_engine.connect() as connection:
        assert_runtime_role(connection)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with (
            migration_engine.connect() as connection,
            pytest.raises(AssertionError, match="exact non-owner login"),
        ):
            assert_runtime_role(connection)
    finally:
        migration_engine.dispose()


@pytest.mark.parametrize(
    ("membership_options", "inherits_probe_privilege"),
    [
        ("WITH SET TRUE, INHERIT FALSE, ADMIN FALSE", False),
        ("WITH SET FALSE, INHERIT TRUE, ADMIN FALSE", True),
        ("WITH SET FALSE, INHERIT FALSE, ADMIN TRUE", False),
    ],
)
def test_role_guard_rejects_every_unrelated_role_membership(
    guarded_runtime_engine: Engine,
    membership_options: str,
    inherits_probe_privilege: bool,
) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    ) as bootstrap_connection:
        bootstrap_connection.execute("DROP ROLE IF EXISTS context_engine_guard_probe")
        bootstrap_connection.execute(
            "CREATE ROLE context_engine_guard_probe NOLOGIN NOSUPERUSER"
        )
        bootstrap_connection.execute(
            "GRANT SELECT ON public.alembic_version TO context_engine_guard_probe"
        )
        bootstrap_connection.execute(
            f"GRANT context_engine_guard_probe TO {RUNTIME_ROLE} "
            f"{membership_options}"
        )
        bootstrap_connection.commit()
        try:
            with guarded_runtime_engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "current_user, 'public.alembic_version', 'SELECT')"
                    )
                ).scalar_one() is inherits_probe_privilege
            with (
                guarded_runtime_engine.connect() as connection,
                pytest.raises(AssertionError, match="role_memberships"),
            ):
                assert_runtime_role(connection)
        finally:
            bootstrap_connection.execute(
                f"REVOKE context_engine_guard_probe FROM {RUNTIME_ROLE}"
            )
            bootstrap_connection.execute(
                "REVOKE SELECT ON public.alembic_version "
                "FROM context_engine_guard_probe"
            )
            bootstrap_connection.execute("DROP ROLE context_engine_guard_probe")
            bootstrap_connection.commit()
