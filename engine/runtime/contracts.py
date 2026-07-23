"""Closed request and exact-authorized delivery contracts for ContextRuntime."""

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from engine.runtime.budget import PackageBudget, PackageBudgetRequest
from engine.runtime.delivery import (
    DeliveryConstructionProvenance,
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.egress import ChannelEgressGrant, EgressGrant, ModelEgressGrant
from engine.runtime.evidence import Evidence, PackageBlock, validate_package_content
from engine.runtime.package_digest import context_package_digest

__all__ = [
    "Acquire",
    "BudgetUsage",
    "CitationNotAvailable",
    "CitationOpenRef",
    "ContextNeed",
    "ContextPackage",
    "context_package_digest_document",
    "context_package_public_document",
    "Continue",
    "ContinuationToken",
    "Coverage",
    "CoverageReason",
    "CoverageStatus",
    "DeliveryConstructionProvenance",
    "OpenCitation",
    "RequestNarrowing",
    "RequestNotAvailable",
    "ResolutionOutcome",
    "Resolved",
    "RuntimeRequest",
    "ScopeDecisionReceipt",
    "TrustedDeliveryContext",
    "_construct_direct_delivery_context",
]

MAX_NARROWING_REFS = 64
MAX_NARROWING_REF_LENGTH = 256
MAX_OPAQUE_CAPABILITY_LENGTH = 4096
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
        raise ValueError(f"narrowing {field_name} exceeds the active profile ref limit")
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


def _require_opaque_capability_value(field_name: str, value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_OPAQUE_CAPABILITY_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank opaque string")


@dataclass(frozen=True, slots=True)
class ContinuationToken:
    """Opaque caller value for the distinct continuation capability class."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_opaque_capability_value("ContinuationToken opaque value", self.value)


@dataclass(frozen=True, slots=True)
class CitationOpenRef:
    """Opaque non-authorizing locator for the citation-open capability class."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_opaque_capability_value("CitationOpenRef opaque value", self.value)


@dataclass(frozen=True, slots=True)
class Continue:
    """Closed continuation request whose production carrier is unavailable at M0."""

    continuation_token: ContinuationToken = field(repr=False)
    package_budget: PackageBudgetRequest | None = None

    def __post_init__(self) -> None:
        if type(self.continuation_token) is not ContinuationToken:
            raise TypeError("continuation_token must be ContinuationToken")
        if (
            self.package_budget is not None
            and type(self.package_budget) is not PackageBudgetRequest
        ):
            raise TypeError("package_budget must be PackageBudgetRequest or None")


@dataclass(frozen=True, slots=True)
class OpenCitation:
    """Closed citation-open request whose locator grants no authority."""

    citation_open_ref: CitationOpenRef = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.citation_open_ref) is not CitationOpenRef:
            raise TypeError("citation_open_ref must be CitationOpenRef")


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Exact resources consumed while assembling one package."""

    tokens: int
    provider_calls: int
    cost_microunits: int
    elapsed_ms: int

    def __post_init__(self) -> None:
        for usage_field in fields(self):
            value = getattr(self, usage_field.name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"{usage_field.name} must be a non-negative exact integer"
                )


class CoverageStatus(StrEnum):
    """Closed coverage states active in the Runtime tracer."""

    EMPTY = "empty"
    SUFFICIENT = "sufficient"


class CoverageReason(StrEnum):
    """Tenant-safe reason categories without resource-existence details."""

    NO_AUTHORIZED_EVIDENCE = "no_authorized_evidence"


@dataclass(frozen=True, slots=True)
class Coverage:
    """Typed package coverage that cannot enumerate denied resources."""

    status: CoverageStatus
    reason: CoverageReason | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not CoverageStatus:
            raise TypeError("status must be CoverageStatus")
        if self.reason is not None and type(self.reason) is not CoverageReason:
            raise TypeError("reason must be CoverageReason or None")
        if self.status is CoverageStatus.EMPTY and (
            self.reason is not CoverageReason.NO_AUTHORIZED_EVIDENCE
        ):
            raise ValueError(
                "empty package coverage reason must be no_authorized_evidence"
            )
        if self.status is CoverageStatus.SUFFICIENT and self.reason is not None:
            raise ValueError("sufficient package coverage must not contain a reason")


def _require_utc(field_name: str, value: object) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"package {field_name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """Tenant-safe Runtime deliverable with exact authorized Evidence closure."""

    organization_ref: str
    purpose: str
    ttl_seconds: int
    as_of: datetime
    expires_at: datetime
    decision_ref: str
    package_digest: str = field(init=False)
    blocks: tuple[PackageBlock, ...]
    evidence: tuple[Evidence, ...]
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

        if type(self.blocks) is not tuple or any(
            type(block) is not PackageBlock for block in self.blocks
        ):
            raise TypeError("package blocks must be a tuple of PackageBlock")
        if type(self.evidence) is not tuple or any(
            type(item) is not Evidence for item in self.evidence
        ):
            raise TypeError("package evidence must be a tuple of Evidence")
        if type(self.gaps) is not tuple or self.gaps:
            raise ValueError("package gaps must be an empty tuple in this tracer")
        validate_package_content(self.blocks, self.evidence)
        if type(self.budget_usage) is not BudgetUsage:
            raise TypeError("package usage must be BudgetUsage")
        if type(self.coverage) is not Coverage:
            raise TypeError("package coverage must be Coverage")
        has_content = bool(self.blocks or self.evidence)
        if has_content:
            if not self.blocks or not self.evidence:
                raise ValueError("content package requires blocks and Evidence")
            if self.coverage.status is not CoverageStatus.SUFFICIENT:
                raise ValueError("content package coverage must be sufficient")
            expected_tokens = sum(
                len(block.body.encode("utf-8")) for block in self.blocks
            )
            if self.budget_usage.tokens != expected_tokens:
                raise ValueError("content package token usage must equal UTF-8 bytes")
            if any(
                value != 0
                for value in (
                    self.budget_usage.provider_calls,
                    self.budget_usage.cost_microunits,
                    self.budget_usage.elapsed_ms,
                )
            ):
                raise ValueError(
                    "internal content package non-token usage must be zero"
                )
        else:
            if self.coverage.status is not CoverageStatus.EMPTY:
                raise ValueError("evidence-free package coverage must be empty")
            if any(
                getattr(self.budget_usage, usage_field.name) != 0
                for usage_field in fields(BudgetUsage)
            ):
                raise ValueError("empty package usage must be zero")
        object.__setattr__(
            self,
            "package_digest",
            context_package_digest(context_package_digest_document(self)),
        )


def _wire_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def context_package_digest_document(package: ContextPackage) -> dict[str, object]:
    """Return the exact active public Package document covered by its digest."""

    if type(package) is not ContextPackage:
        raise TypeError("package digest document requires ContextPackage")
    coverage_document: dict[str, object] = {
        "status": package.coverage.status.value,
    }
    if package.coverage.reason is not None:
        coverage_document["reason"] = package.coverage.reason.value
    return {
        "organizationRef": package.organization_ref,
        "purpose": package.purpose,
        "ttlSeconds": package.ttl_seconds,
        "asOf": _wire_datetime(package.as_of),
        "expiresAt": _wire_datetime(package.expires_at),
        "decisionRef": package.decision_ref,
        "blocks": [
            {
                "blockId": f"block_{block.evidence_ref.removeprefix('ev_')}",
                "text": block.body,
                "evidenceRefs": [block.evidence_ref],
            }
            for block in package.blocks
        ],
        "evidence": [
            {
                "evidenceRef": item.evidence_ref,
                "sourceRef": item.source_ref,
                "resourceRef": item.resource_ref,
                "revisionRef": item.revision_ref,
                "fragmentRef": item.fragment_ref,
                "projectedFields": list(item.projected_field_refs),
                "runRef": item.lineage.run_ref,
                "purpose": item.lineage.purpose,
                "authorizationAsOf": _wire_datetime(item.lineage.as_of),
                "decisionRef": item.lineage.decision_ref,
                "policySnapshotRef": item.lineage.policy_snapshot_ref,
                "policyEpoch": item.lineage.policy_epoch,
                "sourceDecisionRef": item.lineage.source_acl_decision_ref,
            }
            for item in package.evidence
        ],
        "gaps": [],
        "budgetUsage": {
            "tokens": package.budget_usage.tokens,
            "providerCalls": package.budget_usage.provider_calls,
            "costMicrounits": package.budget_usage.cost_microunits,
            "elapsedMs": package.budget_usage.elapsed_ms,
        },
        "coverage": coverage_document,
    }


def context_package_public_document(package: ContextPackage) -> dict[str, object]:
    """Return the sole public Package projection, including its digest."""

    document = context_package_digest_document(package)
    document["packageDigest"] = package.package_digest
    return document


@dataclass(frozen=True, slots=True)
class ScopeDecisionReceipt:
    """Restricted EffectiveScope observation without concrete target identifiers."""

    digest: str = field(repr=False)
    target_count: int
    is_empty: bool

    def __post_init__(self) -> None:
        if (
            type(self.digest) is not str
            or len(self.digest) != 64
            or any(character not in "0123456789abcdef" for character in self.digest)
        ):
            raise ValueError("scope decision digest must be lowercase SHA-256")
        if type(self.target_count) is not int or self.target_count < 0:
            raise ValueError("scope decision target_count must be non-negative")
        if type(self.is_empty) is not bool:
            raise TypeError("scope decision is_empty must be bool")
        if self.is_empty != (self.target_count == 0):
            raise ValueError("scope decision empty state must match target_count")


@dataclass(frozen=True, slots=True)
class Resolved:
    """Successful closed resolution outcome."""

    package: ContextPackage
    effective_budget: PackageBudget
    scope_decision: ScopeDecisionReceipt = field(repr=False)
    egress_grant: EgressGrant | None = field(default=None, repr=False)
    kind: Literal["resolved"] = "resolved"

    def __post_init__(self) -> None:
        if type(self.package) is not ContextPackage:
            raise TypeError("resolved package must be ContextPackage")
        if type(self.effective_budget) is not PackageBudget:
            raise TypeError("resolved effective_budget must be PackageBudget")
        if type(self.scope_decision) is not ScopeDecisionReceipt:
            raise TypeError("resolved scope_decision must be ScopeDecisionReceipt")
        if self.egress_grant is not None and type(self.egress_grant) not in {
            ModelEgressGrant,
            ChannelEgressGrant,
        }:
            raise TypeError("resolved egress_grant has the wrong nominal type")
        if self.kind != "resolved":
            raise ValueError("resolved outcome kind must be resolved")


@dataclass(frozen=True, slots=True)
class RequestNotAvailable:
    """Generic caller-safe outcome for a known unavailable Runtime request."""

    kind: Literal["request_not_available"] = "request_not_available"
    retryable: Literal[False] = False

    def __post_init__(self) -> None:
        if self.kind != "request_not_available" or self.retryable is not False:
            raise ValueError("request-not-available public shape must remain closed")


@dataclass(frozen=True, slots=True)
class CitationNotAvailable:
    """Generic caller-safe outcome for a known unavailable citation open."""

    kind: Literal["citation_not_available"] = "citation_not_available"

    def __post_init__(self) -> None:
        if self.kind != "citation_not_available":
            raise ValueError("citation-not-available public shape must remain closed")


type RuntimeRequest = Acquire | Continue | OpenCitation
type ResolutionOutcome = Resolved | RequestNotAvailable | CitationNotAvailable
