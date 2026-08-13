"""Catalog-backed role guard for authoritative PostgreSQL security tests."""

from __future__ import annotations

from sqlalchemy import Connection, text

from engine.database_roles import (
    ACTION_ROLE,
    CONTROL_ROLE,
    EGRESS_ROLE,
    IDENTITY_ROLE,
    LEARNING_ROLE,
    MIGRATOR_ROLE,
    OPERATOR_ROLE,
    RELEASE_OPERATOR_ROLE,
    RUNTIME_ROLE,
    SCHEDULER_ROLE,
    WORKER_ROLE,
    expected_database_role_facts,
    observe_database_role_facts,
    observe_sensitive_database_role_facts,
)


def _assert_non_owner_role(connection: Connection, expected_role: str) -> None:
    """Reject any application session with authority outside its exact login."""

    observed = observe_database_role_facts(connection)
    expected = expected_database_role_facts(expected_role)
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


def assert_migrator_role(connection: Connection) -> None:
    """Require the explicit migration login for schema and seed operations."""

    row = (
        connection.execute(
            text(
                """
            SELECT current_user AS current_role,
                   session_user AS session_role,
                   role.rolsuper AS is_superuser,
                   role.rolbypassrls AS bypasses_rls
            FROM pg_roles AS role
            WHERE role.rolname = current_user
            """
            )
        )
        .mappings()
        .one()
    )
    if dict(row) != {
        "current_role": MIGRATOR_ROLE,
        "session_role": MIGRATOR_ROLE,
        "is_superuser": False,
        "bypasses_rls": False,
    }:
        raise AssertionError("migration authority requires the exact migrator login")


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


def assert_scheduler_role(connection: Connection) -> None:
    """Require the dedicated content-free File scheduler login."""

    _assert_non_owner_role(connection, SCHEDULER_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def _assert_no_owned_objects_or_role_members(connection: Connection) -> None:
    """Reject object ownership and incoming memberships for sensitive roles."""

    if observe_sensitive_database_role_facts(connection) != (True, True):
        raise AssertionError(
            "PostgreSQL sensitive application authority must own no database "
            "objects and have no role memberships in either direction"
        )


def assert_learning_role(connection: Connection) -> None:
    """Require the dedicated least-privilege ContextLearning login."""

    _assert_non_owner_role(connection, LEARNING_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_release_operator_role(connection: Connection) -> None:
    """Require the candidate-observation-only Release operator login."""

    _assert_non_owner_role(connection, RELEASE_OPERATOR_ROLE)
    _assert_no_owned_objects_or_role_members(connection)


def assert_security_operator_role(connection: Connection) -> None:
    """Require the dedicated restricted security-audit login."""

    _assert_non_owner_role(connection, OPERATOR_ROLE)
    _assert_no_owned_objects_or_role_members(connection)
