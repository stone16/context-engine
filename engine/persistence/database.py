"""SQLAlchemy engine construction with explicit pooled-session cleanup."""

from __future__ import annotations

from typing import Any

from psycopg.pq import TransactionStatus
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.pool import PoolResetState

from engine.persistence.configuration import DatabaseConfiguration


def _reset_session_state(
    dbapi_connection: Any,
    _connection_record: Any,
    reset_state: PoolResetState,
) -> None:
    """Remove reusable session state without invalidating psycopg's cache."""

    if reset_state.terminate_only:
        return
    dbapi_connection.rollback()
    if dbapi_connection.info.transaction_status is not TransactionStatus.IDLE:
        raise DisconnectionError(
            "pooled PostgreSQL connection could not be rolled back before reset"
        )
    previous_autocommit = dbapi_connection.autocommit
    try:
        dbapi_connection.autocommit = True
        with dbapi_connection.cursor() as cursor:
            cursor.execute("CLOSE ALL")
            cursor.execute("RESET ROLE")
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute("RESET ALL")
            cursor.execute("UNLISTEN *")
            cursor.execute("SELECT pg_advisory_unlock_all()")
            cursor.execute("DISCARD TEMP")
    finally:
        dbapi_connection.autocommit = previous_autocommit


def _reject_dirty_checkout(
    dbapi_connection: Any,
    _connection_record: Any,
    _connection_proxy: Any,
) -> None:
    """Invalidate a pooled connection if transaction state survived check-in."""

    if dbapi_connection.info.transaction_status is not TransactionStatus.IDLE:
        raise DisconnectionError(
            "pooled PostgreSQL connection was not idle at checkout"
        )


def create_database_engine(
    configuration: DatabaseConfiguration,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> Engine:
    """Build a PostgreSQL engine whose pool reset point clears every session GUC."""

    options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_reset_on_return": None,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
    }
    engine = create_engine(configuration.url, **options)
    event.listen(engine.pool, "reset", _reset_session_state)
    event.listen(engine.pool, "checkout", _reject_dirty_checkout)
    return engine
