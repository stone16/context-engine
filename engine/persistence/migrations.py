"""One authoritative entry to the packaged Alembic migration graph."""

from __future__ import annotations

from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from engine.persistence.configuration import (
    DatabasePurpose,
    load_database_configuration,
)
from engine.persistence.database import create_database_engine
from engine.persistence.role_guard import assert_migrator_role


def migration_configuration() -> Config:
    """Return an Alembic configuration bound to the packaged revision graph."""

    configuration = Config()
    configuration.set_main_option("script_location", str(files("migrations")))
    return configuration


def head_revision() -> str:
    """Resolve the repository's single migration head without duplicating it."""

    revision = ScriptDirectory.from_config(migration_configuration()).get_current_head()
    if revision is None:
        raise RuntimeError("the migration graph has no head revision")
    return revision


def migrate_to_head() -> str:
    """Assert the exact migrator connection and upgrade it to the current head."""

    expected_head = head_revision()
    database = load_database_configuration(DatabasePurpose.MIGRATION)
    engine = create_database_engine(database)
    try:
        with engine.begin() as connection:
            assert_migrator_role(connection)
            configuration = migration_configuration()
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
            observed_head = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if not isinstance(observed_head, str):
                raise RuntimeError("migration head revision has an invalid type")
            resulting_head = observed_head
            if resulting_head != expected_head:
                raise RuntimeError("migration did not reach the repository head")
    finally:
        engine.dispose()
    return resulting_head
