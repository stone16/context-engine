"""Closed contracts for trusted deletion of one published File Resource."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from engine.control.contracts import (
    SourceRef,
    _require_sha256,
    _require_token,
    _require_utc,
)

MAX_FILE_DELETE_EVENT_SEQUENCE = 2**63 - 1
_FILE_RESOURCE_REF = re.compile(r"resource:file:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ExecuteFileDeleteObservation:
    """Trusted locator for one exact accepted File delete observation."""

    source_ref: SourceRef
    source_version_ref: UUID = field(repr=False)
    page_ref: str = field(repr=False)
    change_ordinal: int

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File delete execution source_ref must be SourceRef")
        if type(self.source_version_ref) is not UUID:
            raise TypeError("File delete execution SourceVersion must be UUID")
        _require_sha256("File delete execution page_ref", self.page_ref)
        if type(self.change_ordinal) is not int or not 1 <= self.change_ordinal <= 100:
            raise ValueError("File delete execution ordinal is invalid")

    def __reduce__(self) -> NoReturn:
        raise TypeError("File delete execution command is not serializable")


@dataclass(frozen=True, slots=True)
class TombstoneFileResource:
    """Trusted-Control command for one monotonic File deletion observation."""

    source_ref: SourceRef
    resource_ref: str = field(repr=False)
    event_ref: str = field(repr=False)
    event_sequence: int

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File tombstone source_ref must be SourceRef")
        if (
            type(self.resource_ref) is not str
            or _FILE_RESOURCE_REF.fullmatch(self.resource_ref) is None
        ):
            raise ValueError("File tombstone ResourceRef is invalid")
        _require_token("File tombstone event_ref", self.event_ref)
        if (
            type(self.event_sequence) is not int
            or not 1 <= self.event_sequence <= MAX_FILE_DELETE_EVENT_SEQUENCE
        ):
            raise ValueError(
                "File tombstone event sequence must fit a positive signed "
                "64-bit integer"
            )

    def __reduce__(self) -> NoReturn:
        raise TypeError("File tombstone command is not serializable")


@dataclass(frozen=True, slots=True)
class FileResourceTombstone:
    """Database-authored durable deletion and cleanup-intent lineage."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef
    resource_ref: str = field(repr=False)
    revision_ref: UUID = field(repr=False)
    event_ref: str = field(repr=False)
    event_sequence: int
    policy_epoch: int
    cleanup_intent_ref: UUID = field(repr=False)
    tombstoned_at: datetime

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("File tombstone organization_id must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File tombstone result source_ref must be SourceRef")
        if (
            type(self.resource_ref) is not str
            or _FILE_RESOURCE_REF.fullmatch(self.resource_ref) is None
        ):
            raise ValueError("File tombstone result ResourceRef is invalid")
        if type(self.revision_ref) is not UUID:
            raise TypeError("File tombstone result revision_ref must be UUID")
        _require_token("File tombstone result event_ref", self.event_ref)
        for name, value in (
            ("event sequence", self.event_sequence),
            ("Policy Epoch", self.policy_epoch),
        ):
            if type(value) is not int or not 1 <= value <= 2**63 - 1:
                raise ValueError(
                    f"File tombstone result {name} must fit a positive signed "
                    "64-bit integer"
                )
        if type(self.cleanup_intent_ref) is not UUID:
            raise TypeError("File tombstone cleanup_intent_ref must be UUID")
        _require_utc("File tombstone tombstoned_at", self.tombstoned_at)


@dataclass(frozen=True, slots=True)
class ExecutedFileDeleteObservation:
    """Database-authored binding from one observation to one tombstone."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef
    source_version_ref: UUID = field(repr=False)
    page_ref: str = field(repr=False)
    change_ordinal: int
    tombstone: FileResourceTombstone = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("executed File delete organization must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("executed File delete source_ref must be SourceRef")
        if type(self.source_version_ref) is not UUID:
            raise TypeError("executed File delete SourceVersion must be UUID")
        _require_sha256("executed File delete page_ref", self.page_ref)
        if type(self.change_ordinal) is not int or not 1 <= self.change_ordinal <= 100:
            raise ValueError("executed File delete ordinal is invalid")
        if type(self.tombstone) is not FileResourceTombstone:
            raise TypeError("executed File delete requires a tombstone")
        if (
            self.tombstone.organization_id != self.organization_id
            or self.tombstone.source_ref != self.source_ref
        ):
            raise ValueError("executed File delete tombstone ownership is invalid")
