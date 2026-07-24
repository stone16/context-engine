from __future__ import annotations

import pytest

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
    DatabaseConfigurationError,
    DatabasePurpose,
    load_database_configuration,
    load_harness_database_configurations,
)


def _database_environment() -> dict[str, str]:
    return {
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
            "postgresql+psycopg://context_engine_migrator:migration-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_CONTROL_DATABASE_URL": (
            "postgresql+psycopg://context_engine_control:control-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_IDENTITY_DATABASE_URL": (
            "postgresql+psycopg://context_engine_identity:identity-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_EGRESS_DATABASE_URL": (
            "postgresql+psycopg://context_engine_egress:egress-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_ACTION_DATABASE_URL": (
            "postgresql+psycopg://context_engine_action:action-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": (
            "postgresql+psycopg://context_engine_runtime:runtime-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_WORKER_DATABASE_URL": (
            "postgresql+psycopg://context_engine_worker:worker-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_LEARNING_DATABASE_URL": (
            "postgresql+psycopg://context_engine_learning:learning-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL": (
            "postgresql+psycopg://context_engine_security_operator:operator-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_TEST_DATABASE_URL": (
            "postgresql+psycopg://context_engine_runtime:runtime-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_MIGRATOR_ROLE": MIGRATOR_ROLE,
        "CONTEXT_ENGINE_CONTROL_ROLE": CONTROL_ROLE,
        "CONTEXT_ENGINE_IDENTITY_ROLE": IDENTITY_ROLE,
        "CONTEXT_ENGINE_EGRESS_ROLE": EGRESS_ROLE,
        "CONTEXT_ENGINE_ACTION_ROLE": ACTION_ROLE,
        "CONTEXT_ENGINE_RUNTIME_ROLE": RUNTIME_ROLE,
        "CONTEXT_ENGINE_WORKER_ROLE": WORKER_ROLE,
        "CONTEXT_ENGINE_LEARNING_ROLE": LEARNING_ROLE,
        "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE": OPERATOR_ROLE,
    }


def test_learning_configuration_requires_its_exact_credential_boundary() -> None:
    environment = _database_environment()

    configuration = load_database_configuration(DatabasePurpose.LEARNING, environment)

    assert configuration.expected_role == LEARNING_ROLE
    assert configuration.url.username == LEARNING_ROLE
    environment.pop("CONTEXT_ENGINE_LEARNING_DATABASE_URL")
    with pytest.raises(
        DatabaseConfigurationError,
        match="CONTEXT_ENGINE_LEARNING_DATABASE_URL",
    ):
        load_database_configuration(DatabasePurpose.LEARNING, environment)


def test_harness_exposes_learning_as_a_distinct_login() -> None:
    configurations = load_harness_database_configurations(_database_environment())

    assert configurations.learning.expected_role == LEARNING_ROLE
    assert {
        configurations.migration.expected_role,
        configurations.control.expected_role,
        configurations.identity.expected_role,
        configurations.egress.expected_role,
        configurations.action.expected_role,
        configurations.runtime.expected_role,
        configurations.worker.expected_role,
        configurations.learning.expected_role,
        configurations.operator.expected_role,
    } == {
        MIGRATOR_ROLE,
        CONTROL_ROLE,
        IDENTITY_ROLE,
        EGRESS_ROLE,
        ACTION_ROLE,
        RUNTIME_ROLE,
        WORKER_ROLE,
        LEARNING_ROLE,
        OPERATOR_ROLE,
    }
    assert configurations.security_test.url == configurations.runtime.url
