"""Short-lived local operator process for ContextEngine control operations."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from applications.operator_authentication import (
    LocalOperatorAuthorities,
    LocalOperatorConfiguration,
)
from engine.persistence.migrations import migrate_to_head


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context-engine-control")
    subcommands = parser.add_subparsers(dest="subcommand", required=True)
    subcommands.add_parser(
        "migrate",
        help="upgrade the configured database to the current schema head",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.subcommand != "migrate":
        parser.error("unknown operation")
    try:
        revision = migrate_to_head()
    except Exception:  # The local process must never render connection details.
        parser.exit(1, "context-engine-control: migration refused\n")
    print(revision, flush=True)


def local_operator_authorities() -> LocalOperatorAuthorities | None:
    """Construct local operator authority only after complete explicit opt-in."""

    configuration = LocalOperatorConfiguration.load(os.environ)
    if configuration is None:
        return None
    return configuration.authorities()


if __name__ == "__main__":
    main()
