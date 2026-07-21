"""Closed request and evidence-free delivery contracts for ContextRuntime."""

from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from engine.runtime.budget import PackageBudget, PackageBudgetRequest
from engine.runtime.delivery import (
    DirectDeliveryConstructionProvenance,
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)

__all__ = [
    "Acquire",
    "BudgetUsage",
    "ContextNeed",
    "ContextPackage",
    "Coverage",
    "CoverageReason",
    "CoverageStatus",
    "DirectDeliveryConstructionProvenance",
    "RequestNarrowing",
    "Resolved",
    "TrustedDeliveryContext",
    "_construct_direct_delivery_context",
]

MAX_NARROWING_REFS = 64
MAX_NARROWING_REF_LENGTH = 256
ORGANIZATION_PACKAGE_REF_PREFIX = "orgpkg"
DECISION_REF_PREFIX = "dec"
ORGANIZATION_PACKAGE_REF_PATTERN = r"^orgpkg_[0-9a-f]{32}$"
DECISION_REF_PATTERN = r"^dec_[0-9a-f]{32}$"


def _require_nonblank_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_closed_opaque_ref(
    field_name: str,
    value: object,
    *,
    prefix: str,
) -> str:
    expected_length = len(prefix) + 1 + 32
    if (
        type(value) is not str
        or len(value) != expected_length
        or not value.startswith(f"{prefix}_")
    ):
        raise ValueError(f"{field_name} must use the closed opaque format")
    entropy = value[len(prefix) + 1 :]
    if any(character not in "0123456789abcdef" for character in entropy):
        raise ValueError(f"{field_name} must use lowercase opaque entropy")
    return value


def _require_optional_ref_tuple(
    field_name: str,
    value: tuple[str, ...] | None,
) -> None:
    if value is None:
        return
    if type(value) is not tuple or not value:
        raise ValueError(f"narrowing {field_name} must be a non-empty tuple")
    if len(value) > MAX_NARROWING_REFS:
        raise ValueError(
            f"narrowing {field_name} exceeds the active profile ref limit"
        )
    for ref in value:
        _require_nonblank_string(f"narrowing {field_name} ref", ref)
        if len(ref) > MAX_NARROWING_REF_LENGTH:
            raise ValueError(
                f"narrowing {field_name} ref exceeds the active profile length"
            )
    if len(set(value)) != len(value):
        raise ValueError(f"narrowing {field_name} must not contain duplicate refs")


@dataclass(frozen=True, slots=True)
class ContextNeed:
    """The caller's untrusted information need."""

    query: str

    def __post_init__(self) -> None:
        _require_nonblank_string("query", self.query)


@dataclass(frozen=True, slots=True)
class RequestNarrowing:
    """Optional caller filters that can only narrow the established scope."""

    source_refs: tuple[str, ...] | None = None
    resource_refs: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _require_optional_ref_tuple("source_refs", self.source_refs)
        _require_optional_ref_tuple("resource_refs", self.resource_refs)
        if self.source_refs is None and self.resource_refs is None:
            raise ValueError("narrowing must contain at least one ref set")


@dataclass(frozen=True, slots=True)
class Acquire:
    """The first closed Runtime request variant."""

    need: ContextNeed
    package_budget: PackageBudgetRequest | None = None
    narrowing: RequestNarrowing | None = None

    def __post_init__(self) -> None:
        if type(self.need) is not ContextNeed:
            raise TypeError("need must be ContextNeed")
        if (
            self.package_budget is not None
            and type(self.package_budget) is not PackageBudgetRequest
        ):
            raise TypeError("package_budget must be PackageBudgetRequest or None")
        if self.narrowing is not None and type(self.narrowing) is not RequestNarrowing:
            raise TypeError("narrowing must be RequestNarrowing or None")


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Exact resources consumed while assembling one package."""

    tokens: int
    provider_calls: int
    cost_microunits: int
    elapsed_ms: int

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"{field.name} must be a non-negative exact integer"
                )


class CoverageStatus(StrEnum):
    """Closed coverage states active in the empty-package tracer."""

    EMPTY = "empty"


class CoverageReason(StrEnum):
    """Tenant-safe reason categories without resource-existence details."""

    NO_AUTHORIZED_EVIDENCE = "no_authorized_evidence"


@dataclass(frozen=True, slots=True)
class Coverage:
    """Typed package coverage that cannot enumerate denied resources."""

    status: CoverageStatus
    reason: CoverageReason

    def __post_init__(self) -> None:
        if type(self.status) is not CoverageStatus:
            raise TypeError("status must be CoverageStatus")
        if type(self.reason) is not CoverageReason:
            raise TypeError("reason must be CoverageReason")
        if self.status is not CoverageStatus.EMPTY:
            raise ValueError("empty package coverage status must be empty")
        if self.reason is not CoverageReason.NO_AUTHORIZED_EVIDENCE:
            raise ValueError(
                "empty package coverage reason must be no_authorized_evidence"
            )


def _require_utc(field_name: str, value: object) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"package {field_name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """Tenant-safe evidence-free Runtime deliverable for the first tracer."""

    organization_ref: str
    purpose: str
    ttl_seconds: int
    as_of: datetime
    expires_at: datetime
    decision_ref: str
    blocks: tuple[()]
    evidence: tuple[()]
    gaps: tuple[()]
    budget_usage: BudgetUsage
    coverage: Coverage

    def __post_init__(self) -> None:
        _require_closed_opaque_ref(
            "package organization_ref",
            self.organization_ref,
            prefix=ORGANIZATION_PACKAGE_REF_PREFIX,
        )
        _require_nonblank_string("package purpose", self.purpose)
        _require_closed_opaque_ref(
            "package decision_ref",
            self.decision_ref,
            prefix=DECISION_REF_PREFIX,
        )
        if type(self.ttl_seconds) is not int or self.ttl_seconds <= 0:
            raise ValueError("package TTL must be a positive exact integer")
        _require_utc("as_of", self.as_of)
        _require_utc("expires_at", self.expires_at)

        lifetime = self.expires_at - self.as_of
        if lifetime <= timedelta(0):
            raise ValueError("package expiry must be later than as_of")
        if lifetime.microseconds != 0 or lifetime != timedelta(
            seconds=self.ttl_seconds
        ):
            raise ValueError("package TTL must exactly match its expiry interval")

        for field_name in ("blocks", "evidence", "gaps"):
            value = getattr(self, field_name)
            if type(value) is not tuple or value:
                raise ValueError(f"empty package {field_name} must be an empty tuple")
        if type(self.budget_usage) is not BudgetUsage:
            raise TypeError("empty package usage must be BudgetUsage")
        if any(
            getattr(self.budget_usage, field.name) != 0
            for field in fields(BudgetUsage)
        ):
            raise ValueError("empty package usage must be zero")
        if type(self.coverage) is not Coverage:
            raise TypeError("empty package coverage must be Coverage")


@dataclass(frozen=True, slots=True)
class Resolved:
    """Successful closed resolution outcome."""

    package: ContextPackage
    effective_budget: PackageBudget
    kind: Literal["resolved"] = "resolved"

    def __post_init__(self) -> None:
        if type(self.package) is not ContextPackage:
            raise TypeError("resolved package must be ContextPackage")
        if type(self.effective_budget) is not PackageBudget:
            raise TypeError("resolved effective_budget must be PackageBudget")
        if self.kind != "resolved":
            raise ValueError("resolved outcome kind must be resolved")
