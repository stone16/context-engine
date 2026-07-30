from __future__ import annotations

from uuid import UUID

from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.prekernel_fusion import fuse_candidate_evidence


def _candidate(label: str) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:rrf",
        resource_ref=f"resource:{label}",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref=f"fragment:{label}",
    )


def _query(
    lexical: tuple[CandidateRef, ...],
    vector: tuple[CandidateRef, ...],
) -> CandidateQuery:
    return CandidateQuery(
        ranked_lists=(
            RankedCandidateList(
                ranker_ref="lexical",
                candidates=tuple(RankedCandidate(item) for item in lexical),
            ),
            RankedCandidateList(
                ranker_ref="vector",
                candidates=tuple(RankedCandidate(item) for item in vector),
            ),
        )
    )


def test_rrf_fusion_matches_worked_fixture_and_has_total_tie_break() -> None:
    both = _candidate("both")
    lexical_only = _candidate("lexical-only")
    vector_only = _candidate("vector-only")
    canonical_first = _candidate("a-tied")
    canonical_last = _candidate("z-tied")

    first = fuse_candidate_evidence(
        _query(
            (lexical_only, both, canonical_last),
            (vector_only, both, canonical_first),
        )
    )
    permuted = fuse_candidate_evidence(
        _query(
            (lexical_only, both, canonical_first),
            (vector_only, both, canonical_last),
        )
    )

    # Worked reciprocal-rank totals (uniform provisional carriage):
    # both=1/2+1/2, lexical-only=1, vector-only=1, each tied item=1/3.
    assert first.candidate_refs == (
        both,
        lexical_only,
        vector_only,
        canonical_first,
        canonical_last,
    )
    assert permuted.candidate_refs == first.candidate_refs
    assert tuple(item.fused_rank for item in first.rank_evidence) == (1, 2, 3, 4, 5)


def test_rrf_dedupes_by_exact_candidate_ref_and_merges_rank_evidence() -> None:
    shared = _candidate("shared")
    same_fragment_other_article = CandidateRef(
        organization_id=shared.organization_id,
        source_ref=shared.source_ref,
        resource_ref="resource:other-article",
        revision_ref=shared.revision_ref,
        fragment_ref=shared.fragment_ref,
    )

    fused = fuse_candidate_evidence(
        _query(
            (shared, shared),
            (same_fragment_other_article, shared),
        )
    )

    assert fused.candidate_refs == (shared, same_fragment_other_article)
    assert len(fused.rank_evidence) == 2
    assert tuple(item.ranker_ref for item in fused.rank_evidence[0].per_ranker) == (
        "lexical",
        "vector",
    )
    assert tuple(item.ranker_ref for item in fused.rank_evidence[1].per_ranker) == (
        "vector",
    )
