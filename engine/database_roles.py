"""Neutral PostgreSQL role identities and read-only authority facts."""

from __future__ import annotations

from sqlalchemy import Connection, text

MIGRATOR_ROLE = "context_engine_migrator"
CONTROL_ROLE = "context_engine_control"
IDENTITY_ROLE = "context_engine_identity"
EGRESS_ROLE = "context_engine_egress"
ACTION_ROLE = "context_engine_action"
ACTION_PREPARE_DEFINER_ROLE = "context_engine_action_prepare_definer"
ACTION_EXECUTE_DEFINER_ROLE = "context_engine_action_execute_definer"
EGRESS_GRANT_DEFINER_ROLE = "context_engine_egress_grant_definer"
DELIVERY_EVIDENCE_DEFINER_ROLE = "context_engine_delivery_evidence_definer"
CITATION_DEFINER_ROLE = "context_engine_citation_definer"
ACCESS_POLICY_DEFINER_ROLE = "context_engine_access_policy_definer"
GRAPH_DEFINER_ROLE = "context_engine_graph_definer"
WORKER_LEASE_DEFINER_ROLE = "context_engine_worker_lease_definer"
FILE_DISPATCH_DEFINER_ROLE = "context_engine_file_dispatch_definer"
CONTEXT_RUN_READER_DEFINER_ROLE = "context_engine_context_run_reader_definer"
RUNTIME_ROLE = "context_engine_runtime"
WORKER_ROLE = "context_engine_worker"
SCHEDULER_ROLE = "context_engine_scheduler"
LEARNING_ROLE = "context_engine_learning"
RELEASE_OPERATOR_ROLE = "context_engine_release_operator"
OPERATOR_ROLE = "context_engine_security_operator"
RELEASE_DEFINER_ROLE = "context_engine_release_definer"

_DATABASE_ROLE_FACTS = text(
    """
    SELECT
        current_user AS current_role,
        session_user AS session_role,
        role.rolsuper AS is_superuser,
        role.rolbypassrls AS bypasses_rls,
        role.rolinherit AS inherits_roles,
        role.rolcreaterole AS can_create_roles,
        role.rolcreatedb AS can_create_databases,
        role.rolreplication AS can_replicate,
        NOT EXISTS (
            SELECT 1 FROM pg_auth_members AS membership
            WHERE membership.member = role.oid
        ) AS has_no_role_memberships,
        pg_has_role(current_user, :migrator_role, 'MEMBER')
            AS is_migrator_member,
        pg_has_role(current_user, :migrator_role, 'USAGE')
            AS can_use_migrator,
        pg_get_userbyid(database.datdba) = current_user AS owns_database,
        pg_get_userbyid(namespace.nspowner) = current_user
            AS owns_public_schema,
        NOT EXISTS (
            SELECT 1
            FROM pg_class AS relation
            JOIN pg_namespace AS relation_namespace
              ON relation_namespace.oid = relation.relnamespace
            WHERE relation_namespace.nspname = 'public'
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND relation.relowner = role.oid
        ) AS owns_no_public_relations,
        has_database_privilege(current_user, current_database(), 'CREATE')
            AS can_create_in_database,
        has_database_privilege(current_user, current_database(), 'TEMPORARY')
            AS can_create_temporary_tables,
        has_schema_privilege(current_user, 'public', 'CREATE')
            AS can_create_in_public_schema
    FROM pg_roles AS role
    JOIN pg_database AS database ON database.datname = current_database()
    JOIN pg_namespace AS namespace ON namespace.nspname = 'public'
    WHERE role.rolname = current_user
    """
)

_SENSITIVE_DATABASE_ROLE_FACTS = text(
    """
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM pg_shdepend AS dependency
            JOIN pg_roles AS owner_role
              ON owner_role.oid = dependency.refobjid
            WHERE dependency.refclassid = 'pg_authid'::regclass
              AND dependency.deptype = 'o'
              AND owner_role.rolname = current_user
        ) AS owns_no_database_objects,
        NOT EXISTS (
            SELECT 1
            FROM pg_auth_members AS membership
            JOIN pg_roles AS granted_role
              ON granted_role.oid = membership.roleid
            WHERE granted_role.rolname = current_user
        ) AS has_no_role_members
    """
)


def expected_database_role_facts(expected_role: str) -> dict[str, object]:
    """Return the exact least-privilege facts for one registered login."""

    return {
        "current_role": expected_role,
        "session_role": expected_role,
        "is_superuser": False,
        "bypasses_rls": False,
        "inherits_roles": False,
        "can_create_roles": False,
        "can_create_databases": False,
        "can_replicate": False,
        "has_no_role_memberships": True,
        "is_migrator_member": False,
        "can_use_migrator": False,
        "owns_database": False,
        "owns_public_schema": False,
        "owns_no_public_relations": True,
        "can_create_in_database": False,
        "can_create_temporary_tables": False,
        "can_create_in_public_schema": False,
    }


def observe_database_role_facts(connection: Connection) -> dict[str, object]:
    """Observe one login through a read-only PostgreSQL catalog projection."""

    row = (
        connection.execute(
            _DATABASE_ROLE_FACTS,
            {"migrator_role": MIGRATOR_ROLE},
        )
        .mappings()
        .one()
    )
    return dict(row)


def observe_sensitive_database_role_facts(
    connection: Connection,
) -> tuple[object, ...]:
    """Observe the additional no-owner/no-member facts for sensitive roles."""

    return tuple(connection.execute(_SENSITIVE_DATABASE_ROLE_FACTS).one())


__all__ = [
    "ACCESS_POLICY_DEFINER_ROLE",
    "ACTION_EXECUTE_DEFINER_ROLE",
    "ACTION_PREPARE_DEFINER_ROLE",
    "ACTION_ROLE",
    "CITATION_DEFINER_ROLE",
    "CONTEXT_RUN_READER_DEFINER_ROLE",
    "CONTROL_ROLE",
    "DELIVERY_EVIDENCE_DEFINER_ROLE",
    "EGRESS_GRANT_DEFINER_ROLE",
    "EGRESS_ROLE",
    "FILE_DISPATCH_DEFINER_ROLE",
    "GRAPH_DEFINER_ROLE",
    "IDENTITY_ROLE",
    "LEARNING_ROLE",
    "MIGRATOR_ROLE",
    "OPERATOR_ROLE",
    "RELEASE_DEFINER_ROLE",
    "RELEASE_OPERATOR_ROLE",
    "RUNTIME_ROLE",
    "SCHEDULER_ROLE",
    "WORKER_LEASE_DEFINER_ROLE",
    "WORKER_ROLE",
    "expected_database_role_facts",
    "observe_database_role_facts",
    "observe_sensitive_database_role_facts",
]
