from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from engine.persistence import DatabaseConfiguration, create_database_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]


def _revision_rows(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
    finally:
        engine.dispose()


def _application_tables(configuration: DatabaseConfiguration) -> list[str]:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def test_empty_baseline_remains_a_reversible_historical_revision(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "base")
        assert _revision_rows(migration_configuration) == []
        command.upgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == ["20260721_0005"]


def test_organization_isolation_revision_downgrades_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0001")
        assert _revision_rows(migration_configuration) == ["20260720_0001"]
        assert _application_tables(migration_configuration) == ["alembic_version"]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260721_0005"]
    assert _application_tables(migration_configuration) == [
        "alembic_version",
        "context_fragment",
        "context_resource",
        "context_revision",
        "membership",
        "organization",
        "organization_policy_epoch",
        "organization_record",
        "resource_access_policy",
        "user_account",
    ]


def test_membership_revision_downgrades_to_issue_8_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260720_0002")
        assert _revision_rows(migration_configuration) == ["20260720_0002"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "organization",
            "organization_record",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260721_0005"]


def test_content_schema_revision_downgrades_to_membership_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0003")
        assert _revision_rows(migration_configuration) == ["20260721_0003"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260721_0005"]
    assert _application_tables(migration_configuration) == [
        "alembic_version",
        "context_fragment",
        "context_resource",
        "context_revision",
        "membership",
        "organization",
        "organization_policy_epoch",
        "organization_record",
        "resource_access_policy",
        "user_account",
    ]


def test_policy_epoch_revision_downgrades_to_content_and_reapplies_cleanly(
    migration_configuration: DatabaseConfiguration,
) -> None:
    """PG-REVOCATION-006: the epoch/access boundary is one reversible revision."""

    alembic_configuration = Config(ROOT / "alembic.ini")

    try:
        command.downgrade(alembic_configuration, "20260721_0004")
        assert _revision_rows(migration_configuration) == ["20260721_0004"]
        assert _application_tables(migration_configuration) == [
            "alembic_version",
            "context_fragment",
            "context_resource",
            "context_revision",
            "membership",
            "organization",
            "organization_record",
            "user_account",
        ]
    finally:
        command.upgrade(alembic_configuration, "head")

    assert _revision_rows(migration_configuration) == ["20260721_0005"]
    assert _application_tables(migration_configuration) == [
        "alembic_version",
        "context_fragment",
        "context_resource",
        "context_revision",
        "membership",
        "organization",
        "organization_policy_epoch",
        "organization_record",
        "resource_access_policy",
        "user_account",
    ]
