#!/usr/bin/env python3
"""Idempotently provision roles added after a harness volume was initialized."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from engine.persistence.configuration import (
    ACCESS_POLICY_DEFINER_ROLE,
    CONTROL_ROLE,
    MIGRATOR_ROLE,
)

_HEX_SECRET = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class RoleProvisioningContract:
    """Exact bootstrap facts needed to reconcile post-init harness roles."""

    database_name: str
    bootstrap_role: str
    bootstrap_password: str
    postgres_port: int
    migrator_role: str
    control_role: str
    control_password: str
    definer_role: str

    def __post_init__(self) -> None:
        for field_name in (
            "database_name",
            "bootstrap_role",
            "migrator_role",
            "control_role",
            "definer_role",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"{field_name} must be non-empty")
        if len({self.migrator_role, self.control_role, self.definer_role}) != 3:
            raise ValueError("provisioned database roles must be distinct")
        if type(self.postgres_port) is not int or not 1 <= self.postgres_port <= 65535:
            raise ValueError("postgres_port must be a valid TCP port")
        for field_name in ("bootstrap_password", "control_password"):
            value = getattr(self, field_name)
            if type(value) is not str or _HEX_SECRET.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a generated 64-hex secret")


def _contract_from_environment(
    environment: Mapping[str, str],
) -> RoleProvisioningContract:
    required = {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "CONTEXT_ENGINE_POSTGRES_PORT",
        "CONTEXT_ENGINE_MIGRATOR_ROLE",
        "CONTEXT_ENGINE_CONTROL_ROLE",
        "CONTEXT_ENGINE_CONTROL_PASSWORD",
    }
    missing = sorted(name for name in required if name not in environment)
    if missing:
        raise ValueError(
            "database role provisioning environment is incomplete: "
            + ", ".join(missing)
        )
    if environment["POSTGRES_DB"] != "context_engine":
        raise ValueError("database role provisioning requires the harness database")
    if environment["POSTGRES_USER"] != "context_engine_bootstrap":
        raise ValueError("database role provisioning requires the bootstrap role")
    if environment["CONTEXT_ENGINE_MIGRATOR_ROLE"] != MIGRATOR_ROLE:
        raise ValueError("database role provisioning has an invalid migrator role")
    if environment["CONTEXT_ENGINE_CONTROL_ROLE"] != CONTROL_ROLE:
        raise ValueError("database role provisioning has an invalid control role")
    try:
        postgres_port = int(environment["CONTEXT_ENGINE_POSTGRES_PORT"])
    except ValueError as error:
        raise ValueError("database role provisioning has an invalid port") from error
    return RoleProvisioningContract(
        database_name=environment["POSTGRES_DB"],
        bootstrap_role=environment["POSTGRES_USER"],
        bootstrap_password=environment["POSTGRES_PASSWORD"],
        postgres_port=postgres_port,
        migrator_role=environment["CONTEXT_ENGINE_MIGRATOR_ROLE"],
        control_role=environment["CONTEXT_ENGINE_CONTROL_ROLE"],
        control_password=environment["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        definer_role=ACCESS_POLICY_DEFINER_ROLE,
    )


def _require_bootstrap_authority(
    connection: psycopg.Connection[Any],
    contract: RoleProvisioningContract,
) -> None:
    observed = connection.execute(
        """
        SELECT current_database(), current_user, role.rolsuper
        FROM pg_roles AS role
        WHERE role.rolname = current_user
        """
    ).fetchone()
    if observed != (
        contract.database_name,
        contract.bootstrap_role,
        True,
    ):
        raise RuntimeError(
            "database role provisioning requires the exact harness bootstrap authority"
        )
    migrator_exists = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (contract.migrator_role,),
    ).fetchone()
    if migrator_exists != (True,):
        raise RuntimeError("database role provisioning requires the migrator role")


def _create_role_if_missing(
    connection: psycopg.Connection[Any],
    role_name: str,
) -> None:
    exists = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (role_name,),
    ).fetchone()
    if exists == (False,):
        connection.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role_name))
        )
    elif exists != (True,):
        raise RuntimeError("database role existence could not be established")


def _revoke_roles_granted_to(
    connection: psycopg.Connection[Any],
    member_role: str,
) -> None:
    granted_roles = connection.execute(
        """
        SELECT granted_role.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
        JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE member_role.rolname = %s
        """,
        (member_role,),
    ).fetchall()
    for (granted_role,) in granted_roles:
        if type(granted_role) is not str:
            raise RuntimeError("database role membership is malformed")
        connection.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(granted_role),
                sql.Identifier(member_role),
            )
        )


def _revoke_members_of(
    connection: psycopg.Connection[Any],
    granted_role: str,
) -> None:
    member_roles = connection.execute(
        """
        SELECT member_role.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
        JOIN pg_roles AS member_role ON member_role.oid = membership.member
        WHERE granted_role.rolname = %s
        """,
        (granted_role,),
    ).fetchall()
    for (member_role,) in member_roles:
        if type(member_role) is not str:
            raise RuntimeError("database role membership is malformed")
        connection.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(granted_role),
                sql.Identifier(member_role),
            )
        )


def provision_security_roles(
    connection: psycopg.Connection[Any],
    contract: RoleProvisioningContract,
) -> None:
    """Create or reconcile the two post-init roles without schema migration."""

    if type(contract) is not RoleProvisioningContract:
        raise TypeError("role provisioning contract has the wrong nominal type")
    _require_bootstrap_authority(connection, contract)
    _create_role_if_missing(connection, contract.control_role)
    _create_role_if_missing(connection, contract.definer_role)

    connection.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(
            sql.Identifier(contract.control_role),
            sql.Literal(contract.control_password),
        )
    )
    connection.execute(
        sql.SQL(
            "ALTER ROLE {} WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(contract.definer_role))
    )

    _revoke_roles_granted_to(connection, contract.control_role)
    _revoke_roles_granted_to(connection, contract.definer_role)
    _revoke_members_of(connection, contract.control_role)
    _revoke_members_of(connection, contract.definer_role)
    connection.execute(
        sql.SQL(
            "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        ).format(
            sql.Identifier(contract.definer_role),
            sql.Identifier(contract.migrator_role),
        )
    )

    for role_name in (contract.control_role, contract.definer_role):
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                sql.Identifier(contract.database_name),
                sql.Identifier(role_name),
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
                sql.Identifier(role_name)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(role_name)
            )
        )
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(contract.database_name),
            sql.Identifier(contract.control_role),
        )
    )


def main() -> int:
    try:
        contract = _contract_from_environment(os.environ)
        with psycopg.connect(
            host="127.0.0.1",
            port=contract.postgres_port,
            dbname=contract.database_name,
            user=contract.bootstrap_role,
            password=contract.bootstrap_password,
            connect_timeout=5,
        ) as connection:
            provision_security_roles(connection, contract)
    except (psycopg.Error, RuntimeError, TypeError, ValueError):
        print("database security-role provisioning failed", file=sys.stderr)
        return 1
    print("PostgreSQL harness security roles are provisioned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
