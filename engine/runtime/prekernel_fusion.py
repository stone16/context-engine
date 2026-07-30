"""Content-free reciprocal-rank evidence carriage before authorization."""

from engine.runtime.candidate_ranking import (
    CandidateQuery,
    CandidateRankEvidence,
    FusedCandidates,
    RankerEvidence,
)
from engine.runtime.evidence import CandidateRef, _candidate_sort_key


def fuse_candidate_evidence(query: CandidateQuery) -> FusedCandidates:
    """Deduplicate exact refs and carry uniform provisional RRF evidence.

    The provisional fused rank is inert. Server-owned weights are applied only
    after authorization, where positions are compacted over admitted candidates.
    """

    if type(query) is not CandidateQuery:
        raise TypeError("fusion requires CandidateQuery")
    query.__post_init__()
    reciprocal_scores: dict[CandidateRef, float] = {}
    evidence_by_ref: dict[CandidateRef, list[RankerEvidence]] = {}
    for ranked_list in query.ranked_lists:
        ranked_list.__post_init__()
        seen_by_ranker: set[CandidateRef] = set()
        for position, ranked in enumerate(ranked_list.candidates, start=1):
            ranked.__post_init__()
            candidate_ref = ranked.candidate_ref
            if candidate_ref in seen_by_ranker:
                continue
            seen_by_ranker.add(candidate_ref)
            reciprocal_scores[candidate_ref] = (
                reciprocal_scores.get(candidate_ref, 0.0) + 1.0 / position
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
            reciprocal_scores,
            key=lambda candidate_ref: (
                -reciprocal_scores[candidate_ref],
                _candidate_sort_key(candidate_ref),
            ),
        )
    )
    return FusedCandidates(
        candidate_refs=ordered,
        rank_evidence=tuple(
            CandidateRankEvidence(
                candidate_ref=candidate_ref,
                per_ranker=tuple(evidence_by_ref[candidate_ref]),
                fused_rank=fused_rank,
            )
            for fused_rank, candidate_ref in enumerate(ordered, start=1)
        ),
    )
