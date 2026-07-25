"""Closed contracts for the first trusted one-file import operation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn
from uuid import UUID

from engine.control.contracts import SourceRef, _require_sha256

FILE_IMPORT_WORKLOAD = "supply.file-import"
FILE_IMPORT_WORKER_AUDIENCE = "context-engine-worker"
FILE_IMPORT_OPERATION = "file.import"


def _require_token(field_name: str, value: object, *, maximum: int = 255) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > maximum
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank token")
    return value


@dataclass(frozen=True, slots=True)
class FileImportPath:
    """One relative Markdown filename; directories and traversal are closed."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        value = self.value
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or not value.casefold().endswith(".md")
            or len(value) > 255
            or any(ord(character) < 0x20 for character in value)
            or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        ):
            raise ValueError("File import path must be one bounded Markdown filename")


@dataclass(frozen=True, slots=True)
class FileImportAudience:
    """Mirrored first-publication grant target; never worker identity."""

    principal_ref: str = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)

    def __post_init__(self) -> None:
        _require_token("File import principal_ref", self.principal_ref)
        if type(self.membership_id) is not UUID:
            raise TypeError("File import membership_id must be UUID")
        if (
            type(self.membership_version) is not int
            or not 1 <= self.membership_version <= 2**63 - 1
        ):
            raise ValueError("File import Membership version must be positive")


@dataclass(frozen=True, slots=True)
class PrepareFileImport:
    """Trusted Control request for one registered-source import envelope."""

    source_ref: SourceRef
    path: FileImportPath = field(repr=False)
    audience: FileImportAudience = field(repr=False)
    idempotency_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File import source_ref must be SourceRef")
        if type(self.path) is not FileImportPath:
            raise TypeError("File import path must be FileImportPath")
        if type(self.audience) is not FileImportAudience:
            raise TypeError("File import audience must be FileImportAudience")
        _require_token("File import idempotency_key", self.idempotency_key)

    def __reduce__(self) -> NoReturn:
        raise TypeError("File import command is not serializable")


@dataclass(frozen=True, slots=True)
class ScheduleFileChangePage:
    """Explicitly schedule one accepted File page for an exact audience."""

    source_ref: SourceRef
    source_version_ref: UUID = field(repr=False)
    page_ref: str = field(repr=False)
    audience: FileImportAudience = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("scheduled File page source_ref must be SourceRef")
        if type(self.source_version_ref) is not UUID:
            raise TypeError("scheduled File page source version must be UUID")
        _require_sha256("scheduled File page reference", self.page_ref)
        if type(self.audience) is not FileImportAudience:
            raise TypeError("scheduled File page audience must be FileImportAudience")

    def __reduce__(self) -> NoReturn:
        raise TypeError("File change page scheduling command is not serializable")


@dataclass(frozen=True, slots=True)
class PreparedFileImport:
    """Content-free exact-job locator returned by trusted ContextControl."""

    organization_id: UUID = field(repr=False)
    job_id: UUID = field(repr=False)
    source_ref: SourceRef = field(repr=False)
    service_principal_id: UUID = field(repr=False)
    workload: str = field(default=FILE_IMPORT_WORKLOAD, init=False)
    worker_audience: str = field(
        default=FILE_IMPORT_WORKER_AUDIENCE,
        init=False,
    )
    operation: str = field(
        default=FILE_IMPORT_OPERATION,
        init=False,
    )

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID or type(self.job_id) is not UUID:
            raise TypeError("Prepared File import identifiers must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("Prepared File import source_ref must be SourceRef")
        if type(self.service_principal_id) is not UUID:
            raise TypeError("Prepared File import service principal must be UUID")


@dataclass(frozen=True, slots=True)
class ScheduledFileChange:
    """One accepted change bound to its existing prepared import job."""

    ordinal: int
    path: FileImportPath
    content_sha256: str = field(repr=False)
    content_length: int
    prepared_import: PreparedFileImport

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 100:
            raise ValueError("scheduled File change ordinal is invalid")
        if type(self.path) is not FileImportPath:
            raise TypeError("scheduled File change path must be FileImportPath")
        _require_sha256("scheduled File change content identity", self.content_sha256)
        if type(self.content_length) is not int or self.content_length < 0:
            raise ValueError("scheduled File change content length is invalid")
        if type(self.prepared_import) is not PreparedFileImport:
            raise TypeError("scheduled File change requires PreparedFileImport")


@dataclass(frozen=True, slots=True)
class ScheduledFileChangePage:
    """Complete ordered job set created from one accepted File page."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef = field(repr=False)
    source_version_ref: UUID = field(repr=False)
    page_ref: str = field(repr=False)
    changes: tuple[ScheduledFileChange, ...]

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("scheduled File page organization must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("scheduled File page source must be SourceRef")
        if type(self.source_version_ref) is not UUID:
            raise TypeError("scheduled File page source version must be UUID")
        _require_sha256("scheduled File page reference", self.page_ref)
        if (
            type(self.changes) is not tuple
            or not self.changes
            or any(type(change) is not ScheduledFileChange for change in self.changes)
        ):
            raise TypeError("scheduled File page requires a nonempty change tuple")
        ordinals = tuple(change.ordinal for change in self.changes)
        if any(
            current >= following
            for current, following in zip(ordinals, ordinals[1:], strict=False)
        ):
            raise ValueError(
                "scheduled File page changes must be strictly increasing"
            )
        if any(
            change.prepared_import.organization_id != self.organization_id
            or change.prepared_import.source_ref != self.source_ref
            for change in self.changes
        ):
            raise ValueError("scheduled File page contains a foreign import")


@dataclass(frozen=True, slots=True)
class FileImportReceiver:
    """Trusted registered worker identity injected into Control composition."""

    service_principal_id: UUID = field(repr=False)
    workload: str = field(default=FILE_IMPORT_WORKLOAD, init=False, repr=False)
    worker_audience: str = field(
        default=FILE_IMPORT_WORKER_AUDIENCE,
        init=False,
        repr=False,
    )
    operation: str = field(
        default=FILE_IMPORT_OPERATION,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.service_principal_id) is not UUID:
            raise TypeError("File import receiver identity must be UUID")
