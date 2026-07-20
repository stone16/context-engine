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


def test_empty_baseline_downgrade_upgrade_cycle(
    migration_configuration: DatabaseConfiguration,
) -> None:
    alembic_configuration = Config(ROOT / "alembic.ini")

    command.downgrade(alembic_configuration, "base")
    assert _revision_rows(migration_configuration) == []

    command.upgrade(alembic_configuration, "head")
    assert _revision_rows(migration_configuration) == ["20260720_0001"]


def test_baseline_contains_no_application_or_tenant_tables(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            tables = list(
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
            rls_tables = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind IN ('r', 'p')
                      AND (relation.relrowsecurity OR relation.relforcerowsecurity)
                    """
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert tables == ["alembic_version"]
    assert rls_tables == 0
