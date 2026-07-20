from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from engine.persistence import (
    DatabaseConfiguration,
    assert_runtime_role,
    create_database_engine,
)
from engine.persistence.configuration import (
    MIGRATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)

pytestmark = pytest.mark.integration


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


def test_migration_runtime_and_worker_roles_have_reviewed_capabilities(
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> None:
    configurations = (
        migration_configuration,
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

    assert set(results) == {MIGRATOR_ROLE, RUNTIME_ROLE, WORKER_ROLE}
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


def test_runtime_and_worker_are_not_owners_or_migrator_members(
    guarded_runtime_engine: Engine,
    worker_configuration: DatabaseConfiguration,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    try:
        engines = (guarded_runtime_engine, worker_engine)
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
        worker_engine.dispose()


def test_runtime_and_worker_have_no_create_or_temporary_table_privilege(
    guarded_runtime_engine: Engine,
    worker_configuration: DatabaseConfiguration,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    try:
        for engine in (guarded_runtime_engine, worker_engine):
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
            pytest.raises(AssertionError, match="non-owner runtime role"),
        ):
            assert_runtime_role(connection)
    finally:
        migration_engine.dispose()
