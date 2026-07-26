"""Independent Supply worker process entry point."""

import argparse
import json
import os
import signal
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.file_source import FileReadLimits, FileRootRegistry
from engine import BUILD_IDENTIFIER
from engine.control import FileImportReceiver, FileRootRef, SourceRef
from engine.persistence import (
    DatabasePurpose,
    FileDispatchLease,
    FileDispatchNoWork,
    FileImportLeaseRedemption,
    FileImportRefused,
    FileImportUnavailable,
    PostgreSQLFileDispatchAuthority,
    PostgreSQLFileImportWorker,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.role_guard import assert_worker_role
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
    WorkNotAvailable,
)

_FILE_DISPATCH_POLL_SECONDS = 1.0


class WorkerNoOpCompletionAuthority(Protocol):
    """Application port for one verified persistent no-op completion."""

    def complete_noop(
        self, redemption: WorkerLeaseRedemption
    ) -> WorkerNoOpCompletion: ...


class FileDispatchAuthority(Protocol):
    """Application port for database-selected bounded-attempt File work."""

    def claim(self) -> FileDispatchLease | FileDispatchNoWork: ...


class FileDispatchWorker(Protocol):
    """Existing exact File import execution seam."""

    def run(self, redemption: FileImportLeaseRedemption) -> object: ...


class FileDispatchWorkerFactory(Protocol):
    def __call__(self, receiver: FileImportReceiver) -> FileDispatchWorker: ...


@dataclass(frozen=True, slots=True)
class FileDispatchCycleResult:
    """Content-free process result for one autonomous dispatch cycle."""

    outcome: str
    status: str = field(default="complete", init=False)

    def __post_init__(self) -> None:
        if self.outcome not in {"dispatched", "no_work", "refused"}:
            raise ValueError("File dispatch cycle outcome must remain closed")


def dispatch_one_file_import(
    authority: FileDispatchAuthority,
    worker_factory: FileDispatchWorkerFactory,
) -> FileDispatchCycleResult:
    """Claim and run at most one exact job without caller routing input."""

    claim = authority.claim()
    if type(claim) is FileDispatchNoWork:
        return FileDispatchCycleResult("no_work")
    if type(claim) is not FileDispatchLease:
        raise TypeError("File dispatch authority returned an invalid result")
    try:
        worker_factory(FileImportReceiver(claim.service_principal_id)).run(
            claim.redemption
        )
    except (FileImportRefused, WorkNotAvailable):
        return FileDispatchCycleResult("refused")
    return FileDispatchCycleResult("dispatched")


def dispatch_file_imports_until_stopped(
    authority: FileDispatchAuthority,
    worker_factory: FileDispatchWorkerFactory,
    stop_event: threading.Event,
    outcome_observer: Callable[[FileDispatchCycleResult], None] | None = None,
) -> None:
    """Run bounded single-job cycles until process shutdown is requested."""

    while not stop_event.is_set():
        result = dispatch_one_file_import(authority, worker_factory)
        if outcome_observer is not None:
            outcome_observer(result)
        if result.outcome == "no_work":
            stop_event.wait(_FILE_DISPATCH_POLL_SECONDS)


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

    signing_key = _worker_signing_key()
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


def _worker_signing_key() -> bytes:
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
    return signing_key


def _file_dispatch_root_bindings() -> dict[FileRootRef, Path]:
    """Load the server-owned registry for every root this dispatcher serves."""

    raw_registry = _required_environment("CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON")
    try:
        document = json.loads(raw_registry)
    except json.JSONDecodeError:
        raise ValueError("Supply worker configuration is not available") from None
    if type(document) is not dict or not document:
        raise ValueError("Supply worker configuration is not available")
    bindings: dict[FileRootRef, Path] = {}
    for raw_ref, raw_path in document.items():
        if (
            type(raw_ref) is not str
            or type(raw_path) is not str
            or not raw_path
            or raw_path != raw_path.strip()
        ):
            raise ValueError("Supply worker configuration is not available")
        bindings[FileRootRef(raw_ref)] = Path(raw_path)
    return bindings


def _file_dispatch_roots() -> FileRootRegistry:
    return FileRootRegistry(
        _file_dispatch_root_bindings(),
        limits=FileReadLimits(max_file_bytes=4_096),
    )


def _worker_database_time(engine: Engine) -> datetime:
    """Read the worker authority's clock for immediate lease verification."""

    try:
        with engine.connect() as connection:
            assert_worker_role(connection)
            checked_at = connection.execute(
                text("SELECT pg_catalog.date_trunc('second', clock_timestamp())")
            ).scalar_one()
    except (SQLAlchemyError, AssertionError, ValueError):
        raise FileImportUnavailable("File import clock is unavailable") from None
    if type(checked_at) is not datetime or checked_at.tzinfo is None:
        raise FileImportUnavailable("File import clock is unavailable")
    return checked_at.astimezone(UTC)


def _run_file_dispatch(*, single_cycle: bool) -> int:
    """Run configured autonomous File dispatch without caller routing facts."""

    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: _worker_signing_key()})
    )
    scheduler_engine = create_database_engine(
        load_database_configuration(DatabasePurpose.SUPPLY_SCHEDULER)
    )
    worker_engine = create_database_engine(
        load_database_configuration(DatabasePurpose.SUPPLY_WORKER)
    )
    root_bindings = _file_dispatch_root_bindings()
    roots = FileRootRegistry(
        root_bindings,
        limits=FileReadLimits(max_file_bytes=4_096),
    )
    try:
        authority = PostgreSQLFileDispatchAuthority(
            scheduler_engine,
            codec,
            configured_root_refs=tuple(
                root_ref.value for root_ref in root_bindings
            ),
        )

        def worker_factory(receiver: FileImportReceiver) -> PostgreSQLFileImportWorker:
            return PostgreSQLFileImportWorker(
                worker_engine,
                codec,
                receiver,
                roots,
                MarkdownCompilerConfig("markdown-config-v1"),
                clock=lambda: _worker_database_time(worker_engine),
            )

        if single_cycle:
            result = dispatch_one_file_import(authority, worker_factory)
            print(
                json.dumps(
                    {
                        "dispatch": "file.import",
                        "outcome": result.outcome,
                        "service": "context-engine-worker",
                        "status": result.status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        else:
            stop_event = threading.Event()

            def request_stop(_signum: int, _frame: object) -> None:
                stop_event.set()

            previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
            previous_sigint = signal.signal(signal.SIGINT, request_stop)
            try:
                print(
                    json.dumps(
                        {
                            "dispatch": "file.import",
                            "service": "context-engine-worker",
                            "status": "ready",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                dispatch_file_imports_until_stopped(
                    authority,
                    worker_factory,
                    stop_event,
                    outcome_observer=lambda result: print(
                        json.dumps(
                            {
                                "dispatch": "file.import",
                                "outcome": result.outcome,
                                "service": "context-engine-worker",
                                "status": result.status,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    ),
                )
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
                signal.signal(signal.SIGTERM, previous_sigterm)
        return 0
    finally:
        roots.close()
        worker_engine.dispose()
        scheduler_engine.dispose()


def run(
    *,
    test_mode: bool,
    run_file_job: bool = False,
    dispatch_file_once: bool = False,
    dispatch_files: bool = False,
) -> int:
    if dispatch_file_once:
        return _run_file_dispatch(single_cycle=True)
    if dispatch_files:
        return _run_file_dispatch(single_cycle=False)
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
    parser.add_argument(
        "--dispatch-file-once",
        action="store_true",
        help="claim and execute at most one eligible scheduled File import",
    )
    parser.add_argument(
        "--dispatch-files",
        action="store_true",
        help="continuously claim eligible scheduled File imports until shutdown",
    )
    args = parser.parse_args(argv)
    selected_modes = sum(
        (
            args.test_mode,
            args.run_file_job,
            args.dispatch_file_once,
            args.dispatch_files,
        )
    )
    if selected_modes > 1:
        parser.error("worker execution modes are mutually exclusive")
    return run(
        test_mode=args.test_mode,
        run_file_job=args.run_file_job,
        dispatch_file_once=args.dispatch_file_once,
        dispatch_files=args.dispatch_files,
    )


if __name__ == "__main__":
    raise SystemExit(main())
