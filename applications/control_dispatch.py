"""Capability-minimal dispatch for the public Control process."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def _top_level_help(arguments: tuple[str, ...]) -> bool:
    return arguments in {("-h",), ("--help",)}


def _render_top_level_help() -> None:
    from applications.control import _parser

    parser = _parser(
        extra_subcommands=(
            (
                "preflight",
                "report read-only readiness for bounded local planes",
            ),
        )
    )
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

    control_main(arguments)


__all__ = ["main"]
