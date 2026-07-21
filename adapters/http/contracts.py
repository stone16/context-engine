"""Closed HTTP wire models for the evidence-free Acquire tracer."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.runtime.contracts import (
    DECISION_REF_PATTERN,
    MAX_NARROWING_REF_LENGTH,
    MAX_NARROWING_REFS,
    ORGANIZATION_PACKAGE_REF_PATTERN,
)

PositiveExactInteger = Annotated[int, Field(strict=True, gt=0)]
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
    """Only the closed untrusted Acquire variant currently activated."""

    kind: Literal["acquire"]
    need: ContextNeedWire
    packageBudget: PackageBudgetWire | None = None
    requestNarrowing: RequestNarrowingWire | None = None


class BudgetUsageWire(ClosedWireModel):
    """Actual resources consumed by this Package."""

    tokens: Literal[0]
    providerCalls: Literal[0]
    costMicrounits: Literal[0]
    elapsedMs: Literal[0]


class CoverageWire(ClosedWireModel):
    """Typed tenant-safe empty coverage without existence detail."""

    status: Literal["empty"]
    reason: Literal["no_authorized_evidence"]


class ContextPackageWire(ClosedWireModel):
    """Staged public evidence-free ContextPackage."""

    organizationRef: OrganizationPackageOutputRef
    purpose: NonblankPurpose
    ttlSeconds: PositiveExactInteger
    asOf: datetime
    expiresAt: datetime
    decisionRef: DecisionOutputRef
    blocks: tuple[()]
    evidence: tuple[()]
    gaps: tuple[()]
    budgetUsage: BudgetUsageWire
    coverage: CoverageWire


class ResolvedWire(ClosedWireModel):
    """Successful public resolution envelope."""

    kind: Literal["resolved"]
    package: ContextPackageWire


class AuthenticationFailureWire(ClosedWireModel):
    """Closed public response for every transport authentication rejection."""

    code: Literal["authentication_failed"]


class InvalidRequestWire(ClosedWireModel):
    """Closed public response for request syntax or schema rejection."""

    code: Literal["invalid_request"]
