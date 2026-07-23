from __future__ import annotations

from collections.abc import Mapping

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import QueuePool

from engine.persistence.configuration import (
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
from engine.persistence.database import create_database_engine


def database_environment() -> dict[str, str]:
    return {
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
            "postgresql+psycopg://context_engine_migrator:migration-secret@"
            "127.0.0.1:5432/context_engine"
        ),
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": (
            "postgresql+psycopg://context_engine_runtime:runtime-secret@"
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
        "CONTEXT_ENGINE_RUNTIME_ROLE": RUNTIME_ROLE,
        "CONTEXT_ENGINE_CONTROL_ROLE": CONTROL_ROLE,
        "CONTEXT_ENGINE_IDENTITY_ROLE": IDENTITY_ROLE,
        "CONTEXT_ENGINE_EGRESS_ROLE": EGRESS_ROLE,
        "CONTEXT_ENGINE_WORKER_ROLE": WORKER_ROLE,
        "CONTEXT_ENGINE_LEARNING_ROLE": LEARNING_ROLE,
        "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE": OPERATOR_ROLE,
    }


@pytest.mark.parametrize(
    ("purpose", "missing_name"),
    [
        (DatabasePurpose.MIGRATION, "CONTEXT_ENGINE_MIGRATION_DATABASE_URL"),
        (DatabasePurpose.CONTROL_PLANE, "CONTEXT_ENGINE_CONTROL_DATABASE_URL"),
        (DatabasePurpose.TRUSTED_IDENTITY, "CONTEXT_ENGINE_IDENTITY_DATABASE_URL"),
        (DatabasePurpose.TRUSTED_EGRESS, "CONTEXT_ENGINE_EGRESS_DATABASE_URL"),
        (DatabasePurpose.API_RUNTIME, "CONTEXT_ENGINE_RUNTIME_DATABASE_URL"),
        (DatabasePurpose.SUPPLY_WORKER, "CONTEXT_ENGINE_WORKER_DATABASE_URL"),
        (DatabasePurpose.LEARNING, "CONTEXT_ENGINE_LEARNING_DATABASE_URL"),
        (
            DatabasePurpose.SECURITY_OPERATOR,
            "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL",
        ),
        (DatabasePurpose.SECURITY_TEST, "CONTEXT_ENGINE_TEST_DATABASE_URL"),
    ],
)
def test_each_process_requires_its_own_database_url(
    purpose: DatabasePurpose, missing_name: str
) -> None:
    environment = database_environment()
    environment.pop(missing_name)

    with pytest.raises(DatabaseConfigurationError, match=missing_name):
        load_database_configuration(purpose, environment)


def test_runtime_never_falls_back_to_migration_credentials() -> None:
    environment = database_environment()
    environment.pop("CONTEXT_ENGINE_RUNTIME_DATABASE_URL")

    with pytest.raises(
        DatabaseConfigurationError,
        match="CONTEXT_ENGINE_RUNTIME_DATABASE_URL",
    ):
        load_database_configuration(DatabasePurpose.API_RUNTIME, environment)


@pytest.mark.parametrize(
    ("purpose", "url_name", "unsafe_role"),
    [
        (
            DatabasePurpose.CONTROL_PLANE,
            "CONTEXT_ENGINE_CONTROL_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.TRUSTED_IDENTITY,
            "CONTEXT_ENGINE_IDENTITY_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.API_RUNTIME,
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.SUPPLY_WORKER,
            "CONTEXT_ENGINE_WORKER_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.LEARNING,
            "CONTEXT_ENGINE_LEARNING_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.SECURITY_OPERATOR,
            "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
        (
            DatabasePurpose.SECURITY_TEST,
            "CONTEXT_ENGINE_TEST_DATABASE_URL",
            MIGRATOR_ROLE,
        ),
    ],
)
def test_non_migration_configuration_rejects_a_privileged_url_username(
    purpose: DatabasePurpose, url_name: str, unsafe_role: str
) -> None:
    environment = database_environment()
    environment[url_name] = (
        f"postgresql+psycopg://{unsafe_role}:secret@127.0.0.1:5432/context_engine"
    )

    with pytest.raises(DatabaseConfigurationError, match="expected database role"):
        load_database_configuration(purpose, environment)


def test_role_name_contract_cannot_be_redefined_by_environment() -> None:
    environment = database_environment()
    environment["CONTEXT_ENGINE_RUNTIME_ROLE"] = MIGRATOR_ROLE

    with pytest.raises(
        DatabaseConfigurationError,
        match="CONTEXT_ENGINE_RUNTIME_ROLE must be 'context_engine_runtime'",
    ):
        load_database_configuration(DatabasePurpose.API_RUNTIME, environment)


@pytest.mark.parametrize(
    "environment",
    [
        {
            **database_environment(),
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": ("sqlite+pysqlite:///:memory:"),
        },
        {
            **database_environment(),
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": (
                "postgresql+psycopg://context_engine_runtime@"
                "127.0.0.1:5432/context_engine"
            ),
        },
    ],
)
def test_database_url_must_be_explicit_postgresql_psycopg_credentials(
    environment: Mapping[str, str],
) -> None:
    with pytest.raises(DatabaseConfigurationError):
        load_database_configuration(DatabasePurpose.API_RUNTIME, environment)


def test_database_url_rejects_query_parameters_that_override_login_identity() -> None:
    environment = database_environment()
    environment["CONTEXT_ENGINE_RUNTIME_DATABASE_URL"] += (
        "?user=context_engine_migrator&password=migration-secret"
    )

    with pytest.raises(DatabaseConfigurationError, match="query parameters"):
        load_database_configuration(DatabasePurpose.API_RUNTIME, environment)


def test_harness_contract_keeps_roles_distinct_and_test_uses_runtime() -> None:
    configurations = load_harness_database_configurations(database_environment())

    assert configurations.migration.expected_role == MIGRATOR_ROLE
    assert configurations.control.expected_role == CONTROL_ROLE
    assert configurations.egress.expected_role == EGRESS_ROLE
    assert configurations.runtime.expected_role == RUNTIME_ROLE
    assert configurations.worker.expected_role == WORKER_ROLE
    assert configurations.learning.expected_role == LEARNING_ROLE
    assert configurations.operator.expected_role == OPERATOR_ROLE
    assert configurations.security_test.expected_role == RUNTIME_ROLE
    assert configurations.security_test.url == configurations.runtime.url


def test_configuration_representation_never_contains_a_password() -> None:
    configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    assert "runtime-secret" not in repr(configuration)
    assert RUNTIME_ROLE in repr(configuration)


def test_configuration_cannot_be_forged_with_a_cross_purpose_role() -> None:
    runtime_configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    with pytest.raises(DatabaseConfigurationError, match="match its purpose"):
        type(runtime_configuration)(
            purpose=DatabasePurpose.MIGRATION,
            url=runtime_configuration.url,
            expected_role=RUNTIME_ROLE,
        )


def test_configuration_cannot_be_forged_with_a_cross_role_url() -> None:
    runtime_configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    with pytest.raises(DatabaseConfigurationError, match="URL username"):
        type(runtime_configuration)(
            purpose=DatabasePurpose.MIGRATION,
            url=runtime_configuration.url,
            expected_role=MIGRATOR_ROLE,
        )


def test_configuration_cannot_be_forged_with_a_non_postgresql_url() -> None:
    runtime_configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    with pytest.raises(DatabaseConfigurationError, match=r"postgresql\+psycopg"):
        type(runtime_configuration)(
            purpose=DatabasePurpose.API_RUNTIME,
            url=make_url("sqlite+pysqlite:///:memory:"),
            expected_role=RUNTIME_ROLE,
        )


def test_configuration_cannot_be_forged_with_query_parameter_overrides() -> None:
    runtime_configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    with pytest.raises(DatabaseConfigurationError, match="query parameters"):
        type(runtime_configuration)(
            purpose=DatabasePurpose.API_RUNTIME,
            url=make_url(
                "postgresql+psycopg://context_engine_runtime:runtime-secret@"
                "127.0.0.1:5432/context_engine?host=privileged.example"
            ),
            expected_role=RUNTIME_ROLE,
        )


def test_database_engine_installs_non_optional_reset_and_checkout_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listeners: list[str] = []
    real_engine = create_engine("sqlite+pysqlite:///:memory:")

    def fake_create_engine(url: object, **options: object) -> object:
        assert options == {
            "hide_parameters": True,
            "pool_pre_ping": True,
            "pool_reset_on_return": None,
            "pool_size": 5,
            "max_overflow": 10,
        }
        return real_engine

    def fake_listen(pool: QueuePool, event_name: str, callback: object) -> None:
        assert pool is real_engine.pool
        assert callable(callback)
        listeners.append(event_name)

    monkeypatch.setattr("engine.persistence.database.create_engine", fake_create_engine)
    monkeypatch.setattr(event, "listen", fake_listen)
    configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    assert create_database_engine(configuration) is real_engine
    assert listeners == ["reset", "checkout"]


def test_database_engine_caller_cannot_disable_the_reset_policy() -> None:
    configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME, database_environment()
    )

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        create_database_engine(  # type: ignore[call-arg]
            configuration, pool_reset_on_return="rollback"
        )
