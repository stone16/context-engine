"""Closed HTTP wire models for Acquire and exact-authorized Evidence."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.runtime.contracts import (
    DECISION_REF_PATTERN,
    MAX_NARROWING_REF_LENGTH,
    MAX_NARROWING_REFS,
    MAX_OPAQUE_CAPABILITY_LENGTH,
    ORGANIZATION_PACKAGE_REF_PATTERN,
)

PositiveExactInteger = Annotated[int, Field(strict=True, gt=0)]
NonnegativeExactInteger = Annotated[int, Field(strict=True, ge=0)]
PositivePolicyEpoch = Annotated[
    int,
    Field(strict=True, ge=1, le=(1 << 63) - 1),
]
BoundedNarrowingRef = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=MAX_NARROWING_REF_LENGTH,
        pattern=r".*\S.*",
    ),
]
NonblankPurpose = Annotated[
    str,
    Field(strict=True, min_length=1, pattern=r".*\S.*"),
]
OrganizationPackageOutputRef = Annotated[
    str,
    Field(strict=True, pattern=ORGANIZATION_PACKAGE_REF_PATTERN),
]
DecisionOutputRef = Annotated[
    str,
    Field(strict=True, pattern=DECISION_REF_PATTERN),
]
OpaqueOutputRef = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=r"^\S+$"),
]
EvidenceOutputRef = Annotated[
    str,
    Field(strict=True, pattern=r"^ev_[0-9a-f]{64}$"),
]
BlockOutputRef = Annotated[
    str,
    Field(strict=True, pattern=r"^block_[0-9a-f]{64}$"),
]
NonblankBlockText = Annotated[
    str,
    Field(strict=True, min_length=1, pattern=r".*\S.*"),
]


class ClosedWireModel(BaseModel):
    """Common recursively closed public JSON contract."""

    model_config = ConfigDict(extra="forbid")


class ContextNeedWire(ClosedWireModel):
    """Untrusted context need; it carries no identity or authority."""

    query: str = Field(strict=True, min_length=1, pattern=r".*\S.*")


class PackageBudgetWire(ClosedWireModel):
    """Caller ceiling; every supplied dimension is a strict positive integer."""

    maxTokens: PositiveExactInteger | None = None
    maxProviderCalls: PositiveExactInteger | None = None
    maxCostMicrounits: PositiveExactInteger | None = None
    maxElapsedMs: PositiveExactInteger | None = None

    @model_validator(mode="after")
    def require_one_dimension(self) -> Self:
        if all(
            value is None
            for value in (
                self.maxTokens,
                self.maxProviderCalls,
                self.maxCostMicrounits,
                self.maxElapsedMs,
            )
        ):
            raise ValueError("packageBudget must contain at least one dimension")
        return self


class RequestNarrowingWire(ClosedWireModel):
    """Untrusted source/resource filters that can only narrow future scope."""

    sourceRefs: Annotated[
        tuple[BoundedNarrowingRef, ...] | None,
        Field(min_length=1, max_length=MAX_NARROWING_REFS),
    ] = None
    resourceRefs: Annotated[
        tuple[BoundedNarrowingRef, ...] | None,
        Field(min_length=1, max_length=MAX_NARROWING_REFS),
    ] = None

    @model_validator(mode="after")
    def require_nonempty_unique_sets(self) -> Self:
        if self.sourceRefs is None and self.resourceRefs is None:
            raise ValueError("requestNarrowing must contain at least one ref set")
        for refs in (self.sourceRefs, self.resourceRefs):
            if refs is not None and len(set(refs)) != len(refs):
                raise ValueError("requestNarrowing refs must be unique")
        return self


class AcquireWire(ClosedWireModel):
    """Closed untrusted Acquire variant."""

    kind: Literal["acquire"]
    need: ContextNeedWire
    packageBudget: PackageBudgetWire | None = None
    requestNarrowing: RequestNarrowingWire | None = None


OpaqueCapabilityInput = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPAQUE_CAPABILITY_LENGTH,
        pattern=r"^\S+$",
        repr=False,
    ),
]


class ContinueWire(ClosedWireModel):
    """Closed known continuation variant; its carrier is unavailable at M0."""

    kind: Literal["continue"]
    continuationToken: OpaqueCapabilityInput
    packageBudget: PackageBudgetWire | None = None


class OpenCitationWire(ClosedWireModel):
    """Closed known citation variant; its locator carries no authority."""

    kind: Literal["open_citation"]
    citationOpenRef: OpaqueCapabilityInput


type ResolveWire = Annotated[
    AcquireWire | ContinueWire | OpenCitationWire,
    Field(discriminator="kind"),
]


class BudgetUsageWire(ClosedWireModel):
    """Actual resources consumed by this Package."""

    tokens: NonnegativeExactInteger
    providerCalls: Literal[0]
    costMicrounits: Literal[0]
    elapsedMs: Literal[0]


class BlockWire(ClosedWireModel):
    """One authorized text block bound to exactly one public Evidence ref."""

    blockId: BlockOutputRef
    text: NonblankBlockText
    evidenceRefs: Annotated[
        tuple[EvidenceOutputRef, ...],
        Field(min_length=1, max_length=1),
    ]

    @model_validator(mode="after")
    def bind_id_to_its_evidence(self) -> Self:
        evidence_ref = self.evidenceRefs[0]
        expected_block_id = f"block_{evidence_ref.removeprefix('ev_')}"
        if self.blockId != expected_block_id:
            raise ValueError("blockId must be derived from its exact Evidence ref")
        return self


class EvidenceWire(ClosedWireModel):
    """Public request-scoped Evidence and its authorization lineage."""

    evidenceRef: EvidenceOutputRef
    sourceRef: OpaqueOutputRef
    resourceRef: OpaqueOutputRef
    revisionRef: OpaqueOutputRef
    fragmentRef: OpaqueOutputRef
    runRef: OpaqueOutputRef
    purpose: NonblankPurpose
    authorizationAsOf: datetime
    decisionRef: DecisionOutputRef
    policySnapshotRef: OpaqueOutputRef
    policyEpoch: PositivePolicyEpoch
    sourceDecisionRef: OpaqueOutputRef


class CoverageWire(ClosedWireModel):
    """Typed tenant-safe coverage for the selected package content."""

    status: Literal["empty", "sufficient"]
    reason: Literal["no_authorized_evidence"] | None = None

    @model_validator(mode="after")
    def require_status_specific_reason(self) -> Self:
        if self.status == "empty" and self.reason != "no_authorized_evidence":
            raise ValueError("empty coverage requires its tenant-safe reason")
        if self.status == "sufficient" and self.reason is not None:
            raise ValueError("sufficient coverage cannot carry an empty reason")
        return self


class ContextPackageWire(ClosedWireModel):
    """Public package with an exact block/Evidence closure."""

    organizationRef: OrganizationPackageOutputRef
    purpose: NonblankPurpose
    ttlSeconds: PositiveExactInteger
    asOf: datetime
    expiresAt: datetime
    decisionRef: DecisionOutputRef
    blocks: tuple[BlockWire, ...]
    evidence: tuple[EvidenceWire, ...]
    gaps: tuple[()]
    budgetUsage: BudgetUsageWire
    coverage: CoverageWire

    @model_validator(mode="after")
    def require_exact_authorized_content_closure(self) -> Self:
        block_refs = tuple(block.evidenceRefs[0] for block in self.blocks)
        evidence_refs = tuple(item.evidenceRef for item in self.evidence)
        if (
            len(block_refs) != len(set(block_refs))
            or len(evidence_refs) != len(set(evidence_refs))
            or set(block_refs) != set(evidence_refs)
        ):
            raise ValueError("package must have an exact block/Evidence closure")

        has_content = bool(self.blocks or self.evidence)
        if has_content != (self.coverage.status == "sufficient"):
            raise ValueError("package content must match its coverage status")
        if not has_content and self.budgetUsage.tokens != 0:
            raise ValueError("empty package token usage must be zero")
        if has_content and self.budgetUsage.tokens != sum(
            len(block.text.encode("utf-8")) for block in self.blocks
        ):
            raise ValueError(
                "content package token usage must equal authorized UTF-8 bytes"
            )

        for item in self.evidence:
            if (
                item.purpose != self.purpose
                or item.authorizationAsOf != self.asOf
                or item.decisionRef != self.decisionRef
            ):
                raise ValueError(
                    "Evidence lineage must match its enclosing package decision"
                )
        return self


class ResolvedWire(ClosedWireModel):
    """Successful public resolution envelope."""

    kind: Literal["resolved"]
    package: ContextPackageWire


class RequestNotAvailableWire(ClosedWireModel):
    """Caller-safe outcome for an unavailable known request."""

    kind: Literal["request_not_available"]
    retryable: Literal[False]


class CitationNotAvailableWire(ClosedWireModel):
    """Caller-safe outcome for an unavailable citation open."""

    kind: Literal["citation_not_available"]


type ResolutionOutcomeWire = Annotated[
    ResolvedWire | RequestNotAvailableWire | CitationNotAvailableWire,
    Field(discriminator="kind"),
]


class AuthenticationFailureWire(ClosedWireModel):
    """Closed public response for every transport authentication rejection."""

    code: Literal["authentication_failed"]


class InvalidRequestWire(ClosedWireModel):
    """Closed public response for request syntax or schema rejection."""

    code: Literal["invalid_request"]


class ServiceUnavailableWire(ClosedWireModel):
    """Closed response when a required trusted authority is unavailable."""

    code: Literal["service_unavailable"]
