"""Fail-closed sealed Runtime authorization and Package construction path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as new_hmac
from secrets import token_bytes
from threading import Lock
from typing import Literal, overload
from uuid import UUID

from engine.runtime.actor import _require_active_user_actor
from engine.runtime.budget import PackageBudget, effective_package_budget
from engine.runtime.capabilities import (
    RuntimeCapability,
    RuntimeCapabilityGate,
    RuntimeRefusalCategory,
    UnsupportedCapability,
    UnsupportedCapabilityAuditReceipt,
    _required_capability_for_request,
)
from engine.runtime.content_io import (
    CandidateIndex,
    RuntimeContentIo,
    prohibited_empty_path_content_io,
)
from engine.runtime.context_run import (
    ContextRunPersistenceUnavailable,
    build_context_run_records,
    persist_context_run,
)
from engine.runtime.contracts import (
    DECISION_REF_PREFIX,
    ORGANIZATION_PACKAGE_REF_PREFIX,
    Acquire,
    BudgetUsage,
    CitationNotAvailable,
    ContextPackage,
    Continue,
    Coverage,
    CoverageReason,
    CoverageStatus,
    OpenCitation,
    RequestNotAvailable,
    ResolutionOutcome,
    Resolved,
    RuntimeRequest,
    ScopeDecisionReceipt,
    _require_closed_opaque_ref,
)
from engine.runtime.delivery import TrustedDeliveryContext
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
from engine.runtime.invocation import AuthenticatedInvocation
from engine.runtime.materialized import (
    MaterializedFragmentLocator,
    MaterializedProjectionSession,
    _locate_materialized_fragment,
    _project_materialized_fragment,
)
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.policy_epoch import (
    PolicyEpochAuthorityUnavailable,
    PolicyEpochVerification,
    _policy_epoch_is_current,
    _require_active_policy_epoch_verification,
)
from engine.runtime.scope import (
    OMITTED_REQUEST_NARROWING,
    EffectiveScope,
    ScopeTarget,
    compute_effective_scope,
)
from engine.runtime.scope_authority import (
    _trusted_operands_from_snapshot,
)
from engine.runtime.trusted_inputs import _validate_trusted_invocation_and_delivery


class RuntimeConfigurationError(RuntimeError):
    """Raised when the sealed Runtime composition is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class PolicyReceipt:
    """Trusted-input policy result before candidate discovery."""

    request_id: str
    purpose: str
    policy_epoch: int
    effective_scope: EffectiveScope = field(repr=False)


@dataclass(frozen=True, slots=True)
class DecisionProvenanceReceipt:
    """Server-owned request and policy lineage for one decision."""

    decision_ref: str
    package_organization_ref: str
    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    authenticated_application_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    effective_scope_digest: str = field(repr=False)
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
    """Pre-delivery result awaiting the final Policy Epoch and audit gates."""

    effective_budget: PackageBudget
    policy_receipt: PolicyReceipt
    provenance_receipt: DecisionProvenanceReceipt
    content: PackageContent


@dataclass(frozen=True, slots=True)
class FinalizedAuthorizationResult:
    """Final policy, provenance, content, and audit after the delivery veto."""

    policy_receipt: PolicyReceipt
    provenance_receipt: DecisionProvenanceReceipt
    content: PackageContent
    audit_receipt: DecisionAuditReceipt


@dataclass(frozen=True, slots=True)
class PolicyGate:
    """Concrete, non-substitutable trusted-input policy gate."""

    def validate_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> PolicyReceipt:
        if type(request) is not Acquire:
            raise TypeError("Runtime request must be Acquire")
        _validate_trusted_invocation_and_delivery(invocation, delivery_context)
        effective_scope = (
            compute_effective_scope(
                _trusted_operands_from_snapshot(invocation.trusted_scope_snapshot),
                request.narrowing
                if request.narrowing is not None
                else OMITTED_REQUEST_NARROWING,
            )
            if invocation.trusted_scope_snapshot.policy_epoch == invocation.policy_epoch
            else EffectiveScope(frozenset())
        )
        return PolicyReceipt(
            request_id=invocation.request_id,
            purpose=delivery_context.purpose,
            policy_epoch=invocation.policy_epoch,
            effective_scope=effective_scope,
        )

    def validate_unavailable(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: RuntimeRequest,
    ) -> PolicyReceipt:
        """Validate trusted operands without authorizing any content scope."""

        if type(request) not in {Acquire, Continue, OpenCitation}:
            raise TypeError("request must be one closed Runtime request variant")
        _validate_trusted_invocation_and_delivery(invocation, delivery_context)
        return PolicyReceipt(
            request_id=invocation.request_id,
            purpose=delivery_context.purpose,
            policy_epoch=invocation.policy_epoch,
            effective_scope=EffectiveScope(frozenset()),
        )


@dataclass(frozen=True, slots=True)
class PolicyEpochGate:
    """Concrete final durable-epoch validation gate; never replaceable."""

    def is_current(self, verification: PolicyEpochVerification) -> bool:
        try:
            _require_active_policy_epoch_verification(verification)
            return _policy_epoch_is_current(verification)
        except PolicyEpochAuthorityUnavailable:
            raise
        except (TypeError, ValueError) as error:
            raise PolicyEpochAuthorityUnavailable(
                "Policy Epoch validation authority is unavailable"
            ) from error


@dataclass(frozen=True, slots=True)
class PackageBudgetGate:
    """Concrete finite-budget intersection gate."""

    def intersect(
        self,
        server_budget: PackageBudget,
        request: Acquire | Continue,
    ) -> PackageBudget:
        return effective_package_budget(server_budget, request.package_budget)

    def preflight(
        self,
        server_budget: PackageBudget,
        request: RuntimeRequest,
    ) -> PackageBudget:
        if type(request) is Acquire or type(request) is Continue:
            return effective_package_budget(server_budget, request.package_budget)
        if type(request) is OpenCitation:
            return effective_package_budget(server_budget, None)
        raise TypeError("budget preflight requires one closed Runtime request")


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
            organization_id=(invocation.organization_verification.organization_id),
            user_id=invocation.user_actor.user_id,
            membership_id=invocation.user_actor.membership_id,
            membership_version=invocation.user_actor.membership_version,
            principal_ref=invocation.principal_ref,
            agent_version_ref=invocation.agent_version_ref,
            authenticated_application_ref=(invocation.authenticated_application_ref),
            authentication_binding_ref=invocation.authentication_binding_ref,
            effective_scope_digest=policy_receipt.effective_scope.digest,
            request_id=policy_receipt.request_id,
            purpose=policy_receipt.purpose,
            as_of=as_of,
            run_ref=references.run_ref,
            policy_snapshot_ref=references.policy_snapshot_ref,
            policy_epoch=policy_receipt.policy_epoch,
            source_acl_decision_ref=references.source_acl_decision_ref,
        )


class DecisionAuditGate:
    """Concrete safe in-memory audit gate; persistence belongs to Issue #19."""

    __slots__ = ("_lock", "_unsupported_category_counts")

    def __init__(self) -> None:
        self._lock = Lock()
        self._unsupported_category_counts: dict[RuntimeRefusalCategory, int] = {
            RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY: 0
        }

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

    def record_unsupported(
        self,
        provenance_receipt: DecisionProvenanceReceipt,
    ) -> None:
        """Record only the closed category, never carrier or resource detail."""

        if type(provenance_receipt) is not DecisionProvenanceReceipt:
            raise TypeError("unsupported capability audit requires decision provenance")
        _require_closed_opaque_ref(
            "decision reference",
            provenance_receipt.decision_ref,
            prefix=DECISION_REF_PREFIX,
        )
        receipt = UnsupportedCapabilityAuditReceipt()
        with self._lock:
            self._unsupported_category_counts[receipt.category] += 1

    def _unsupported_capability_snapshot(
        self,
    ) -> tuple[RuntimeRefusalCategory, int, Literal[0]]:
        """Return only the restricted category, occurrence count, and zero detail."""

        category = RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY
        with self._lock:
            count = self._unsupported_category_counts[category]
        return category, count, 0


type KernelDependency = (
    PolicyGate
    | PolicyEpochGate
    | DecisionAuditGate
    | PackageBudgetGate
    | ProvenanceGate
)


@dataclass(frozen=True, slots=True)
class KernelDependencies:
    """Exact mandatory concrete gates; callers cannot replace their behavior."""

    policy: PolicyGate
    policy_epoch: PolicyEpochGate
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
        ("policy_epoch", PolicyEpochGate),
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
        self._policy_epoch = validated.policy_epoch
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
        """Run policy, budget, provenance, exact projection, and assembly."""

        policy_receipt = self._policy.validate_acquire(
            invocation,
            delivery_context,
            request,
        )
        epoch_verification = invocation.user_actor.policy_epoch_verification
        if not self._policy_epoch.is_current(epoch_verification):
            policy_receipt = PolicyReceipt(
                request_id=policy_receipt.request_id,
                purpose=policy_receipt.purpose,
                policy_epoch=policy_receipt.policy_epoch,
                effective_scope=EffectiveScope(frozenset()),
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
        return AuthorizationDecision(
            effective_budget=effective_budget,
            policy_receipt=policy_receipt,
            provenance_receipt=provenance_receipt,
            content=content,
        )

    def preflight_unavailable_request(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: RuntimeRequest,
        *,
        server_budget: PackageBudget,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
    ) -> None:
        """Run the mandatory content-free gates before a capability veto."""

        policy_receipt = self._policy.validate_unavailable(
            invocation,
            delivery_context,
            request,
        )
        self._budget.preflight(server_budget, request)

        scope_snapshot = invocation.trusted_scope_snapshot
        provenance_receipt = self._provenance.issue(
            invocation,
            policy_receipt,
            as_of=as_of,
            reference_issuer=reference_issuer,
        )
        if (
            scope_snapshot.policy_epoch != invocation.policy_epoch
            or not self._policy_epoch.is_current(
                invocation.user_actor.policy_epoch_verification
            )
        ):
            raise PolicyEpochAuthorityUnavailable(
                "unavailable capability preflight requires a current Policy Epoch"
            )
        self._audit.record_unsupported(provenance_receipt)

    def finalize_for_delivery(
        self,
        invocation: AuthenticatedInvocation,
        decision: AuthorizationDecision,
    ) -> FinalizedAuthorizationResult:
        """Revalidate immediately before audit and Package construction."""

        if type(decision) is not AuthorizationDecision:
            raise TypeError("final Policy Epoch gate requires AuthorizationDecision")
        _require_active_user_actor(invocation.user_actor)
        policy_receipt = decision.policy_receipt
        content = decision.content
        provenance = decision.provenance_receipt
        content_binding_matches_decision = all(
            evidence.lineage.run_ref == provenance.run_ref
            and evidence.lineage.decision_ref == provenance.decision_ref
            and evidence.lineage.principal_ref == provenance.principal_ref
            and evidence.lineage.purpose == provenance.purpose
            and evidence.lineage.as_of == provenance.as_of
            and evidence.lineage.policy_snapshot_ref == provenance.policy_snapshot_ref
            and evidence.lineage.policy_epoch == provenance.policy_epoch
            and evidence.lineage.source_acl_decision_ref
            == provenance.source_acl_decision_ref
            for evidence in content.evidence
        )
        decision_binding_matches_invocation = (
            provenance.organization_id == invocation.user_actor.organization_id
            and provenance.user_id == invocation.user_actor.user_id
            and provenance.membership_id == invocation.user_actor.membership_id
            and provenance.membership_version
            == invocation.user_actor.membership_version
            and provenance.principal_ref == invocation.principal_ref
            and provenance.agent_version_ref == invocation.agent_version_ref
            and provenance.authenticated_application_ref
            == invocation.authenticated_application_ref
            and provenance.authentication_binding_ref
            == invocation.authentication_binding_ref
            and policy_receipt.policy_epoch
            == provenance.policy_epoch
            == invocation.policy_epoch
            == invocation.user_actor.policy_epoch
            and policy_receipt.request_id
            == provenance.request_id
            == invocation.request_id
            and policy_receipt.purpose == provenance.purpose
            and provenance.effective_scope_digest
            == policy_receipt.effective_scope.digest
        )
        final_epoch_is_current = False
        if content_binding_matches_decision and decision_binding_matches_invocation:
            final_epoch_is_current = self._policy_epoch.is_current(
                invocation.user_actor.policy_epoch_verification
            )
        if not (
            content_binding_matches_decision
            and decision_binding_matches_invocation
            and final_epoch_is_current
        ):
            policy_receipt = PolicyReceipt(
                request_id=policy_receipt.request_id,
                purpose=policy_receipt.purpose,
                policy_epoch=policy_receipt.policy_epoch,
                effective_scope=EffectiveScope(frozenset()),
            )
            content = construct_package_content(())
            if (
                content_binding_matches_decision
                and decision_binding_matches_invocation
                and not final_epoch_is_current
            ):
                provenance = replace(
                    provenance,
                    effective_scope_digest=policy_receipt.effective_scope.digest,
                )
        audit_receipt = self._audit.record(
            provenance,
            authorized_evidence_count=len(content.evidence),
        )
        if audit_receipt.decision_ref != provenance.decision_ref:
            raise RuntimeConfigurationError("audit and provenance decision mismatch")
        return FinalizedAuthorizationResult(
            policy_receipt=policy_receipt,
            provenance_receipt=provenance,
            content=content,
            audit_receipt=audit_receipt,
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
                field_projection = _project_materialized_fragment(
                    projection_session,
                    locator,
                )
                if field_projection is None:
                    continue
                projection = _construct_authorized_projection(
                    kernel_scope=kernel_scope,
                    candidate_ref=candidate,
                    body=field_projection.rendered_body,
                    projected_field_refs=(
                        field_projection.projected_field_refs
                    ),
                    lineage=EvidenceLineage(
                        run_ref=provenance_receipt.run_ref,
                        principal_ref=invocation.principal_ref,
                        purpose=provenance_receipt.purpose,
                        as_of=provenance_receipt.as_of,
                        decision_ref=provenance_receipt.decision_ref,
                        policy_snapshot_ref=(provenance_receipt.policy_snapshot_ref),
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
            source_acl_decision_ref=f"sourceacl_{entropies['source-acl']}",
        )


class Runtime:
    """Single sealed Runtime entry point for the closed request union."""

    def __init__(
        self,
        dependencies: KernelDependencies,
        *,
        package_ttl_seconds: int = DEFAULT_PACKAGE_TTL_SECONDS,
        server_budget: PackageBudget = DEFAULT_SERVER_PACKAGE_BUDGET,
        content_io: RuntimeContentIo | None = None,
        candidate_index: CandidateIndex | None = None,
        acquire_capability: RuntimeCapability = (
            RuntimeCapability.MATERIALIZED_ACQUIRE
        ),
        clock: Callable[[], datetime] = _utc_now,
        query_digest_keyring: QueryDigestKeyring | None = None,
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
        if (
            content_io is not None
            and candidate_index is not None
            and content_io.index is not candidate_index
        ):
            raise RuntimeConfigurationError(
                "candidate_index must be the composed content_io index"
            )
        if content_io is None and candidate_index is not None:
            selected_content_io = RuntimeContentIo(
                index=candidate_index,
                provider=selected_content_io.provider,
                source_content=selected_content_io.source_content,
            )
        if type(
            acquire_capability
        ) is not RuntimeCapability or acquire_capability not in {
            RuntimeCapability.MATERIALIZED_ACQUIRE,
            RuntimeCapability.FEDERATED_DISCOVERY,
            RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION,
        }:
            raise RuntimeConfigurationError(
                "acquire capability must be a server-owned Acquire capability"
            )
        self._dependencies = validated
        self._kernel = AuthorizationKernel(validated)
        self._package_ttl_seconds = package_ttl_seconds
        self._server_budget = server_budget
        self._content_io = selected_content_io
        self._candidate_discovery_enabled = candidate_index is not None
        self._acquire_capability = acquire_capability
        self._capability_gate = RuntimeCapabilityGate()
        self._clock = clock
        if (
            query_digest_keyring is not None
            and type(query_digest_keyring) is not QueryDigestKeyring
        ):
            raise TypeError("query_digest_keyring must be QueryDigestKeyring")
        self._query_digest_keyring = query_digest_keyring
        self._reference_issuer = _OpaqueReferenceIssuer()

    @overload
    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
    ) -> Resolved | RequestNotAvailable: ...

    @overload
    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Continue,
    ) -> RequestNotAvailable: ...

    @overload
    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: OpenCitation,
    ) -> CitationNotAvailable: ...

    @overload
    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: RuntimeRequest,
    ) -> ResolutionOutcome: ...

    def resolve(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: RuntimeRequest,
    ) -> ResolutionOutcome:
        """Resolve one closed request after a pre-content capability check."""

        request_type = type(request)
        capability = _required_capability_for_request(
            request,
            acquire_capability=self._acquire_capability,
        )

        if type(self._capability_gate) is not RuntimeCapabilityGate:
            raise RuntimeConfigurationError(
                "mandatory Runtime capability gate is missing or invalid"
            )
        if type(self._kernel) is not AuthorizationKernel:
            raise RuntimeConfigurationError(
                "mandatory AuthorizationKernel is missing or invalid"
            )
        try:
            self._capability_gate.require_available(capability)
        except UnsupportedCapability:
            self._kernel.preflight_unavailable_request(
                invocation,
                delivery_context,
                request,
                server_budget=self._server_budget,
                as_of=_require_utc("Runtime clock", self._clock()),
                reference_issuer=self._reference_issuer,
            )
            if request_type is OpenCitation:
                return CitationNotAvailable()
            return RequestNotAvailable()

        if capability is not RuntimeCapability.MATERIALIZED_ACQUIRE:
            raise RuntimeConfigurationError(
                "available Acquire capability has no sealed implementation"
            )
        if request_type is not Acquire:
            raise RuntimeConfigurationError(
                "available future Runtime carrier has no sealed implementation"
            )
        assert isinstance(request, Acquire)
        acquire = request

        as_of = _require_utc("Runtime clock", self._clock())
        decision = self._kernel.authorize_acquire(
            invocation,
            delivery_context,
            acquire,
            server_budget=self._server_budget,
            as_of=as_of,
            reference_issuer=self._reference_issuer,
            candidate_index=(
                self._content_io.index if self._candidate_discovery_enabled else None
            ),
            projection_session=(invocation.user_actor.materialized_projection_session),
        )
        finalized = self._kernel.finalize_for_delivery(invocation, decision)
        policy_receipt = finalized.policy_receipt
        content = finalized.content
        audit_receipt = finalized.audit_receipt
        provenance = finalized.provenance_receipt

        package = ContextPackage(
            organization_ref=provenance.package_organization_ref,
            purpose=policy_receipt.purpose,
            ttl_seconds=self._package_ttl_seconds,
            as_of=provenance.as_of,
            expires_at=provenance.as_of + timedelta(seconds=self._package_ttl_seconds),
            decision_ref=provenance.decision_ref,
            blocks=content.blocks,
            evidence=content.evidence,
            gaps=(),
            budget_usage=BudgetUsage(
                tokens=sum(len(block.body.encode("utf-8")) for block in content.blocks),
                provider_calls=0,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            coverage=Coverage(
                status=(
                    CoverageStatus.SUFFICIENT
                    if content.evidence
                    else CoverageStatus.EMPTY
                ),
                reason=audit_receipt.reason,
            ),
        )
        persistence_session = invocation.user_actor.context_run_persistence_session
        if persistence_session is None:
            raise ContextRunPersistenceUnavailable(
                "Acquire requires durable ContextRun persistence"
            )
        if self._query_digest_keyring is None:
            raise ContextRunPersistenceUnavailable(
                "ContextRun persistence requires an explicit query digest keyring"
            )
        run_record, decision_audit = build_context_run_records(
            invocation=invocation,
            request=acquire,
            provenance=provenance,
            package=package,
            final_effective_scope=policy_receipt.effective_scope,
            effective_budget=decision.effective_budget,
            keyring=self._query_digest_keyring,
        )
        persist_context_run(
            persistence_session,
            run_record,
            decision_audit,
        )
        return Resolved(
            package=package,
            effective_budget=decision.effective_budget,
            scope_decision=ScopeDecisionReceipt(
                digest=policy_receipt.effective_scope.digest,
                target_count=len(policy_receipt.effective_scope.targets),
                is_empty=not policy_receipt.effective_scope.targets,
            ),
        )

    def _required_capability(self, request: RuntimeRequest) -> RuntimeCapability:
        """Expose the sealed server-owned plan to trusted ingress composition."""

        return _required_capability_for_request(
            request,
            acquire_capability=self._acquire_capability,
        )

    def _requires_active_scope_authority(self, request: RuntimeRequest) -> bool:
        """Tell trusted ingress whether this server plan can perform content work."""

        capability = self._required_capability(request)
        gate = RuntimeCapabilityGate()
        try:
            gate.require_available(capability)
        except UnsupportedCapability:
            return False
        return True

    def _unsupported_capability_audit_snapshot(
        self,
    ) -> tuple[RuntimeRefusalCategory, int, Literal[0]]:
        """Expose restricted audit evidence only to trusted in-process checks."""

        return self._dependencies.audit._unsupported_capability_snapshot()


def required_kernel_dependencies() -> KernelDependencies:
    """Return the only allowed concrete composition; no disable flag exists."""

    return KernelDependencies(
        policy=PolicyGate(),
        policy_epoch=PolicyEpochGate(),
        audit=DecisionAuditGate(),
        budget=PackageBudgetGate(),
        provenance=ProvenanceGate(),
    )
