"""Independent Supply worker process entry point."""

import argparse
import json
from collections.abc import Sequence

from engine import BUILD_IDENTIFIER
from engine.runtime import Runtime
from engine.runtime.construction import required_kernel_dependencies


def run(*, test_mode: bool) -> int:
    Runtime(required_kernel_dependencies())
    lifecycle = "test-complete" if test_mode else "ready"
    print(
        json.dumps(
            {
                "status": lifecycle,
                "service": "context-engine-worker",
                "version": BUILD_IDENTIFIER,
                "job_behavior": "NOT_ACTIVE",
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ContextEngine Supply worker")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="complete the deterministic no-op lifecycle and exit",
    )
    args = parser.parse_args(argv)
    return run(test_mode=args.test_mode)


if __name__ == "__main__":
    raise SystemExit(main())
