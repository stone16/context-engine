"""Registered downgrade-guard evidence must not depend on retained corpus.

Alembic downgrades newest-revision-first, so a chain to an older target stops at
whichever guard the retained corpus trips first. Before this evidence existed,
nested File lineage owned by any Organization made the recursive-path guard
displace the guard each registered test asserts, and `make security-gate` went
red on every populated volume. These tests fix the property: each guard's
refusal is a function of its own retained facts.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence import DatabaseConfiguration, create_database_engine
from tests.support.migrations import HEAD_REVISION, downgrade_revision
from tests.support.retained_corpus import retained_file_lineage

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
RECURSIVE_PATH_GUARD = "recursive File path downgrade requires no retained nested"
MIXED_UPSERT_GUARD = "mixed File upsert scheduling downgrade requires no retained"


@pytest.fixture
def foreign_organizations(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[UUID, ...]]:
    with retained_file_lineage(migration_configuration) as organization_ids:
        yield organization_ids


def _live_revision(configuration: DatabaseConfiguration) -> str:
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            return str(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_chain_downgrade_never_reaches_the_guard_its_target_names(
    foreign_organizations: tuple[UUID, ...],
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Reproduce the traversal-order shape the registered evidence must not use.

    Downgrading to `20260725_0031` makes revision `20260725_0032` the guard that
    owns the boundary. Alembic evaluates every newer guard first, so with
    foreign lineage retained the chain always refuses somewhere else — which one
    depends on what the volume holds. The exact same database answers the
    property when the owning revision is asked directly.
    """

    assert len(foreign_organizations) >= 2
    with pytest.raises((RuntimeError, SQLAlchemyError)) as chain:
        command.downgrade(Config(ROOT / "alembic.ini"), "20260725_0031")
    assert MIXED_UPSERT_GUARD not in str(chain.value)
    with pytest.raises(RuntimeError, match=MIXED_UPSERT_GUARD):
        downgrade_revision(migration_configuration, "20260725_0032")
    assert _live_revision(migration_configuration) == HEAD_REVISION


@pytest.mark.parametrize(
    ("revision", "guard"),
    [
        ("20260726_0035", RECURSIVE_PATH_GUARD),
        ("20260725_0032", MIXED_UPSERT_GUARD),
        ("20260725_0029", "requires no retained accepted-change acquisition lineage"),
        ("20260725_0028", "File change-feed downgrade requires no retained"),
    ],
)
def test_each_revision_refuses_on_its_own_retained_property(
    revision: str,
    guard: str,
    foreign_organizations: tuple[UUID, ...],
    migration_configuration: DatabaseConfiguration,
) -> None:
    """Each revision decides its refusal from the facts it retains itself."""

    with pytest.raises(RuntimeError, match=guard):
        downgrade_revision(migration_configuration, revision)
    assert _live_revision(migration_configuration) == HEAD_REVISION


def test_recursive_path_guard_stays_whole_database(
    foreign_organizations: tuple[UUID, ...],
    migration_configuration: DatabaseConfiguration,
) -> None:
    """The corpus-independent evidence never relaxes the guard it displaced."""

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            nested = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM file_source_change
                    WHERE organization_id = ANY(:organization_ids)
                      AND strpos(relative_path, '/') > 0
                    """
                ),
                {"organization_ids": list(foreign_organizations)},
            ).scalar_one()
    finally:
        engine.dispose()
    assert nested > 0
    with pytest.raises(RuntimeError, match=RECURSIVE_PATH_GUARD):
        downgrade_revision(migration_configuration, "20260726_0035")
