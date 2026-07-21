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
    assert_runtime_role,
    create_database_engine,
    load_harness_database_configurations,
)

ROOT = Path(__file__).parents[2]


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
def migration_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.migration


@pytest.fixture(scope="session")
def worker_configuration(
    database_configurations: HarnessDatabaseConfigurations,
) -> DatabaseConfiguration:
    return database_configurations.worker


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


@pytest.fixture(scope="session", autouse=True)
def migrated_database(guarded_runtime_engine: Engine) -> Iterator[None]:
    """Put the disposable database at the current reviewed migration head."""

    configuration = Config(ROOT / "alembic.ini")
    command.upgrade(configuration, "head")
    yield
