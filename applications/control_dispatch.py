"""Capability-minimal dispatch for the public Control process."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Select read-only preflight before importing mutation-bearing Control."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "preflight":
        from applications.preflight import main as preflight_main

        preflight_main(arguments[1:])
        return

    from applications.control import main as control_main

    control_main(arguments)


__all__ = ["main"]
