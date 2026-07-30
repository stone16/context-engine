"""Independent Supply worker process entry point."""

import argparse
import json
import math
import os
import signal
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.embeddings import (
    DeterministicEmbeddingTwin,
    ExternalEmbeddingConfiguration,
    ExternalEmbeddingProvider,
)
from adapters.file_source import FileReadLimits, FileRootRegistry
from applications.file_root_configuration import (
    DEFAULT_WORKER_MAX_FILE_BYTES as _DEFAULT_WORKER_MAX_FILE_BYTES,
)
from applications.file_root_configuration import (
    file_read_limits as _configured_file_read_limits,
)
from applications.file_root_configuration import (
    file_root_bindings as _file_dispatch_root_bindings,
)
from applications.file_root_configuration import (
    file_roots as _configured_file_roots,
)
from applications.file_root_configuration import (
    required_environment as _required_environment,
)
from applications.worker_progress import (
    FileBatchProgressReporter,
    FileDispatchCycleResult,
    FileDispatchFailureCategory,
    opaque_file_job_ref,
)
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
    ACTIVE_FILE_IMPORT_MARKDOWN_CONFIG_VERSION,
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProvider,
    MarkdownCompilerConfig,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseToken,
    WorkNotAvailable,
)

_FILE_DISPATCH_POLL_SECONDS = 1.0
_FILE_DISPATCH_PROGRESS_SECONDS = 1.0
DEFAULT_WORKER_MAX_FILE_BYTES = _DEFAULT_WORKER_MAX_FILE_BYTES
_file_dispatch_roots = _configured_file_roots
_WORKER_EMBEDDING_PROVIDER_ENV = "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER"
_WORKER_EMBEDDING_DIMENSION_ENV = "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION"


def _file_read_limits() -> FileReadLimits:
    return _configured_file_read_limits()


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


def dispatch_one_file_import(
    authority: FileDispatchAuthority,
    worker_factory: FileDispatchWorkerFactory,
    *,
    active_job_observer: Callable[[str], None] | None = None,
    progress_interval_seconds: float = _FILE_DISPATCH_PROGRESS_SECONDS,
) -> FileDispatchCycleResult:
    """Claim and run at most one exact job without caller routing input."""

    _require_progress_interval(progress_interval_seconds)
    claim = authority.claim()
    if type(claim) is FileDispatchNoWork:
        return FileDispatchCycleResult("no_work")
    if type(claim) is not FileDispatchLease:
        raise TypeError("File dispatch authority returned an invalid result")
    job_ref = opaque_file_job_ref(claim.job_id)
    progress = _ActiveFileJobProgress(
        job_ref=job_ref,
        observer=active_job_observer,
        interval_seconds=progress_interval_seconds,
    )
    try:
        worker_factory(FileImportReceiver(claim.service_principal_id)).run(
            claim.redemption
        )
    except FileImportRefused:
        result = FileDispatchCycleResult(
            "refused",
            job_ref=job_ref,
            reason_category=FileDispatchFailureCategory.FILE_IMPORT_REFUSED,
        )
    except WorkNotAvailable:
        result = FileDispatchCycleResult(
            "refused",
            job_ref=job_ref,
            reason_category=FileDispatchFailureCategory.WORKER_LEASE_REFUSED,
        )
    else:
        result = FileDispatchCycleResult("dispatched", job_ref=job_ref)
    finally:
        progress.close()
    progress.raise_if_failed()
    return result


def dispatch_file_imports_until_stopped(
    authority: FileDispatchAuthority,
    worker_factory: FileDispatchWorkerFactory,
    stop_event: threading.Event,
    outcome_observer: Callable[[FileDispatchCycleResult], None] | None = None,
    *,
    active_job_observer: Callable[[str], None] | None = None,
    progress_interval_seconds: float = _FILE_DISPATCH_PROGRESS_SECONDS,
) -> None:
    """Run bounded single-job cycles until process shutdown is requested."""

    while not stop_event.is_set():
        result = dispatch_one_file_import(
            authority,
            worker_factory,
            active_job_observer=active_job_observer,
            progress_interval_seconds=progress_interval_seconds,
        )
        if outcome_observer is not None:
            outcome_observer(result)
        if result.outcome == "no_work":
            stop_event.wait(_FILE_DISPATCH_POLL_SECONDS)


class _ActiveFileJobProgress:
    """Emit immediate and bounded in-flight observations for one claimed job."""

    __slots__ = ("_errors", "_stop_event", "_thread")

    def __init__(
        self,
        *,
        job_ref: str,
        observer: Callable[[str], None] | None,
        interval_seconds: float,
    ) -> None:
        self._stop_event = threading.Event()
        self._errors: list[Exception] = []
        self._thread: threading.Thread | None = None
        if observer is None:
            return
        observer(job_ref)

        def emit_until_complete() -> None:
            while not self._stop_event.wait(interval_seconds):
                try:
                    observer(job_ref)
                except Exception as error:
                    self._errors.append(error)
                    self._stop_event.set()

        self._thread = threading.Thread(
            target=emit_until_complete,
            name="file-dispatch-progress",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()

    def raise_if_failed(self) -> None:
        if self._errors:
            raise FileImportUnavailable("File dispatch progress is unavailable")


def _require_progress_interval(value: float) -> None:
    if (
        type(value) not in {float, int}
        or not math.isfinite(value)
        or not 0.01 <= value <= 60.0
    ):
        raise ValueError("File dispatch progress interval is unavailable")


def complete_persistent_noop_job(
    authority: WorkerNoOpCompletionAuthority,
    redemption: WorkerLeaseRedemption,
) -> WorkerNoOpCompletion:
    """Execute the bounded Issue #17 worker flow through its durable authority."""

    if type(redemption) is not WorkerLeaseRedemption:
        raise TypeError("redemption must be WorkerLeaseRedemption")
    return authority.complete_noop(redemption)


def _required_bounded_integer_environment(
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = _required_environment(name)
    if not raw_value.isascii() or not raw_value.isdecimal():
        raise ValueError("Supply worker configuration is not available")
    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError("Supply worker configuration is not available") from None
    if not minimum <= value <= maximum:
        raise ValueError("Supply worker configuration is not available")
    return value


def _embedding_provider() -> EmbeddingProvider:
    """Compose the explicit CI twin or one environment-only external provider."""

    mode = _required_environment(_WORKER_EMBEDDING_PROVIDER_ENV)
    raw_dimension = _required_environment(_WORKER_EMBEDDING_DIMENSION_ENV)
    if not raw_dimension.isdecimal():
        raise ValueError("Supply worker configuration is not available")
    try:
        dimension = int(raw_dimension)
    except ValueError:
        raise ValueError("Supply worker configuration is not available") from None
    if dimension != CONTEXT_FRAGMENT_EMBEDDING_DIMENSION:
        raise ValueError("Supply worker configuration is not available")
    if mode == "twin":
        return DeterministicEmbeddingTwin(dimension)
    if mode != "external":
        raise ValueError("Supply worker configuration is not available")
    raw_timeout = os.environ.get("CONTEXT_ENGINE_WORKER_EMBEDDING_TIMEOUT_SECONDS")
    if raw_timeout is None:
        timeout_seconds = 30.0
    else:
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError:
            raise ValueError("Supply worker configuration is not available") from None
    return ExternalEmbeddingProvider(
        ExternalEmbeddingConfiguration(
            endpoint=_required_environment("CONTEXT_ENGINE_WORKER_EMBEDDING_ENDPOINT"),
            model=_required_environment("CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL"),
            api_key=_required_environment("CONTEXT_ENGINE_WORKER_EMBEDDING_API_KEY"),
            dimension=dimension,
            batch_size=_required_bounded_integer_environment(
                "CONTEXT_ENGINE_WORKER_EMBEDDING_BATCH_SIZE",
                minimum=1,
                maximum=256,
            ),
            timeout_seconds=timeout_seconds,
        )
    )


def _run_one_file_import() -> int:
    """Consume one exact, signed File job in the independent Supply process."""

    signing_key = _worker_signing_key()
    configuration = load_database_configuration(DatabasePurpose.SUPPLY_WORKER)
    engine = create_database_engine(configuration)
    roots = FileRootRegistry(
        {
            FileRootRef(
                _required_environment("CONTEXT_ENGINE_WORKER_FILE_ROOT_REF")
            ): Path(_required_environment("CONTEXT_ENGINE_WORKER_FILE_ROOT_PATH"))
        },
        limits=_file_read_limits(),
    )
    try:
        outcome = PostgreSQLFileImportWorker(
            engine,
            WorkerLeaseCodec(
                WorkerLeaseKeyring(active_version=1, keys={1: signing_key})
            ),
            FileImportReceiver(
                UUID(
                    _required_environment("CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID")
                )
            ),
            roots,
            MarkdownCompilerConfig(ACTIVE_FILE_IMPORT_MARKDOWN_CONFIG_VERSION),
            embedding_provider=_embedding_provider(),
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

    embedding_provider = _embedding_provider()
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
        limits=_file_read_limits(),
    )
    try:
        authority = PostgreSQLFileDispatchAuthority(
            scheduler_engine,
            codec,
            configured_root_refs=tuple(root_ref.value for root_ref in root_bindings),
        )

        def worker_factory(receiver: FileImportReceiver) -> PostgreSQLFileImportWorker:
            return PostgreSQLFileImportWorker(
                worker_engine,
                codec,
                receiver,
                roots,
                MarkdownCompilerConfig(ACTIVE_FILE_IMPORT_MARKDOWN_CONFIG_VERSION),
                embedding_provider=embedding_provider,
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
                reporter = FileBatchProgressReporter(
                    lambda rendered: print(rendered, flush=True)
                )
                dispatch_file_imports_until_stopped(
                    authority,
                    worker_factory,
                    stop_event,
                    outcome_observer=reporter.observe_cycle,
                    active_job_observer=reporter.job_active,
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
