"""Independent Supply worker process entry point."""

import argparse
import json
import threading
from collections.abc import Sequence
from typing import Protocol

from engine import BUILD_IDENTIFIER
from engine.persistence.worker_jobs import (
    WorkerLeaseRedemption,
    WorkerNoOpCompletion,
)
from engine.runtime import Runtime
from engine.runtime.construction import required_kernel_dependencies


class WorkerNoOpCompletionAuthority(Protocol):
    """Application port for one verified persistent no-op completion."""

    def complete_noop(
        self, redemption: WorkerLeaseRedemption
    ) -> WorkerNoOpCompletion: ...


def complete_persistent_noop_job(
    authority: WorkerNoOpCompletionAuthority,
    redemption: WorkerLeaseRedemption,
) -> WorkerNoOpCompletion:
    """Execute the bounded Issue #17 worker flow through its durable authority."""

    if type(redemption) is not WorkerLeaseRedemption:
        raise TypeError("redemption must be WorkerLeaseRedemption")
    return authority.complete_noop(redemption)


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
        ),
        flush=True,
    )
    if not test_mode:
        threading.Event().wait()
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
