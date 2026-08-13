"""Capability-minimal dispatch for the public Control process."""

from __future__ import annotations

import sys
from collections.abc import Sequence

_DISCOVERY_SUBCOMMANDS = (
    (
        "preflight",
        "report read-only readiness for bounded local planes",
    ),
)


def _top_level_help(arguments: tuple[str, ...]) -> bool:
    return arguments in {("-h",), ("--help",)}


def _render_top_level_help() -> None:
    from applications.control import _parser

    parser = _parser(extra_subcommands=_DISCOVERY_SUBCOMMANDS)
    parser.print_help()


def main(argv: Sequence[str] | None = None) -> None:
    """Select read-only preflight before importing mutation-bearing Control."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if _top_level_help(arguments):
        _render_top_level_help()
        return
    if arguments and arguments[0] == "preflight":
        from applications.preflight import main as preflight_main

        preflight_main(arguments[1:])
        return

    from applications.control import main as control_main

    control_main(arguments, extra_subcommands=_DISCOVERY_SUBCOMMANDS)


__all__ = ["main"]


if __name__ == "__main__":
    main()
