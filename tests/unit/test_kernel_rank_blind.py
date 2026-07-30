from __future__ import annotations

import inspect
from dataclasses import fields
from typing import cast

from engine.runtime.candidate_ranking import CandidateRankEvidence, RankerEvidence
from engine.runtime.construction import (
    DEFAULT_SERVER_PACKAGE_BUDGET,
    AuthorizationDecision,
    AuthorizationKernel,
    PreparedAcquireAuthorization,
    SealedPackageSelection,
    _close_authorization_decision,
    _construct_authorization_kernel_and_selector,
    _OpaqueReferenceIssuer,
    required_kernel_dependencies,
)
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, ContextNeed
from engine.runtime.evidence import CandidateRef
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    AUTHORIZED_SECOND,
    RecordingMaterializedPort,
    trusted_operands,
)


def _rank(candidate: object, position: int) -> CandidateRankEvidence:
    return CandidateRankEvidence(
        candidate_ref=cast("CandidateRef", candidate),
        per_ranker=(
            RankerEvidence(
                ranker_ref="lexical",
                position=position,
                score=float(10 - position),
            ),
        ),
        fused_rank=position,
    )


def _candidate_key(candidate: CandidateRef) -> tuple[str, ...]:
    return (
        str(candidate.organization_id),
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    )


def _decision_bytes(decision: AuthorizationDecision) -> bytes:
    projections = tuple(
        (
            _candidate_key(projection.candidate_ref),
            projection.projected_body,
            projection.projected_field_refs,
            projection.lineage.run_ref,
            projection.lineage.principal_ref,
            projection.lineage.purpose,
            projection.lineage.as_of.isoformat(timespec="microseconds"),
            projection.lineage.decision_ref,
            projection.lineage.policy_snapshot_ref,
            projection.lineage.policy_epoch,
            projection.lineage.source_acl_decision_ref,
        )
        for projection in decision.projections
    )
    return repr(
        (
            decision.effective_budget,
            decision.policy_receipt.request_id,
            decision.policy_receipt.purpose,
            decision.policy_receipt.policy_epoch,
            decision.policy_receipt.effective_scope.digest,
            decision.provenance_receipt,
            projections,
        )
    ).encode()


def test_kernel_accepts_sorted_refs_but_no_rank_or_discovery_dependency() -> None:
    signature = inspect.signature(AuthorizationKernel.authorize_acquire)
    hints = inspect.get_annotations(AuthorizationKernel.authorize_acquire)

    assert tuple(signature.parameters) == (
        "self",
        "invocation",
        "preparation",
        "candidate_refs",
        "projection_session",
    )
    assert hints["candidate_refs"] == "tuple[CandidateRef, ...]"
    assert all("rank" not in name.lower() for name in signature.parameters)
    assert all(
        value not in {CandidateRankEvidence, CandidateIndex}
        for value in hints.values()
    )
    for method_name, method in inspect.getmembers(
        AuthorizationKernel,
        predicate=inspect.isfunction,
    ):
        assert "rank" not in method_name.lower()
        method_hints = inspect.get_annotations(method)
        assert all(
            "rank" not in name.lower() and "rank" not in str(annotation).lower()
            for name, annotation in method_hints.items()
        )
    assert all(
        "rank" not in item.name.lower()
        for item in fields(SealedPackageSelection)
    )


def test_permuting_rank_evidence_cannot_change_kernel_decision() -> None:
    kernel, _selector = _construct_authorization_kernel_and_selector(
        required_kernel_dependencies()
    )
    port = RecordingMaterializedPort()
    request = Acquire(need=ContextNeed(query="rank-blind authorization"))

    with trusted_operands(port) as (invocation, delivery):
        preparation = kernel.prepare_acquire(
            invocation,
            delivery,
            request,
            server_budget=DEFAULT_SERVER_PACKAGE_BUDGET,
            as_of=AS_OF,
            reference_issuer=_OpaqueReferenceIssuer(),
        )
        assert type(preparation) is PreparedAcquireAuthorization
        rank_permutations = (
            (_rank(AUTHORIZED, 1), _rank(AUTHORIZED_SECOND, 2)),
            (_rank(AUTHORIZED_SECOND, 1), _rank(AUTHORIZED, 2)),
        )
        assert rank_permutations[0] != rank_permutations[1]
        decisions = []
        snapshots = []
        for rank_evidence in rank_permutations:
            sorted_refs = tuple(
                sorted(
                    (evidence.candidate_ref for evidence in rank_evidence),
                    key=_candidate_key,
                )
            )
            decision = kernel.authorize_acquire(
                invocation,
                preparation,
                sorted_refs,
                projection_session=(
                    invocation.user_actor.materialized_projection_session
                ),
            )
            decisions.append(decision)
            snapshots.append(_decision_bytes(decision))

        assert decisions[0] is not decisions[1]
        assert snapshots[0] == snapshots[1]
        assert tuple(
            item.candidate_ref for item in decisions[0].projections
        ) == (AUTHORIZED,)
        for decision in decisions:
            _close_authorization_decision(decision)

    assert port.locator_calls == [
        AUTHORIZED,
        AUTHORIZED_SECOND,
        AUTHORIZED,
        AUTHORIZED_SECOND,
    ]
