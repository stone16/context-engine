#!/usr/bin/env python3
"""Run the complete one-shot M0 security gate and retain its artifacts."""

from __future__ import annotations

import argparse
import io
import logging
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.security_gate.runner import GatePaths, run_gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".context-engine/security-gate"),
        help="artifact directory (default: .context-engine/security-gate)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    paths = GatePaths.defaults(arguments.output_dir.resolve())
    prior_logging_disable = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            report = run_gate(paths)
    except Exception as error:
        print(f"M0 security gate failed: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        logging.disable(prior_logging_disable)
    if report.get("m0SecurityDecision") != "pass":
        print("M0 SECURITY FAIL", file=sys.stderr)
        return 1
    print("M0 SECURITY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
