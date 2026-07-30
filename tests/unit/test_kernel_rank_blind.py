from __future__ import annotations

import inspect
from collections import deque
from collections.abc import Iterator
from dataclasses import fields
from types import CodeType, FunctionType, MethodType
from typing import Any, cast

from engine.runtime.authorized_ranking import (
    AuthorizedRerankItem,
    join_authorized_ranking,
    select_authorized_ranking,
)
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    CandidateRankEvidence,
    FusedCandidates,
    RankedCandidate,
    RankedCandidateList,
    RankerEvidence,
)
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
from engine.runtime.prekernel_fusion import fuse_candidate_evidence
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    AUTHORIZED_SECOND,
    RecordingMaterializedPort,
    trusted_operands,
)

RANK_TYPES = (
    AuthorizedRerankItem,
    CandidateQuery,
    CandidateRankEvidence,
    FusedCandidates,
    RankedCandidate,
    RankedCandidateList,
    RankerEvidence,
)
RANK_CALLABLES = (
    join_authorized_ranking,
    select_authorized_ranking,
    fuse_candidate_evidence,
)


def _is_rank_channel(value: object) -> bool:
    if any(value is rank_callable for rank_callable in RANK_CALLABLES):
        return True
    if isinstance(value, type):
        return value in RANK_TYPES
    return isinstance(value, RANK_TYPES)


def _referenced_globals(function: FunctionType) -> Iterator[object]:
    code_objects = [function.__code__]
    while code_objects:
        code = code_objects.pop()
        code_objects.extend(
            constant for constant in code.co_consts if isinstance(constant, CodeType)
        )
        for name in code.co_names:
            if name in function.__globals__:
                yield function.__globals__[name]


def _reachable_rank_channels(root: object) -> set[str]:
    """Report rank reachable by attribute, closure, or module global.

    Signature inspection cannot see any of these three channels, so a rank-blind
    claim that rests on signatures alone is unverified.
    """

    findings: set[str] = set()
    seen: set[int] = set()
    queue: deque[tuple[str, Any]] = deque(
        [("attribute", root)]
        + [
            ("attribute", member)
            for _name, member in inspect.getmembers(type(root), inspect.isfunction)
        ]
    )
    while queue:
        channel, value = queue.popleft()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if value is not root and _is_rank_channel(value):
            findings.add(channel)
            continue
        if isinstance(value, MethodType):
            queue.append((channel, value.__func__))
            continue
        if isinstance(value, FunctionType):
            for cell in value.__closure__ or ():
                try:
                    queue.append(("closure", cell.cell_contents))
                except ValueError:
                    continue
            queue.extend(("global", item) for item in _referenced_globals(value))
            continue
        if isinstance(value, dict):
            queue.extend((channel, item) for item in value.values())
            continue
        if isinstance(value, tuple | list | set | frozenset):
            queue.extend((channel, item) for item in value)
            continue
        slots = getattr(type(value), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            try:
                queue.append(("attribute", getattr(value, slot)))
            except AttributeError:
                continue
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict):
            queue.extend(("attribute", item) for item in attributes.values())
    return findings


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


_PLANTED_RANK_EVIDENCE = _rank(AUTHORIZED, 1)


def _read_planted_global() -> CandidateRankEvidence:
    return _PLANTED_RANK_EVIDENCE


class _PlantedAttributeKernel:
    __slots__ = ("_rank_hint",)

    def __init__(self, evidence: CandidateRankEvidence) -> None:
        self._rank_hint = evidence


class _PlantedGlobalKernel:
    def authorize(self) -> CandidateRankEvidence:
        return _read_planted_global()


class _PlantedClosureKernel:
    pass


def _planted_closure_kernel(evidence: CandidateRankEvidence) -> _PlantedClosureKernel:
    def authorize() -> CandidateRankEvidence:
        return evidence

    kernel = _PlantedClosureKernel()
    kernel.authorize = authorize  # type: ignore[attr-defined]
    return kernel


def test_rank_channel_oracle_detects_non_signature_channels() -> None:
    """None of these three channels appears in any signature or annotation."""

    evidence = _rank(AUTHORIZED_SECOND, 2)

    assert _reachable_rank_channels(_PlantedAttributeKernel(evidence)) == {"attribute"}
    assert _reachable_rank_channels(_planted_closure_kernel(evidence)) == {"closure"}
    assert _reachable_rank_channels(_PlantedGlobalKernel()) == {"global"}
    for planted in (
        _PlantedAttributeKernel(evidence),
        _planted_closure_kernel(evidence),
        _PlantedGlobalKernel(),
    ):
        assert all(
            "rank" not in name.lower()
            for method_name, method in inspect.getmembers(
                type(planted),
                predicate=inspect.isfunction,
            )
            for name in inspect.get_annotations(method)
            if method_name
        )


def test_kernel_carries_no_rank_channel_while_the_selector_does() -> None:
    kernel, selector = _construct_authorization_kernel_and_selector(
        required_kernel_dependencies()
    )

    assert _reachable_rank_channels(kernel) == set()
    assert _reachable_rank_channels(selector) == {"global"}
    assert all(
        not hasattr(getattr(kernel, slot, None), "__dict__")
        for slot in type(kernel).__slots__
        if slot != "__weakref__"
    )


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
        value not in {CandidateRankEvidence, CandidateIndex} for value in hints.values()
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
        "rank" not in item.name.lower() for item in fields(SealedPackageSelection)
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
        assert tuple(item.candidate_ref for item in decisions[0].projections) == (
            AUTHORIZED,
        )
        for decision in decisions:
            _close_authorization_decision(decision)

    assert port.locator_calls == [
        AUTHORIZED,
        AUTHORIZED_SECOND,
        AUTHORIZED,
        AUTHORIZED_SECOND,
    ]
