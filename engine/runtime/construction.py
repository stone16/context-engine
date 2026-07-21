"""Fail-closed construction and first sealed empty-Package Runtime path."""

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
from engine.runtime.content_io import RuntimeContentIo, prohibited_empty_path_content_io
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
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    InvocationConstructionProvenance,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    OrganizationVerificationProvenance,
)
from engine.runtime.scope import (
    OMITTED_REQUEST_NARROWING,
    EffectiveScope,
    compute_effective_scope,
)
from engine.runtime.scope_authority import (
    _require_active_trusted_scope_snapshot,
    _trusted_operands_from_snapshot,
)


class RuntimeConfigurationError(RuntimeError):
    """Raised when the sealed Runtime composition is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class EmptyPolicyReceipt:
    """Trusted-input policy result for a request with zero candidates."""

    request_id: str
    purpose: str
    effective_scope: EffectiveScope = field(repr=False)
    candidate_count: Literal[0] = 0
    authorized_projection_count: Literal[0] = 0


@dataclass(frozen=True, slots=True)
class DecisionProvenanceReceipt:
    """In-memory provenance binding for the staged empty decision."""

    decision_ref: str
    package_organization_ref: str
    request_id: str
    purpose: str
    as_of: datetime
    authorized_projection_count: Literal[0] = 0


@dataclass(frozen=True, slots=True)
class DecisionAuditReceipt:
    """Restricted safe audit result with no denied identifiers or counts."""

    decision_ref: str
    reason: CoverageReason
    authorized_evidence_count: Literal[0] = 0
    denied_detail_count: Literal[0] = 0


@dataclass(frozen=True, slots=True)
class EmptyAuthorizationDecision:
    """Result proving each mandatory Kernel behavior completed."""

    effective_budget: PackageBudget
    policy_receipt: EmptyPolicyReceipt
    provenance_receipt: DecisionProvenanceReceipt
    audit_receipt: DecisionAuditReceipt


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

    def validate_empty_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> EmptyPolicyReceipt:
        _validate_trusted_operands(invocation, delivery_context, request)
        effective_scope = compute_effective_scope(
            _trusted_operands_from_snapshot(invocation.trusted_scope_snapshot),
            request.narrowing
            if request.narrowing is not None
            else OMITTED_REQUEST_NARROWING,
        )
        return EmptyPolicyReceipt(
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
        policy_receipt: EmptyPolicyReceipt,
        *,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
    ) -> DecisionProvenanceReceipt:
        _require_utc("Runtime clock", as_of)
        organization_ref, decision_ref = reference_issuer.issue_pair()
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
        )


@dataclass(frozen=True, slots=True)
class DecisionAuditGate:
    """Concrete safe in-memory audit gate; persistence belongs to Issue #19."""

    def record_empty(
        self,
        provenance_receipt: DecisionProvenanceReceipt,
    ) -> DecisionAuditReceipt:
        return DecisionAuditReceipt(
            decision_ref=provenance_receipt.decision_ref,
            reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
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
    """Non-pluggable kernel gate for the first zero-candidate Runtime slice."""

    def __init__(self, dependencies: KernelDependencies) -> None:
        validated = _validate_kernel_dependencies(dependencies)
        self._policy = validated.policy
        self._audit = validated.audit
        self._budget = validated.budget
        self._provenance = validated.provenance

    def authorize_empty_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
        *,
        server_budget: PackageBudget,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
    ) -> EmptyAuthorizationDecision:
        """Run policy, budget, provenance, and audit in their fixed order."""

        policy_receipt = self._policy.validate_empty_acquire(
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
        audit_receipt = self._audit.record_empty(provenance_receipt)
        if audit_receipt.decision_ref != provenance_receipt.decision_ref:
            raise RuntimeConfigurationError("audit and provenance decision mismatch")
        return EmptyAuthorizationDecision(
            effective_budget=effective_budget,
            policy_receipt=policy_receipt,
            provenance_receipt=provenance_receipt,
            audit_receipt=audit_receipt,
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


class _OpaqueReferenceIssuer:
    """Runtime-owned, lock-serialized issuer with no caller-controlled factory."""

    def __init__(self) -> None:
        self._secret = token_bytes(32)
        self._sequence = 0
        self._lock = Lock()

    def issue_pair(self) -> tuple[str, str]:
        with self._lock:
            self._sequence += 1
            material = self._sequence.to_bytes(16, byteorder="big")
            organization_entropy = new_hmac(
                self._secret,
                b"organization:" + material,
                sha256,
            ).hexdigest()[:32]
            decision_entropy = new_hmac(
                self._secret,
                b"decision:" + material,
                sha256,
            ).hexdigest()[:32]
        return (
            f"{ORGANIZATION_PACKAGE_REF_PREFIX}_{organization_entropy}",
            f"{DECISION_REF_PREFIX}_{decision_entropy}",
        )


class Runtime:
    """Single sealed Runtime entry point for the evidence-free Acquire tracer."""

    def __init__(
        self,
        dependencies: KernelDependencies,
        *,
        package_ttl_seconds: int = DEFAULT_PACKAGE_TTL_SECONDS,
        server_budget: PackageBudget = DEFAULT_SERVER_PACKAGE_BUDGET,
        content_io: RuntimeContentIo | None = None,
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
        self._dependencies = validated
        self._kernel = AuthorizationKernel(validated)
        self._package_ttl_seconds = package_ttl_seconds
        self._server_budget = server_budget
        self._content_io = selected_content_io
        self._clock = clock
        self._reference_issuer = _OpaqueReferenceIssuer()

    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> Resolved:
        """Resolve one Acquire through the sole sealed empty-Package path."""

        as_of = _require_utc("Runtime clock", self._clock())
        decision = self._kernel.authorize_empty_acquire(
            invocation,
            delivery_context,
            request,
            server_budget=self._server_budget,
            as_of=as_of,
            reference_issuer=self._reference_issuer,
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
            blocks=(),
            evidence=(),
            gaps=(),
            budget_usage=BudgetUsage(
                tokens=0,
                provider_calls=0,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            coverage=Coverage(
                status=CoverageStatus.EMPTY,
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
