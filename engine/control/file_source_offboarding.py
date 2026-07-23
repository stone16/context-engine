"""Closed contracts for trusted offboarding of one File ContextSource."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NoReturn
from uuid import UUID

from engine.control.contracts import SourceRef, _require_utc

MAX_OFFBOARDING_COUNT = 2**63 - 1


class FileSourceCleanupState(StrEnum):
    """Cleanup remains asynchronous after synchronous security completion."""

    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class OffboardFileSource:
    """Trusted-Control command with no caller-authored security facts."""

    source_ref: SourceRef

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File source offboarding requires SourceRef")

    def __reduce__(self) -> NoReturn:
        raise TypeError("File source offboarding command is not serializable")


@dataclass(frozen=True, slots=True)
class FileSourceOffboarding:
    """Database-authored security completion and pending cleanup lineage."""

    organization_id: UUID = field(repr=False)
    source_ref: SourceRef
    source_version_ref: UUID = field(repr=False)
    policy_epoch: int
    cleanup_intent_ref: UUID = field(repr=False)
    cancelled_job_count: int
    retained_resource_count: int
    security_completed_at: datetime
    cleanup_state: FileSourceCleanupState

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("File source offboarding organization_id must be UUID")
        if type(self.source_ref) is not SourceRef:
            raise TypeError("File source offboarding result requires SourceRef")
        if type(self.source_version_ref) is not UUID:
            raise TypeError("File source offboarding version ref must be UUID")
        if type(self.cleanup_intent_ref) is not UUID:
            raise TypeError("File source offboarding cleanup ref must be UUID")
        if (
            type(self.policy_epoch) is not int
            or not 1 <= self.policy_epoch <= MAX_OFFBOARDING_COUNT
        ):
            raise ValueError("File source offboarding Policy Epoch is invalid")
        for name, value in (
            ("cancelled job count", self.cancelled_job_count),
            ("retained Resource count", self.retained_resource_count),
        ):
            if (
                type(value) is not int
                or not 0 <= value <= MAX_OFFBOARDING_COUNT
            ):
                raise ValueError(f"File source offboarding {name} is invalid")
        _require_utc(
            "File source offboarding security_completed_at",
            self.security_completed_at,
        )
        if self.cleanup_state is not FileSourceCleanupState.PENDING:
            raise ValueError("File source offboarding cleanup must start pending")

