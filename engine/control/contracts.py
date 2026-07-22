"""Public ContextControl contracts for the first File source registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NoReturn
from uuid import UUID

MAX_SOURCE_DISPLAY_NAME_LENGTH = 200
MAX_SOURCE_TOKEN_LENGTH = 128


def _require_bounded_text(field_name: str, value: object, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError(f"{field_name} must be bounded nonblank Unicode")
    return value


def _require_token(field_name: str, value: object) -> str:
    token = _require_bounded_text(field_name, value, MAX_SOURCE_TOKEN_LENGTH)
    if not (token[0].isascii() and token[0].isalnum()) or any(
        not (character.isascii() and (character.isalnum() or character in "._-"))
        for character in token
    ):
        raise ValueError(f"{field_name} must be a bounded opaque token")
    return token


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an aware UTC datetime")
    return value


class SourceKind(StrEnum):
    FILE = "file"


class SourceMode(StrEnum):
    MATERIALIZED = "materialized"


class SourceContentKind(StrEnum):
    MARKDOWN = "markdown"


class SourceResourceKind(StrEnum):
    MARKDOWN_DOCUMENT = "markdown_document"


class SourceAclEvidenceMode(StrEnum):
    MIRRORED = "mirrored"


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class FileCapabilityManifest:
    """Exact Issue #21 declaration; registration is not acquisition readiness."""

    declaration_version: str = "file-capabilities-v1"
    source_mode: SourceMode = SourceMode.MATERIALIZED
    content_kinds: tuple[SourceContentKind, ...] = (SourceContentKind.MARKDOWN,)
    resource_kinds: tuple[SourceResourceKind, ...] = (
        SourceResourceKind.MARKDOWN_DOCUMENT,
    )
    acl_evidence_mode: SourceAclEvidenceMode = SourceAclEvidenceMode.MIRRORED
    projection_fields: tuple[str, ...] = ()
    cursor_semantics: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    checkpoint_semantics: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    batch_limits: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    freshness: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    consistency_guarantees: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    describe_capabilities: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    read_changes: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    discover: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    authorize_and_project: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    checkpoint: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    deletion: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    file_source_access: CapabilityStatus = CapabilityStatus.UNAVAILABLE
    ingestion_jobs: CapabilityStatus = CapabilityStatus.UNAVAILABLE

    def __post_init__(self) -> None:
        if (
            self.declaration_version != "file-capabilities-v1"
            or self.source_mode is not SourceMode.MATERIALIZED
            or self.content_kinds != (SourceContentKind.MARKDOWN,)
            or self.resource_kinds != (SourceResourceKind.MARKDOWN_DOCUMENT,)
            or self.acl_evidence_mode is not SourceAclEvidenceMode.MIRRORED
            or self.projection_fields != ()
            or any(
                status is not CapabilityStatus.UNAVAILABLE
                for status in (
                    self.cursor_semantics,
                    self.checkpoint_semantics,
                    self.batch_limits,
                    self.freshness,
                    self.consistency_guarantees,
                    self.describe_capabilities,
                    self.read_changes,
                    self.discover,
                    self.authorize_and_project,
                    self.checkpoint,
                    self.deletion,
                    self.file_source_access,
                    self.ingestion_jobs,
                )
            )
        ):
            raise ValueError("File capability manifest is closed at Issue #21")

    def document(self) -> dict[str, object]:
        """Return the exact persisted/public declaration without activation claims."""

        return {
            "aclEvidenceMode": self.acl_evidence_mode.value,
            "authorizeAndProject": self.authorize_and_project.value,
            "batchLimits": self.batch_limits.value,
            "checkpoint": self.checkpoint.value,
            "checkpointSemantics": self.checkpoint_semantics.value,
            "contentKinds": [value.value for value in self.content_kinds],
            "consistencyGuarantees": self.consistency_guarantees.value,
            "cursorSemantics": self.cursor_semantics.value,
            "declarationVersion": self.declaration_version,
            "deletion": self.deletion.value,
            "describeCapabilities": self.describe_capabilities.value,
            "discover": self.discover.value,
            "fileSourceAccess": self.file_source_access.value,
            "freshness": self.freshness.value,
            "ingestionJobs": self.ingestion_jobs.value,
            "projectionFields": list(self.projection_fields),
            "readChanges": self.read_changes.value,
            "resourceKinds": [value.value for value in self.resource_kinds],
            "sourceMode": self.source_mode.value,
        }


FILE_CAPABILITY_MANIFEST = FileCapabilityManifest()


@dataclass(frozen=True, slots=True)
class FileRootRef:
    """Opaque logical File root identity; never a host filesystem path."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            value = _require_token("FileRootRef", self.value)
        except ValueError:
            raise ValueError(
                "FileRootRef must be an opaque logical File root reference"
            ) from None
        if value in {".", ".."}:
            raise ValueError(
                "FileRootRef must be an opaque logical File root reference"
            )


@dataclass(frozen=True, slots=True)
class RegisterFileSource:
    """Untrusted registration values; trusted identity and mode are absent."""

    display_name: str
    root_ref: FileRootRef = field(repr=False)
    idempotency_key: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_bounded_text(
            "File source display_name",
            self.display_name,
            MAX_SOURCE_DISPLAY_NAME_LENGTH,
        )
        if type(self.root_ref) is not FileRootRef:
            raise TypeError("root_ref must be FileRootRef")
        _require_token("File registration idempotency_key", self.idempotency_key)

    def __reduce__(self) -> NoReturn:
        raise TypeError("File source registration command is not serializable")


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Opaque source locator; it carries no Organization or read authority."""

    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.value) is not UUID:
            raise TypeError("SourceRef value must be UUID")


@dataclass(frozen=True, slots=True)
class SourceVersion:
    """Immutable active source-configuration snapshot returned by Control."""

    source_ref: SourceRef
    version_ref: UUID = field(repr=False)
    kind: SourceKind
    root_ref: FileRootRef = field(repr=False)
    capabilities: FileCapabilityManifest
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("SourceVersion source_ref must be SourceRef")
        if type(self.version_ref) is not UUID:
            raise TypeError("SourceVersion version_ref must be UUID")
        if self.kind is not SourceKind.FILE:
            raise ValueError("SourceVersion kind must be file")
        if type(self.root_ref) is not FileRootRef:
            raise TypeError("SourceVersion root_ref must be FileRootRef")
        if type(self.capabilities) is not FileCapabilityManifest:
            raise TypeError("SourceVersion requires FileCapabilityManifest")
        _require_utc("SourceVersion created_at", self.created_at)


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """Control read model; its references are locators, never trusted identity."""

    source_ref: SourceRef
    display_name: str
    kind: SourceKind
    active_version: SourceVersion
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("SourceManifest source_ref must be SourceRef")
        _require_bounded_text(
            "SourceManifest display_name",
            self.display_name,
            MAX_SOURCE_DISPLAY_NAME_LENGTH,
        )
        if self.kind is not SourceKind.FILE:
            raise ValueError("SourceManifest kind must be file")
        if (
            type(self.active_version) is not SourceVersion
            or self.active_version.source_ref != self.source_ref
            or self.active_version.kind is not self.kind
        ):
            raise ValueError("active SourceVersion must belong to its source")
        _require_utc("SourceManifest created_at", self.created_at)

    @classmethod
    def issue_21_file(
        cls,
        *,
        source_ref: SourceRef,
        version_ref: UUID,
        display_name: str,
        root_ref: FileRootRef,
        created_at: datetime,
    ) -> SourceManifest:
        """Construct the exact first File manifest from trusted stored facts."""

        version = SourceVersion(
            source_ref=source_ref,
            version_ref=version_ref,
            kind=SourceKind.FILE,
            root_ref=root_ref,
            capabilities=FILE_CAPABILITY_MANIFEST,
            created_at=created_at,
        )
        return cls(
            source_ref=source_ref,
            display_name=display_name,
            kind=SourceKind.FILE,
            active_version=version,
            created_at=created_at,
        )


class SourceNotAvailable(Exception):
    """One generic result for unauthorized, unknown, or unavailable sources."""

    def __init__(self) -> None:
        super().__init__("source is not available")


class SourceControlUnavailable(RuntimeError):
    """The trusted Control persistence boundary could not complete safely."""
