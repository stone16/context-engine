from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from engine.persistence import (
    DatabaseConfiguration,
    HarnessDatabaseConfigurations,
    assert_action_role,
    assert_control_role,
    assert_runtime_role,
    assert_security_operator_role,
    assert_worker_role,
    create_database_engine,
    load_harness_database_configurations,
)
from engine.persistence.role_guard import assert_learning_role
from engine.runtime.package_digest import QueryDigestKeyring
from tests.support.releases import clear_all_test_runtime_releases

ROOT = Path(__file__).parents[2]
TEST_QUERY_DIGEST_KEY = b"issue-19-query-digest-test-key!!"


@pytest.fixture(autouse=True)
def clear_openapi_v0_test_release_after_each_test() -> Iterator[None]:
    """Keep test-only v0 Runtime history from polluting migration boundaries."""

    yield
    clear_all_test_runtime_releases()


@pytest.fixture(scope="session")
def database_configurations() -> HarnessDatabaseConfigurations:
    """Load the role-isolated URLs; missing harness state is a hard failure."""

    return load_harness_database_configurations()


@pytest.fixture(scope="session")
def runtime_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.security_test


@pytest.fixture(scope="session")
def control_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.control


@pytest.fixture(scope="session")
def identity_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.identity


@pytest.fixture(scope="session")
def egress_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.egress


@pytest.fixture(scope="session")
def action_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.action


@pytest.fixture(scope="session")
def guarded_action_engine(
    action_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Expose only the verified non-owner ActionPlane database engine."""

    engine = create_database_engine(action_configuration)
    try:
        with engine.connect() as connection:
            assert_action_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migration_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.migration


@pytest.fixture(scope="session")
def worker_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.worker


@pytest.fixture(scope="session")
def learning_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.learning


@pytest.fixture(scope="session")
def operator_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.operator


@pytest.fixture(scope="session")
def query_digest_keyring() -> QueryDigestKeyring:
    """Explicit test-only key; production composition has no fallback secret."""

    return QueryDigestKeyring(active_version=1, keys={1: TEST_QUERY_DIGEST_KEY})


@pytest.fixture(scope="session", autouse=True)
def guarded_runtime_engine(
    runtime_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Guard every integration run against owner/superuser credential fallback."""

    engine = create_database_engine(runtime_configuration)
    try:
        with engine.connect() as connection:
            assert_runtime_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def guarded_worker_engine(
    worker_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Expose only a verified non-owner worker engine to lease tests."""

    engine = create_database_engine(worker_configuration)
    try:
        with engine.connect() as connection:
            assert_worker_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def guarded_learning_engine(
    learning_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Expose only the verified non-owner ContextLearning engine."""

    engine = create_database_engine(learning_configuration)
    try:
        with engine.connect() as connection:
            assert_learning_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def guarded_control_engine(
    control_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Expose only the verified non-owner ContextControl engine."""

    engine = create_database_engine(control_configuration)
    try:
        with engine.connect() as connection:
            assert_control_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def guarded_operator_engine(
    operator_configuration: DatabaseConfiguration,
) -> Iterator[Engine]:
    """Expose only the verified restricted security-operator engine."""

    engine = create_database_engine(operator_configuration)
    try:
        with engine.connect() as connection:
            assert_security_operator_role(connection)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(guarded_runtime_engine: Engine) -> Iterator[None]:
    """Put the disposable database at the current reviewed migration head."""

    configuration = Config(ROOT / "alembic.ini")
    command.upgrade(configuration, "head")
    yield
