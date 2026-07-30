"""Read-only File Source acquisition-checkpoint and publication-watermark contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from engine.control.contracts import (
    SourceRef,
    _require_bounded_text,
    _require_token,
    _require_utc,
)
from engine.control.file_change_pages import MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE

if TYPE_CHECKING:
    from engine.control.file_change_pages import FileChangeBaseline, FileChangeScanHead

_MAX_BIGINT = 9_223_372_036_854_775_807


class FileSourceChangeKind(StrEnum):
    """Closed durable change carriers that participate in File progress."""

    FILE_IMPORT = "file_import"
    FILE_CHANGE_PAGE = "file_change_page"
    FILE_TOMBSTONE = "file_tombstone"


class FileSourcePublishOutcome(StrEnum):
    """Closed visibility outcomes that may advance a publish watermark."""

    PUBLISHED = "published"
    REPLACED = "replaced"
    UNCHANGED = "unchanged"
    TOMBSTONED = "tombstoned"


class FileCompilationRefusalCategory(StrEnum):
    """Closed content-free compilation categories retained for operations."""

    INVALID_UTF8 = "invalid_utf8"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    UNSUPPORTED_DOCUMENT_SHAPE = "unsupported_document_shape"


class FileScanRefusalCategory(StrEnum):
    """Closed content-free scan-level conditions retained for operations."""

    SCAN_BOUND_EXCEEDED = "scan_bound_exceeded"


@dataclass(frozen=True, slots=True)
class PendingFileChangeSchedule:
    """One accepted current-scan page whose upserts have no durable jobs."""

    source_version_ref: UUID = field(repr=False)
    page_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source_version_ref) is not UUID:
            raise TypeError("pending File schedule SourceVersion must be UUID")
        _require_progress_ref("pending File schedule page_ref", self.page_ref, "")


@dataclass(frozen=True, slots=True)
class FileCompilationRefusal:
    """Content-free status for one current observed path not yet published."""

    path: str
    category: FileCompilationRefusalCategory

    def __post_init__(self) -> None:
        from engine.control.file_imports import FileImportPath

        FileImportPath(self.path)
        if type(self.category) is not FileCompilationRefusalCategory:
            raise TypeError("File compilation refusal category is invalid")


@dataclass(frozen=True, slots=True)
class FileSourceStatus:
    """Operational File status that never participates in Runtime authority."""

    observed_at: datetime
    active_resource_count: int
    last_successful_acquisition_at: datetime | None
    last_successful_acquisition_age_seconds: int | None
    refusals: tuple[FileCompilationRefusal, ...] = ()
    scan_refusal_category: FileScanRefusalCategory | None = None
    scan_refusal_bound: int | None = None

    def __post_init__(self) -> None:
        _require_utc("File Source status observed_at", self.observed_at)
        if (
            type(self.active_resource_count) is not int
            or self.active_resource_count < 0
        ):
            raise ValueError("File Source active Resource count must be nonnegative")
        if self.last_successful_acquisition_at is None:
            if self.last_successful_acquisition_age_seconds is not None:
                raise ValueError("File Source absent success cannot have an age")
        else:
            _require_utc(
                "File Source last successful acquisition",
                self.last_successful_acquisition_at,
            )
            if (
                type(self.last_successful_acquisition_age_seconds) is not int
                or self.last_successful_acquisition_age_seconds < 0
            ):
                raise ValueError("File Source successful acquisition age is invalid")
        if type(self.refusals) is not tuple or any(
            type(refusal) is not FileCompilationRefusal for refusal in self.refusals
        ):
            raise TypeError("File Source refusals must be a tuple")
        paths = tuple(refusal.path for refusal in self.refusals)
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("File Source refusal paths require canonical order")
        if len(paths) != len(set(paths)):
            raise ValueError("File Source refusal paths must be unique")
        if self.scan_refusal_category is None:
            if self.scan_refusal_bound is not None:
                raise ValueError("File Source absent scan refusal cannot have a bound")
        elif (
            type(self.scan_refusal_category) is not FileScanRefusalCategory
            or type(self.scan_refusal_bound) is not int
            or not 1
            <= self.scan_refusal_bound
            <= MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE
        ):
            raise ValueError("File Source scan refusal is invalid")


def _require_sequence(name: str, value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_BIGINT:
        raise ValueError(f"{name} must fit a positive signed bigint")
    return value


def _require_resource_ref(value: object) -> str:
    return _require_bounded_text("File Source progress ResourceRef", value, 512)


def _require_progress_ref(name: str, value: object, prefix: str) -> str:
    token = _require_token(name, value)
    digest = token.removeprefix(prefix)
    if (
        not token.startswith(prefix)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"{name} is not a recognized opaque reference")
    return token


def _require_sha256_ref(name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} is not a SHA-256 reference")
    return value


def _require_lineage(
    *,
    change_kind: FileSourceChangeKind,
    acquisition_ref: object,
    job_ref: object,
    cleanup_intent_ref: object,
    resource_ref: object,
    revision_ref: object,
    event_ref: object,
    event_sequence: object,
    allow_unresolved_import_resource: bool,
    source_version_ref: object = None,
    change_page_ref: object = None,
) -> None:
    if type(change_kind) is not FileSourceChangeKind:
        raise TypeError("File Source progress change_kind is invalid")
    if change_kind is FileSourceChangeKind.FILE_IMPORT:
        if type(acquisition_ref) is not UUID or type(job_ref) is not UUID:
            raise TypeError("File import progress requires acquisition and job lineage")
        if any(
            value is not None
            for value in (
                cleanup_intent_ref,
                event_ref,
                event_sequence,
                source_version_ref,
                change_page_ref,
            )
        ):
            raise ValueError("File import progress cannot carry tombstone lineage")
        if allow_unresolved_import_resource and resource_ref is revision_ref is None:
            return
    elif change_kind is FileSourceChangeKind.FILE_TOMBSTONE:
        if type(cleanup_intent_ref) is not UUID:
            raise TypeError("File tombstone progress requires cleanup lineage")
        if acquisition_ref is not None or job_ref is not None:
            raise ValueError("File tombstone progress cannot carry import lineage")
        _require_token("File tombstone progress event_ref", event_ref)
        _require_sequence("File tombstone progress event_sequence", event_sequence)
        if source_version_ref is not None or change_page_ref is not None:
            raise ValueError("File tombstone progress cannot carry page lineage")
    else:
        if type(source_version_ref) is not UUID:
            raise TypeError("File page progress requires SourceVersion lineage")
        _require_sha256_ref("File page progress change_page_ref", change_page_ref)
        if any(
            value is not None
            for value in (
                acquisition_ref,
                job_ref,
                cleanup_intent_ref,
                resource_ref,
                revision_ref,
                event_ref,
                event_sequence,
            )
        ):
            raise ValueError("File page progress cannot carry publication lineage")
        return
    _require_resource_ref(resource_ref)
    if type(revision_ref) is not UUID:
        raise TypeError("File Source progress revision_ref must be UUID")


@dataclass(frozen=True, slots=True)
class FileSourceAcquisitionCheckpoint:
    """Latest source change durably accepted, irrespective of visibility."""

    sequence: int
    checkpoint_ref: str = field(repr=False)
    change_kind: FileSourceChangeKind
    acquisition_ref: UUID | None = field(repr=False)
    job_ref: UUID | None = field(repr=False)
    cleanup_intent_ref: UUID | None = field(repr=False)
    resource_ref: str | None
    revision_ref: UUID | None = field(repr=False)
    event_ref: str | None = field(repr=False)
    event_sequence: int | None
    accepted_at: datetime
    source_version_ref: UUID | None = field(default=None, repr=False)
    change_page_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_sequence("File Source acquisition sequence", self.sequence)
        _require_progress_ref(
            "File Source acquisition checkpoint_ref", self.checkpoint_ref, "facp_"
        )
        _require_lineage(
            change_kind=self.change_kind,
            acquisition_ref=self.acquisition_ref,
            job_ref=self.job_ref,
            cleanup_intent_ref=self.cleanup_intent_ref,
            resource_ref=self.resource_ref,
            revision_ref=self.revision_ref,
            event_ref=self.event_ref,
            event_sequence=self.event_sequence,
            source_version_ref=self.source_version_ref,
            change_page_ref=self.change_page_ref,
            allow_unresolved_import_resource=True,
        )
        _require_utc("File Source acquisition accepted_at", self.accepted_at)


@dataclass(frozen=True, slots=True)
class FileSourcePublishWatermark:
    """Latest resolved publication-bearing change reflected in Runtime visibility."""

    sequence: int
    watermark_ref: str = field(repr=False)
    checkpoint_ref: str = field(repr=False)
    change_kind: FileSourceChangeKind
    outcome: FileSourcePublishOutcome
    acquisition_ref: UUID | None = field(repr=False)
    job_ref: UUID | None = field(repr=False)
    cleanup_intent_ref: UUID | None = field(repr=False)
    resource_ref: str
    revision_ref: UUID = field(repr=False)
    event_ref: str | None = field(repr=False)
    event_sequence: int | None
    published_at: datetime

    def __post_init__(self) -> None:
        _require_sequence("File Source publish sequence", self.sequence)
        _require_progress_ref(
            "File Source publish watermark_ref", self.watermark_ref, "fpwm_"
        )
        _require_progress_ref(
            "File Source publish checkpoint_ref", self.checkpoint_ref, "facp_"
        )
        if type(self.outcome) is not FileSourcePublishOutcome:
            raise TypeError("File Source publish outcome is invalid")
        if self.change_kind is FileSourceChangeKind.FILE_CHANGE_PAGE:
            raise ValueError("File change pages cannot advance a publish watermark")
        _require_lineage(
            change_kind=self.change_kind,
            acquisition_ref=self.acquisition_ref,
            job_ref=self.job_ref,
            cleanup_intent_ref=self.cleanup_intent_ref,
            resource_ref=self.resource_ref,
            revision_ref=self.revision_ref,
            event_ref=self.event_ref,
            event_sequence=self.event_sequence,
            allow_unresolved_import_resource=False,
        )
        if (self.change_kind is FileSourceChangeKind.FILE_TOMBSTONE) is not (
            self.outcome is FileSourcePublishOutcome.TOMBSTONED
        ):
            raise ValueError("File Source publish outcome does not match its change")
        _require_utc("File Source publish published_at", self.published_at)


@dataclass(frozen=True, slots=True)
class FileSourceProgress:
    """Organization/Source-scoped read model for durable progress signals."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef
    acquisition_checkpoint: FileSourceAcquisitionCheckpoint | None
    publish_watermark: FileSourcePublishWatermark | None
    change_scan_head: FileChangeScanHead | None = field(default=None, repr=False)
    complete_change_baseline: FileChangeBaseline | None = field(
        default=None,
        repr=False,
    )
    pending_change_schedules: tuple[PendingFileChangeSchedule, ...] = field(
        default=(),
        repr=False,
    )
    status: FileSourceStatus | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("File Source progress organization_id must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File Source progress source_ref must be SourceRef")
        if (
            self.acquisition_checkpoint is not None
            and type(self.acquisition_checkpoint) is not FileSourceAcquisitionCheckpoint
        ):
            raise TypeError("File Source acquisition checkpoint is invalid")
        if (
            self.publish_watermark is not None
            and type(self.publish_watermark) is not FileSourcePublishWatermark
        ):
            raise TypeError("File Source publish watermark is invalid")
        if self.change_scan_head is not None:
            from engine.control.file_change_pages import FileChangeScanHead

            if type(self.change_scan_head) is not FileChangeScanHead:
                raise TypeError("File Source change scan head is invalid")
            if self.acquisition_checkpoint is None:
                raise ValueError("File Source change head requires a checkpoint")
            if self.change_scan_head.sequence > self.acquisition_checkpoint.sequence:
                raise ValueError("File Source change head exceeds its checkpoint")
            if (
                self.change_scan_head.sequence == self.acquisition_checkpoint.sequence
                and (
                    self.change_scan_head.checkpoint_ref
                    != self.acquisition_checkpoint.checkpoint_ref
                    or self.acquisition_checkpoint.change_kind
                    is not FileSourceChangeKind.FILE_CHANGE_PAGE
                    or self.change_scan_head.source_version_ref
                    != self.acquisition_checkpoint.source_version_ref
                    or self.change_scan_head.page_ref
                    != self.acquisition_checkpoint.change_page_ref
                )
            ):
                raise ValueError("File Source change head lineage is invalid")
        if self.complete_change_baseline is not None:
            from engine.control.file_change_pages import FileChangeBaseline

            if type(self.complete_change_baseline) is not FileChangeBaseline:
                raise TypeError("File Source complete change baseline is invalid")
            if self.acquisition_checkpoint is None:
                raise ValueError("File Source complete baseline requires a checkpoint")
            baseline_ref = self.complete_change_baseline.reference
            if baseline_ref.sequence > self.acquisition_checkpoint.sequence:
                raise ValueError("File Source complete baseline exceeds its checkpoint")
            if (
                self.change_scan_head is not None
                and baseline_ref.source_version_ref
                != self.change_scan_head.source_version_ref
            ):
                raise ValueError(
                    "File Source complete baseline belongs to another SourceVersion"
                )
        if type(self.pending_change_schedules) is not tuple or any(
            type(pending) is not PendingFileChangeSchedule
            for pending in self.pending_change_schedules
        ):
            raise TypeError("File Source pending schedules must be a tuple")
        pending_refs = tuple(
            pending.page_ref for pending in self.pending_change_schedules
        )
        if len(pending_refs) != len(set(pending_refs)):
            raise ValueError("File Source pending schedules must be unique")
        if self.pending_change_schedules and (
            self.change_scan_head is None
            or any(
                pending.source_version_ref != self.change_scan_head.source_version_ref
                for pending in self.pending_change_schedules
            )
        ):
            raise ValueError("File Source pending schedules must belong to the head")
        if self.status is not None and type(self.status) is not FileSourceStatus:
            raise TypeError("File Source status is invalid")
        if self.publish_watermark is not None and (
            self.acquisition_checkpoint is None
            or self.publish_watermark.sequence > self.acquisition_checkpoint.sequence
        ):
            raise ValueError("File Source publish watermark cannot exceed checkpoint")
        if (
            self.publish_watermark is not None
            and self.acquisition_checkpoint is not None
            and self.publish_watermark.sequence == self.acquisition_checkpoint.sequence
            and self.publish_watermark.checkpoint_ref
            != self.acquisition_checkpoint.checkpoint_ref
        ):
            raise ValueError("File Source publish watermark lineage is invalid")
