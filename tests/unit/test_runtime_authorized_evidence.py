from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, ContextNeed, RequestNarrowing
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
    MaterializedFragmentLocator,
    MaterializedProjectionPort,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.scope import ScopeSet, ScopeTarget, TrustedScopeOperands
from engine.runtime.scope_authority import (
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)

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

    def discover(self, request: Acquire) -> tuple[CandidateRef, ...]:
        del request
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

    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(candidate_ref)
        return locator(candidate_ref)

    def project_body(
        self,
        selected_locator: MaterializedFragmentLocator,
    ) -> str | None:
        self.body_calls.append(selected_locator)
        for candidate, body in self.body_by_candidate.items():
            if selected_locator == locator(candidate):
                return body
        return None


class MismatchedLocatorPort(RecordingMaterializedPort):
    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None:
        self.locator_calls.append(candidate_ref)
        return locator(AUTHORIZED)


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


@contextmanager
def trusted_operands(
    port: RecordingMaterializedPort,
) -> Iterator[tuple[AuthenticatedInvocation, TrustedDeliveryContext]]:
    membership_scope = _open_membership_authority_scope()
    materialized_scope = _open_materialized_projection_scope()
    scope_authority_scope = _open_scope_authority_scope()
    try:
        projection_session = _construct_materialized_projection_session(
            authority_scope=materialized_scope,
            port=cast(MaterializedProjectionPort, port),
        )
        organization_verification = (
            _construct_existing_http_organization_verification(
                organization_id=ORGANIZATION_ID,
                request_id="request-authorized-evidence",
                authentication_binding_ref="binding-authorized-evidence",
                verified_at=AS_OF,
            )
        )
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
            materialized_projection_session=projection_session,
        )
        operands = exact_operands()
        scope_snapshot = _construct_trusted_scope_snapshot(
            authority_scope=scope_authority_scope,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=7,
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
        _close_scope_authority_scope(scope_authority_scope)
        _close_materialized_projection_scope(materialized_scope)
        _close_membership_authority_scope(membership_scope)


@pytest.mark.parametrize(
    "ranked",
    tuple(permutations((AUTHORIZED, DENIED, CROSS_ORGANIZATION))),
)
def test_hostile_candidate_order_delivers_only_exact_authorized_evidence(
    ranked: tuple[CandidateRef, ...],
) -> None:
    index = HostileCandidateIndex(ranked)
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="hostile index")),
        )

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
    assert package.evidence[0].lineage.policy_epoch > 0
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


def test_explicit_candidate_index_requires_same_transaction_projection_session(
) -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
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


def test_runtime_rejects_authority_locator_that_does_not_match_candidate_exactly(
) -> None:
    index = HostileCandidateIndex((DENIED,))
    port = MismatchedLocatorPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="mismatched authoritative locator")),
        )

    assert outcome.package.evidence == ()
    assert outcome.package.blocks == ()
    assert outcome.package.coverage.status == "empty"
    assert port.body_calls == []


def test_empty_effective_scope_performs_zero_candidate_or_body_io() -> None:
    index = HostileCandidateIndex((AUTHORIZED, DENIED, CROSS_ORGANIZATION))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
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

        return tuple(block.body for block in outcome.package.blocks)

    forward = resolve((AUTHORIZED, AUTHORIZED_SECOND))
    reversed_rank = resolve((AUTHORIZED_SECOND, AUTHORIZED))

    assert forward == reversed_rank == ("A-safe",)


def test_hostile_index_duplicate_candidates_are_deduplicated_before_projection(
) -> None:
    index = HostileCandidateIndex((AUTHORIZED, AUTHORIZED, AUTHORIZED))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="duplicate hostile candidates")),
        )

    assert port.locator_calls == [AUTHORIZED]
    assert port.body_calls == [locator(AUTHORIZED)]
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)
    assert len(outcome.package.evidence) == 1


def test_agent_ceiling_denial_keeps_candidate_content_out_of_package() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
    )

    with trusted_operands(port) as (invocation, delivery):
        object.__setattr__(
            invocation.trusted_scope_snapshot,
            "agent_ceiling",
            ScopeSet(frozenset()),
        )
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="agent ceiling")),
        )

    assert outcome.package.evidence == ()
    assert index.calls == 0
    assert port.body_calls == []


def test_request_narrowing_filters_candidate_before_body_projection() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = RecordingMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(
                need=ContextNeed(query="monotonic narrowing"),
                narrowing=RequestNarrowing(source_refs=("source:other",)),
            ),
        )

    assert outcome.package.evidence == ()
    assert index.calls == 0
    assert port.body_calls == []
