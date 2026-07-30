"""Privacy-shaped progress records for autonomous File dispatch batches."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast
from uuid import UUID

BATCH_PROGRESS_SCHEMA_VERSION = "context-engine-worker-batch-progress-v1"
_OPAQUE_FILE_JOB_DOMAIN = b"context-engine.worker-batch-job-ref.v1\x00"
_SHA256_LENGTH = 64


class FileDispatchFailureCategory(StrEnum):
    """Closed, content-free reasons for one non-fatal dispatch refusal."""

    FILE_IMPORT_REFUSED = "file_import_refused"
    WORKER_LEASE_REFUSED = "worker_lease_refused"


@dataclass(frozen=True, slots=True)
class FileDispatchCycleResult:
    """Content-free process result for one autonomous dispatch cycle."""

    outcome: str
    job_ref: str | None = field(default=None, repr=False)
    reason_category: FileDispatchFailureCategory | None = None
    status: str = field(default="complete", init=False)

    def __post_init__(self) -> None:
        if self.outcome not in {"dispatched", "no_work", "refused"}:
            raise ValueError("File dispatch cycle outcome must remain closed")
        if self.status != "complete":  # pragma: no cover - init=False fence
            raise ValueError("File dispatch cycle status must remain closed")
        if self.outcome == "no_work":
            if self.job_ref is not None or self.reason_category is not None:
                raise ValueError("no-work cannot carry job attribution")
            return
        _require_opaque_ref("File dispatch job", self.job_ref)
        if self.outcome == "dispatched":
            if self.reason_category is not None:
                raise ValueError("successful dispatch cannot carry a failure reason")
            return
        if type(self.reason_category) is not FileDispatchFailureCategory:
            raise ValueError("refused dispatch requires a closed reason category")


@dataclass(frozen=True, slots=True)
class WorkerBatchProgress:
    """One schema-versioned aggregate progress observation."""

    batch_ref: str
    phase: str
    processed: int
    total: int
    failed: int
    current_job_ref: str | None
    outcome: str | None
    reason_category: FileDispatchFailureCategory | None

    def __post_init__(self) -> None:
        _require_opaque_ref("worker batch", self.batch_ref)
        if self.phase not in {"dispatching", "complete"}:
            raise ValueError("worker batch phase must remain closed")
        if any(
            type(value) is not int or value < 0
            for value in (self.processed, self.total, self.failed)
        ):
            raise ValueError("worker batch counters must be non-negative integers")
        if self.failed > self.processed or self.processed > self.total:
            raise ValueError("worker batch counters are inconsistent")
        if self.phase == "complete":
            if self.processed != self.total:
                raise ValueError(
                    "complete worker batch counters must account for every job"
                )
            if any(
                value is not None
                for value in (
                    self.current_job_ref,
                    self.outcome,
                    self.reason_category,
                )
            ):
                raise ValueError("complete worker batch cannot carry a current job")
            return
        _require_opaque_ref("current worker job", self.current_job_ref)
        if self.outcome not in {None, "dispatched", "refused"}:
            raise ValueError("worker batch outcome must remain closed")
        if self.outcome == "refused":
            if type(self.reason_category) is not FileDispatchFailureCategory:
                raise ValueError("refused worker job requires a closed reason")
        elif self.reason_category is not None:
            raise ValueError("non-refused worker job cannot carry a failure reason")

    def document(self) -> dict[str, object]:
        """Return exactly the public schema projection."""

        return {
            "batchRef": self.batch_ref,
            "currentJobRef": self.current_job_ref,
            "failed": self.failed,
            "outcome": self.outcome,
            "phase": self.phase,
            "processed": self.processed,
            "reasonCategory": (
                None if self.reason_category is None else self.reason_category.value
            ),
            "schemaVersion": BATCH_PROGRESS_SCHEMA_VERSION,
            "total": self.total,
        }


class FileBatchProgressReporter:
    """Aggregate job observations and suppress content-free idle polling chatter."""

    __slots__ = (
        "_batch_ref",
        "_batch_ref_factory",
        "_current_job_ref",
        "_emit",
        "_failed",
        "_lock",
        "_processed",
        "_total",
    )

    def __init__(
        self,
        emit: Callable[[str], None],
        *,
        batch_ref_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(emit):
            raise TypeError("worker batch progress emitter must be callable")
        if batch_ref_factory is not None and not callable(batch_ref_factory):
            raise TypeError("worker batch reference factory must be callable")
        self._emit = emit
        self._batch_ref_factory = batch_ref_factory or _fresh_batch_ref
        self._lock = threading.Lock()
        self._batch_ref: str | None = None
        self._current_job_ref: str | None = None
        self._processed = 0
        self._total = 0
        self._failed = 0

    def job_active(self, job_ref: str) -> None:
        """Emit immediately and on each bounded tick while one exact job runs."""

        _require_opaque_ref("active File job", job_ref)
        with self._lock:
            if self._batch_ref is None:
                batch_ref = self._batch_ref_factory()
                _require_opaque_ref("worker batch", batch_ref)
                self._batch_ref = batch_ref
            if self._current_job_ref is None:
                self._current_job_ref = job_ref
                self._total += 1
            elif self._current_job_ref != job_ref:
                raise ValueError("worker batch cannot overlap File jobs")
            self._emit_progress(
                WorkerBatchProgress(
                    batch_ref=self._batch_ref,
                    phase="dispatching",
                    processed=self._processed,
                    total=self._total,
                    failed=self._failed,
                    current_job_ref=job_ref,
                    outcome=None,
                    reason_category=None,
                )
            )

    def observe_cycle(self, result: FileDispatchCycleResult) -> None:
        """Account for one terminal job or close one observed batch on idle."""

        if type(result) is not FileDispatchCycleResult:
            raise TypeError("worker batch observer requires FileDispatchCycleResult")
        with self._lock:
            if result.outcome == "no_work":
                if self._batch_ref is None:
                    return
                if self._current_job_ref is not None:
                    raise ValueError("worker batch cannot close with an active job")
                self._emit_progress(
                    WorkerBatchProgress(
                        batch_ref=self._batch_ref,
                        phase="complete",
                        processed=self._processed,
                        total=self._total,
                        failed=self._failed,
                        current_job_ref=None,
                        outcome=None,
                        reason_category=None,
                    )
                )
                self._reset()
                return
            if (
                self._batch_ref is None
                or self._current_job_ref is None
                or result.job_ref != self._current_job_ref
            ):
                raise ValueError("worker result does not match the active batch job")
            self._processed += 1
            if result.outcome == "refused":
                self._failed += 1
            self._emit_progress(
                WorkerBatchProgress(
                    batch_ref=self._batch_ref,
                    phase="dispatching",
                    processed=self._processed,
                    total=self._total,
                    failed=self._failed,
                    current_job_ref=self._current_job_ref,
                    outcome=result.outcome,
                    reason_category=result.reason_category,
                )
            )
            self._current_job_ref = None

    def _emit_progress(self, progress: WorkerBatchProgress) -> None:
        self._emit(
            json.dumps(progress.document(), separators=(",", ":"), sort_keys=True)
        )

    def _reset(self) -> None:
        self._batch_ref = None
        self._current_job_ref = None
        self._processed = 0
        self._total = 0
        self._failed = 0


def opaque_file_job_ref(job_id: UUID) -> str:
    """Derive a stable batch-only attribution without rendering the durable UUID."""

    if type(job_id) is not UUID:
        raise TypeError("File job identity must be UUID")
    return hashlib.sha256(_OPAQUE_FILE_JOB_DOMAIN + job_id.bytes).hexdigest()


def validate_worker_batch_progress_document(
    value: object,
) -> WorkerBatchProgress:
    """Validate the tracked shape plus its relational counter invariants."""

    if type(value) is not dict:
        raise ValueError("worker batch progress document must be an object")
    document = cast(dict[str, object], value)
    expected_keys = {
        "batchRef",
        "currentJobRef",
        "failed",
        "outcome",
        "phase",
        "processed",
        "reasonCategory",
        "schemaVersion",
        "total",
    }
    if set(document) != expected_keys:
        raise ValueError("worker batch progress document fields are invalid")
    if document["schemaVersion"] != BATCH_PROGRESS_SCHEMA_VERSION:
        raise ValueError("worker batch progress schema version is invalid")
    reason_value = document["reasonCategory"]
    if reason_value is None:
        reason = None
    elif type(reason_value) is str:
        try:
            reason = FileDispatchFailureCategory(reason_value)
        except ValueError:
            raise ValueError("worker batch failure category is invalid") from None
    else:
        raise ValueError("worker batch failure category is invalid")
    progress = WorkerBatchProgress(
        batch_ref=_document_string(document, "batchRef"),
        phase=_document_string(document, "phase"),
        processed=_document_integer(document, "processed"),
        total=_document_integer(document, "total"),
        failed=_document_integer(document, "failed"),
        current_job_ref=_document_optional_string(document, "currentJobRef"),
        outcome=_document_optional_string(document, "outcome"),
        reason_category=reason,
    )
    if progress.document() != document:
        raise ValueError("worker batch progress document is not canonical")
    return progress


def _fresh_batch_ref() -> str:
    return secrets.token_hex(32)


def _document_string(document: dict[str, object], name: str) -> str:
    value = document[name]
    if type(value) is not str:
        raise ValueError("worker batch progress document field is invalid")
    return value


def _document_optional_string(
    document: dict[str, object],
    name: str,
) -> str | None:
    value = document[name]
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("worker batch progress document field is invalid")
    return value


def _document_integer(document: dict[str, object], name: str) -> int:
    value = document[name]
    if type(value) is not int:
        raise ValueError("worker batch progress document field is invalid")
    return value


def _require_opaque_ref(name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} reference must be opaque SHA-256")
    return value
