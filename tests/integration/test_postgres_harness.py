from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
    assert_runtime_role,
    assert_security_operator_role,
    create_database_engine,
)
from engine.persistence.configuration import (
    ACTION_ROLE,
    CONTEXT_RUN_READER_DEFINER_ROLE,
    CONTROL_ROLE,
    EGRESS_ROLE,
    IDENTITY_ROLE,
    LEARNING_ROLE,
    MIGRATOR_ROLE,
    OPERATOR_ROLE,
    RELEASE_DEFINER_ROLE,
    RUNTIME_ROLE,
    SCHEDULER_ROLE,
    WORKER_LEASE_DEFINER_ROLE,
    WORKER_ROLE,
)
from engine.persistence.role_guard import assert_learning_role
from scripts.provision_database_roles import (
    RoleProvisioningContract,
    provision_security_roles,
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
    identity_configuration: DatabaseConfiguration,
    egress_configuration: DatabaseConfiguration,
    action_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    scheduler_configuration: DatabaseConfiguration,
    learning_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
) -> None:
    configurations = (
        migration_configuration,
        control_configuration,
        identity_configuration,
        egress_configuration,
        action_configuration,
        runtime_configuration,
        worker_configuration,
        scheduler_configuration,
        learning_configuration,
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
        IDENTITY_ROLE,
        EGRESS_ROLE,
        ACTION_ROLE,
        RUNTIME_ROLE,
        WORKER_ROLE,
        SCHEDULER_ROLE,
        LEARNING_ROLE,
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
    guarded_learning_engine: Engine,
    guarded_operator_engine: Engine,
) -> None:
    probe_prefix = f"ce_probe_{uuid4().hex[:8]}"

    def probe_role(name: str) -> str:
        return f"{probe_prefix}_{name}"

    contract = RoleProvisioningContract(
        database_name=os.environ["POSTGRES_DB"],
        bootstrap_role=os.environ["POSTGRES_USER"],
        bootstrap_password=os.environ["POSTGRES_PASSWORD"],
        postgres_port=int(os.environ["CONTEXT_ENGINE_POSTGRES_PORT"]),
        migrator_role=MIGRATOR_ROLE,
        control_role=probe_role("control"),
        control_password=os.environ["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        identity_role=probe_role("identity"),
        identity_password=os.environ["CONTEXT_ENGINE_IDENTITY_PASSWORD"],
        egress_role=probe_role("egress"),
        egress_password=os.environ["CONTEXT_ENGINE_EGRESS_PASSWORD"],
        action_role=probe_role("action"),
        action_password=os.environ["CONTEXT_ENGINE_ACTION_PASSWORD"],
        scheduler_role=probe_role("scheduler"),
        scheduler_password=os.environ["CONTEXT_ENGINE_SCHEDULER_PASSWORD"],
        learning_role=probe_role("learning"),
        learning_password=os.environ["CONTEXT_ENGINE_LEARNING_PASSWORD"],
        release_operator_role=probe_role("release_operator"),
        release_operator_password=os.environ[
            "CONTEXT_ENGINE_RELEASE_OPERATOR_PASSWORD"
        ],
        security_operator_role=probe_role("security_operator"),
        security_operator_password=os.environ[
            "CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD"
        ],
        definer_role=probe_role("access_definer"),
        graph_definer_role=probe_role("graph_definer"),
        worker_lease_definer_role=probe_role("worker_definer"),
        file_dispatch_definer_role=probe_role("dispatch_definer"),
        context_run_reader_definer_role=probe_role("reader_definer"),
        release_definer_role=probe_role("release_definer"),
        delivery_evidence_definer_role=probe_role("delivery_definer"),
        citation_definer_role=probe_role("citation_definer"),
        egress_grant_definer_role=probe_role("egress_definer"),
        action_prepare_definer_role=probe_role("prepare_definer"),
        action_execute_definer_role=probe_role("execute_definer"),
    )
    probe_roles = (
        contract.control_role,
        contract.identity_role,
        contract.egress_role,
        contract.action_role,
        contract.scheduler_role,
        contract.learning_role,
        contract.release_operator_role,
        contract.security_operator_role,
        contract.definer_role,
        contract.graph_definer_role,
        contract.worker_lease_definer_role,
        contract.file_dispatch_definer_role,
        contract.context_run_reader_definer_role,
        contract.release_definer_role,
        contract.delivery_evidence_definer_role,
        contract.citation_definer_role,
        contract.egress_grant_definer_role,
        contract.action_prepare_definer_role,
        contract.action_execute_definer_role,
    )
    login_credentials = {
        contract.control_role: contract.control_password,
        contract.identity_role: contract.identity_password,
        contract.egress_role: contract.egress_password,
        contract.action_role: contract.action_password,
        contract.scheduler_role: contract.scheduler_password,
        contract.learning_role: contract.learning_password,
        contract.release_operator_role: contract.release_operator_password,
        contract.security_operator_role: contract.security_operator_password,
    }

    def drop_probe_roles() -> None:
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            existing_roles = tuple(
                row[0]
                for row in bootstrap_connection.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                    (list(probe_roles),),
                )
            )
            for role_name in existing_roles:
                bootstrap_connection.execute(
                    sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name))
                )
            for role_name in reversed(existing_roles):
                bootstrap_connection.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(role_name))
                )

    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            missing_roles = bootstrap_connection.execute(
                """
                SELECT count(*)
                FROM pg_roles
                WHERE rolname = ANY(%s)
                """,
                (list(probe_roles),),
            ).fetchone()
            assert missing_roles == (0,)

            provision_security_roles(bootstrap_connection, contract)
            bootstrap_connection.execute(
                f"GRANT {contract.control_role} TO "
                f"{contract.action_execute_definer_role}"
            )
            bootstrap_connection.execute(
                f"GRANT {contract.action_execute_definer_role} TO "
                f"{contract.action_role}"
            )
            provision_security_roles(bootstrap_connection, contract)
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
                    ),
                    citation_definer.rolcanlogin,
                    citation_definer.rolsuper,
                    citation_definer.rolcreaterole,
                    citation_definer.rolcreatedb,
                    citation_definer.rolinherit,
                    citation_definer.rolreplication,
                    citation_definer.rolbypassrls,
                    citation_membership.admin_option,
                    citation_membership.inherit_option,
                    citation_membership.set_option,
                    NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS granted_to_citation
                        WHERE granted_to_citation.member = citation_definer.oid
                    ),
                    (
                        SELECT count(*)
                        FROM pg_auth_members AS citation_members
                        WHERE citation_members.roleid = citation_definer.oid
                    ),
                    action_execute_definer.rolcanlogin,
                    action_execute_definer.rolsuper,
                    action_execute_definer.rolcreaterole,
                    action_execute_definer.rolcreatedb,
                    action_execute_definer.rolinherit,
                    action_execute_definer.rolreplication,
                    action_execute_definer.rolbypassrls,
                    action_execute_membership.admin_option,
                    action_execute_membership.inherit_option,
                    action_execute_membership.set_option,
                    NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS granted_to_action_execute
                        WHERE granted_to_action_execute.member =
                              action_execute_definer.oid
                    ),
                    (
                        SELECT count(*)
                        FROM pg_auth_members AS action_execute_members
                        WHERE action_execute_members.roleid =
                              action_execute_definer.oid
                    )
                FROM pg_roles AS control
                CROSS JOIN pg_roles AS operator
                CROSS JOIN pg_roles AS definer
                CROSS JOIN pg_roles AS worker_definer
                CROSS JOIN pg_roles AS reader_definer
                CROSS JOIN pg_roles AS citation_definer
                CROSS JOIN pg_roles AS action_execute_definer
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
                JOIN pg_auth_members AS citation_membership
                  ON citation_membership.roleid = citation_definer.oid
                 AND citation_membership.member = migrator.oid
                JOIN pg_auth_members AS action_execute_membership
                  ON action_execute_membership.roleid = action_execute_definer.oid
                 AND action_execute_membership.member = migrator.oid
                WHERE control.rolname = %s
                  AND operator.rolname = %s
                  AND definer.rolname = %s
                  AND worker_definer.rolname = %s
                  AND reader_definer.rolname = %s
                  AND citation_definer.rolname = %s
                  AND action_execute_definer.rolname = %s
                  AND migrator.rolname = %s
                """,
                (
                    contract.control_role,
                    contract.security_operator_role,
                    contract.definer_role,
                    contract.worker_lease_definer_role,
                    contract.context_run_reader_definer_role,
                    contract.citation_definer_role,
                    contract.action_execute_definer_role,
                    contract.migrator_role,
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
            dispatch_facts = bootstrap_connection.execute(
                """
                SELECT
                    dispatch.rolcanlogin,
                    dispatch.rolsuper,
                    dispatch.rolcreaterole,
                    dispatch.rolcreatedb,
                    dispatch.rolinherit,
                    dispatch.rolreplication,
                    dispatch.rolbypassrls,
                    has_database_privilege(dispatch.oid, current_database(), 'CONNECT'),
                    NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS granted_to_dispatch
                        WHERE granted_to_dispatch.member = dispatch.oid
                    ),
                    (
                        SELECT count(*)
                        FROM pg_auth_members AS dispatch_members
                        WHERE dispatch_members.roleid = dispatch.oid
                    ),
                    dispatch_membership.admin_option,
                    dispatch_membership.inherit_option,
                    dispatch_membership.set_option
                FROM pg_roles AS dispatch
                JOIN pg_auth_members AS dispatch_membership
                  ON dispatch_membership.roleid = dispatch.oid
                JOIN pg_roles AS migrator
                  ON migrator.oid = dispatch_membership.member
                WHERE dispatch.rolname = %s
                  AND migrator.rolname = %s
                """,
                (contract.file_dispatch_definer_role, contract.migrator_role),
            ).fetchone()
            assert dispatch_facts == (
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                1,
                False,
                False,
                True,
            )
            learning_facts = bootstrap_connection.execute(
                """
                SELECT
                    learning.rolcanlogin,
                    learning.rolsuper,
                    learning.rolinherit,
                    release_definer.rolcanlogin,
                    release_definer.rolsuper,
                    release_definer.rolinherit,
                    membership.admin_option,
                    membership.inherit_option,
                    membership.set_option
                FROM pg_roles AS learning
                CROSS JOIN pg_roles AS release_definer
                JOIN pg_auth_members AS membership
                  ON membership.roleid = release_definer.oid
                JOIN pg_roles AS migrator
                  ON migrator.oid = membership.member
                WHERE learning.rolname = %s
                  AND release_definer.rolname = %s
                  AND migrator.rolname = %s
                """,
                (
                    contract.learning_role,
                    contract.release_definer_role,
                    contract.migrator_role,
                ),
            ).fetchone()
            assert learning_facts == (
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
            bootstrap_connection.commit()

        for role_name, password in login_credentials.items():
            with psycopg.connect(
                host="127.0.0.1",
                port=contract.postgres_port,
                dbname=contract.database_name,
                user=role_name,
                password=password,
                connect_timeout=5,
            ) as role_connection:
                assert role_connection.execute(
                    "SELECT current_user"
                ).fetchone() == (role_name,)

        drop_probe_roles()
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
        ) as bootstrap_connection:
            remaining_probe_roles = bootstrap_connection.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
                (list(probe_roles),),
            ).fetchone()
        assert remaining_probe_roles == (0,)
        assert role_attributes(guarded_control_engine)[0] == CONTROL_ROLE
        assert role_attributes(guarded_learning_engine)[0] == LEARNING_ROLE
        with guarded_operator_engine.connect() as connection:
            assert_security_operator_role(connection)
    finally:
        drop_probe_roles()
        guarded_control_engine.dispose()
        guarded_learning_engine.dispose()
        guarded_operator_engine.dispose()


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


def test_learning_role_guard_passes_learning_and_rejects_runtime(
    guarded_learning_engine: Engine,
    guarded_runtime_engine: Engine,
) -> None:
    with guarded_learning_engine.connect() as connection:
        assert_learning_role(connection)

    with (
        guarded_runtime_engine.connect() as connection,
        pytest.raises(AssertionError, match="exact non-owner login"),
    ):
        assert_learning_role(connection)


def test_learning_and_release_definer_roles_have_exact_authority(
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
                            learning.rolcanlogin,
                            learning.rolsuper,
                            learning.rolcreaterole,
                            learning.rolcreatedb,
                            learning.rolinherit,
                            learning.rolreplication,
                            learning.rolbypassrls,
                            has_database_privilege(
                                :learning, current_database(), 'CONNECT'
                            ),
                            has_database_privilege(
                                :learning, current_database(), 'CREATE'
                            ),
                            has_database_privilege(
                                :learning, current_database(), 'TEMPORARY'
                            ),
                            has_schema_privilege(:learning, 'public', 'USAGE'),
                            has_schema_privilege(:learning, 'public', 'CREATE'),
                            definer.rolcanlogin,
                            definer.rolsuper,
                            definer.rolcreaterole,
                            definer.rolcreatedb,
                            definer.rolinherit,
                            definer.rolreplication,
                            definer.rolbypassrls,
                            has_database_privilege(
                                :definer, current_database(), 'CONNECT'
                            ),
                            has_database_privilege(
                                :definer, current_database(), 'CREATE'
                            ),
                            has_database_privilege(
                                :definer, current_database(), 'TEMPORARY'
                            ),
                            has_schema_privilege(:definer, 'public', 'USAGE'),
                            has_schema_privilege(:definer, 'public', 'CREATE'),
                            pg_has_role(:migrator, :definer, 'SET')
                        FROM pg_roles AS learning
                        CROSS JOIN pg_roles AS definer
                        WHERE learning.rolname = :learning
                          AND definer.rolname = :definer
                        """
                    ),
                    {
                        "definer": RELEASE_DEFINER_ROLE,
                        "learning": LEARNING_ROLE,
                        "migrator": MIGRATOR_ROLE,
                    },
                ).one()
            )
            memberships = [
                tuple(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            granted.rolname,
                            member.rolname,
                            membership.admin_option,
                            membership.inherit_option,
                            membership.set_option
                        FROM pg_auth_members AS membership
                        JOIN pg_roles AS granted
                          ON granted.oid = membership.roleid
                        JOIN pg_roles AS member
                          ON member.oid = membership.member
                        WHERE granted.rolname IN (:learning, :definer)
                           OR member.rolname IN (:learning, :definer)
                        ORDER BY granted.rolname, member.rolname
                        """
                    ),
                    {"definer": RELEASE_DEFINER_ROLE, "learning": LEARNING_ROLE},
                )
            ]
            owned_objects = [
                tuple(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT owner.rolname, dependency.classid::regclass::text,
                               dependency.objid
                        FROM pg_shdepend AS dependency
                        JOIN pg_roles AS owner
                          ON owner.oid = dependency.refobjid
                        WHERE dependency.refclassid = 'pg_authid'::regclass
                          AND dependency.deptype = 'o'
                          AND owner.rolname IN (:learning, :definer)
                        ORDER BY owner.rolname,
                                 dependency.classid::regclass::text,
                                 dependency.objid
                        """
                    ),
                    {"definer": RELEASE_DEFINER_ROLE, "learning": LEARNING_ROLE},
                )
            ]
            promote_owner = connection.execute(
                text(
                    """
                    SELECT pg_get_userbyid(procedure.proowner)
                    FROM pg_proc AS procedure
                    WHERE procedure.oid = CAST(
                        :signature AS regprocedure
                    )
                    """
                ),
                {
                    "signature": (
                        "public.context_learning_promote_release("
                        "uuid,text,text,text,text,text,text,text,text,text,text,"
                        "text,bigint,bytea,bigint,text,timestamptz,timestamptz,"
                        "text,text,text)"
                    )
                },
            ).scalar_one()
    finally:
        engine.dispose()

    assert role_facts == (
        True,
        False,
        False,
        False,
        False,
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
    assert memberships == [(RELEASE_DEFINER_ROLE, MIGRATOR_ROLE, False, False, True)]
    assert not [row for row in owned_objects if row[0] == LEARNING_ROLE]
    assert promote_owner == RELEASE_DEFINER_ROLE
    assert any(
        owner == RELEASE_DEFINER_ROLE and catalog == "pg_proc"
        for owner, catalog, _object_id in owned_objects
    )


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
