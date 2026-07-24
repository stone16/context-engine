"""Catalog-backed role guard for authoritative PostgreSQL security tests."""

from __future__ import annotations

from sqlalchemy import Connection, text

from engine.persistence.configuration import (
    ACTION_ROLE,
    CONTROL_ROLE,
    EGRESS_ROLE,
    IDENTITY_ROLE,
    LEARNING_ROLE,
    MIGRATOR_ROLE,
    OPERATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)


def _assert_non_owner_role(connection: Connection, expected_role: str) -> None:
    """Reject any application session with authority outside its exact login."""

    row = (
        connection.execute(
            text(
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
            ),
            {"migrator_role": MIGRATOR_ROLE},
        )
        .mappings()
        .one()
    )
    expected = {
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
    observed = dict(row)
    if observed != expected:
        raise AssertionError(
            "PostgreSQL authority requires the exact non-owner login with "
            "NOSUPERUSER, NOBYPASSRLS, NOINHERIT, no role memberships, no "
            "object ownership, and no database or schema creation privilege "
            f"(observed={observed!r}, expected={expected!r})"
        )


def assert_control_role(connection: Connection) -> None:
    """Require the dedicated least-privilege internal Control login."""

    _assert_non_owner_role(connection, CONTROL_ROLE)


def assert_identity_role(connection: Connection) -> None:
    """Require the dedicated trusted-identity evidence issuer login."""

    _assert_non_owner_role(connection, IDENTITY_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_egress_role(connection: Connection) -> None:
    """Require the dedicated trusted cleartext-hop consumer login."""

    _assert_non_owner_role(connection, EGRESS_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_action_role(connection: Connection) -> None:
    """Require the dedicated trusted ActionPlane database login."""

    _assert_non_owner_role(connection, ACTION_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_runtime_role(connection: Connection) -> None:
    """Reject owner, superuser, BYPASSRLS, inheriting, or CREATE-capable sessions."""

    _assert_non_owner_role(connection, RUNTIME_ROLE)


def assert_worker_role(connection: Connection) -> None:
    """Require the dedicated least-privilege Supply worker login."""

    _assert_non_owner_role(connection, WORKER_ROLE)


def _assert_no_owned_objects_or_role_members(connection: Connection) -> None:
    """Reject object ownership and incoming memberships for sensitive roles."""

    operator_facts = connection.execute(
        text(
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
    ).one()
    if tuple(operator_facts) != (True, True):
        raise AssertionError(
            "PostgreSQL sensitive application authority must own no database "
            "objects and have no role memberships in either direction"
        )


def assert_learning_role(connection: Connection) -> None:
    """Require the dedicated least-privilege ContextLearning login."""

    _assert_non_owner_role(connection, LEARNING_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_security_operator_role(connection: Connection) -> None:
    """Require the dedicated restricted security-audit login."""

    _assert_non_owner_role(connection, OPERATOR_ROLE)
    _assert_no_owned_objects_or_role_members(connection)
