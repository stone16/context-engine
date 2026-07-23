"""Read-only File Source acquisition-checkpoint and publication-watermark contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from engine.control.contracts import (
    SourceRef,
    _require_bounded_text,
    _require_token,
    _require_utc,
)

_MAX_BIGINT = 9_223_372_036_854_775_807


class FileSourceChangeKind(StrEnum):
    """Closed durable change carriers that participate in File progress."""

    FILE_IMPORT = "file_import"
    FILE_TOMBSTONE = "file_tombstone"


class FileSourcePublishOutcome(StrEnum):
    """Closed visibility outcomes that may advance a publish watermark."""

    PUBLISHED = "published"
    REPLACED = "replaced"
    UNCHANGED = "unchanged"
    TOMBSTONED = "tombstoned"


def _require_sequence(name: str, value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_BIGINT:
        raise ValueError(f"{name} must fit a positive signed bigint")
    return value


def _require_resource_ref(value: object) -> str:
    return _require_bounded_text("File Source progress ResourceRef", value, 512)


def _require_progress_ref(name: str, value: object, prefix: str) -> str:
    token = _require_token(name, value)
    digest = token.removeprefix(prefix)
    if not token.startswith(prefix) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError(f"{name} is not a recognized opaque reference")
    return token


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
) -> None:
    if type(change_kind) is not FileSourceChangeKind:
        raise TypeError("File Source progress change_kind is invalid")
    if change_kind is FileSourceChangeKind.FILE_IMPORT:
        if type(acquisition_ref) is not UUID or type(job_ref) is not UUID:
            raise TypeError("File import progress requires acquisition and job lineage")
        if any(
            value is not None
            for value in (cleanup_intent_ref, event_ref, event_sequence)
        ):
            raise ValueError("File import progress cannot carry tombstone lineage")
        if allow_unresolved_import_resource and resource_ref is revision_ref is None:
            return
    else:
        if type(cleanup_intent_ref) is not UUID:
            raise TypeError("File tombstone progress requires cleanup lineage")
        if acquisition_ref is not None or job_ref is not None:
            raise ValueError("File tombstone progress cannot carry import lineage")
        _require_token("File tombstone progress event_ref", event_ref)
        _require_sequence("File tombstone progress event_sequence", event_sequence)
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
            allow_unresolved_import_resource=True,
        )
        _require_utc("File Source acquisition accepted_at", self.accepted_at)


@dataclass(frozen=True, slots=True)
class FileSourcePublishWatermark:
    """Latest contiguous accepted change fully reflected in Runtime visibility."""

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
        if (
            self.change_kind is FileSourceChangeKind.FILE_TOMBSTONE
        ) is not (self.outcome is FileSourcePublishOutcome.TOMBSTONED):
            raise ValueError("File Source publish outcome does not match its change")
        _require_utc("File Source publish published_at", self.published_at)


@dataclass(frozen=True, slots=True)
class FileSourceProgress:
    """Organization/Source-scoped read model for the two progress signals."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef
    acquisition_checkpoint: FileSourceAcquisitionCheckpoint | None
    publish_watermark: FileSourcePublishWatermark | None

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("File Source progress organization_id must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File Source progress source_ref must be SourceRef")
        if self.acquisition_checkpoint is not None and type(
            self.acquisition_checkpoint
        ) is not FileSourceAcquisitionCheckpoint:
            raise TypeError("File Source acquisition checkpoint is invalid")
        if self.publish_watermark is not None and type(
            self.publish_watermark
        ) is not FileSourcePublishWatermark:
            raise TypeError("File Source publish watermark is invalid")
        if self.publish_watermark is not None and (
            self.acquisition_checkpoint is None
            or self.publish_watermark.sequence
            > self.acquisition_checkpoint.sequence
        ):
            raise ValueError("File Source publish watermark cannot exceed checkpoint")
