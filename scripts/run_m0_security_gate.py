#!/usr/bin/env python3
"""Run the complete one-shot M0 security gate and retain its artifacts."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
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
    try:
        report = run_gate(paths)
    except Exception as error:
        print(f"M0 security gate failed: {type(error).__name__}", file=sys.stderr)
        return 1
    if report.get("m0SecurityDecision") != "pass":
        print("M0 SECURITY FAIL", file=sys.stderr)
        return 1
    print(f"M0 SECURITY PASS ({paths.output_directory})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
