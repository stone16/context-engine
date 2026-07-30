"""Shared migration assertions for tests that require the current schema head."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.migrations import head_revision

HEAD_REVISION = head_revision()
_ALEMBIC_CONFIGURATION = Path(__file__).parents[2] / "alembic.ini"


def downgrade_revision(configuration: DatabaseConfiguration, revision: str) -> None:
    """Run exactly one revision's downgrade and roll it back unconditionally.

    Alembic downgrades newest-revision-first, so downgrading to an older target
    evaluates every intervening guard and stops at whichever one the retained
    corpus trips first. Evidence that one revision refuses must be a function of
    that revision's own retained facts, so each guard is exercised against its
    own precondition instead of through a traversal whose shape depends on rows
    other Organizations left behind.
    """

    script = ScriptDirectory.from_config(Config(_ALEMBIC_CONFIGURATION))
    step = script.get_revision(revision)
    if step is None:
        raise LookupError(f"Alembic revision is unknown: {revision}")
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Operations.context(
                    MigrationContext.configure(connection=connection)
                ):
                    step.module.downgrade()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
