"""Shared migration assertions for tests that require the current schema head."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql

from engine.persistence import (
    DatabaseConfiguration,
    HarnessDatabaseConfigurations,
    create_database_engine,
    load_harness_database_configurations,
)
from engine.persistence.migrations import head_revision
from engine.persistence.role_guard import assert_migrator_role

HEAD_REVISION = head_revision()
_ALEMBIC_CONFIGURATION = Path(__file__).parents[2] / "alembic.ini"


def _revision_database_configurations(
    database_name: str,
) -> HarnessDatabaseConfigurations:
    configurations = load_harness_database_configurations()

    def retarget(configuration: DatabaseConfiguration) -> DatabaseConfiguration:
        return replace(
            configuration,
            url=configuration.url.set(database=database_name),
        )

    return HarnessDatabaseConfigurations(
        migration=retarget(configurations.migration),
        control=retarget(configurations.control),
        identity=retarget(configurations.identity),
        egress=retarget(configurations.egress),
        action=retarget(configurations.action),
        runtime=retarget(configurations.runtime),
        worker=retarget(configurations.worker),
        scheduler=retarget(configurations.scheduler),
        learning=retarget(configurations.learning),
        release_operator=retarget(configurations.release_operator),
        operator=retarget(configurations.operator),
        security_test=retarget(configurations.security_test),
    )


@contextmanager
def isolated_revision_database(
    revision: str,
) -> Iterator[HarnessDatabaseConfigurations]:
    """Yield a disposable database migrated only through ``revision``."""

    source = load_harness_database_configurations().migration
    host = source.url.host
    port = source.url.port
    bootstrap_database = source.url.database
    if host is None or port is None or bootstrap_database is None:
        raise RuntimeError("the harness migration URL is incomplete")
    database_name = f"context_engine_revision_{uuid4().hex}"
    bootstrap_user = os.environ["POSTGRES_USER"]
    bootstrap_password = os.environ["POSTGRES_PASSWORD"]
    configurations = _revision_database_configurations(database_name)
    with psycopg.connect(
        host=host,
        port=port,
        dbname=bootstrap_database,
        user=bootstrap_user,
        password=bootstrap_password,
        autocommit=True,
    ) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database_name),
                sql.Identifier(source.expected_role),
            )
        )

    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=bootstrap_database,
            user=bootstrap_user,
            password=bootstrap_password,
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database_name)
                )
            )
            for role in {
                configuration.expected_role
                for configuration in (
                    configurations.migration,
                    configurations.control,
                    configurations.identity,
                    configurations.egress,
                    configurations.action,
                    configurations.runtime,
                    configurations.worker,
                    configurations.scheduler,
                    configurations.learning,
                    configurations.release_operator,
                    configurations.operator,
                )
            }:
                connection.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database_name),
                        sql.Identifier(role),
                    )
                )
        with psycopg.connect(
            host=host,
            port=port,
            dbname=database_name,
            user=bootstrap_user,
            password=bootstrap_password,
            autocommit=True,
        ) as connection:
            connection.execute("CREATE EXTENSION pgcrypto WITH SCHEMA public")
            connection.execute("CREATE EXTENSION vector WITH SCHEMA public")
        engine = create_database_engine(configurations.migration)
        try:
            with engine.begin() as connection:
                assert_migrator_role(connection)
                alembic_configuration = Config(_ALEMBIC_CONFIGURATION)
                alembic_configuration.attributes["connection"] = connection
                command.upgrade(alembic_configuration, revision)
        finally:
            engine.dispose()
        yield configurations
    finally:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=bootstrap_database,
            user=bootstrap_user,
            password=bootstrap_password,
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )


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
