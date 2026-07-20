from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine

pytestmark = pytest.mark.integration


def setting(engine: Engine, name: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT current_setting(:name, true)"), {"name": name}
        ).scalar_one_or_none()


def test_pool_reset_discards_session_state_before_connection_reuse(
    runtime_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(
        runtime_configuration,
        pool_size=1,
        max_overflow=0,
    )
    marker = "context_engine.harness_session_marker"
    try:
        with engine.connect() as connection:
            backend_pid = connection.execute(text("SELECT pg_backend_pid()"))
            first_backend_pid = backend_pid.scalar_one()
            connection.execute(
                text("SELECT set_config(:name, 'org-a', false)"), {"name": marker}
            )
            connection.execute(text("LISTEN harness_pool_poison"))
            assert connection.execute(
                text("SELECT pg_try_advisory_lock(7007)")
            ).scalar_one() is True
            connection.commit()
            assert connection.execute(
                text("SELECT current_setting(:name, true)"), {"name": marker}
            ).scalar_one() == "org-a"

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                == first_backend_pid
            )
            assert connection.execute(
                text("SELECT current_setting(:name, true)"), {"name": marker}
            ).scalar_one_or_none() in {None, ""}
            assert list(
                connection.execute(text("SELECT * FROM pg_listening_channels()"))
            ) == []
            advisory_locks = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE locktype = 'advisory'
                      AND pid = pg_backend_pid()
                    """
                )
            ).scalar_one()
            assert advisory_locks == 0
    finally:
        engine.dispose()


def test_pool_reset_preserves_driver_prepared_statement_consistency(
    runtime_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(
        runtime_configuration,
        pool_size=1,
        max_overflow=0,
    )
    statement = text("SELECT CAST(:value AS integer)")
    try:
        with engine.connect() as connection:
            for value in range(8):
                assert connection.execute(
                    statement, {"value": value}
                ).scalar_one() == value
            connection.commit()
            prepared_before_reset = connection.execute(
                text("SELECT count(*) FROM pg_prepared_statements")
            ).scalar_one()
            assert prepared_before_reset > 0

        with engine.connect() as connection:
            assert connection.execute(statement, {"value": 9}).scalar_one() == 9
    finally:
        engine.dispose()


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
def test_transaction_local_context_is_gone_after_transaction_end(
    runtime_configuration: DatabaseConfiguration,
    outcome: str,
) -> None:
    engine = create_database_engine(
        runtime_configuration,
        pool_size=1,
        max_overflow=0,
    )
    marker = "context_engine.harness_transaction_marker"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT set_config(:name, 'org-a', true)"), {"name": marker}
            )
            assert connection.execute(
                text("SELECT current_setting(:name, true)"), {"name": marker}
            ).scalar_one() == "org-a"
            if outcome == "commit":
                transaction.commit()
            else:
                transaction.rollback()

        assert setting(engine, marker) in {None, ""}
    finally:
        engine.dispose()
