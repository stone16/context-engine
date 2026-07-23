from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime
from itertools import permutations
from typing import cast
from uuid import UUID

import pytest

from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.budget import PackageBudgetRequest
from engine.runtime.construction import (
    DEFAULT_SERVER_PACKAGE_BUDGET,
    AuthorizationDecision,
    AuthorizationKernel,
    DecisionAuditGate,
    DecisionProvenanceReceipt,
    Runtime,
    _OpaqueReferenceIssuer,
    required_kernel_dependencies,
)
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import (
    Acquire,
    ContextNeed,
    ContextPackage,
    RequestNarrowing,
    Resolved,
)
from engine.runtime.delivery import (
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    _construct_authenticated_http_invocation,
)
from engine.runtime.materialized import (
    MaterializedFieldValue,
    MaterializedFragmentLocator,
    MaterializedFragmentProjection,
    MaterializedProjectionKind,
    MaterializedProjectionPort,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.scope import (
    EffectiveScope,
    ScopeSet,
    ScopeTarget,
    TrustedScopeOperands,
)
from engine.runtime.scope_authority import (
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.support.context_run import (
    TEST_QUERY_DIGEST_KEYRING,
    RecordingContextRunPort,
    recording_context_run_session,
)
from tests.support.releases import active_runtime_release
from tests.support.security_gate import record_security_oracles

AS_OF = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
OTHER_ORGANIZATION_ID = UUID("48f519e3-c9f1-4e45-af3a-ef48ca5b23f0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")

AUTHORIZED = CandidateRef(
    organization_id=ORGANIZATION_ID,
    source_ref="source:synthetic",
    resource_ref="resource:authorized",
    revision_ref="11111111-1111-4111-8111-111111111111",
    fragment_ref="fragment:authorized",
)
AUTHORIZED_SECOND = CandidateRef(
    organization_id=ORGANIZATION_ID,
    source_ref="source:synthetic",
    resource_ref="resource:authorized-z",
    revision_ref="44444444-4444-4444-8444-444444444444",
    fragment_ref="fragment:authorized-z",
)
DENIED = CandidateRef(
    organization_id=ORGANIZATION_ID,
    source_ref="source:synthetic",
    resource_ref="resource:denied",
    revision_ref="22222222-2222-4222-8222-222222222222",
    fragment_ref="fragment:denied",
)
CROSS_ORGANIZATION = CandidateRef(
    organization_id=OTHER_ORGANIZATION_ID,
    source_ref="source:hostile",
    resource_ref="resource:cross-organization",
    revision_ref="33333333-3333-4333-8333-333333333333",
    fragment_ref="fragment:cross-organization",
)
MISSING = CandidateRef(
    organization_id=ORGANIZATION_ID,
    source_ref="source:synthetic",
    resource_ref="resource:missing",
    revision_ref="55555555-5555-4555-8555-555555555555",
    fragment_ref="fragment:missing",
)


def locator(candidate: CandidateRef) -> MaterializedFragmentLocator:
    return MaterializedFragmentLocator(
        organization_id=candidate.organization_id,
        source_ref=candidate.source_ref,
        resource_ref=candidate.resource_ref,
        revision_ref=candidate.revision_ref,
        fragment_ref=candidate.fragment_ref,
    )


class HostileCandidateIndex:
    def __init__(self, ranked: tuple[CandidateRef, ...]) -> None:
        self.ranked = ranked
        self.calls = 0

    def discover(
        self, request: Acquire, projection_session: object
    ) -> tuple[CandidateRef, ...]:
        del request, projection_session
        self.calls += 1
        return self.ranked


class RecordingMaterializedPort:
    def __init__(self) -> None:
        self.locator_calls: list[CandidateRef] = []
        self.body_calls: list[MaterializedFragmentLocator] = []
        self.body_by_candidate = {
            AUTHORIZED: "A-safe",
            AUTHORIZED_SECOND: "Z-safe",
            DENIED: "DENIED-BODY-MUST-NEVER-BE-READ",
            CROSS_ORGANIZATION: "CROSS-ORG-BODY-MUST-NEVER-BE-READ",
        }

    def discover_exact_phrase(self, phrase_digest: str) -> tuple[()]:
        del phrase_digest
        return ()

    def source_is_active(self, source_ref: UUID) -> bool:
        del source_ref
        return True

    def observe_publication(self, candidate_ref: CandidateRef) -> None:
        del candidate_ref

    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(candidate_ref)
        if candidate_ref == MISSING:
            return None
        return locator(candidate_ref)

    def project(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> MaterializedFragmentProjection | None:
        self.body_calls.append(selected_locator)
        for candidate, body in self.body_by_candidate.items():
            if selected_locator == locator(candidate):
                return MaterializedFragmentProjection(
                    kind=MaterializedProjectionKind.LEGACY_BODY,
                    fields=(
                        MaterializedFieldValue(
                            field_ref="body",
                            field_value=body,
                            ordinal=0,
                        ),
                    ),
                    projection_ceiling=frozenset({"body"}),
                )
        return None


class SequencedPolicyEpochPort:
    def __init__(self, *epochs: int) -> None:
        self._epochs = list(epochs)
        self.reads = 0

    def read_current_epoch(self, organization_id: UUID) -> object:
        assert organization_id == ORGANIZATION_ID
        self.reads += 1
        if len(self._epochs) > 1:
            return self._epochs.pop(0)
        return self._epochs[0]


class MismatchedLocatorPort(RecordingMaterializedPort):
    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(candidate_ref)
        return locator(AUTHORIZED)


class MissingLocatorPort(RecordingMaterializedPort):
    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> None:
        self.locator_calls.append(candidate_ref)
        return None


class MissingBodyPort(RecordingMaterializedPort):
    def project(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> None:
        self.body_calls.append(selected_locator)
        return None


def exact_operands() -> TrustedScopeOperands:
    allowed = ScopeSet(
        frozenset(
            {
                ScopeTarget(
                    ORGANIZATION_ID,
                    AUTHORIZED.source_ref,
                    AUTHORIZED.resource_ref,
                )
            }
        )
    )
    return TrustedScopeOperands(
        organization_boundary=allowed,
        membership_rights=allowed,
        principal_grants=allowed,
        agent_ceiling=allowed,
        source_native_acl=allowed,
        resource_acl=allowed,
        purpose_policy=allowed,
    )


def scope_for(*candidates: CandidateRef) -> ScopeSet:
    return ScopeSet(
        frozenset(
            ScopeTarget(
                candidate.organization_id,
                candidate.source_ref,
                candidate.resource_ref,
            )
            for candidate in candidates
        )
    )


@contextmanager
def trusted_operands(
    port: RecordingMaterializedPort,
    *,
    policy_epoch_port: SequencedPolicyEpochPort | None = None,
    scope_policy_epoch: int | None = None,
    context_run_port: RecordingContextRunPort | None = None,
) -> Iterator[tuple[AuthenticatedInvocation, TrustedDeliveryContext]]:
    membership_scope = _open_membership_authority_scope()
    materialized_scope = _open_materialized_projection_scope()
    scope_authority_scope = _open_scope_authority_scope()
    policy_epoch_scope = _open_policy_epoch_authority_scope()
    try:
        selected_epoch_port = policy_epoch_port or SequencedPolicyEpochPort(7)
        policy_epoch_verification = _observe_current_policy_epoch(
            _construct_policy_epoch_session(
                authority_scope=policy_epoch_scope,
                organization_id=ORGANIZATION_ID,
                port=selected_epoch_port,
            )
        )
        projection_session = _construct_materialized_projection_session(
            authority_scope=materialized_scope,
            port=cast(MaterializedProjectionPort, port),
        )
        organization_verification = _construct_existing_http_organization_verification(
            organization_id=ORGANIZATION_ID,
            request_id="request-authorized-evidence",
            authentication_binding_ref="binding-authorized-evidence",
            verified_at=AS_OF,
        )
        operands = exact_operands()
        with recording_context_run_session(port=context_run_port) as (
            persistence_session,
            _,
        ):
            membership_verification = _construct_current_membership_verification(
                authority_scope=membership_scope,
                organization_id=ORGANIZATION_ID,
                user_id=USER_ID,
                membership_id=MEMBERSHIP_ID,
                membership_version=7,
                principal_ref="principal-authorized-evidence",
                request_id="request-authorized-evidence",
                authentication_binding_ref="binding-authorized-evidence",
                checked_at=AS_OF,
                policy_epoch_verification=policy_epoch_verification,
                active_runtime_release=active_runtime_release(
                    ORGANIZATION_ID,
                    active_revision_refs=tuple(
                        sorted(
                            {
                                AUTHORIZED.revision_ref,
                                AUTHORIZED_SECOND.revision_ref,
                                CROSS_ORGANIZATION.revision_ref,
                                DENIED.revision_ref,
                                MISSING.revision_ref,
                            }
                        )
                    ),
                ),
                materialized_projection_session=projection_session,
                context_run_persistence_session=persistence_session,
            )
            scope_snapshot = _construct_trusted_scope_snapshot(
                authority_scope=scope_authority_scope,
                organization_id=ORGANIZATION_ID,
                user_id=USER_ID,
                membership_id=MEMBERSHIP_ID,
                membership_version=7,
                policy_epoch=(
                    policy_epoch_verification.policy_epoch
                    if scope_policy_epoch is None
                    else scope_policy_epoch
                ),
                principal_ref="principal-authorized-evidence",
                agent_version_ref="agent-version-authorized-evidence",
                purpose="context.answer",
                request_id="request-authorized-evidence",
                authentication_binding_ref="binding-authorized-evidence",
                checked_at=AS_OF,
                organization_boundary=operands.organization_boundary,
                membership_rights=operands.membership_rights,
                principal_grants=operands.principal_grants,
                agent_ceiling=operands.agent_ceiling,
                source_native_acl=operands.source_native_acl,
                resource_acl=operands.resource_acl,
                purpose_policy=operands.purpose_policy,
            )
            invocation = _construct_authenticated_http_invocation(
                request_id="request-authorized-evidence",
                authenticated_organization_ref=str(ORGANIZATION_ID),
                organization_verification=organization_verification,
                user_ref=str(USER_ID),
                principal_ref="principal-authorized-evidence",
                membership_ref=str(MEMBERSHIP_ID),
                membership_version=7,
                current_membership_verification=membership_verification,
                agent_version_ref="agent-version-authorized-evidence",
                authenticated_application_ref="application-authorized-evidence",
                authentication_binding_ref="binding-authorized-evidence",
                trusted_purpose="context.answer",
                received_at=AS_OF,
                trusted_scope_snapshot=scope_snapshot,
            )
            delivery = _construct_direct_delivery_context(
                purpose="context.answer",
                authenticated_application_ref="application-authorized-evidence",
                delivery_binding_ref="binding-authorized-evidence",
                established_at=AS_OF,
            )
            yield invocation, delivery
    finally:
        _close_policy_epoch_authority_scope(policy_epoch_scope)
        _close_scope_authority_scope(scope_authority_scope)
        _close_materialized_projection_scope(materialized_scope)
        _close_membership_authority_scope(membership_scope)


@pytest.mark.security_evidence(id="RUNTIME-INDEX-NOT-AUTHORITY-005", layer="runtime")
@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-006", layer="runtime")
@pytest.mark.parametrize(
    "ranked",
    tuple(permutations((AUTHORIZED, DENIED, CROSS_ORGANIZATION))),
)
def test_hostile_candidate_order_delivers_only_exact_authorized_evidence(
    ranked: tuple[CandidateRef, ...],
    record_property: Callable[[str, object], None],
) -> None:
    index = HostileCandidateIndex(ranked)
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="hostile index")),
        )

    assert type(outcome) is Resolved
    package = outcome.package
    assert index.calls == 1
    assert port.body_calls == [locator(AUTHORIZED)]
    assert len(package.blocks) == len(package.evidence) == 1
    assert package.blocks[0].body == "A-safe"
    assert package.blocks[0].evidence_ref == package.evidence[0].evidence_ref
    assert package.evidence[0].fragment_ref == AUTHORIZED.fragment_ref
    assert package.evidence[0].lineage.run_ref
    assert package.evidence[0].lineage.purpose == "context.answer"
    assert package.evidence[0].lineage.as_of == AS_OF
    assert package.evidence[0].lineage.decision_ref == package.decision_ref
    assert package.evidence[0].lineage.policy_snapshot_ref
    assert package.evidence[0].lineage.policy_epoch == 7
    assert package.evidence[0].lineage.source_acl_decision_ref
    assert package.coverage.status == "sufficient"
    assert package.coverage.reason is None
    assert package.budget_usage.tokens == len(b"A-safe")
    assert package.budget_usage.provider_calls == 0
    assert package.budget_usage.cost_microunits == 0
    assert package.budget_usage.elapsed_ms == 0

    rendered = repr(package)
    for forbidden in (
        "DENIED-BODY-MUST-NEVER-BE-READ",
        "CROSS-ORG-BODY-MUST-NEVER-BE-READ",
        DENIED.resource_ref,
        DENIED.fragment_ref,
        CROSS_ORGANIZATION.resource_ref,
        CROSS_ORGANIZATION.fragment_ref,
        str(OTHER_ORGANIZATION_ID),
    ):
        assert forbidden not in rendered
    unauthorized_evidence_count = sum(
        item.fragment_ref in {DENIED.fragment_ref, CROSS_ORGANIZATION.fragment_ref}
        for item in package.evidence
    )
    wrong_organization_effect_count = sum(
        selected == locator(CROSS_ORGANIZATION) for selected in port.body_calls
    )
    missing_context_fallback_count = int(
        package.coverage.status != "sufficient"
        or len(package.blocks) == 0
        or len(package.evidence) == 0
    )
    assert unauthorized_evidence_count == 0
    assert wrong_organization_effect_count == 0
    assert missing_context_fallback_count == 0
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-006",
        unauthorized_evidence_count=unauthorized_evidence_count,
        wrong_organization_effect_count=wrong_organization_effect_count,
        missing_context_fallback_count=missing_context_fallback_count,
    )


def test_stale_scope_epoch_stops_before_candidate_or_body_io() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port, scope_policy_epoch=6) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="stale cached scope")),
        )

    assert type(outcome) is Resolved
    assert outcome.scope_decision.is_empty is True
    assert outcome.scope_decision.target_count == 0
    assert outcome.package.blocks == ()
    assert type(outcome) is Resolved
    assert outcome.package.evidence == ()
    assert index.calls == 0
    assert port.locator_calls == []
    assert port.body_calls == []


def test_mid_resolve_epoch_change_discards_content_before_delivery() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    context_run_port = RecordingContextRunPort()
    epoch = SequencedPolicyEpochPort(7, 7, 8)
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(
        port,
        policy_epoch_port=epoch,
        context_run_port=context_run_port,
    ) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="mid-resolve revocation")),
        )

    assert type(outcome) is Resolved
    # This is the mutation witness: without the final validation, A-safe leaks.
    assert epoch.reads == 3
    assert index.calls == 1
    assert port.body_calls == [locator(AUTHORIZED)]
    assert outcome.package.blocks == ()
    assert type(outcome) is Resolved
    assert outcome.package.evidence == ()
    assert outcome.package.coverage.status == "empty"
    assert outcome.package.coverage.reason == "no_authorized_evidence"
    assert outcome.package.budget_usage.tokens == 0
    assert "A-safe" not in repr(outcome)
    assert len(context_run_port.calls) == 1
    persisted_run, persisted_audit = context_run_port.calls[0]
    assert persisted_run.effective_scope_digest == outcome.scope_decision.digest
    assert (
        persisted_run.effective_scope_digest
        != EffectiveScope(
            frozenset(
                {
                    ScopeTarget(
                        ORGANIZATION_ID,
                        AUTHORIZED.source_ref,
                        AUTHORIZED.resource_ref,
                    )
                }
            )
        ).digest
    )
    assert persisted_audit is not None


def test_final_epoch_veto_changes_only_the_decision_scope_digest() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    kernel = AuthorizationKernel(required_kernel_dependencies())
    issuer = _OpaqueReferenceIssuer()

    with trusted_operands(
        port,
        policy_epoch_port=SequencedPolicyEpochPort(7, 7, 8),
    ) as (invocation, delivery):
        decision = kernel.authorize_acquire(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="finalized provenance")),
            server_budget=DEFAULT_SERVER_PACKAGE_BUDGET,
            as_of=AS_OF,
            reference_issuer=issuer,
            candidate_index=cast(CandidateIndex, index),
            projection_session=invocation.user_actor.materialized_projection_session,
        )
        finalized = kernel.finalize_for_delivery(invocation, decision)

    assert finalized.policy_receipt.effective_scope == EffectiveScope(frozenset())
    assert finalized.provenance_receipt == replace(
        decision.provenance_receipt,
        effective_scope_digest=finalized.policy_receipt.effective_scope.digest,
    )
    assert finalized.content.evidence == ()
    assert finalized.audit_receipt.authorized_evidence_count == 0


def test_pre_revocation_decision_cannot_be_laundered_by_fresh_invocation() -> None:
    """CACHE-002: a current invocation cannot make stale decision content current."""

    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    kernel = AuthorizationKernel(required_kernel_dependencies())
    issuer = _OpaqueReferenceIssuer()

    with trusted_operands(
        port,
        policy_epoch_port=SequencedPolicyEpochPort(7),
    ) as (pre_revocation_invocation, delivery):
        stale_decision = kernel.authorize_acquire(
            pre_revocation_invocation,
            delivery,
            Acquire(need=ContextNeed(query="cached pre-revocation decision")),
            server_budget=DEFAULT_SERVER_PACKAGE_BUDGET,
            as_of=AS_OF,
            reference_issuer=issuer,
            candidate_index=cast(CandidateIndex, index),
            projection_session=(
                pre_revocation_invocation.user_actor.materialized_projection_session
            ),
        )

    assert stale_decision.policy_receipt.policy_epoch == 7
    assert stale_decision.content.blocks[0].body == "A-safe"

    with trusted_operands(
        RecordingMaterializedPort(),
        policy_epoch_port=SequencedPolicyEpochPort(8),
    ) as (post_revocation_invocation, _delivery):
        finalized = kernel.finalize_for_delivery(
            post_revocation_invocation,
            stale_decision,
        )

    # Mutation witness: checking only the fresh invocation's current epoch leaks
    # the stale decision's already assembled A-safe content here.
    policy_receipt = finalized.policy_receipt
    content = finalized.content
    audit_receipt = finalized.audit_receipt
    assert policy_receipt.effective_scope.targets == frozenset()
    assert content.blocks == ()
    assert content.evidence == ()
    assert audit_receipt.authorized_evidence_count == 0
    assert "A-safe" not in repr(content)


def test_stale_content_cannot_be_spliced_into_a_current_decision() -> None:
    """Decision receipts cannot relabel old Evidence lineage as current."""

    kernel = AuthorizationKernel(required_kernel_dependencies())
    issuer = _OpaqueReferenceIssuer()
    request = Acquire(need=ContextNeed(query="spliced cached decision"))

    with trusted_operands(
        RecordingMaterializedPort(),
        policy_epoch_port=SequencedPolicyEpochPort(7),
    ) as (pre_revocation_invocation, delivery):
        stale_decision = kernel.authorize_acquire(
            pre_revocation_invocation,
            delivery,
            request,
            server_budget=DEFAULT_SERVER_PACKAGE_BUDGET,
            as_of=AS_OF,
            reference_issuer=issuer,
            candidate_index=cast(
                CandidateIndex,
                HostileCandidateIndex((AUTHORIZED,)),
            ),
            projection_session=(
                pre_revocation_invocation.user_actor.materialized_projection_session
            ),
        )

    with trusted_operands(
        RecordingMaterializedPort(),
        policy_epoch_port=SequencedPolicyEpochPort(8),
    ) as (post_revocation_invocation, delivery):
        current_decision = kernel.authorize_acquire(
            post_revocation_invocation,
            delivery,
            request,
            server_budget=DEFAULT_SERVER_PACKAGE_BUDGET,
            as_of=AS_OF,
            reference_issuer=issuer,
            candidate_index=cast(
                CandidateIndex,
                HostileCandidateIndex((AUTHORIZED,)),
            ),
            projection_session=(
                post_revocation_invocation.user_actor.materialized_projection_session
            ),
        )
        spliced_decision: AuthorizationDecision = replace(
            current_decision,
            content=stale_decision.content,
        )
        finalized = kernel.finalize_for_delivery(
            post_revocation_invocation,
            spliced_decision,
        )

    # Mutation witness: receipt/invocation epoch checks alone accept the epoch-8
    # wrapper and leak the epoch-7 A-safe content.
    policy_receipt = finalized.policy_receipt
    content = finalized.content
    audit_receipt = finalized.audit_receipt
    assert policy_receipt.effective_scope.targets == frozenset()
    assert content.blocks == ()
    assert content.evidence == ()
    assert audit_receipt.authorized_evidence_count == 0
    assert "A-safe" not in repr(content)


def test_explicit_candidate_index_requires_same_transaction_projection_session() -> (
    None
):
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        object.__setattr__(
            invocation.user_actor,
            "materialized_projection_session",
            None,
        )
        object.__setattr__(
            invocation.user_actor.current_membership_verification,
            "materialized_projection_session",
            None,
        )
        with pytest.raises(RuntimeError, match="same-transaction projection"):
            runtime.resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="missing projection session")),
            )

    assert index.calls == 0
    assert port.body_calls == []


def test_runtime_rejects_authority_locator_that_does_not_match_candidate_exactly() -> (
    None
):
    index = HostileCandidateIndex((DENIED,))
    port = MismatchedLocatorPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="mismatched authoritative locator")),
        )

    assert type(outcome) is Resolved
    assert outcome.package.evidence == ()
    assert outcome.package.blocks == ()
    assert outcome.package.coverage.status == "empty"
    assert port.body_calls == []


def test_missing_materialized_body_uses_the_same_tenant_safe_empty_outcome() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = MissingBodyPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="missing projected body")),
        )

    assert type(outcome) is Resolved
    assert port.locator_calls == [AUTHORIZED]
    assert port.body_calls == [locator(AUTHORIZED)]
    assert outcome.package.blocks == ()
    assert outcome.package.evidence == ()
    assert outcome.package.gaps == ()
    assert outcome.package.coverage.status == "empty"
    assert outcome.package.coverage.reason == "no_authorized_evidence"


def _canonical_empty_package(package: ContextPackage) -> tuple[object, ...]:
    return (
        package.purpose,
        package.ttl_seconds,
        package.as_of,
        package.expires_at,
        package.blocks,
        package.evidence,
        package.gaps,
        package.budget_usage,
        package.coverage,
    )


@pytest.mark.security_evidence(id="RUNTIME-NON-ENUMERATION-009", layer="runtime")
def test_runtime_canonical_empty_package_is_equal_for_every_internal_branch() -> None:
    scenarios: tuple[
        tuple[tuple[CandidateRef, ...], RecordingMaterializedPort], ...
    ] = (
        ((DENIED,), RecordingMaterializedPort()),
        ((DENIED, MISSING), RecordingMaterializedPort()),
        ((AUTHORIZED,), MissingLocatorPort()),
        ((CROSS_ORGANIZATION,), RecordingMaterializedPort()),
        ((AUTHORIZED,), MissingBodyPort()),
    )
    packages = []
    for ranked, port in scenarios:
        runtime = Runtime(
            required_kernel_dependencies(),
            candidate_index=cast(CandidateIndex, HostileCandidateIndex(ranked)),
            clock=lambda: AS_OF,
            query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
        )
        with trusted_operands(port) as (invocation, delivery):
            outcome = runtime.resolve(
                invocation,
                delivery,
                Acquire(need=ContextNeed(query="canonical empty outcome")),
            )
        assert type(outcome) is Resolved
        packages.append(_canonical_empty_package(outcome.package))

    assert packages == [packages[0]] * len(packages)


@pytest.mark.security_evidence(id="PROP-NON-ENUMERATION-009", layer="property")
@pytest.mark.parametrize(
    "ranked",
    (
        (DENIED,),
        (DENIED, MISSING),
        (MISSING,),
        (CROSS_ORGANIZATION,),
    ),
)
def test_denied_cross_organization_and_missing_candidates_share_one_runtime_outcome(
    ranked: tuple[CandidateRef, ...],
) -> None:
    index = HostileCandidateIndex(ranked)
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="non-enumerating probe")),
        )

    assert type(outcome) is Resolved
    package = outcome.package
    assert index.calls == 1
    assert port.locator_calls == sorted(
        set(ranked),
        key=lambda candidate: (
            str(candidate.organization_id),
            candidate.source_ref,
            candidate.resource_ref,
            candidate.revision_ref,
            candidate.fragment_ref,
        ),
    )
    assert port.body_calls == []
    assert package.blocks == ()
    assert package.evidence == ()
    assert package.gaps == ()
    assert package.coverage.status == "empty"
    assert package.coverage.reason == "no_authorized_evidence"
    assert package.budget_usage.tokens == 0
    assert package.budget_usage.provider_calls == 0
    assert package.budget_usage.cost_microunits == 0
    assert package.budget_usage.elapsed_ms == 0

    rendered = repr(outcome)
    for candidate in ranked:
        for forbidden in (
            candidate.source_ref,
            candidate.resource_ref,
            candidate.revision_ref,
            candidate.fragment_ref,
        ):
            assert forbidden not in rendered
    for forbidden_field in (
        "denied_count",
        "candidate_count",
        "denial_reason",
        "existence_detail",
    ):
        assert forbidden_field not in rendered.casefold()


@pytest.mark.security_evidence(id="RUNTIME-TRACE-REDACTION-012", layer="runtime")
def test_empty_decision_audit_is_generic_and_retains_no_denied_detail() -> None:
    receipt = DecisionAuditGate().record(
        DecisionProvenanceReceipt(
            decision_ref="dec_" + "a" * 32,
            package_id="pkg_" + "b" * 32,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=7,
            principal_ref="principal-non-enumeration-audit",
            agent_version_ref="agent-version-non-enumeration-audit",
            authenticated_application_ref="application-non-enumeration-audit",
            authentication_binding_ref="binding-non-enumeration-audit",
            effective_scope_digest="0" * 64,
            request_id="request-non-enumeration-audit",
            purpose="context.answer",
            as_of=AS_OF,
            run_ref="run-non-enumeration-audit",
            policy_snapshot_ref="policy-non-enumeration-audit",
            policy_epoch=1,
            source_acl_decision_ref="sourceacl-non-enumeration-audit",
        ),
        authorized_evidence_count=0,
    )

    assert receipt.reason == "no_authorized_evidence"
    assert receipt.authorized_evidence_count == 0
    assert receipt.denied_detail_count == 0
    assert tuple(field.name for field in fields(receipt)) == (
        "decision_ref",
        "reason",
        "authorized_evidence_count",
        "denied_detail_count",
    )
    rendered = repr(receipt).casefold()
    for forbidden in (
        "resource_ref",
        "fragment_ref",
        "candidate_ref",
        "denial_reason",
        "denied_count",
    ):
        assert forbidden not in rendered


def test_empty_effective_scope_performs_zero_candidate_or_body_io() -> None:
    index = HostileCandidateIndex((AUTHORIZED, DENIED, CROSS_ORGANIZATION))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        for operand_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "agent_ceiling",
            "source_native_acl",
            "resource_acl",
            "purpose_policy",
        ):
            object.__setattr__(
                invocation.trusted_scope_snapshot,
                operand_name,
                ScopeSet(frozenset()),
            )
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="empty effective scope")),
        )

    assert type(outcome) is Resolved
    assert outcome.scope_decision.is_empty is True
    assert outcome.package.evidence == ()
    assert index.calls == 0
    assert port.locator_calls == []
    assert port.body_calls == []


def test_authorized_body_over_budget_is_not_delivered() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(
                need=ContextNeed(query="bounded authorized body"),
                package_budget=PackageBudgetRequest(max_tokens=5),
            ),
        )

    assert type(outcome) is Resolved
    assert port.body_calls == [locator(AUTHORIZED)]
    assert outcome.package.blocks == ()
    assert outcome.package.evidence == ()
    assert outcome.package.budget_usage.tokens == 0
    assert outcome.package.coverage.status == "empty"


def test_budget_selection_is_independent_of_hostile_candidate_rank() -> None:
    def resolve(ranked: tuple[CandidateRef, ...]) -> tuple[str, ...]:
        index = HostileCandidateIndex(ranked)
        port = RecordingMaterializedPort()
        runtime = Runtime(
            required_kernel_dependencies(),
            candidate_index=cast(CandidateIndex, index),
            clock=lambda: AS_OF,
            query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
        )

        with trusted_operands(port) as (invocation, delivery):
            authorized = ScopeSet(
                frozenset(
                    ScopeTarget(
                        candidate.organization_id,
                        candidate.source_ref,
                        candidate.resource_ref,
                    )
                    for candidate in (AUTHORIZED, AUTHORIZED_SECOND)
                )
            )
            for operand_name in (
                "organization_boundary",
                "membership_rights",
                "principal_grants",
                "agent_ceiling",
                "source_native_acl",
                "resource_acl",
                "purpose_policy",
            ):
                object.__setattr__(
                    invocation.trusted_scope_snapshot,
                    operand_name,
                    authorized,
                )
            outcome = runtime.resolve(
                invocation,
                delivery,
                Acquire(
                    need=ContextNeed(query="rank-independent budget"),
                    package_budget=PackageBudgetRequest(max_tokens=6),
                ),
            )

        assert type(outcome) is Resolved
        return tuple(block.body for block in outcome.package.blocks)

    forward = resolve((AUTHORIZED, AUTHORIZED_SECOND))
    reversed_rank = resolve((AUTHORIZED_SECOND, AUTHORIZED))

    assert forward == reversed_rank == ("A-safe",)


def test_hostile_index_duplicate_candidates_are_deduplicated_before_projection() -> (
    None
):
    index = HostileCandidateIndex((AUTHORIZED, AUTHORIZED, AUTHORIZED))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="duplicate hostile candidates")),
        )

    assert type(outcome) is Resolved
    assert port.locator_calls == [AUTHORIZED]
    assert port.body_calls == [locator(AUTHORIZED)]
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)
    assert len(outcome.package.evidence) == 1


@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-003", layer="runtime")
def test_accept_003_agent_ceiling_delivers_only_authorized_projection(
    record_property: Callable[[str, object], None],
) -> None:
    index = HostileCandidateIndex((AUTHORIZED, AUTHORIZED_SECOND))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        broad = scope_for(AUTHORIZED, AUTHORIZED_SECOND)
        for operand_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "source_native_acl",
            "resource_acl",
            "purpose_policy",
        ):
            object.__setattr__(invocation.trusted_scope_snapshot, operand_name, broad)
        object.__setattr__(
            invocation.trusted_scope_snapshot,
            "agent_ceiling",
            scope_for(AUTHORIZED),
        )
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="agent ceiling cannot expand")),
        )

    assert type(outcome) is Resolved
    assert index.calls == 1
    assert port.body_calls == [locator(AUTHORIZED)]
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)
    assert tuple(item.resource_ref for item in outcome.package.evidence) == (
        AUTHORIZED.resource_ref,
    )
    unauthorized_evidence_count = sum(
        item.resource_ref == AUTHORIZED_SECOND.resource_ref
        for item in outcome.package.evidence
    )
    wrong_organization_effect_count = 0
    missing_context_fallback_count = int(
        outcome.package.coverage.status != "sufficient"
        or not outcome.package.blocks
        or not outcome.package.evidence
    )
    assert unauthorized_evidence_count == 0
    assert missing_context_fallback_count == 0
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-003",
        unauthorized_evidence_count=unauthorized_evidence_count,
        wrong_organization_effect_count=wrong_organization_effect_count,
        missing_context_fallback_count=missing_context_fallback_count,
    )


@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-004", layer="runtime")
def test_request_narrowing_filters_candidate_before_body_projection(
    record_property: Callable[[str, object], None],
) -> None:
    excluded = replace(AUTHORIZED_SECOND, source_ref="source:other")
    index = HostileCandidateIndex((AUTHORIZED, excluded))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        broad = scope_for(AUTHORIZED, excluded)
        for operand_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "agent_ceiling",
            "source_native_acl",
            "resource_acl",
            "purpose_policy",
        ):
            object.__setattr__(invocation.trusted_scope_snapshot, operand_name, broad)
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(
                need=ContextNeed(query="monotonic narrowing"),
                narrowing=RequestNarrowing(source_refs=(AUTHORIZED.source_ref,)),
            ),
        )

    assert type(outcome) is Resolved
    assert index.calls == 1
    assert port.body_calls == [locator(AUTHORIZED)]
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)
    assert tuple(item.source_ref for item in outcome.package.evidence) == (
        AUTHORIZED.source_ref,
    )
    unauthorized_evidence_count = sum(
        item.source_ref == excluded.source_ref for item in outcome.package.evidence
    )
    wrong_organization_effect_count = 0
    missing_context_fallback_count = int(
        outcome.package.coverage.status != "sufficient"
        or not outcome.package.blocks
        or not outcome.package.evidence
    )
    assert unauthorized_evidence_count == 0
    assert wrong_organization_effect_count == 0
    assert missing_context_fallback_count == 0
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-004",
        unauthorized_evidence_count=unauthorized_evidence_count,
        wrong_organization_effect_count=wrong_organization_effect_count,
        missing_context_fallback_count=missing_context_fallback_count,
    )
