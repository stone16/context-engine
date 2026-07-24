"""Independent Supply worker process entry point."""

import argparse
import json
import os
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from adapters.file_source import FileReadLimits, FileRootRegistry
from engine import BUILD_IDENTIFIER
from engine.control import FileImportReceiver, FileRootRef, SourceRef
from engine.persistence import (
    DatabasePurpose,
    FileImportLeaseRedemption,
    PostgreSQLFileImportWorker,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.worker_jobs import (
    WorkerLeaseRedemption,
    WorkerNoOpCompletion,
)
from engine.runtime import Runtime
from engine.runtime.construction import required_kernel_dependencies
from engine.supply import (
    MarkdownCompilerConfig,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseToken,
)


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


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value or value != value.strip():
        raise ValueError("Supply worker configuration is not available")
    return value


def _run_one_file_import() -> int:
    """Consume one exact, signed File job in the independent Supply process."""

    signing_key_hex = _required_environment(
        "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX"
    )
    if len(signing_key_hex) != 64:
        raise ValueError("Supply worker configuration is not available")
    try:
        signing_key = bytes.fromhex(signing_key_hex)
    except ValueError:
        raise ValueError("Supply worker configuration is not available") from None
    if len(signing_key) != 32:
        raise ValueError("Supply worker configuration is not available")
    configuration = load_database_configuration(DatabasePurpose.SUPPLY_WORKER)
    engine = create_database_engine(configuration)
    roots = FileRootRegistry(
        {
            FileRootRef(_required_environment("CONTEXT_ENGINE_WORKER_FILE_ROOT_REF")):
                Path(_required_environment("CONTEXT_ENGINE_WORKER_FILE_ROOT_PATH"))
        },
        limits=FileReadLimits(max_file_bytes=4_096),
    )
    try:
        outcome = PostgreSQLFileImportWorker(
            engine,
            WorkerLeaseCodec(
                WorkerLeaseKeyring(active_version=1, keys={1: signing_key})
            ),
            FileImportReceiver(
                UUID(
                    _required_environment(
                        "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"
                    )
                )
            ),
            roots,
            MarkdownCompilerConfig("markdown-config-v1"),
            clock=lambda: datetime.now(UTC).replace(microsecond=0),
        ).run(
            FileImportLeaseRedemption(
                WorkerLeaseToken(
                    _required_environment("CONTEXT_ENGINE_WORKER_LEASE_TOKEN")
                ),
                UUID(_required_environment("CONTEXT_ENGINE_WORKER_ORGANIZATION_ID")),
                UUID(_required_environment("CONTEXT_ENGINE_WORKER_JOB_ID")),
                SourceRef(
                    UUID(_required_environment("CONTEXT_ENGINE_WORKER_SOURCE_ID"))
                ),
            )
        )
        print(
            json.dumps(
                {
                    "acquisitionId": str(outcome.acquisition_id),
                    "candidateRefs": [
                        {
                            "fragmentRef": candidate.fragment_ref,
                            "organizationId": str(candidate.organization_id),
                            "resourceRef": candidate.resource_ref,
                            "revisionRef": candidate.revision_ref,
                            "sourceRef": candidate.source_ref,
                        }
                        for candidate in outcome.candidate_refs
                    ],
                    "contentIdentityDigest": outcome.content_identity_digest,
                    "effectCount": outcome.effect_count,
                    "jobBehavior": "file.import",
                    "outcome": outcome.outcome,
                    "reasonDigest": outcome.reason_digest,
                    "service": "context-engine-worker",
                    "status": "complete",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        roots.close()
        engine.dispose()


def run(*, test_mode: bool, run_file_job: bool = False) -> int:
    if run_file_job:
        return _run_one_file_import()
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
    parser.add_argument(
        "--run-file-job",
        action="store_true",
        help="consume one exact configured FileImport WorkerLease and exit",
    )
    args = parser.parse_args(argv)
    if args.test_mode and args.run_file_job:
        parser.error("--test-mode and --run-file-job are mutually exclusive")
    return run(test_mode=args.test_mode, run_file_job=args.run_file_job)


if __name__ == "__main__":
    raise SystemExit(main())
