"""Alembic environment bound exclusively to the migration-role URL."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from engine.persistence.configuration import (
    DatabasePurpose,
    load_database_configuration,
)

configuration = context.config
if configuration.config_file_name is not None:
    fileConfig(configuration.config_file_name)

target_metadata = None


def _migration_section() -> dict[str, str]:
    section = configuration.get_section(configuration.config_ini_section) or {}
    database = load_database_configuration(DatabasePurpose.MIGRATION)
    section["sqlalchemy.url"] = database.url.render_as_string(hide_password=False)
    return section


def run_migrations_offline() -> None:
    """Configure offline SQL generation without embedding a repository URL."""

    context.configure(
        url=_migration_section()["sqlalchemy.url"],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through the reviewed migrator and a one-shot pool."""

    connectable = engine_from_config(
        _migration_section(),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
