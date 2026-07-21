"""Fail-closed sealed Runtime authorization and Package construction path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as new_hmac
from secrets import token_bytes
from threading import Lock
from typing import Literal

from engine.runtime.actor import _require_active_user_actor
from engine.runtime.budget import PackageBudget, effective_package_budget
from engine.runtime.content_io import (
    CandidateIndex,
    RuntimeContentIo,
    prohibited_empty_path_content_io,
)
from engine.runtime.contracts import (
    DECISION_REF_PREFIX,
    ORGANIZATION_PACKAGE_REF_PREFIX,
    Acquire,
    BudgetUsage,
    ContextPackage,
    Coverage,
    CoverageReason,
    CoverageStatus,
    Resolved,
    ScopeDecisionReceipt,
    _require_closed_opaque_ref,
)
from engine.runtime.delivery import (
    DirectDeliveryConstructionProvenance,
    TrustedDeliveryContext,
)
from engine.runtime.evidence import (
    CandidateRef,
    EvidenceLineage,
    PackageContent,
    _candidate_sort_key,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
    construct_package_content,
)
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    InvocationConstructionProvenance,
)
from engine.runtime.materialized import (
    MaterializedFragmentLocator,
    MaterializedProjectionSession,
    _locate_materialized_fragment,
    _project_materialized_fragment_body,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    OrganizationVerificationProvenance,
)
from engine.runtime.scope import (
    OMITTED_REQUEST_NARROWING,
    EffectiveScope,
    ScopeTarget,
    compute_effective_scope,
)
from engine.runtime.scope_authority import (
    _require_active_trusted_scope_snapshot,
    _trusted_operands_from_snapshot,
)


class RuntimeConfigurationError(RuntimeError):
    """Raised when the sealed Runtime composition is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class PolicyReceipt:
    """Trusted-input policy result before candidate discovery."""

    request_id: str
    purpose: str
    effective_scope: EffectiveScope = field(repr=False)


@dataclass(frozen=True, slots=True)
class DecisionProvenanceReceipt:
    """Server-owned request and policy lineage for one decision."""

    decision_ref: str
    package_organization_ref: str
    request_id: str
    purpose: str
    as_of: datetime
    run_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    source_acl_decision_ref: str


@dataclass(frozen=True, slots=True)
class DecisionAuditReceipt:
    """Restricted safe audit result with no denied identifiers or counts."""

    decision_ref: str
    reason: CoverageReason | None
    authorized_evidence_count: int = 0
    denied_detail_count: Literal[0] = 0

    def __post_init__(self) -> None:
        if (
            type(self.authorized_evidence_count) is not int
            or self.authorized_evidence_count < 0
        ):
            raise ValueError("authorized Evidence count must be non-negative")
        if self.denied_detail_count != 0:
            raise ValueError("denied decision detail count must remain zero")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Result proving each mandatory Kernel behavior completed."""

    effective_budget: PackageBudget
    policy_receipt: PolicyReceipt
    provenance_receipt: DecisionProvenanceReceipt
    audit_receipt: DecisionAuditReceipt
    content: PackageContent


def _validate_trusted_operands(
    invocation: AuthenticatedInvocation,
    delivery_context: TrustedDeliveryContext,
    request: Acquire,
) -> None:
    if (
        type(invocation) is not AuthenticatedInvocation
        or invocation.construction_provenance
        is not InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS
    ):
        raise TypeError("Runtime requires a trusted AuthenticatedInvocation")
    verification = invocation.organization_verification
    if (
        type(verification) is not ExistingOrganizationVerification
        or verification.construction_provenance
        is not OrganizationVerificationProvenance.AUTHENTICATED_HTTP_AUTHORITY
        or str(verification.organization_id) != invocation.organization_ref
        or verification.request_id != invocation.request_id
        or verification.authentication_binding_ref
        != invocation.authentication_binding_ref
        or verification.verified_at != invocation.received_at
    ):
        raise ValueError(
            "Runtime requires a matching existing-Organization verification"
        )
    actor = invocation.user_actor
    _require_active_user_actor(actor)
    if (
        str(actor.organization_id) != invocation.organization_ref
        or str(actor.user_id) != invocation.user_ref
        or str(actor.membership_id) != invocation.membership_ref
        or actor.membership_version != invocation.membership_version
        or actor.principal_ref != invocation.principal_ref
        or actor.request_id != invocation.request_id
        or actor.authentication_binding_ref
        != invocation.authentication_binding_ref
        or actor.checked_at != invocation.received_at
    ):
        raise ValueError("Runtime requires a matching current UserActor")
    if (
        type(delivery_context) is not TrustedDeliveryContext
        or delivery_context.construction_provenance
        is not DirectDeliveryConstructionProvenance.AUTHENTICATED_DIRECT_INGRESS
        or delivery_context.authenticated_application_ref
        != invocation.authenticated_application_ref
        or delivery_context.delivery_binding_ref
        != invocation.authentication_binding_ref
        or delivery_context.established_at != invocation.received_at
    ):
        raise ValueError("Runtime requires a matching trusted delivery context")
    if type(request) is not Acquire:
        raise TypeError("Runtime request must be Acquire")
    scope_snapshot = invocation.trusted_scope_snapshot
    _require_active_trusted_scope_snapshot(scope_snapshot)
    if (
        scope_snapshot.organization_id != actor.organization_id
        or scope_snapshot.user_id != actor.user_id
        or scope_snapshot.membership_id != actor.membership_id
        or scope_snapshot.membership_version != actor.membership_version
        or scope_snapshot.principal_ref != invocation.principal_ref
        or scope_snapshot.agent_version_ref != invocation.agent_version_ref
        or scope_snapshot.purpose != delivery_context.purpose
        or scope_snapshot.request_id != invocation.request_id
        or scope_snapshot.authentication_binding_ref
        != invocation.authentication_binding_ref
        or scope_snapshot.checked_at != invocation.received_at
    ):
        raise ValueError("Runtime requires a matching trusted scope snapshot")


@dataclass(frozen=True, slots=True)
class PolicyGate:
    """Concrete, non-substitutable trusted-input policy gate."""

    def validate_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> PolicyReceipt:
        _validate_trusted_operands(invocation, delivery_context, request)
        effective_scope = compute_effective_scope(
            _trusted_operands_from_snapshot(invocation.trusted_scope_snapshot),
            request.narrowing
            if request.narrowing is not None
            else OMITTED_REQUEST_NARROWING,
        )
        return PolicyReceipt(
            request_id=invocation.request_id,
            purpose=delivery_context.purpose,
            effective_scope=effective_scope,
        )


@dataclass(frozen=True, slots=True)
class PackageBudgetGate:
    """Concrete finite-budget intersection gate."""

    def intersect(
        self,
        server_budget: PackageBudget,
        request: Acquire,
    ) -> PackageBudget:
        return effective_package_budget(server_budget, request.package_budget)


@dataclass(frozen=True, slots=True)
class ProvenanceGate:
    """Concrete server-owned reference and decision-provenance gate."""

    def issue(
        self,
        invocation: AuthenticatedInvocation,
        policy_receipt: PolicyReceipt,
        *,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
    ) -> DecisionProvenanceReceipt:
        _require_utc("Runtime clock", as_of)
        references = reference_issuer.issue()
        organization_ref = references.package_organization_ref
        decision_ref = references.decision_ref
        organization_ref = _require_closed_opaque_ref(
            "organization reference",
            organization_ref,
            prefix=ORGANIZATION_PACKAGE_REF_PREFIX,
        )
        decision_ref = _require_closed_opaque_ref(
            "decision reference",
            decision_ref,
            prefix=DECISION_REF_PREFIX,
        )
        trusted_organization_hex = (
            invocation.organization_verification.organization_id.hex
        )
        if (
            trusted_organization_hex in organization_ref
            or trusted_organization_hex in decision_ref
        ):
            raise ValueError("server references must not embed trusted Organization")
        return DecisionProvenanceReceipt(
            decision_ref=decision_ref,
            package_organization_ref=organization_ref,
            request_id=policy_receipt.request_id,
            purpose=policy_receipt.purpose,
            as_of=as_of,
            run_ref=references.run_ref,
            policy_snapshot_ref=references.policy_snapshot_ref,
            policy_epoch=references.policy_epoch,
            source_acl_decision_ref=references.source_acl_decision_ref,
        )


@dataclass(frozen=True, slots=True)
class DecisionAuditGate:
    """Concrete safe in-memory audit gate; persistence belongs to Issue #19."""

    def record(
        self,
        provenance_receipt: DecisionProvenanceReceipt,
        *,
        authorized_evidence_count: int,
    ) -> DecisionAuditReceipt:
        if type(authorized_evidence_count) is not int or authorized_evidence_count < 0:
            raise ValueError("authorized Evidence count must be non-negative")
        return DecisionAuditReceipt(
            decision_ref=provenance_receipt.decision_ref,
            reason=(
                CoverageReason.NO_AUTHORIZED_EVIDENCE
                if authorized_evidence_count == 0
                else None
            ),
            authorized_evidence_count=authorized_evidence_count,
        )


type KernelDependency = (
    PolicyGate | DecisionAuditGate | PackageBudgetGate | ProvenanceGate
)


@dataclass(frozen=True, slots=True)
class KernelDependencies:
    """Exact mandatory concrete gates; callers cannot replace their behavior."""

    policy: PolicyGate
    audit: DecisionAuditGate
    budget: PackageBudgetGate
    provenance: ProvenanceGate


def _validate_kernel_dependencies(dependencies: object) -> KernelDependencies:
    if type(dependencies) is not KernelDependencies:
        raise RuntimeConfigurationError(
            "runtime dependencies must be KernelDependencies"
        )
    for field_name, expected_type in (
        ("policy", PolicyGate),
        ("audit", DecisionAuditGate),
        ("budget", PackageBudgetGate),
        ("provenance", ProvenanceGate),
    ):
        if type(getattr(dependencies, field_name)) is not expected_type:
            raise RuntimeConfigurationError(
                f"mandatory kernel dependency is missing or invalid: {field_name}"
            )
    return dependencies


class AuthorizationKernel:
    """Non-pluggable exact authorization and projection boundary."""

    def __init__(self, dependencies: KernelDependencies) -> None:
        validated = _validate_kernel_dependencies(dependencies)
        self._policy = validated.policy
        self._audit = validated.audit
        self._budget = validated.budget
        self._provenance = validated.provenance

    def authorize_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
        *,
        server_budget: PackageBudget,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
        candidate_index: CandidateIndex | None,
        projection_session: MaterializedProjectionSession | None,
    ) -> AuthorizationDecision:
        """Run policy, budget, provenance, exact projection, assembly, and audit."""

        policy_receipt = self._policy.validate_acquire(
            invocation,
            delivery_context,
            request,
        )
        effective_budget = self._budget.intersect(server_budget, request)
        provenance_receipt = self._provenance.issue(
            invocation,
            policy_receipt,
            as_of=as_of,
            reference_issuer=reference_issuer,
        )
        candidates: tuple[CandidateRef, ...] = ()
        if policy_receipt.effective_scope.targets and candidate_index is not None:
            if projection_session is None:
                raise RuntimeConfigurationError(
                    "candidate discovery requires same-transaction projection session"
                )
            discovered = candidate_index.discover(request)
            if type(discovered) is not tuple or any(
                type(candidate) is not CandidateRef for candidate in discovered
            ):
                raise TypeError(
                    "CandidateIndex must return a tuple of exact CandidateRef values"
                )
            candidates = discovered
        content = self._authorize_and_assemble(
            invocation,
            policy_receipt,
            provenance_receipt,
            effective_budget,
            candidates,
            projection_session,
        )
        audit_receipt = self._audit.record(
            provenance_receipt,
            authorized_evidence_count=len(content.evidence),
        )
        if audit_receipt.decision_ref != provenance_receipt.decision_ref:
            raise RuntimeConfigurationError("audit and provenance decision mismatch")
        return AuthorizationDecision(
            effective_budget=effective_budget,
            policy_receipt=policy_receipt,
            provenance_receipt=provenance_receipt,
            audit_receipt=audit_receipt,
            content=content,
        )

    def _authorize_and_assemble(
        self,
        invocation: AuthenticatedInvocation,
        policy_receipt: PolicyReceipt,
        provenance_receipt: DecisionProvenanceReceipt,
        effective_budget: PackageBudget,
        candidates: tuple[CandidateRef, ...],
        projection_session: MaterializedProjectionSession | None,
    ) -> PackageContent:
        if not candidates:
            return construct_package_content(())
        if projection_session is None:
            raise RuntimeConfigurationError(
                "candidate discovery requires same-transaction projection session"
            )

        kernel_scope = _open_authorization_kernel_scope()
        try:
            projections = []
            consumed_tokens = 0
            ordered_candidates = sorted(
                set(candidates),
                key=_candidate_sort_key,
            )
            for candidate in ordered_candidates:
                locator = _locate_materialized_fragment(
                    projection_session,
                    candidate,
                )
                if locator is None or not _locator_matches_candidate(
                    locator,
                    candidate,
                ):
                    continue
                exact_target = ScopeTarget(
                    locator.organization_id,
                    locator.source_ref,
                    locator.resource_ref,
                )
                if exact_target not in policy_receipt.effective_scope.targets:
                    continue
                body = _project_materialized_fragment_body(
                    projection_session,
                    locator,
                )
                if body is None:
                    continue
                projection = _construct_authorized_projection(
                    kernel_scope=kernel_scope,
                    candidate_ref=candidate,
                    body=body,
                    lineage=EvidenceLineage(
                        run_ref=provenance_receipt.run_ref,
                        principal_ref=invocation.principal_ref,
                        purpose=provenance_receipt.purpose,
                        as_of=provenance_receipt.as_of,
                        decision_ref=provenance_receipt.decision_ref,
                        policy_snapshot_ref=(
                            provenance_receipt.policy_snapshot_ref
                        ),
                        policy_epoch=provenance_receipt.policy_epoch,
                        source_acl_decision_ref=(
                            provenance_receipt.source_acl_decision_ref
                        ),
                    ),
                )
                body_tokens = len(projection.projected_body.encode("utf-8"))
                if consumed_tokens + body_tokens > effective_budget.max_tokens:
                    continue
                projections.append(projection)
                consumed_tokens += body_tokens
            return construct_package_content(tuple(projections))
        finally:
            _close_authorization_kernel_scope(kernel_scope)


def _locator_matches_candidate(
    locator: MaterializedFragmentLocator,
    candidate: CandidateRef,
) -> bool:
    return type(candidate) is CandidateRef and (
        locator.organization_id == candidate.organization_id
        and locator.source_ref == candidate.source_ref
        and locator.resource_ref == candidate.resource_ref
        and locator.revision_ref == candidate.revision_ref
        and locator.fragment_ref == candidate.fragment_ref
    )


DEFAULT_PACKAGE_TTL_SECONDS = 300
DEFAULT_SERVER_PACKAGE_BUDGET = PackageBudget(
    max_tokens=4_096,
    max_provider_calls=8,
    max_cost_microunits=100_000,
    max_elapsed_ms=5_000,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must return an aware UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class _IssuedReferences:
    package_organization_ref: str
    decision_ref: str
    run_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    source_acl_decision_ref: str


class _OpaqueReferenceIssuer:
    """Runtime-owned, lock-serialized issuer with no caller-controlled factory."""

    def __init__(self) -> None:
        self._secret = token_bytes(32)
        self._sequence = 0
        self._lock = Lock()

    def issue(self) -> _IssuedReferences:
        with self._lock:
            self._sequence += 1
            material = self._sequence.to_bytes(16, byteorder="big")
            entropies = {
                label: new_hmac(
                    self._secret,
                    label.encode("ascii") + b":" + material,
                    sha256,
                ).hexdigest()[:32]
                for label in (
                    "organization",
                    "decision",
                    "run",
                    "policy",
                    "source-acl",
                )
            }
        return _IssuedReferences(
            package_organization_ref=(
                f"{ORGANIZATION_PACKAGE_REF_PREFIX}_{entropies['organization']}"
            ),
            decision_ref=f"{DECISION_REF_PREFIX}_{entropies['decision']}",
            run_ref=f"run_{entropies['run']}",
            policy_snapshot_ref=f"policy_{entropies['policy']}",
            policy_epoch=1,
            source_acl_decision_ref=f"sourceacl_{entropies['source-acl']}",
        )


class Runtime:
    """Single sealed Runtime entry point for the Acquire tracer."""

    def __init__(
        self,
        dependencies: KernelDependencies,
        *,
        package_ttl_seconds: int = DEFAULT_PACKAGE_TTL_SECONDS,
        server_budget: PackageBudget = DEFAULT_SERVER_PACKAGE_BUDGET,
        content_io: RuntimeContentIo | None = None,
        candidate_index: CandidateIndex | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        validated = _validate_kernel_dependencies(dependencies)
        if type(package_ttl_seconds) is not int or package_ttl_seconds <= 0:
            raise ValueError("package_ttl_seconds must be a positive exact integer")
        if type(server_budget) is not PackageBudget:
            raise TypeError("server_budget must be PackageBudget")
        selected_content_io = content_io or prohibited_empty_path_content_io()
        if type(selected_content_io) is not RuntimeContentIo:
            raise RuntimeConfigurationError("content_io must be RuntimeContentIo")
        if candidate_index is not None and not callable(
            getattr(candidate_index, "discover", None)
        ):
            raise RuntimeConfigurationError("candidate_index is incomplete")
        self._dependencies = validated
        self._kernel = AuthorizationKernel(validated)
        self._package_ttl_seconds = package_ttl_seconds
        self._server_budget = server_budget
        self._content_io = selected_content_io
        self._candidate_index = candidate_index
        self._clock = clock
        self._reference_issuer = _OpaqueReferenceIssuer()

    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> Resolved:
        """Resolve one Acquire through the sole sealed Kernel path."""

        as_of = _require_utc("Runtime clock", self._clock())
        decision = self._kernel.authorize_acquire(
            invocation,
            delivery_context,
            request,
            server_budget=self._server_budget,
            as_of=as_of,
            reference_issuer=self._reference_issuer,
            candidate_index=self._candidate_index,
            projection_session=(
                invocation.user_actor.materialized_projection_session
            ),
        )
        provenance = decision.provenance_receipt

        package = ContextPackage(
            organization_ref=provenance.package_organization_ref,
            purpose=decision.policy_receipt.purpose,
            ttl_seconds=self._package_ttl_seconds,
            as_of=provenance.as_of,
            expires_at=provenance.as_of
            + timedelta(seconds=self._package_ttl_seconds),
            decision_ref=provenance.decision_ref,
            blocks=decision.content.blocks,
            evidence=decision.content.evidence,
            gaps=(),
            budget_usage=BudgetUsage(
                tokens=sum(
                    len(block.body.encode("utf-8"))
                    for block in decision.content.blocks
                ),
                provider_calls=0,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            coverage=Coverage(
                status=(
                    CoverageStatus.SUFFICIENT
                    if decision.content.evidence
                    else CoverageStatus.EMPTY
                ),
                reason=decision.audit_receipt.reason,
            ),
        )
        return Resolved(
            package=package,
            effective_budget=decision.effective_budget,
            scope_decision=ScopeDecisionReceipt(
                digest=decision.policy_receipt.effective_scope.digest,
                target_count=len(decision.policy_receipt.effective_scope.targets),
                is_empty=not decision.policy_receipt.effective_scope.targets,
            ),
        )


def required_kernel_dependencies() -> KernelDependencies:
    """Return the only allowed concrete composition; no disable flag exists."""

    return KernelDependencies(
        policy=PolicyGate(),
        audit=DecisionAuditGate(),
        budget=PackageBudgetGate(),
        provenance=ProvenanceGate(),
    )
