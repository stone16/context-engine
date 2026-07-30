from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import cast

from sqlalchemy import Engine

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.file_source import FileReadLimits, FileRootRegistry
from applications.worker import (
    _worker_database_time,
    dispatch_file_imports_until_stopped,
)
from applications.worker_progress import FileBatchProgressReporter
from engine.control import FileImportReceiver, FileRootRef
from engine.persistence import (
    FileDispatchLease,
    FileDispatchNoWork,
    PostgreSQLFileDispatchAuthority,
    PostgreSQLFileImportWorker,
)
from engine.supply import MarkdownCompilerConfig, WorkerLeaseCodec, WorkerLeaseKeyring


@dataclass(frozen=True, slots=True)
class BatchProgressCapture:
    documents: tuple[dict[str, object], ...]
    emitted_at: tuple[float, ...]


class _StopOnIdleAuthority:
    def __init__(
        self,
        authority: PostgreSQLFileDispatchAuthority,
        stop_event: threading.Event,
    ) -> None:
        self._authority = authority
        self._stop_event = stop_event

    def claim(self) -> FileDispatchLease | FileDispatchNoWork:
        claim = self._authority.claim()
        if type(claim) is FileDispatchNoWork:
            self._stop_event.set()
        return claim


class _DelayedWorker:
    def __init__(
        self,
        worker: PostgreSQLFileImportWorker,
        delay_seconds: float,
    ) -> None:
        self._worker = worker
        self._delay_seconds = delay_seconds

    def run(self, redemption: object) -> object:
        sleep(self._delay_seconds)
        return self._worker.run(redemption)  # type: ignore[arg-type]


def run_scheduled_file_batch(
    *,
    scheduler_engine: Engine,
    worker_engine: Engine,
    root_ref: FileRootRef,
    root_registry: FileRootRegistry,
    signing_key: bytes,
    progress_interval_seconds: float = 1.0,
    job_delay_seconds: float = 0.0,
) -> BatchProgressCapture:
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: signing_key})
    )
    authority = PostgreSQLFileDispatchAuthority(
        scheduler_engine,
        codec,
        configured_root_refs=(root_ref.value,),
    )
    stop_event = threading.Event()
    rendered: list[str] = []
    emitted_at: list[float] = []

    def emit(value: str) -> None:
        emitted_at.append(monotonic())
        rendered.append(value)

    reporter = FileBatchProgressReporter(
        emit,
        batch_ref_factory=lambda: "a" * 64,
    )

    def worker_factory(receiver: FileImportReceiver) -> object:
        worker = PostgreSQLFileImportWorker(
            worker_engine,
            codec,
            receiver,
            root_registry,
            MarkdownCompilerConfig("markdown-config-v1"),
            embedding_provider=DeterministicEmbeddingTwin(),
            clock=lambda: _worker_database_time(worker_engine),
        )
        if job_delay_seconds:
            return _DelayedWorker(worker, job_delay_seconds)
        return worker

    dispatch_file_imports_until_stopped(
        _StopOnIdleAuthority(authority, stop_event),
        worker_factory,  # type: ignore[arg-type]
        stop_event,
        outcome_observer=reporter.observe_cycle,
        active_job_observer=reporter.job_active,
        progress_interval_seconds=progress_interval_seconds,
    )
    return BatchProgressCapture(
        documents=tuple(
            cast(dict[str, object], json.loads(value)) for value in rendered
        ),
        emitted_at=tuple(emitted_at),
    )


def file_root_registry(root_ref: FileRootRef, root: Path) -> FileRootRegistry:
    if not isinstance(root, Path):
        raise TypeError("test File root must be Path")
    return FileRootRegistry(
        {root_ref: root},
        limits=FileReadLimits(max_file_bytes=4_096),
    )
