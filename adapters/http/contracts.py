"""Closed HTTP wire models for Acquire and exact-authorized Evidence."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.runtime.contracts import (
    DECISION_REF_PATTERN,
    MAX_NARROWING_REF_LENGTH,
    MAX_NARROWING_REFS,
    MAX_OPAQUE_CAPABILITY_LENGTH,
    PACKAGE_REF_PATTERN,
    complete_context_package_nullable_fields,
)
from engine.runtime.evidence import (
    MAX_PROJECTED_FIELD_REF_LENGTH,
    MAX_PROJECTED_FIELD_REFS,
    validate_projected_field_refs,
)
from engine.runtime.package_digest import verify_context_package_digest

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
PackageOutputRef = Annotated[
    str,
    Field(strict=True, pattern=PACKAGE_REF_PATTERN),
]
DecisionOutputRef = Annotated[
    str,
    Field(strict=True, pattern=DECISION_REF_PATTERN),
]
OpaqueOutputRef = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=r"^\S+$"),
]
ProjectedFieldOutputRef = Annotated[
    str,
    Field(
        strict=True,
        pattern=rf"^[a-z][a-z0-9_]{{0,{MAX_PROJECTED_FIELD_REF_LENGTH - 1}}}$",
    ),
]
EvidenceOutputRef = Annotated[
    str,
    Field(strict=True, pattern=r"^ev_[0-9a-f]{64}$"),
]
PackageDigestOutput = Annotated[
    str,
    Field(strict=True, pattern=r"^[0-9a-f]{64}$"),
]
OpaqueModelEgressGrant = Annotated[
    str,
    Field(strict=True, pattern=r"^egrm_[0-9a-f]{64}$", repr=False),
]
OpaqueChannelEgressGrant = Annotated[
    str,
    Field(strict=True, pattern=r"^egrc_[0-9a-f]{64}$", repr=False),
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
    providerCalls: NonnegativeExactInteger
    costMicrounits: NonnegativeExactInteger
    elapsedMs: NonnegativeExactInteger


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


class LiveSourceAclEvidenceWire(ClosedWireModel):
    kind: Literal["live"]
    sourceDecisionRef: OpaqueOutputRef
    checkedAt: datetime
    verificationProtocolRef: OpaqueOutputRef


class MirroredSourceAclEvidenceWire(ClosedWireModel):
    kind: Literal["mirrored"]
    projectionRef: OpaqueOutputRef
    aclAsOf: datetime
    freshnessProfileRef: OpaqueOutputRef


class WeakSourceAclEvidenceWire(ClosedWireModel):
    kind: Literal["weak"]
    declarationRef: OpaqueOutputRef
    checkedAt: datetime
    boundedMembershipEvidenceRef: OpaqueOutputRef
    snapshotAsOf: datetime
    expiresAt: datetime
    membershipCompleteness: Literal["complete"]
    sensitivityPolicyRef: OpaqueOutputRef
    historySemanticsRef: OpaqueOutputRef


type SourceAclEvidenceWire = Annotated[
    LiveSourceAclEvidenceWire
    | MirroredSourceAclEvidenceWire
    | WeakSourceAclEvidenceWire,
    Field(discriminator="kind"),
]


class EvidenceWire(ClosedWireModel):
    """Public request-scoped Evidence and its authorization lineage."""

    evidenceRef: EvidenceOutputRef
    sourceRef: OpaqueOutputRef
    resourceRef: OpaqueOutputRef
    revisionRef: OpaqueOutputRef
    fragmentRef: OpaqueOutputRef
    projectedFields: Annotated[
        tuple[ProjectedFieldOutputRef, ...],
        Field(min_length=1, max_length=MAX_PROJECTED_FIELD_REFS),
    ]
    runRef: OpaqueOutputRef
    purpose: NonblankPurpose
    authorizationAsOf: datetime
    decisionRef: DecisionOutputRef
    policySnapshotRef: OpaqueOutputRef
    policyEpoch: PositivePolicyEpoch
    sourceAclEvidence: SourceAclEvidenceWire
    citationOpenRef: OpaqueOutputRef | None

    @model_validator(mode="after")
    def require_unique_projected_fields(self) -> Self:
        try:
            validate_projected_field_refs(self.projectedFields)
        except ValueError as error:
            raise ValueError(
                "projectedFields must be unique, valid, and contain at most "
                f"{MAX_PROJECTED_FIELD_REFS} items"
            ) from error
        return self


class GapWire(ClosedWireModel):
    category: Literal[
        "source_unavailable",
        "stale_evidence",
        "budget_exhausted",
        "capability_unsupported",
    ]
    retryable: bool = Field(strict=True)


class CoverageWire(ClosedWireModel):
    """Typed tenant-safe coverage for the selected package content."""

    status: Literal["empty", "partial", "sufficient"]
    reason: (
        Literal[
            "no_authorized_evidence",
            "source_unavailable",
            "stale_evidence",
            "budget_exhausted",
            "capability_unsupported",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_status_specific_reason(self) -> Self:
        if self.status == "empty" and self.reason is None:
            raise ValueError("empty coverage requires its tenant-safe reason")
        if self.status == "sufficient" and self.reason is not None:
            raise ValueError("sufficient coverage cannot carry a gap reason")
        if self.status == "partial" and self.reason in {
            None,
            "no_authorized_evidence",
        }:
            raise ValueError("partial coverage requires a non-empty gap reason")
        return self


class ContinuationOfferWire(ClosedWireModel):
    continuationToken: OpaqueCapabilityInput
    remainingBudgetDigest: PackageDigestOutput


class ContextPackageWire(ClosedWireModel):
    """Public package with an exact block/Evidence closure."""

    packageId: PackageOutputRef
    packageDigest: PackageDigestOutput
    purpose: NonblankPurpose
    audienceDigest: PackageDigestOutput
    policyEpoch: PositivePolicyEpoch
    policySnapshotRef: OpaqueOutputRef
    decisionRef: DecisionOutputRef
    runRef: OpaqueOutputRef
    releaseManifestRef: OpaqueOutputRef
    retentionPolicyRef: OpaqueOutputRef
    asOf: datetime
    expiresAt: datetime
    ttlSeconds: PositiveExactInteger
    tokenizerRef: OpaqueOutputRef
    packageSchemaRef: OpaqueOutputRef
    blocks: tuple[BlockWire, ...]
    evidence: tuple[EvidenceWire, ...]
    gaps: tuple[GapWire, ...]
    coverage: CoverageWire
    budgetUsage: BudgetUsageWire
    continuation: ContinuationOfferWire | None

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
                or item.policyEpoch != self.policyEpoch
                or item.policySnapshotRef != self.policySnapshotRef
                or item.runRef != self.runRef
            ):
                raise ValueError(
                    "Evidence lineage must match its enclosing package decision"
                )
        digest_document = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"packageDigest"},
            exclude_none=True,
        )
        complete_context_package_nullable_fields(digest_document)
        if not verify_context_package_digest(
            digest_document,
            self.packageDigest,
        ):
            raise ValueError(
                "packageDigest must match the exact public Package document"
            )
        return self


class ModelEgressGrantWire(ClosedWireModel):
    """Opaque one-hop model grant; no trusted claim is exposed on the wire."""

    kind: Literal["model"]
    value: OpaqueModelEgressGrant = Field(repr=False)


class ChannelEgressGrantWire(ClosedWireModel):
    """Opaque one-hop channel grant; it carries no write authority."""

    kind: Literal["channel"]
    value: OpaqueChannelEgressGrant = Field(repr=False)


class ResolvedWire(ClosedWireModel):
    """Successful public resolution envelope."""

    kind: Literal["resolved"]
    package: ContextPackageWire
    egressGrant: ModelEgressGrantWire | ChannelEgressGrantWire | None = Field(
        discriminator="kind",
        repr=False,
    )


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


class ApplicationForbiddenWire(ClosedWireModel):
    code: Literal["application_forbidden"]


class RateLimitedWire(ClosedWireModel):
    code: Literal["rate_limited"]


def resolution_outcome_public_document(
    outcome: ResolutionOutcomeWire,
) -> dict[str, object]:
    """Serialize one closed outcome with every frozen required-nullable field."""

    document = outcome.model_dump(mode="json", by_alias=True, exclude_none=True)
    if type(outcome) is not ResolvedWire:
        return document
    document["egressGrant"] = document.get("egressGrant")
    package = document.get("package")
    if not isinstance(package, dict):
        raise TypeError("resolved wire package must be an object")
    complete_context_package_nullable_fields(package)
    return document
