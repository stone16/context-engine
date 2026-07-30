"""Fail-closed sealed Runtime authorization and Package construction path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from hmac import new as new_hmac
from secrets import token_bytes
from threading import Lock
from typing import Literal, overload
from uuid import UUID
from weakref import WeakKeyDictionary

from engine.runtime.actor import _require_active_user_actor
from engine.runtime.authorized_ranking import (
    UNIFORM_RANKER_WEIGHTS,
    RankerWeights,
    join_authorized_ranking,
    rank_authorized_one_hop,
    select_authorized_ranking,
)
from engine.runtime.budget import PackageBudget, effective_package_budget
from engine.runtime.candidate_ranking import (
    DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
    CandidateQuery,
    CandidateRankEvidence,
    require_bounded_candidate_submission,
    require_candidate_submission_limit,
)
from engine.runtime.capabilities import (
    RuntimeCapability,
    RuntimeCapabilityGate,
    RuntimeRefusalCategory,
    UnsupportedCapability,
    UnsupportedCapabilityAuditReceipt,
    _required_capability_for_request,
)
from engine.runtime.citation import (
    CitationAuthorityUnavailable,
    CitationLocatorNotAvailable,
    CitationOpenIssue,
    CitationOpenProfile,
    CitationOpenRedemption,
    issue_citation_open_ref,
    redeem_citation_open_ref,
)
from engine.runtime.content_io import (
    CandidateIndex,
    RuntimeContentIo,
    prohibited_empty_path_content_io,
)
from engine.runtime.context_run import (
    PACKAGE_RETENTION_POLICY_REF,
    ContextRunPersistenceUnavailable,
    build_context_run_records,
    persist_context_run,
)
from engine.runtime.contracts import (
    DECISION_REF_PREFIX,
    PACKAGE_REF_PREFIX,
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
    context_package_digest_document,
)
from engine.runtime.delivery import (
    DeliveryConstructionProvenance,
    TrustedDeliveryContext,
)
from engine.runtime.egress import (
    INTERNAL_ONLY_EGRESS_PROFILE,
    ChannelEgressProfile,
    EgressGrant,
    EgressGrantIssuanceUnavailable,
    EgressGrantIssue,
    EgressProfile,
    InternalOnlyEgressProfile,
    ModelEgressProfile,
    direct_egress_audience_digest,
    issue_egress_grant,
)
from engine.runtime.egress_payload import channel_payload_digest, model_input_digest
from engine.runtime.evidence import (
    AuthorizedProjection,
    CandidateRef,
    EvidenceLineage,
    PackageContent,
    _attach_citation_open_refs,
    _AuthorizationKernelScope,
    _candidate_sort_key,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _construct_inherited_authorized_projection,
    _open_authorization_kernel_scope,
    construct_package_content,
)
from engine.runtime.fragment_window import (
    FragmentWindowRead,
    FragmentWindowReader,
    FragmentWindowRequest,
    FragmentWindowResult,
    _close_fragment_window_session,
    _construct_fragment_window_session,
    _fragment_window_read_snapshot,
)
from engine.runtime.invocation import AuthenticatedInvocation
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    MaterializedFragmentLocator,
    MaterializedOneHopCandidate,
    MaterializedProjectionSession,
    _close_candidate_discovery_session,
    _construct_candidate_discovery_session,
    _discover_materialized_one_hop,
    _locate_materialized_fragment,
    _project_materialized_fragment,
    _read_materialized_fragment_window,
    require_bounded_discovery_request,
)
from engine.runtime.package_digest import QueryDigestKeyring, context_package_digest
from engine.runtime.policy_epoch import (
    PolicyEpochAuthorityUnavailable,
    PolicyEpochVerification,
    _policy_epoch_is_current,
    _require_active_policy_epoch_verification,
)
from engine.runtime.prekernel_fusion import fuse_candidate_evidence
from engine.runtime.release_lineage import ActiveReleaseUnavailable
from engine.runtime.scope import (
    OMITTED_REQUEST_NARROWING,
    EffectiveScope,
    ScopeTarget,
    _require_candidate_discovery_scope_integrity,
    _require_effective_scope_integrity,
    candidate_discovery_scope,
    compute_effective_scope,
)
from engine.runtime.scope_authority import (
    _trusted_operands_from_snapshot,
)
from engine.runtime.trusted_inputs import _validate_trusted_invocation_and_delivery


class RuntimeConfigurationError(RuntimeError):
    """Raised when the sealed Runtime composition is incomplete or invalid."""


def _package_budget_limits(budget: PackageBudget) -> tuple[int, int, int, int]:
    if type(budget) is not PackageBudget:
        raise TypeError("PackageBudget has the wrong nominal type")
    budget.__post_init__()
    return (
        budget.max_tokens,
        budget.max_provider_calls,
        budget.max_cost_microunits,
        budget.max_elapsed_ms,
    )


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
    package_id: str
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
    projections: tuple[AuthorizedProjection, ...]
    _projection_scope: _AuthorizationKernelScope | None = field(repr=False)
    expanded_candidate_refs: frozenset[CandidateRef] = field(
        default_factory=frozenset,
        repr=False,
    )
    _effective_budget_limits: tuple[int, int, int, int] = field(
        init=False,
        repr=False,
    )
    _integrity_seal: bytes = field(init=False, repr=False, default=b"")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_effective_budget_limits",
            _package_budget_limits(self.effective_budget),
        )


@dataclass(frozen=True, slots=True)
class SealedPackageSelection:
    """Exact sealed-Runtime budget selection awaiting final epoch veto."""

    decision: AuthorizationDecision = field(repr=False)
    content: PackageContent = field(repr=False)
    effective_budget_limits: tuple[int, int, int, int] = field(repr=False)
    integrity_seal: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedAcquireAuthorization:
    """Rank-blind policy and provenance state prepared before discovery."""

    effective_budget: PackageBudget
    policy_receipt: PolicyReceipt
    provenance_receipt: DecisionProvenanceReceipt


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

    def validate_open_citation(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: OpenCitation,
    ) -> PolicyReceipt:
        """Compute current full trusted scope; the locator contributes no authority."""

        if type(request) is not OpenCitation:
            raise TypeError("Runtime request must be OpenCitation")
        _validate_trusted_invocation_and_delivery(invocation, delivery_context)
        effective_scope = (
            compute_effective_scope(
                _trusted_operands_from_snapshot(invocation.trusted_scope_snapshot),
                OMITTED_REQUEST_NARROWING,
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
        request: Acquire | Continue | OpenCitation,
    ) -> PackageBudget:
        if isinstance(request, Acquire | Continue):
            return effective_package_budget(server_budget, request.package_budget)
        return effective_package_budget(server_budget, None)

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
        package_id = references.package_id
        decision_ref = references.decision_ref
        package_id = _require_closed_opaque_ref(
            "package reference",
            package_id,
            prefix=PACKAGE_REF_PREFIX,
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
            trusted_organization_hex in package_id
            or trusted_organization_hex in decision_ref
        ):
            raise ValueError("server references must not embed trusted Organization")
        return DecisionProvenanceReceipt(
            decision_ref=decision_ref,
            package_id=package_id,
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


@dataclass(frozen=True, slots=True)
class EgressGate:
    """Concrete final Package/hop policy gate; no external profile is a closed deny."""

    def finalize(
        self,
        *,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        provenance: DecisionProvenanceReceipt,
        package: ContextPackage,
        profile: EgressProfile,
        issued_at: datetime,
    ) -> EgressGrant | None:
        if type(profile) not in {
            InternalOnlyEgressProfile,
            ModelEgressProfile,
            ChannelEgressProfile,
        }:
            raise RuntimeConfigurationError("egress profile has the wrong nominal type")
        _require_active_user_actor(invocation.user_actor)
        if (
            package.package_digest
            != context_package_digest(context_package_digest_document(package))
            or provenance.organization_id != invocation.user_actor.organization_id
            or provenance.package_id != package.package_id
            or provenance.decision_ref != package.decision_ref
            or provenance.purpose != package.purpose
            or provenance.purpose != delivery_context.purpose
            or not (
                provenance.policy_epoch
                == invocation.policy_epoch
                == invocation.user_actor.policy_epoch
            )
            or provenance.as_of != package.as_of
            or package.audience_digest
            != (
                delivery_context.audience_digest
                or direct_egress_audience_digest(
                    organization_id=invocation.user_actor.organization_id,
                    membership_id=invocation.user_actor.membership_id,
                    membership_version=invocation.user_actor.membership_version,
                    authenticated_application_ref=(
                        delivery_context.authenticated_application_ref
                    ),
                    delivery_binding_ref=delivery_context.delivery_binding_ref,
                )
            )
            or package.policy_epoch != provenance.policy_epoch
            or package.policy_snapshot_ref != provenance.policy_snapshot_ref
            or package.run_ref != provenance.run_ref
            or issued_at != package.as_of
            or package.expires_at <= issued_at
        ):
            raise EgressGrantIssuanceUnavailable(
                "final egress policy could not bind the current Package"
            )
        if type(profile) is InternalOnlyEgressProfile:
            return None
        assert isinstance(profile, ModelEgressProfile | ChannelEgressProfile)
        if type(profile) is ChannelEgressProfile and (
            delivery_context.construction_provenance
            is not DeliveryConstructionProvenance.REDEEMED_PRIVATE_DELIVERY_EVIDENCE
            or delivery_context.destination_ref != profile.destination_ref
            or delivery_context.consumer_ref != profile.consumer_ref
        ):
            raise EgressGrantIssuanceUnavailable(
                "channel egress is not bound to the trusted private delivery"
            )
        session = invocation.user_actor.egress_grant_issuance_session
        if session is None:
            raise EgressGrantIssuanceUnavailable(
                "external egress requires durable one-shot issuance"
            )
        audience_digest = package.audience_digest
        expires_at = min(
            package.expires_at,
            issued_at + profile.maximum_ttl,
        )
        if type(profile) is ModelEgressProfile:
            issue = EgressGrantIssue.for_model(
                organization_id=invocation.user_actor.organization_id,
                package_digest=package.package_digest,
                payload_digest=model_input_digest(package),
                purpose=package.purpose,
                audience_digest=audience_digest,
                policy_epoch=invocation.policy_epoch,
                issued_at=issued_at,
                expires_at=expires_at,
                profile=profile,
            )
        elif type(profile) is ChannelEgressProfile:
            issue = EgressGrantIssue.for_channel(
                organization_id=invocation.user_actor.organization_id,
                package_digest=package.package_digest,
                payload_digest=channel_payload_digest(package),
                purpose=package.purpose,
                audience_digest=audience_digest,
                policy_epoch=invocation.policy_epoch,
                issued_at=issued_at,
                expires_at=expires_at,
                profile=profile,
            )
        else:  # pragma: no cover - closed nominal union above
            raise RuntimeConfigurationError("egress profile variant is unavailable")
        return issue_egress_grant(session, issue)


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
    | EgressGate
)


def _decision_integrity_snapshot(decision: AuthorizationDecision) -> tuple[object, ...]:
    """Snapshot every rank-free decision value trusted after authorization."""

    policy = decision.policy_receipt
    provenance = decision.provenance_receipt
    projections = tuple(
        (
            projection.candidate_ref.organization_id,
            projection.candidate_ref.source_ref,
            projection.candidate_ref.resource_ref,
            projection.candidate_ref.revision_ref,
            projection.candidate_ref.fragment_ref,
            projection.projected_body,
            projection.projected_field_refs,
            tuple(
                getattr(projection.lineage, item.name)
                for item in fields(projection.lineage)
            ),
        )
        for projection in decision.projections
    )
    return (
        id(decision),
        _package_budget_limits(decision.effective_budget),
        decision._effective_budget_limits,
        policy.request_id,
        policy.purpose,
        policy.policy_epoch,
        tuple(
            sorted(
                (
                    target.organization_id.bytes,
                    target.source_ref,
                    target.resource_ref or "",
                )
                for target in policy.effective_scope.targets
            )
        ),
        policy.effective_scope.digest,
        tuple(getattr(provenance, item.name) for item in fields(provenance)),
        projections,
        tuple(
            sorted(
                (
                    candidate.organization_id.bytes,
                    candidate.source_ref,
                    candidate.resource_ref,
                    candidate.revision_ref,
                    candidate.fragment_ref,
                )
                for candidate in decision.expanded_candidate_refs
            )
        ),
    )


def _decision_integrity_material(decision: AuthorizationDecision) -> bytes:
    return repr(
        (
            "context-engine:authorization-decision:v1",
            _decision_integrity_snapshot(decision),
        )
    ).encode("utf-8", "surrogatepass")


def _selection_integrity_material(
    decision: AuthorizationDecision,
    content: PackageContent,
    effective_budget_limits: tuple[int, int, int, int],
) -> bytes:
    """Encode every rank-free value trusted by final Package construction."""

    content_snapshot = (
        tuple((block.evidence_ref, block.body) for block in content.blocks),
        tuple(
            tuple(getattr(evidence, item.name) for item in fields(evidence))
            for evidence in content.evidence
        ),
    )
    return repr(
        (
            "context-engine:sealed-package-selection:v1",
            _decision_integrity_snapshot(decision),
            decision._integrity_seal,
            effective_budget_limits,
            content_snapshot,
        )
    ).encode("utf-8", "surrogatepass")


class _SelectionAuthority:
    """Opaque per-composition signer; key bytes never enter the object graph."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("selection authority is not directly constructible")


_SELECTION_AUTHORITY_KEYS: WeakKeyDictionary[_SelectionAuthority, bytes] = (
    WeakKeyDictionary()
)


def _new_selection_authority() -> _SelectionAuthority:
    authority = object.__new__(_SelectionAuthority)
    _SELECTION_AUTHORITY_KEYS[authority] = token_bytes(32)
    return authority


def _issue_selection_authority_seal(
    authority: _SelectionAuthority,
    material: bytes,
) -> bytes:
    if type(authority) is not _SelectionAuthority or type(material) is not bytes:
        raise TypeError("selection sealing requires exact authority and bytes")
    secret = _SELECTION_AUTHORITY_KEYS.get(authority)
    if secret is None:
        raise RuntimeConfigurationError("selection authority is unavailable")
    return new_hmac(secret, material, sha256).digest()


def _verify_selection_authority_seal(
    authority: _SelectionAuthority,
    seal: bytes,
    material: bytes,
) -> bool:
    if type(seal) is not bytes:
        return False
    return compare_digest(
        seal,
        _issue_selection_authority_seal(authority, material),
    )


class SealedRuntimeSelector:
    """Non-pluggable post-authorization ranking and budget boundary."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("SealedRuntimeSelector can only be constructed by Runtime")

    def select_for_delivery(
        self,
        decision: AuthorizationDecision,
        rank_evidence: tuple[CandidateRankEvidence, ...],
        *,
        ranker_weights: RankerWeights | None = None,
    ) -> SealedPackageSelection:
        if type(decision) is not AuthorizationDecision:
            raise TypeError("sealed selection requires AuthorizationDecision")
        if type(rank_evidence) is not tuple or any(
            type(evidence) is not CandidateRankEvidence for evidence in rank_evidence
        ):
            raise TypeError("sealed selection requires exact rank evidence")
        authority = _SELECTOR_AUTHORITIES.get(self)
        if type(authority) is not _SelectionAuthority:
            raise RuntimeConfigurationError("sealed selector authority is unavailable")
        if type(
            decision._integrity_seal
        ) is not bytes or not _verify_selection_authority_seal(
            authority,
            decision._integrity_seal,
            _decision_integrity_material(decision),
        ):
            raise ValueError("authorization decision integrity validation failed")
        ranked_refs = frozenset(
            evidence.candidate_ref for evidence in rank_evidence
        )
        eligible_projections = tuple(
            projection
            for projection in decision.projections
            if projection.candidate_ref not in decision.expanded_candidate_refs
            or projection.candidate_ref in ranked_refs
        )
        selected = select_authorized_ranking(
            join_authorized_ranking(
                eligible_projections,
                rank_evidence,
                ranker_weights=(
                    ranker_weights.values if ranker_weights is not None else None
                ),
            ),
            decision.effective_budget,
        )
        content = construct_package_content(
            tuple(item.projection for item in selected),
        )
        budget_limits = decision._effective_budget_limits
        material = _selection_integrity_material(
            decision,
            content,
            budget_limits,
        )
        return SealedPackageSelection(
            decision=decision,
            content=content,
            effective_budget_limits=budget_limits,
            integrity_seal=_issue_selection_authority_seal(
                authority,
                material,
            ),
        )


_SELECTOR_AUTHORITIES: WeakKeyDictionary[SealedRuntimeSelector, _SelectionAuthority] = (
    WeakKeyDictionary()
)


@dataclass(frozen=True, slots=True)
class KernelDependencies:
    """Exact mandatory concrete gates; callers cannot replace their behavior."""

    policy: PolicyGate
    policy_epoch: PolicyEpochGate
    audit: DecisionAuditGate
    budget: PackageBudgetGate
    provenance: ProvenanceGate
    egress: EgressGate


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
        ("egress", EgressGate),
    ):
        if type(getattr(dependencies, field_name)) is not expected_type:
            raise RuntimeConfigurationError(
                f"mandatory kernel dependency is missing or invalid: {field_name}"
            )
    return dependencies


_KERNEL_SELECTION_AUTHORITIES: WeakKeyDictionary[
    AuthorizationKernel, _SelectionAuthority
] = WeakKeyDictionary()


def _kernel_selection_authority(
    kernel: AuthorizationKernel,
) -> _SelectionAuthority:
    authority = _KERNEL_SELECTION_AUTHORITIES.get(kernel)
    if type(authority) is not _SelectionAuthority:
        raise RuntimeConfigurationError("Kernel selection authority is unavailable")
    return authority


class AuthorizationKernel:
    """Non-pluggable exact authorization and projection boundary."""

    __slots__ = (
        "_policy",
        "_policy_epoch",
        "_audit",
        "_budget",
        "_provenance",
        "_egress",
        "_fragment_window_reader",
        "__weakref__",
    )

    def __init__(
        self,
        dependencies: KernelDependencies,
        *,
        _selection_authority: _SelectionAuthority | None = None,
        _fragment_window_reader: FragmentWindowReader | None = None,
    ) -> None:
        validated = _validate_kernel_dependencies(dependencies)
        if (
            type(_selection_authority) is not _SelectionAuthority
            or _selection_authority not in _SELECTION_AUTHORITY_KEYS
        ):
            raise RuntimeConfigurationError(
                "AuthorizationKernel requires Runtime-owned selection authority"
            )
        self._policy = validated.policy
        self._policy_epoch = validated.policy_epoch
        self._audit = validated.audit
        self._budget = validated.budget
        self._provenance = validated.provenance
        self._egress = validated.egress
        self._fragment_window_reader = _fragment_window_reader
        _KERNEL_SELECTION_AUTHORITIES[self] = _selection_authority

    def prepare_acquire(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: Acquire,
        *,
        server_budget: PackageBudget,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
    ) -> PreparedAcquireAuthorization:
        """Run policy, budget, and provenance before content-free discovery."""

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
        return PreparedAcquireAuthorization(
            effective_budget=effective_budget,
            policy_receipt=policy_receipt,
            provenance_receipt=provenance_receipt,
        )

    def authorize_acquire(
        self,
        invocation: AuthenticatedInvocation,
        preparation: PreparedAcquireAuthorization,
        candidate_refs: tuple[CandidateRef, ...],
        *,
        projection_session: MaterializedProjectionSession | None,
    ) -> AuthorizationDecision:
        """Project sorted opaque refs under one prepared rank-blind decision."""

        if type(preparation) is not PreparedAcquireAuthorization:
            raise TypeError("Kernel requires PreparedAcquireAuthorization")
        if type(candidate_refs) is not tuple or any(
            type(candidate) is not CandidateRef for candidate in candidate_refs
        ):
            raise TypeError("Kernel candidate_refs must be exact CandidateRef values")
        _require_effective_scope_integrity(preparation.policy_receipt.effective_scope)
        projections, projection_scope = self._authorize_and_project(
            invocation,
            preparation.policy_receipt,
            preparation.provenance_receipt,
            candidate_refs,
            projection_session,
        )
        decision = AuthorizationDecision(
            effective_budget=preparation.effective_budget,
            policy_receipt=preparation.policy_receipt,
            provenance_receipt=preparation.provenance_receipt,
            projections=projections,
            _projection_scope=projection_scope,
        )
        object.__setattr__(
            decision,
            "_integrity_seal",
            _issue_selection_authority_seal(
                _kernel_selection_authority(self),
                _decision_integrity_material(decision),
            ),
        )
        return decision

    def authorize_one_hop(
        self,
        invocation: AuthenticatedInvocation,
        preparation: PreparedAcquireAuthorization,
        decision: AuthorizationDecision,
        candidates: tuple[MaterializedOneHopCandidate, ...],
        *,
        projection_session: MaterializedProjectionSession,
    ) -> AuthorizationDecision:
        """Admit inherited and cross-Article graph candidates through the Kernel."""

        if type(preparation) is not PreparedAcquireAuthorization:
            raise TypeError("Kernel graph expansion requires prepared authorization")
        if type(decision) is not AuthorizationDecision:
            raise TypeError("Kernel graph expansion requires AuthorizationDecision")
        if type(candidates) is not tuple or any(
            type(candidate) is not MaterializedOneHopCandidate
            for candidate in candidates
        ):
            raise TypeError("Kernel graph expansion requires exact graph candidates")
        anchors = {
            projection.candidate_ref: projection for projection in decision.projections
        }
        inherited: list[AuthorizedProjection] = []
        reauthorization_refs: list[CandidateRef] = []
        for candidate in candidates:
            anchor = anchors.get(candidate.anchor_ref)
            if anchor is None:
                raise ValueError("graph candidate is not rooted in this decision")
            candidate_ref = candidate.candidate_ref
            anchor_ref = candidate.anchor_ref
            same_article = (
                candidate_ref.organization_id == anchor_ref.organization_id
                and candidate_ref.source_ref == anchor_ref.source_ref
                and candidate_ref.resource_ref == anchor_ref.resource_ref
            )
            if not same_article:
                reauthorization_refs.append(candidate_ref)
                continue
            if candidate_ref.revision_ref != anchor_ref.revision_ref:
                continue
            locator = _locate_materialized_fragment(
                projection_session,
                candidate_ref,
            )
            if locator is None or not _locator_matches_candidate(
                locator,
                candidate_ref,
            ):
                continue
            projection = _project_materialized_fragment(
                projection_session,
                locator,
            )
            if projection is None:
                continue
            inherited.append(
                _construct_inherited_authorized_projection(
                    anchor=anchor,
                    candidate_ref=candidate_ref,
                    body=projection.rendered_body,
                    projected_field_refs=projection.projected_field_refs,
                )
            )
        reauthorized, _scope = self._authorize_and_project(
            invocation,
            preparation.policy_receipt,
            preparation.provenance_receipt,
            tuple(reauthorization_refs),
            projection_session,
            kernel_scope=decision._projection_scope,
        )
        expanded = replace(
            decision,
            projections=decision.projections + tuple(inherited) + reauthorized,
            expanded_candidate_refs=frozenset(
                projection.candidate_ref for projection in (*inherited, *reauthorized)
            ),
        )
        object.__setattr__(
            expanded,
            "_integrity_seal",
            _issue_selection_authority_seal(
                _kernel_selection_authority(self),
                _decision_integrity_material(expanded),
            ),
        )
        return expanded

    def authorize_open_citation(
        self,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        request: OpenCitation,
        *,
        candidate: CandidateRef | None,
        server_budget: PackageBudget,
        as_of: datetime,
        reference_issuer: _OpaqueReferenceIssuer,
        projection_session: MaterializedProjectionSession | None,
    ) -> AuthorizationDecision:
        """Reauthorize one content-free locator target through the exact Kernel."""

        policy_receipt = self._policy.validate_open_citation(
            invocation,
            delivery_context,
            request,
        )
        if not self._policy_epoch.is_current(
            invocation.user_actor.policy_epoch_verification
        ):
            policy_receipt = replace(
                policy_receipt,
                effective_scope=EffectiveScope(frozenset()),
            )
        effective_budget = self._budget.intersect(server_budget, request)
        provenance_receipt = self._provenance.issue(
            invocation,
            policy_receipt,
            as_of=as_of,
            reference_issuer=reference_issuer,
        )
        projections, projection_scope = self._authorize_and_project(
            invocation,
            policy_receipt,
            provenance_receipt,
            (candidate,) if candidate is not None else (),
            projection_session,
        )
        decision = AuthorizationDecision(
            effective_budget=effective_budget,
            policy_receipt=policy_receipt,
            provenance_receipt=provenance_receipt,
            projections=projections,
            _projection_scope=projection_scope,
        )
        object.__setattr__(
            decision,
            "_integrity_seal",
            _issue_selection_authority_seal(
                _kernel_selection_authority(self),
                _decision_integrity_material(decision),
            ),
        )
        return decision

    def expand_fragment_window(
        self,
        request: FragmentWindowRequest,
        *,
        projection_session: MaterializedProjectionSession,
    ) -> FragmentWindowResult:
        """Construct inherited projections only after current-lineage read proof."""

        if type(request) is not FragmentWindowRequest:
            raise TypeError("Kernel fragment expansion requires FragmentWindowRequest")
        reader = self._fragment_window_reader
        if reader is None:
            raise RuntimeConfigurationError("fragment window reader is not composed")
        authoritative_read = _read_materialized_fragment_window(
            projection_session,
            MaterializedFragmentLocator(
                organization_id=request.anchor.candidate_ref.organization_id,
                source_ref=request.anchor.candidate_ref.source_ref,
                resource_ref=request.anchor.candidate_ref.resource_ref,
                revision_ref=request.anchor.candidate_ref.revision_ref,
                fragment_ref=request.anchor.candidate_ref.fragment_ref,
                source_acl_projection_ref=(
                    request.anchor.lineage.source_acl_projection_ref
                ),
                source_acl_as_of=request.anchor.lineage.source_acl_as_of,
                source_acl_freshness_profile_ref=(
                    request.anchor.lineage.source_acl_freshness_profile_ref
                ),
            ),
            request.before,
            request.after,
            request.expansion_candidates,
        )
        authoritative_snapshot = _fragment_window_read_snapshot(authoritative_read)
        window_session = _construct_fragment_window_session(authoritative_read)
        try:
            read = reader.read_window(request, window_session)
        finally:
            _close_fragment_window_session(window_session)
        if type(read) is not FragmentWindowRead:
            raise TypeError("fragment window reader returned the wrong nominal type")
        read.__post_init__()
        if _fragment_window_read_snapshot(read) != authoritative_snapshot:
            raise ValueError("fragment window reader failed authoritative verification")
        anchor_ref = request.anchor.candidate_ref
        projections = tuple(
            _construct_inherited_authorized_projection(
                anchor=request.anchor,
                candidate_ref=CandidateRef(
                    organization_id=item.locator.organization_id,
                    source_ref=item.locator.source_ref,
                    resource_ref=item.locator.resource_ref,
                    revision_ref=item.locator.revision_ref,
                    fragment_ref=item.locator.fragment_ref,
                ),
                body=item.projection.rendered_body,
                projected_field_refs=item.projection.projected_field_refs,
            )
            for item in read.items
        )
        if any(
            candidate.organization_id == anchor_ref.organization_id
            and candidate.source_ref == anchor_ref.source_ref
            and candidate.resource_ref == anchor_ref.resource_ref
            for candidate in read.reauthorization_refs
        ):
            raise ValueError("same-Article expansion cannot request reauthorization")
        return FragmentWindowResult(
            projections=projections,
            reauthorization_refs=read.reauthorization_refs,
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
        selection: SealedPackageSelection,
    ) -> FinalizedAuthorizationResult:
        """Revalidate a Kernel-owned exact budget selection before delivery."""

        if type(selection) is not SealedPackageSelection:
            raise TypeError("sealed Runtime requires SealedPackageSelection")
        decision = selection.decision
        budget_limits = _package_budget_limits(decision.effective_budget)
        material = _selection_integrity_material(
            decision,
            selection.content,
            selection.effective_budget_limits,
        )
        expected_seal = _issue_selection_authority_seal(
            _kernel_selection_authority(self),
            material,
        )
        if (
            budget_limits != decision._effective_budget_limits
            or budget_limits != selection.effective_budget_limits
            or type(selection.integrity_seal) is not bytes
            or not compare_digest(selection.integrity_seal, expected_seal)
        ):
            raise ValueError("sealed selection integrity validation failed")
        content = selection.content
        if (
            sum(len(block.body.encode("utf-8")) for block in content.blocks)
            > budget_limits[0]
        ):
            raise ValueError("sealed selection exceeds PackageBudget")
        _require_active_user_actor(invocation.user_actor)
        policy_receipt = decision.policy_receipt
        _require_effective_scope_integrity(policy_receipt.effective_scope)
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

    def finalize_egress(
        self,
        *,
        invocation: AuthenticatedInvocation,
        delivery_context: TrustedDeliveryContext,
        provenance: DecisionProvenanceReceipt,
        package: ContextPackage,
        profile: EgressProfile,
        issued_at: datetime,
    ) -> EgressGrant | None:
        """Apply the mandatory final egress gate after Package construction."""

        if type(self._egress) is not EgressGate:
            raise RuntimeConfigurationError("mandatory final egress gate is invalid")
        return self._egress.finalize(
            invocation=invocation,
            delivery_context=delivery_context,
            provenance=provenance,
            package=package,
            profile=profile,
            issued_at=issued_at,
        )

    def _authorize_and_project(
        self,
        invocation: AuthenticatedInvocation,
        policy_receipt: PolicyReceipt,
        provenance_receipt: DecisionProvenanceReceipt,
        candidates: tuple[CandidateRef, ...],
        projection_session: MaterializedProjectionSession | None,
        *,
        kernel_scope: _AuthorizationKernelScope | None = None,
    ) -> tuple[tuple[AuthorizedProjection, ...], _AuthorizationKernelScope | None]:
        if not candidates or not policy_receipt.effective_scope.targets:
            return (), None
        if projection_session is None:
            raise RuntimeConfigurationError(
                "candidate discovery requires same-transaction projection session"
            )

        selected_kernel_scope = kernel_scope or _open_authorization_kernel_scope()
        owns_scope = kernel_scope is None
        try:
            projections = []
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
                    kernel_scope=selected_kernel_scope,
                    candidate_ref=candidate,
                    body=field_projection.rendered_body,
                    projected_field_refs=(field_projection.projected_field_refs),
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
                        source_acl_projection_ref=locator.source_acl_projection_ref,
                        source_acl_as_of=locator.source_acl_as_of,
                        source_acl_freshness_profile_ref=(
                            locator.source_acl_freshness_profile_ref
                        ),
                    ),
                )
                projections.append(projection)
            return tuple(projections), selected_kernel_scope
        except BaseException:
            if owns_scope:
                _close_authorization_kernel_scope(selected_kernel_scope)
            raise


def _construct_authorization_kernel_and_selector(
    dependencies: KernelDependencies,
    *,
    fragment_window_reader: FragmentWindowReader | None = None,
) -> tuple[AuthorizationKernel, SealedRuntimeSelector]:
    """Create the inseparable rank-blind Kernel and post-Kernel selector pair."""

    selection_authority = _new_selection_authority()
    selector = object.__new__(SealedRuntimeSelector)
    _SELECTOR_AUTHORITIES[selector] = selection_authority
    return (
        AuthorizationKernel(
            dependencies,
            _selection_authority=selection_authority,
            _fragment_window_reader=fragment_window_reader,
        ),
        selector,
    )


def _close_authorization_decision(decision: AuthorizationDecision) -> None:
    """Close any post-projection lifetime after authorized consumers finish."""

    if type(decision) is not AuthorizationDecision:
        raise TypeError("closing authorization requires AuthorizationDecision")
    if decision._projection_scope is not None:
        _close_authorization_kernel_scope(decision._projection_scope)


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
    package_id: str
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
                    "package",
                    "decision",
                    "run",
                    "policy",
                    "source-acl",
                )
            }
        return _IssuedReferences(
            package_id=(f"{PACKAGE_REF_PREFIX}_{entropies['package']}"),
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
        candidate_submission_limit: int = DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
        ranker_weights: RankerWeights = UNIFORM_RANKER_WEIGHTS,
        acquire_capability: RuntimeCapability = (
            RuntimeCapability.MATERIALIZED_ACQUIRE
        ),
        clock: Callable[[], datetime] = _utc_now,
        query_digest_keyring: QueryDigestKeyring | None = None,
        egress_profile: EgressProfile = INTERNAL_ONLY_EGRESS_PROFILE,
        citation_profile: CitationOpenProfile | None = None,
    ) -> None:
        validated = _validate_kernel_dependencies(dependencies)
        if type(package_ttl_seconds) is not int or package_ttl_seconds <= 0:
            raise ValueError("package_ttl_seconds must be a positive exact integer")
        if type(server_budget) is not PackageBudget:
            raise TypeError("server_budget must be PackageBudget")
        selected_content_io = content_io or prohibited_empty_path_content_io()
        if type(selected_content_io) is not RuntimeContentIo:
            raise RuntimeConfigurationError("content_io must be RuntimeContentIo")
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
        if any(
            not callable(getattr(selected_content_io.index, method_name, None))
            for method_name in ("prepare_discovery", "discover")
        ):
            raise RuntimeConfigurationError("candidate_index is incomplete")
        try:
            require_candidate_submission_limit(candidate_submission_limit)
        except ValueError as error:
            raise RuntimeConfigurationError(
                "candidate submission limit must be a server-owned positive bound"
            ) from error
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
        self._kernel, self._selector = _construct_authorization_kernel_and_selector(
            validated,
            fragment_window_reader=selected_content_io.fragment_windows,
        )
        self._package_ttl_seconds = package_ttl_seconds
        self._server_budget = server_budget
        self._content_io = selected_content_io
        self._candidate_discovery_enabled = candidate_index is not None
        self._candidate_submission_limit = candidate_submission_limit
        if type(ranker_weights) is not RankerWeights:
            raise RuntimeConfigurationError("ranker weights must be server-owned")
        self._ranker_weights = ranker_weights
        self._acquire_capability = acquire_capability
        self._capability_gate = RuntimeCapabilityGate()
        self._clock = clock
        if (
            query_digest_keyring is not None
            and type(query_digest_keyring) is not QueryDigestKeyring
        ):
            raise TypeError("query_digest_keyring must be QueryDigestKeyring")
        self._query_digest_keyring = query_digest_keyring
        if type(egress_profile) not in {
            InternalOnlyEgressProfile,
            ModelEgressProfile,
            ChannelEgressProfile,
        }:
            raise RuntimeConfigurationError(
                "egress_profile must be one closed server-owned profile"
            )
        self._egress_profile = egress_profile
        if citation_profile is not None:
            if type(citation_profile) is not CitationOpenProfile:
                raise TypeError("citation_profile must be CitationOpenProfile or None")
            if timedelta(seconds=package_ttl_seconds) > citation_profile.maximum_ttl:
                raise RuntimeConfigurationError(
                    "citation profile lifetime must cover the Package TTL"
                )
        self._citation_profile = citation_profile
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
    ) -> Resolved | CitationNotAvailable: ...

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
            self._capability_gate.require_available(
                capability,
                citation_open_active=self._citation_profile is not None,
            )
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

        if capability not in {
            RuntimeCapability.MATERIALIZED_ACQUIRE,
            RuntimeCapability.OPEN_CITATION,
        }:
            raise RuntimeConfigurationError(
                "available Acquire capability has no sealed implementation"
            )
        if request_type not in {Acquire, OpenCitation}:
            raise RuntimeConfigurationError(
                "available future Runtime carrier has no sealed implementation"
            )

        active_release = invocation.user_actor.active_runtime_release
        if active_release is None:
            raise ActiveReleaseUnavailable(
                "Runtime delivery requires one Learning-published active release"
            )
        if active_release.organization_id != invocation.user_actor.organization_id:
            raise ActiveReleaseUnavailable(
                "active Runtime release crossed Organization"
            )

        as_of = _require_utc("Runtime clock", self._clock())
        if request_type is Acquire:
            assert isinstance(request, Acquire)
            preparation = self._kernel.prepare_acquire(
                invocation,
                delivery_context,
                request,
                server_budget=self._server_budget,
                as_of=as_of,
                reference_issuer=self._reference_issuer,
            )
            candidate_refs: tuple[CandidateRef, ...] = ()
            rank_evidence: tuple[CandidateRankEvidence, ...] = ()
            if (
                preparation.policy_receipt.effective_scope.targets
                and self._candidate_discovery_enabled
            ):
                projection_session = (
                    invocation.user_actor.materialized_projection_session
                )
                if projection_session is None:
                    raise RuntimeConfigurationError(
                        "candidate discovery requires same-transaction projection "
                        "session"
                    )
                discovery_scope = candidate_discovery_scope(
                    preparation.policy_receipt.effective_scope
                )
                discovery_request = self._content_io.index.prepare_discovery(
                    request,
                    effective_scope=discovery_scope,
                )
                _require_candidate_discovery_scope_integrity(discovery_scope)
                require_bounded_discovery_request(
                    discovery_request,
                    submission_limit=self._candidate_submission_limit,
                )
                discovery_session: CandidateDiscoverySession | None = None
                try:
                    discovery_session = _construct_candidate_discovery_session(
                        projection_session,
                        discovery_request,
                        effective_scope=(preparation.policy_receipt.effective_scope),
                    )
                    discovered = self._content_io.index.discover(
                        request,
                        discovery_session,
                        effective_scope=discovery_scope,
                    )
                    _require_candidate_discovery_scope_integrity(discovery_scope)
                finally:
                    if discovery_session is not None:
                        _close_candidate_discovery_session(discovery_session)
                if type(discovered) is not CandidateQuery:
                    raise TypeError("CandidateIndex must return CandidateQuery")
                require_bounded_candidate_submission(
                    discovered,
                    submission_limit=self._candidate_submission_limit,
                )
                fused = fuse_candidate_evidence(discovered)
                candidate_refs = tuple(
                    sorted(fused.candidate_refs, key=_candidate_sort_key)
                )
                rank_evidence = fused.rank_evidence
            decision = self._kernel.authorize_acquire(
                invocation,
                preparation,
                candidate_refs,
                projection_session=(
                    invocation.user_actor.materialized_projection_session
                ),
            )
            projection_session = invocation.user_actor.materialized_projection_session
            if projection_session is not None and decision.projections:
                one_hop = _discover_materialized_one_hop(
                    projection_session,
                    decision.projections,
                    min(64, self._candidate_submission_limit),
                )
                main_candidate_refs = set(candidate_refs)
                one_hop = tuple(
                    item
                    for item in one_hop
                    if item.candidate_ref not in main_candidate_refs
                )
                if one_hop:
                    main_projection_count = len(decision.projections)
                    decision = self._kernel.authorize_one_hop(
                        invocation,
                        preparation,
                        decision,
                        one_hop,
                        projection_session=projection_session,
                    )
                    graph_evidence = rank_authorized_one_hop(
                        request.need.query,
                        tuple(
                            projection
                            for projection in decision.projections[
                                main_projection_count:
                            ]
                            if projection.candidate_ref
                            in decision.expanded_candidate_refs
                        ),
                    )
                    rank_evidence = rank_evidence + graph_evidence
        else:
            assert isinstance(request, OpenCitation)
            citation_session = invocation.user_actor.citation_open_session
            if citation_session is None:
                raise CitationAuthorityUnavailable("citation authority unavailable")
            try:
                target = redeem_citation_open_ref(
                    citation_session,
                    CitationOpenRedemption(
                        citation_open_ref=request.citation_open_ref,
                        organization_id=invocation.user_actor.organization_id,
                        opened_at=as_of,
                    ),
                )
            except CitationLocatorNotAvailable:
                target = None
            decision = self._kernel.authorize_open_citation(
                invocation,
                delivery_context,
                request,
                candidate=(target.candidate_ref if target is not None else None),
                server_budget=self._server_budget,
                as_of=as_of,
                reference_issuer=self._reference_issuer,
                projection_session=(
                    invocation.user_actor.materialized_projection_session
                ),
            )
            rank_evidence = ()
        try:
            selection = self._selector.select_for_delivery(
                decision,
                rank_evidence,
                ranker_weights=self._ranker_weights,
            )
            finalized = self._kernel.finalize_for_delivery(
                invocation,
                selection,
            )
        finally:
            _close_authorization_decision(decision)
        policy_receipt = finalized.policy_receipt
        content = finalized.content
        audit_receipt = finalized.audit_receipt
        provenance = finalized.provenance_receipt
        if self._citation_profile is not None and content.evidence:
            citation_session = invocation.user_actor.citation_open_session
            if citation_session is None:
                raise CitationAuthorityUnavailable("citation authority unavailable")
            citation_references = {}
            for item in content.evidence:
                try:
                    revision_id = UUID(item.revision_ref)
                except ValueError:
                    raise CitationAuthorityUnavailable(
                        "citation authority unavailable"
                    ) from None
                citation_references[item.evidence_ref] = issue_citation_open_ref(
                    citation_session,
                    CitationOpenIssue(
                        organization_id=invocation.user_actor.organization_id,
                        package_ref=provenance.package_id,
                        evidence_ref=item.evidence_ref,
                        resource_ref=item.resource_ref,
                        revision_id=revision_id,
                        fragment_ref=item.fragment_ref,
                        issued_at=provenance.as_of,
                        expires_at=provenance.as_of
                        + timedelta(seconds=self._package_ttl_seconds),
                    ),
                    profile=self._citation_profile,
                )
            content = _attach_citation_open_refs(content, citation_references)
        audience_digest = delivery_context.audience_digest
        if audience_digest is None:
            audience_digest = direct_egress_audience_digest(
                organization_id=invocation.user_actor.organization_id,
                membership_id=invocation.user_actor.membership_id,
                membership_version=invocation.user_actor.membership_version,
                authenticated_application_ref=(
                    delivery_context.authenticated_application_ref
                ),
                delivery_binding_ref=delivery_context.delivery_binding_ref,
            )

        package = ContextPackage(
            package_id=provenance.package_id,
            purpose=policy_receipt.purpose,
            audience_digest=audience_digest,
            policy_epoch=provenance.policy_epoch,
            policy_snapshot_ref=provenance.policy_snapshot_ref,
            run_ref=provenance.run_ref,
            release_manifest_ref=active_release.manifest_ref,
            retention_policy_ref=PACKAGE_RETENTION_POLICY_REF,
            tokenizer_ref=active_release.tokenizer_ref,
            package_schema_ref=active_release.package_schema_ref,
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
        egress_grant = (
            self._kernel.finalize_egress(
                invocation=invocation,
                delivery_context=delivery_context,
                provenance=provenance,
                package=package,
                profile=self._egress_profile,
                issued_at=as_of,
            )
            if request_type is Acquire or package.evidence
            else None
        )
        persistence_session = invocation.user_actor.context_run_persistence_session
        if persistence_session is None:
            raise ContextRunPersistenceUnavailable(
                "Runtime delivery requires durable ContextRun persistence"
            )
        if self._query_digest_keyring is None:
            raise ContextRunPersistenceUnavailable(
                "ContextRun persistence requires an explicit query digest keyring"
            )
        run_record, decision_audit = build_context_run_records(
            invocation=invocation,
            request=request,
            provenance=provenance,
            package=package,
            final_effective_scope=policy_receipt.effective_scope,
            effective_budget=decision.effective_budget,
            keyring=self._query_digest_keyring,
            active_release=active_release,
        )
        persist_context_run(
            persistence_session,
            run_record,
            decision_audit,
        )
        if request_type is OpenCitation and not package.evidence:
            return CitationNotAvailable()
        return Resolved(
            package=package,
            effective_budget=decision.effective_budget,
            scope_decision=ScopeDecisionReceipt(
                digest=policy_receipt.effective_scope.digest,
                target_count=len(policy_receipt.effective_scope.targets),
                is_empty=not policy_receipt.effective_scope.targets,
            ),
            egress_grant=egress_grant,
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
            gate.require_available(
                capability,
                citation_open_active=self._citation_profile is not None,
            )
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
        egress=EgressGate(),
    )
