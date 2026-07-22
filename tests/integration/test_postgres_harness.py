from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
    assert_runtime_role,
    assert_security_operator_role,
    create_database_engine,
)
from engine.persistence.configuration import (
    ACCESS_POLICY_DEFINER_ROLE,
    CONTEXT_RUN_READER_DEFINER_ROLE,
    CONTROL_ROLE,
    MIGRATOR_ROLE,
    OPERATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_LEASE_DEFINER_ROLE,
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


def test_server_has_pinned_postgresql_pgvector_and_bootstrap_pgcrypto(
    guarded_runtime_engine: Engine,
) -> None:
    with guarded_runtime_engine.connect() as connection:
        version_number = connection.execute(
            text("SELECT current_setting('server_version_num')::integer")
        ).scalar_one()
        extensions = {
            str(row.extname): str(row.extversion)
            for row in connection.execute(
                text(
                    """
                    SELECT extension.extname, extension.extversion
                    FROM pg_extension AS extension
                    WHERE extension.extname IN ('vector', 'pgcrypto')
                    """
                )
            )
        }

    assert version_number // 10_000 == 17
    assert extensions == {"pgcrypto": "1.3", "vector": "0.8.5"}


def test_all_login_roles_have_reviewed_capabilities(
    migration_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
) -> None:
    configurations = (
        migration_configuration,
        control_configuration,
        runtime_configuration,
        worker_configuration,
        operator_configuration,
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
        OPERATOR_ROLE,
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
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
) -> None:
    contract = RoleProvisioningContract(
        database_name=os.environ["POSTGRES_DB"],
        bootstrap_role=os.environ["POSTGRES_USER"],
        bootstrap_password=os.environ["POSTGRES_PASSWORD"],
        postgres_port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
        migrator_role=MIGRATOR_ROLE,
        control_role=CONTROL_ROLE,
        control_password=os.environ["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        security_operator_role=OPERATOR_ROLE,
        security_operator_password=os.environ[
            "CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD"
        ],
        definer_role=ACCESS_POLICY_DEFINER_ROLE,
        worker_lease_definer_role=WORKER_LEASE_DEFINER_ROLE,
        context_run_reader_definer_role=CONTEXT_RUN_READER_DEFINER_ROLE,
    )
    alembic_configuration = Config(ROOT / "alembic.ini")
    try:
        guarded_control_engine.dispose()
        guarded_operator_engine.dispose()
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
            for role_name in (
                ACCESS_POLICY_DEFINER_ROLE,
                WORKER_LEASE_DEFINER_ROLE,
                CONTEXT_RUN_READER_DEFINER_ROLE,
                CONTROL_ROLE,
                OPERATOR_ROLE,
            ):
                bootstrap_connection.execute(f"DROP OWNED BY {role_name}")
                bootstrap_connection.execute(f"DROP ROLE {role_name}")
            bootstrap_connection.commit()
            missing_roles = bootstrap_connection.execute(
                """
                SELECT count(*)
                FROM pg_roles
                WHERE rolname IN (%s, %s, %s, %s, %s)
                """,
                (
                    CONTROL_ROLE,
                    ACCESS_POLICY_DEFINER_ROLE,
                    WORKER_LEASE_DEFINER_ROLE,
                    CONTEXT_RUN_READER_DEFINER_ROLE,
                    OPERATOR_ROLE,
                ),
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
                    operator.rolcanlogin,
                    operator.rolsuper,
                    operator.rolinherit,
                    NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS operator_membership
                        WHERE operator_membership.member = operator.oid
                    ),
                    definer.rolcanlogin,
                    definer.rolsuper,
                    definer.rolinherit,
                    access_membership.admin_option,
                    access_membership.inherit_option,
                    access_membership.set_option,
                    worker_definer.rolcanlogin,
                    worker_definer.rolsuper,
                    worker_definer.rolinherit,
                    worker_membership.admin_option,
                    worker_membership.inherit_option,
                    worker_membership.set_option,
                    reader_definer.rolcanlogin,
                    reader_definer.rolsuper,
                    reader_definer.rolcreaterole,
                    reader_definer.rolcreatedb,
                    reader_definer.rolinherit,
                    reader_definer.rolreplication,
                    reader_definer.rolbypassrls,
                    reader_membership.admin_option,
                    reader_membership.inherit_option,
                    reader_membership.set_option,
                    NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS granted_to_reader
                        WHERE granted_to_reader.member = reader_definer.oid
                    ),
                    (
                        SELECT count(*)
                        FROM pg_auth_members AS reader_members
                        WHERE reader_members.roleid = reader_definer.oid
                    )
                FROM pg_roles AS control
                CROSS JOIN pg_roles AS operator
                CROSS JOIN pg_roles AS definer
                CROSS JOIN pg_roles AS worker_definer
                CROSS JOIN pg_roles AS reader_definer
                JOIN pg_auth_members AS access_membership
                  ON access_membership.roleid = definer.oid
                JOIN pg_roles AS migrator
                  ON migrator.oid = access_membership.member
                JOIN pg_auth_members AS worker_membership
                  ON worker_membership.roleid = worker_definer.oid
                 AND worker_membership.member = migrator.oid
                JOIN pg_auth_members AS reader_membership
                  ON reader_membership.roleid = reader_definer.oid
                 AND reader_membership.member = migrator.oid
                WHERE control.rolname = %s
                  AND operator.rolname = %s
                  AND definer.rolname = %s
                  AND worker_definer.rolname = %s
                  AND reader_definer.rolname = %s
                  AND migrator.rolname = %s
                """,
                (
                    CONTROL_ROLE,
                    OPERATOR_ROLE,
                    ACCESS_POLICY_DEFINER_ROLE,
                    WORKER_LEASE_DEFINER_ROLE,
                    CONTEXT_RUN_READER_DEFINER_ROLE,
                    MIGRATOR_ROLE,
                ),
            ).fetchone()
            assert facts == (
                True,
                False,
                False,
                True,
                False,
                False,
                True,
                False,
                False,
                False,
                False,
                False,
                True,
                False,
                False,
                False,
                False,
                False,
                True,
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
                True,
                1,
            )

        command.upgrade(alembic_configuration, "head")
        assert role_attributes(guarded_control_engine)[0] == CONTROL_ROLE
        with guarded_operator_engine.connect() as connection:
            assert_security_operator_role(connection)
    finally:
        guarded_control_engine.dispose()
        guarded_operator_engine.dispose()
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            provision_security_roles(bootstrap_connection, contract)
        command.upgrade(alembic_configuration, "head")


def test_context_run_reader_definer_role_has_only_its_exact_set_membership(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            role_facts = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            reader.rolcanlogin,
                            reader.rolsuper,
                            reader.rolcreaterole,
                            reader.rolcreatedb,
                            reader.rolinherit,
                            reader.rolreplication,
                            reader.rolbypassrls,
                            has_database_privilege(
                                :reader, current_database(), 'CONNECT'
                            ),
                            has_database_privilege(
                                :reader, current_database(), 'CREATE'
                            ),
                            has_database_privilege(
                                :reader, current_database(), 'TEMPORARY'
                            ),
                            has_schema_privilege(:reader, 'public', 'USAGE'),
                            has_schema_privilege(:reader, 'public', 'CREATE'),
                            pg_has_role(:migrator, :reader, 'SET')
                        FROM pg_roles AS reader
                        WHERE reader.rolname = :reader
                        """
                    ),
                    {
                        "migrator": MIGRATOR_ROLE,
                        "reader": CONTEXT_RUN_READER_DEFINER_ROLE,
                    },
                ).one()
            )
            granted_roles = connection.execute(
                text(
                    """
                    SELECT granted_role.rolname
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS granted_role
                      ON granted_role.oid = membership.roleid
                    JOIN pg_roles AS member_role
                      ON member_role.oid = membership.member
                    WHERE member_role.rolname = :reader
                    """
                ),
                {"reader": CONTEXT_RUN_READER_DEFINER_ROLE},
            ).all()
            members = connection.execute(
                text(
                    """
                    SELECT
                        member_role.rolname,
                        membership.admin_option,
                        membership.inherit_option,
                        membership.set_option
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS granted_role
                      ON granted_role.oid = membership.roleid
                    JOIN pg_roles AS member_role
                      ON member_role.oid = membership.member
                    WHERE granted_role.rolname = :reader
                    """
                ),
                {"reader": CONTEXT_RUN_READER_DEFINER_ROLE},
            ).all()
    finally:
        engine.dispose()

    assert role_facts == (
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
        True,
    )
    assert granted_roles == []
    assert [tuple(member) for member in members] == [
        (MIGRATOR_ROLE, False, False, True)
    ]


def test_worker_lease_definer_and_functions_are_narrow_and_non_public(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            role_facts = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            role.rolcanlogin,
                            role.rolsuper,
                            role.rolcreaterole,
                            role.rolcreatedb,
                            role.rolinherit,
                            role.rolreplication,
                            role.rolbypassrls
                        FROM pg_roles AS role
                        WHERE role.rolname = :definer
                        """
                    ),
                    {"definer": WORKER_LEASE_DEFINER_ROLE},
                ).one()
            )
            function_facts = connection.execute(
                text(
                    """
                    SELECT
                        procedure.proname,
                        procedure.prosecdef,
                        owner.rolname,
                        procedure.proconfig,
                        NOT EXISTS (
                            SELECT 1
                            FROM aclexplode(procedure.proacl) AS privilege
                            WHERE privilege.grantee = 0
                              AND privilege.privilege_type = 'EXECUTE'
                        ) AS no_public_execute
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    JOIN pg_roles AS owner
                      ON owner.oid = procedure.proowner
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname IN (
                          'context_worker_issue_noop_lease',
                          'context_worker_complete_noop_job'
                      )
                    ORDER BY procedure.proname
                    """
                )
            ).all()
            privileges = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            has_table_privilege(
                                :worker, 'public.worker_noop_job', 'UPDATE'
                            ),
                            has_table_privilege(
                                :worker, 'public.worker_noop_job', 'SELECT'
                            ),
                            has_table_privilege(
                                :control, 'public.worker_noop_job', 'SELECT'
                            ),
                            has_table_privilege(
                                :control, 'public.worker_noop_job', 'UPDATE'
                            ),
                            has_table_privilege(
                                :definer, 'public.worker_noop_job', 'SELECT'
                            ),
                            has_any_column_privilege(
                                :definer, 'public.worker_noop_job', 'UPDATE'
                            ),
                            has_function_privilege(
                                :control,
                                'public.context_worker_issue_noop_lease('
                                'uuid,uuid,uuid,text,text,bigint,bytea,integer)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                :worker,
                                'public.context_worker_issue_noop_lease('
                                'uuid,uuid,uuid,text,text,bigint,bytea,integer)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                :worker,
                                'public.context_worker_complete_noop_job('
                                'uuid,uuid,bigint,bytea,'
                                'timestamptz,timestamptz)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                :control,
                                'public.context_worker_complete_noop_job('
                                'uuid,uuid,bigint,bytea,'
                                'timestamptz,timestamptz)',
                                'EXECUTE'
                            )
                        """
                    ),
                    {
                        "control": CONTROL_ROLE,
                        "definer": WORKER_LEASE_DEFINER_ROLE,
                        "worker": WORKER_ROLE,
                    },
                ).one()
            )
    finally:
        engine.dispose()

    assert role_facts == (False, False, False, False, False, False, False)
    assert len(function_facts) == 2
    for _name, security_definer, owner, configuration, no_public in function_facts:
        assert security_definer is True
        assert owner == WORKER_LEASE_DEFINER_ROLE
        assert set(configuration) == {
            "row_security=on",
            "search_path=pg_catalog, pg_temp",
        }
        assert no_public is True
    assert privileges == (
        False,
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        True,
        False,
    )


def test_worker_and_control_cannot_cross_worker_lease_function_authority(
    control_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> None:
    control_engine = create_database_engine(control_configuration)
    worker_engine = create_database_engine(worker_configuration)
    issue_sql = text(
        """
        SELECT * FROM public.context_worker_issue_noop_lease(
            gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
            'supply.noop', 'context-engine-worker', 1, gen_random_bytes(32), 60
        )
        """
    )
    complete_sql = text(
        """
        SELECT * FROM public.context_worker_complete_noop_job(
            gen_random_uuid(), gen_random_uuid(), 1,
            gen_random_bytes(32), transaction_timestamp(),
            transaction_timestamp() + interval '60 seconds'
        )
        """
    )
    try:
        with (
            worker_engine.connect() as connection,
            pytest.raises(ProgrammingError, match="permission denied for function"),
        ):
            connection.execute(issue_sql).all()
        with (
            control_engine.connect() as connection,
            pytest.raises(ProgrammingError, match="permission denied for function"),
        ):
            connection.execute(complete_sql).all()
        with (
            worker_engine.connect() as connection,
            pytest.raises(ProgrammingError, match="permission denied for table"),
        ):
            connection.execute(text("UPDATE public.worker_noop_job SET state = state"))
    finally:
        control_engine.dispose()
        worker_engine.dispose()


def test_application_roles_are_not_owners_or_migrator_members(
    guarded_runtime_engine: Engine,
    control_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    guarded_operator_engine: Engine,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    control_engine = create_database_engine(control_configuration)
    try:
        engines = (
            control_engine,
            guarded_runtime_engine,
            worker_engine,
            guarded_operator_engine,
        )
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


def test_application_roles_have_no_create_or_temporary_table_privilege(
    guarded_runtime_engine: Engine,
    control_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    guarded_operator_engine: Engine,
) -> None:
    worker_engine = create_database_engine(worker_configuration)
    control_engine = create_database_engine(control_configuration)
    try:
        for engine in (
            control_engine,
            guarded_runtime_engine,
            worker_engine,
            guarded_operator_engine,
        ):
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


def test_security_operator_role_guard_passes_operator_and_rejects_runtime(
    guarded_operator_engine: Engine,
    guarded_runtime_engine: Engine,
) -> None:
    with guarded_operator_engine.connect() as connection:
        assert_security_operator_role(connection)

    with (
        guarded_runtime_engine.connect() as connection,
        pytest.raises(AssertionError, match="exact non-owner login"),
    ):
        assert_security_operator_role(connection)


@pytest.mark.parametrize(
    ("grant_sql", "revoke_sql"),
    [
        (
            f"GRANT context_engine_operator_guard_probe TO {OPERATOR_ROLE} "
            "WITH SET FALSE, INHERIT FALSE, ADMIN FALSE",
            f"REVOKE context_engine_operator_guard_probe FROM {OPERATOR_ROLE}",
        ),
        (
            f"GRANT {OPERATOR_ROLE} TO context_engine_operator_guard_probe "
            "WITH SET FALSE, INHERIT FALSE, ADMIN FALSE",
            f"REVOKE {OPERATOR_ROLE} FROM context_engine_operator_guard_probe",
        ),
    ],
)
def test_security_operator_role_guard_rejects_membership_in_either_direction(
    guarded_operator_engine: Engine,
    grant_sql: str,
    revoke_sql: str,
) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    ) as bootstrap_connection:
        bootstrap_connection.execute(
            "DROP ROLE IF EXISTS context_engine_operator_guard_probe"
        )
        bootstrap_connection.execute(
            "CREATE ROLE context_engine_operator_guard_probe NOLOGIN NOSUPERUSER"
        )
        bootstrap_connection.execute(grant_sql)
        bootstrap_connection.commit()
        try:
            with (
                guarded_operator_engine.connect() as connection,
                pytest.raises(AssertionError, match="role memberships"),
            ):
                assert_security_operator_role(connection)
        finally:
            bootstrap_connection.execute(revoke_sql)
            bootstrap_connection.execute(
                "DROP ROLE context_engine_operator_guard_probe"
            )
            bootstrap_connection.commit()


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
            f"GRANT context_engine_guard_probe TO {RUNTIME_ROLE} {membership_options}"
        )
        bootstrap_connection.commit()
        try:
            with guarded_runtime_engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT has_table_privilege("
                            "current_user, 'public.alembic_version', 'SELECT')"
                        )
                    ).scalar_one()
                    is inherits_probe_privilege
                )
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
