"""Rank evidence rejoin strictly after exact authorization and projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from typing import Final

from engine.runtime.budget import PackageBudget
from engine.runtime.candidate_ranking import CandidateRankEvidence, RankerEvidence
from engine.runtime.evidence import (
    AuthorizedProjection,
    CandidateRef,
    _candidate_sort_key,
    _require_active_authorized_projection,
)

__all__ = [
    "NEUTRAL_FUSED_RANK",
    "UNIFORM_RANKER_WEIGHT",
    "AuthorizedRerankItem",
    "join_authorized_ranking",
    "select_authorized_ranking",
]

NEUTRAL_FUSED_RANK: Final = 1
UNIFORM_RANKER_WEIGHT: Final = 1.0


def _authorized_ranker_weights(
    ranker_weights: dict[str, float] | None,
    admitted_ranker_refs: frozenset[str],
) -> dict[str, float]:
    """Resolve one server-owned weight per admitted ranker; never caller-supplied.

    Weights may cover rankers that admitted nothing, so containment rather than
    exact identity equality is required.
    """

    if ranker_weights is None:
        return dict.fromkeys(admitted_ranker_refs, UNIFORM_RANKER_WEIGHT)
    if type(ranker_weights) is not dict or not admitted_ranker_refs.issubset(
        ranker_weights
    ):
        raise ValueError("authorized fusion weights must cover every admitted ranker")
    if any(
        type(weight) not in {int, float}
        or type(weight) is bool
        or not isfinite(weight)
        or weight <= 0.0
        for weight in ranker_weights.values()
    ):
        raise ValueError("authorized fusion weights must be positive finite floats")
    return {
        ranker_ref: float(ranker_weights[ranker_ref])
        for ranker_ref in admitted_ranker_refs
    }


@dataclass(frozen=True, slots=True)
class RankerWeights:
    """Tracked server-owned retrieval weights, never request input."""

    values: dict[str, float] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.values is None:
            return
        if type(self.values) is not dict or not self.values:
            raise ValueError("ranker weights require a nonempty exact mapping")
        validated = _authorized_ranker_weights(self.values, frozenset(self.values))
        object.__setattr__(self, "values", validated)


UNIFORM_RANKER_WEIGHTS: Final = RankerWeights()
HYBRID_RANKER_WEIGHTS: Final = RankerWeights({"fts": 1.0, "vector": 1.0})


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedRerankItem:
    """Content-bearing relevance input constructible only from a projection."""

    projection: AuthorizedProjection = field(repr=False)
    rank_evidence: CandidateRankEvidence | None = field(repr=False)
    fused_rank: int

    def __init__(
        self,
        projection: AuthorizedProjection,
        rank_evidence: CandidateRankEvidence | None = None,
    ) -> None:
        if type(projection) is not AuthorizedProjection:
            raise TypeError("AuthorizedRerankItem requires AuthorizedProjection")
        _require_active_authorized_projection(projection)
        if rank_evidence is not None:
            if type(rank_evidence) is not CandidateRankEvidence:
                raise TypeError("rerank item evidence has the wrong nominal type")
            if rank_evidence.candidate_ref != projection.candidate_ref:
                raise ValueError("rerank evidence must match the exact CandidateRef")
        object.__setattr__(self, "projection", projection)
        object.__setattr__(self, "rank_evidence", rank_evidence)
        object.__setattr__(
            self,
            "fused_rank",
            (
                rank_evidence.fused_rank
                if rank_evidence is not None
                else NEUTRAL_FUSED_RANK
            ),
        )


def join_authorized_ranking(
    projections: tuple[AuthorizedProjection, ...],
    rank_evidence: tuple[CandidateRankEvidence, ...],
    *,
    ranker_weights: dict[str, float] | None = None,
) -> tuple[AuthorizedRerankItem, ...]:
    """Join only admitted projections; evidence without one is discarded.

    Retrieval fusion weighting belongs here and nowhere earlier: only this stage
    can see rank positions recomputed over admitted candidates alone. Omitted
    weights fuse uniformly.
    """

    if type(projections) is not tuple or any(
        type(projection) is not AuthorizedProjection for projection in projections
    ):
        raise TypeError("authorized ranking requires exact projections")
    if type(rank_evidence) is not tuple or any(
        type(evidence) is not CandidateRankEvidence for evidence in rank_evidence
    ):
        raise TypeError("authorized ranking requires exact rank evidence")
    evidence_by_ref = {evidence.candidate_ref: evidence for evidence in rank_evidence}
    if len(evidence_by_ref) != len(rank_evidence):
        raise ValueError("rank evidence requires unique exact CandidateRef values")
    admitted_refs = {projection.candidate_ref for projection in projections}
    admitted_evidence = {
        candidate_ref: evidence
        for candidate_ref, evidence in evidence_by_ref.items()
        if candidate_ref in admitted_refs
    }
    compacted_positions: dict[tuple[CandidateRef, str], int] = {}
    by_ranker: dict[str, list[tuple[CandidateRef, int]]] = defaultdict(list)
    for admitted in admitted_evidence.values():
        for ranker in admitted.per_ranker:
            by_ranker[ranker.ranker_ref].append(
                (admitted.candidate_ref, ranker.position)
            )
    for ranker_ref, entries in by_ranker.items():
        for compacted, (candidate_ref, _position) in enumerate(
            sorted(
                entries,
                key=lambda value: (value[1], _candidate_sort_key(value[0])),
            ),
            start=1,
        ):
            compacted_positions[(candidate_ref, ranker_ref)] = compacted

    weights = _authorized_ranker_weights(ranker_weights, frozenset(by_ranker))
    normalized_evidence: dict[CandidateRef, CandidateRankEvidence] = {}
    fused_scores: dict[CandidateRef, float] = {}
    for candidate_ref, admitted in admitted_evidence.items():
        fused_scores[candidate_ref] = sum(
            weights[ranker.ranker_ref]
            / compacted_positions[(candidate_ref, ranker.ranker_ref)]
            for ranker in admitted.per_ranker
        )
    ranked_refs = tuple(
        sorted(
            fused_scores,
            key=lambda candidate_ref: (
                -fused_scores[candidate_ref],
                _candidate_sort_key(candidate_ref),
            ),
        )
    )
    authorized_rank = {
        candidate_ref: 2 * rank - 1
        for rank, candidate_ref in enumerate(ranked_refs, start=1)
    }
    for candidate_ref, admitted in admitted_evidence.items():
        normalized_evidence[candidate_ref] = CandidateRankEvidence(
            candidate_ref=candidate_ref,
            per_ranker=tuple(
                RankerEvidence(
                    ranker_ref=ranker.ranker_ref,
                    position=compacted_positions[(candidate_ref, ranker.ranker_ref)],
                    score=ranker.score,
                )
                for ranker in admitted.per_ranker
            ),
            fused_rank=authorized_rank[candidate_ref],
        )
    neutral_rank = (
        (min(authorized_rank.values()) + max(authorized_rank.values())) // 2
        if authorized_rank
        else NEUTRAL_FUSED_RANK
    )
    joined = []
    for projection in projections:
        evidence = normalized_evidence.get(projection.candidate_ref)
        item = AuthorizedRerankItem(projection, evidence)
        object.__setattr__(
            item,
            "fused_rank",
            authorized_rank.get(projection.candidate_ref, neutral_rank),
        )
        joined.append(item)
    return tuple(joined)


def select_authorized_ranking(
    joined: tuple[AuthorizedRerankItem, ...],
    budget: PackageBudget,
) -> tuple[AuthorizedRerankItem, ...]:
    """Order admitted content, then pack only that authorized order to budget."""

    if type(joined) is not tuple or any(
        type(item) is not AuthorizedRerankItem for item in joined
    ):
        raise TypeError("authorized selection requires rerank items")
    if type(budget) is not PackageBudget:
        raise TypeError("authorized selection requires PackageBudget")
    ranked_values = tuple(
        item.fused_rank for item in joined if item.rank_evidence is not None
    )
    neutral_rank = (
        (min(ranked_values) + max(ranked_values)) // 2
        if ranked_values
        else NEUTRAL_FUSED_RANK
    )
    for item in joined:
        if item.rank_evidence is None:
            object.__setattr__(item, "fused_rank", neutral_rank)
    ordered = sorted(
        joined,
        key=lambda item: (
            item.fused_rank,
            _candidate_sort_key(item.projection.candidate_ref),
        ),
    )
    selected = []
    consumed_tokens = 0
    for item in ordered:
        body_tokens = len(item.projection.projected_body.encode("utf-8"))
        if consumed_tokens + body_tokens > budget.max_tokens:
            continue
        selected.append(item)
        consumed_tokens += body_tokens
    return tuple(selected)
