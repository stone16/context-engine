"""Catalog-backed role guard for authoritative PostgreSQL security tests."""

from __future__ import annotations

from sqlalchemy import Connection, text

from engine.persistence.configuration import MIGRATOR_ROLE, RUNTIME_ROLE


def assert_runtime_role(connection: Connection) -> None:
    """Reject owner, superuser, BYPASSRLS, inheriting, or CREATE-capable sessions."""

    row = connection.execute(
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
                pg_has_role(current_user, :migrator_role, 'MEMBER')
                    AS is_migrator_member,
                pg_has_role(current_user, :migrator_role, 'USAGE')
                    AS can_use_migrator,
                pg_get_userbyid(database.datdba) = current_user
                    AS owns_database,
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
                has_database_privilege(
                    current_user, current_database(), 'TEMPORARY'
                ) AS can_create_temporary_tables,
                has_schema_privilege(current_user, 'public', 'CREATE')
                    AS can_create_in_public_schema
            FROM pg_roles AS role
            JOIN pg_database AS database
              ON database.datname = current_database()
            JOIN pg_namespace AS namespace
              ON namespace.nspname = 'public'
            WHERE role.rolname = current_user
            """
        ),
        {"migrator_role": MIGRATOR_ROLE},
    ).mappings().one()
    expected = {
        "current_role": RUNTIME_ROLE,
        "session_role": RUNTIME_ROLE,
        "is_superuser": False,
        "bypasses_rls": False,
        "inherits_roles": False,
        "can_create_roles": False,
        "can_create_databases": False,
        "can_replicate": False,
        "is_migrator_member": False,
        "can_use_migrator": False,
        "owns_database": False,
        "owns_public_schema": False,
        "owns_no_public_relations": True,
        "can_create_in_database": False,
        "can_create_temporary_tables": False,
        "can_create_in_public_schema": False,
    }
    if dict(row) != expected:
        raise AssertionError(
            "PostgreSQL security integration tests require the exact non-owner "
            "runtime role with NOSUPERUSER, NOBYPASSRLS, NOINHERIT, NOCREATEROLE, "
            "NOCREATEDB, NOREPLICATION, no migrator membership or object ownership, "
            "and no database CREATE/TEMPORARY or schema CREATE privilege"
        )
