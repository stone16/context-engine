"""Fail-closed database configuration for each process and test purpose."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

MIGRATOR_ROLE = "context_engine_migrator"
CONTROL_ROLE = "context_engine_control"
IDENTITY_ROLE = "context_engine_identity"
EGRESS_ROLE = "context_engine_egress"
ACTION_ROLE = "context_engine_action"
ACTION_PREPARE_DEFINER_ROLE = "context_engine_action_prepare_definer"
EGRESS_GRANT_DEFINER_ROLE = "context_engine_egress_grant_definer"
DELIVERY_EVIDENCE_DEFINER_ROLE = "context_engine_delivery_evidence_definer"
ACCESS_POLICY_DEFINER_ROLE = "context_engine_access_policy_definer"
WORKER_LEASE_DEFINER_ROLE = "context_engine_worker_lease_definer"
CONTEXT_RUN_READER_DEFINER_ROLE = "context_engine_context_run_reader_definer"
RUNTIME_ROLE = "context_engine_runtime"
WORKER_ROLE = "context_engine_worker"
LEARNING_ROLE = "context_engine_learning"
OPERATOR_ROLE = "context_engine_security_operator"
RELEASE_DEFINER_ROLE = "context_engine_release_definer"


class DatabasePurpose(Enum):
    """A database credential boundary that may never fall back to another role."""

    MIGRATION = ("CONTEXT_ENGINE_MIGRATION_DATABASE_URL", MIGRATOR_ROLE)
    CONTROL_PLANE = ("CONTEXT_ENGINE_CONTROL_DATABASE_URL", CONTROL_ROLE)
    TRUSTED_IDENTITY = ("CONTEXT_ENGINE_IDENTITY_DATABASE_URL", IDENTITY_ROLE)
    TRUSTED_EGRESS = ("CONTEXT_ENGINE_EGRESS_DATABASE_URL", EGRESS_ROLE)
    TRUSTED_ACTION = ("CONTEXT_ENGINE_ACTION_DATABASE_URL", ACTION_ROLE)
    API_RUNTIME = ("CONTEXT_ENGINE_RUNTIME_DATABASE_URL", RUNTIME_ROLE)
    SUPPLY_WORKER = ("CONTEXT_ENGINE_WORKER_DATABASE_URL", WORKER_ROLE)
    LEARNING = ("CONTEXT_ENGINE_LEARNING_DATABASE_URL", LEARNING_ROLE)
    SECURITY_OPERATOR = (
        "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL",
        OPERATOR_ROLE,
    )
    SECURITY_TEST = ("CONTEXT_ENGINE_TEST_DATABASE_URL", RUNTIME_ROLE)

    @property
    def environment_variable(self) -> str:
        return self.value[0]

    @property
    def expected_role(self) -> str:
        return self.value[1]


ROLE_ENVIRONMENT_VARIABLES: dict[DatabasePurpose, str] = {
    DatabasePurpose.MIGRATION: "CONTEXT_ENGINE_MIGRATOR_ROLE",
    DatabasePurpose.CONTROL_PLANE: "CONTEXT_ENGINE_CONTROL_ROLE",
    DatabasePurpose.TRUSTED_IDENTITY: "CONTEXT_ENGINE_IDENTITY_ROLE",
    DatabasePurpose.TRUSTED_EGRESS: "CONTEXT_ENGINE_EGRESS_ROLE",
    DatabasePurpose.TRUSTED_ACTION: "CONTEXT_ENGINE_ACTION_ROLE",
    DatabasePurpose.API_RUNTIME: "CONTEXT_ENGINE_RUNTIME_ROLE",
    DatabasePurpose.SUPPLY_WORKER: "CONTEXT_ENGINE_WORKER_ROLE",
    DatabasePurpose.LEARNING: "CONTEXT_ENGINE_LEARNING_ROLE",
    DatabasePurpose.SECURITY_OPERATOR: "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE",
    DatabasePurpose.SECURITY_TEST: "CONTEXT_ENGINE_RUNTIME_ROLE",
}


class DatabaseConfigurationError(ValueError):
    """The required role-specific PostgreSQL configuration is absent or unsafe."""


@dataclass(frozen=True)
class DatabaseConfiguration:
    """A validated URL paired with the exact role the connection must report."""

    purpose: DatabasePurpose
    url: URL
    expected_role: str

    def __post_init__(self) -> None:
        if self.expected_role != self.purpose.expected_role:
            raise DatabaseConfigurationError(
                "database configuration expected role must match its purpose"
            )
        if self.url.drivername != "postgresql+psycopg":
            raise DatabaseConfigurationError(
                "database configuration must use the postgresql+psycopg driver"
            )
        if self.url.query:
            raise DatabaseConfigurationError(
                "database configuration URL must not contain query parameters"
            )
        if self.url.username != self.expected_role:
            raise DatabaseConfigurationError(
                "database configuration URL username must match its expected role"
            )
        if self.url.password is None or not str(self.url.password):
            raise DatabaseConfigurationError(
                "database configuration must contain explicit PostgreSQL credentials"
            )
        if not self.url.host or not self.url.database:
            raise DatabaseConfigurationError(
                "database configuration must contain an explicit host and database"
            )

    def __repr__(self) -> str:
        return (
            "DatabaseConfiguration("
            f"purpose={self.purpose.name}, "
            f"url={self.url.render_as_string(hide_password=True)!r}, "
            f"expected_role={self.expected_role!r})"
        )


@dataclass(frozen=True)
class HarnessDatabaseConfigurations:
    """All role-isolated URLs required by the authoritative database harness."""

    migration: DatabaseConfiguration
    control: DatabaseConfiguration
    identity: DatabaseConfiguration
    egress: DatabaseConfiguration
    action: DatabaseConfiguration
    runtime: DatabaseConfiguration
    worker: DatabaseConfiguration
    learning: DatabaseConfiguration
    operator: DatabaseConfiguration
    security_test: DatabaseConfiguration


def _require_url(variable: str, environment: Mapping[str, str]) -> URL:
    raw_url = environment.get(variable)
    if raw_url is None or not raw_url.strip():
        raise DatabaseConfigurationError(
            f"{variable} is required; database roles never fall back to another URL"
        )
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        raise DatabaseConfigurationError(
            f"{variable} must be a valid SQLAlchemy URL"
        ) from error
    if url.drivername != "postgresql+psycopg":
        raise DatabaseConfigurationError(
            f"{variable} must use the postgresql+psycopg driver"
        )
    if url.query:
        raise DatabaseConfigurationError(
            f"{variable} must not contain query parameters"
        )
    if not url.username or url.password is None or not str(url.password):
        raise DatabaseConfigurationError(
            f"{variable} must contain explicit PostgreSQL credentials"
        )
    if not url.host or not url.database:
        raise DatabaseConfigurationError(
            f"{variable} must contain an explicit host and database"
        )
    return url


def load_database_configuration(
    purpose: DatabasePurpose,
    environment: Mapping[str, str] | None = None,
) -> DatabaseConfiguration:
    """Load exactly one role URL without any privileged or cross-process fallback."""

    source = os.environ if environment is None else environment
    role_variable = ROLE_ENVIRONMENT_VARIABLES[purpose]
    configured_role = source.get(role_variable)
    if configured_role != purpose.expected_role:
        raise DatabaseConfigurationError(
            f"{role_variable} must be {purpose.expected_role!r}"
        )
    url = _require_url(purpose.environment_variable, source)
    if url.username != purpose.expected_role:
        raise DatabaseConfigurationError(
            f"{purpose.environment_variable} expected database role "
            f"{purpose.expected_role!r}"
        )
    return DatabaseConfiguration(
        purpose=purpose,
        url=url,
        expected_role=purpose.expected_role,
    )


def load_harness_database_configurations(
    environment: Mapping[str, str] | None = None,
) -> HarnessDatabaseConfigurations:
    """Load and cross-check the explicit harness credential contracts."""

    source = os.environ if environment is None else environment
    configurations = HarnessDatabaseConfigurations(
        migration=load_database_configuration(DatabasePurpose.MIGRATION, source),
        control=load_database_configuration(DatabasePurpose.CONTROL_PLANE, source),
        identity=load_database_configuration(DatabasePurpose.TRUSTED_IDENTITY, source),
        egress=load_database_configuration(DatabasePurpose.TRUSTED_EGRESS, source),
        action=load_database_configuration(DatabasePurpose.TRUSTED_ACTION, source),
        runtime=load_database_configuration(DatabasePurpose.API_RUNTIME, source),
        worker=load_database_configuration(DatabasePurpose.SUPPLY_WORKER, source),
        learning=load_database_configuration(DatabasePurpose.LEARNING, source),
        operator=load_database_configuration(DatabasePurpose.SECURITY_OPERATOR, source),
        security_test=load_database_configuration(
            DatabasePurpose.SECURITY_TEST, source
        ),
    )
    distinct_roles = {
        configurations.migration.expected_role,
        configurations.control.expected_role,
        configurations.identity.expected_role,
        configurations.egress.expected_role,
        configurations.action.expected_role,
        configurations.runtime.expected_role,
        configurations.worker.expected_role,
        configurations.learning.expected_role,
        configurations.operator.expected_role,
    }
    if len(distinct_roles) != 9:
        raise DatabaseConfigurationError(
            "migration, control, identity, egress, action, runtime, worker, "
            "learning, and security-operator "
            "database roles must be distinct"
        )
    if configurations.security_test.url != configurations.runtime.url:
        raise DatabaseConfigurationError(
            "CONTEXT_ENGINE_TEST_DATABASE_URL must exactly equal the runtime URL"
        )
    return configurations
