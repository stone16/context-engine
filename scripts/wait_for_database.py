#!/usr/bin/env python3
"""Wait until every role-isolated PostgreSQL harness connection is usable."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence import (
    DatabasePurpose,
    assert_security_operator_role,
    create_database_engine,
    load_harness_database_configurations,
)


def wait_for_database(timeout_seconds: float) -> None:
    """Probe every role-isolated harness URL without credential fallback."""

    configurations = load_harness_database_configurations()
    deadline = time.monotonic() + timeout_seconds
    last_error: SQLAlchemyError | None = None
    while time.monotonic() < deadline:
        engines: list[Engine] = []
        try:
            for configuration in (
                configurations.migration,
                configurations.control,
                configurations.runtime,
                configurations.worker,
                configurations.operator,
                configurations.security_test,
            ):
                engine = create_database_engine(configuration)
                engines.append(engine)
                with engine.connect() as connection:
                    current_role = connection.execute(
                        text("SELECT current_user")
                    ).scalar_one()
                    if current_role != configuration.expected_role:
                        raise RuntimeError(
                            "database connection reported an unexpected role"
                        )
                    if configuration.purpose is DatabasePurpose.SECURITY_OPERATOR:
                        assert_security_operator_role(connection)
            return
        except SQLAlchemyError as error:
            last_error = error
            time.sleep(0.25)
        finally:
            for engine in engines:
                engine.dispose()
    raise TimeoutError(
        "PostgreSQL harness did not become ready before the configured timeout"
    ) from last_error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    arguments = parser.parse_args(argv)
    wait_for_database(arguments.timeout)
    purpose_names = (
        "migration, control, runtime, worker, security-operator, security-test"
    )
    print("PostgreSQL harness ready: " + purpose_names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
