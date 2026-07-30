"""Generic weighted fusion and exact-ref dedupe before authorization."""

from math import isfinite

from engine.runtime.candidate_ranking import (
    CandidateQuery,
    CandidateRankEvidence,
    FusedCandidates,
    RankerEvidence,
)
from engine.runtime.evidence import CandidateRef


def weighted_fuse_candidates(
    query: CandidateQuery,
    *,
    ranker_weights: dict[str, float],
) -> FusedCandidates:
    """Fuse opaque refs using caller-owned weights and reciprocal positions."""

    if type(query) is not CandidateQuery:
        raise TypeError("fusion requires CandidateQuery")
    expected_rankers = {ranked_list.ranker_ref for ranked_list in query.ranked_lists}
    if type(ranker_weights) is not dict or set(ranker_weights) != expected_rankers:
        raise ValueError("fusion weights must cover the exact ranker identities")
    if any(
        type(weight) not in {int, float}
        or type(weight) is bool
        or not isfinite(weight)
        or weight <= 0.0
        for weight in ranker_weights.values()
    ):
        raise ValueError("fusion weights must be positive finite floats")

    accumulated: dict[CandidateRef, float] = {}
    evidence_by_ref: dict[CandidateRef, list[RankerEvidence]] = {}
    first_seen: dict[CandidateRef, int] = {}
    next_seen = 0
    for ranked_list in query.ranked_lists:
        weight = ranker_weights[ranked_list.ranker_ref]
        for position, ranked in enumerate(ranked_list.candidates, start=1):
            candidate_ref = ranked.candidate_ref
            existing_rankers = {
                item.ranker_ref for item in evidence_by_ref.get(candidate_ref, ())
            }
            if ranked_list.ranker_ref in existing_rankers:
                continue
            if candidate_ref not in first_seen:
                first_seen[candidate_ref] = next_seen
                next_seen += 1
            accumulated[candidate_ref] = accumulated.get(candidate_ref, 0.0) + (
                weight / position
            )
            evidence_by_ref.setdefault(candidate_ref, []).append(
                RankerEvidence(
                    ranker_ref=ranked_list.ranker_ref,
                    position=position,
                    score=ranked.score,
                )
            )
    ordered = tuple(
        sorted(
            accumulated,
            key=lambda candidate_ref: (
                -accumulated[candidate_ref],
                first_seen[candidate_ref],
            ),
        )
    )
    rank_evidence = tuple(
        CandidateRankEvidence(
            candidate_ref=candidate_ref,
            per_ranker=tuple(evidence_by_ref[candidate_ref]),
            fused_rank=fused_rank,
        )
        for fused_rank, candidate_ref in enumerate(ordered, start=1)
    )
    return FusedCandidates(candidate_refs=ordered, rank_evidence=rank_evidence)
