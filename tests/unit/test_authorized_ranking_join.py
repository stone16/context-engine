from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from engine.runtime.authorized_ranking import (
    NEUTRAL_FUSED_RANK,
    AuthorizedRerankItem,
    join_authorized_ranking,
    select_authorized_ranking,
)
from engine.runtime.budget import PackageBudget
from engine.runtime.candidate_ranking import CandidateRankEvidence, RankerEvidence
from engine.runtime.evidence import (
    AuthorizedProjection,
    CandidateRef,
    EvidenceLineage,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
    construct_package_content,
)


def _candidate(label: str) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:authorized-ranking",
        resource_ref=f"resource:{label}",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref=f"fragment:{label}",
    )


@contextmanager
def _projections(
    *candidates: CandidateRef,
) -> Iterator[tuple[AuthorizedProjection, ...]]:
    scope = _open_authorization_kernel_scope()
    try:
        yield tuple(
            _construct_authorized_projection(
                kernel_scope=scope,
                candidate_ref=candidate,
                body=f"safe body {ordinal}",
                projected_field_refs=("body",),
                lineage=EvidenceLineage(
                    run_ref="run:authorized-ranking",
                    principal_ref="principal:authorized-ranking",
                    purpose="context.answer",
                    as_of=datetime(2026, 7, 29, tzinfo=UTC),
                    decision_ref="decision:authorized-ranking",
                    policy_snapshot_ref="policy:authorized-ranking",
                    policy_epoch=1,
                    source_acl_decision_ref="sourceacl:authorized-ranking",
                ),
            )
            for ordinal, candidate in enumerate(candidates)
        )
    finally:
        _close_authorization_kernel_scope(scope)


def _rank(candidate: CandidateRef, fused_rank: int) -> CandidateRankEvidence:
    return CandidateRankEvidence(
        candidate_ref=candidate,
        per_ranker=(
            RankerEvidence(
                ranker_ref="lexical",
                position=fused_rank,
                score=float(10 - fused_rank),
            ),
        ),
        fused_rank=fused_rank,
    )


def test_join_uses_exact_ref_discards_refused_and_assigns_neutral_rank() -> None:
    allowed = _candidate("allowed")
    allowed_missing_rank = _candidate("allowed-neutral")
    refused = _candidate("refused")
    consumer_calls: list[AuthorizedRerankItem] = []

    with _projections(allowed, allowed_missing_rank) as projections:
        joined = join_authorized_ranking(
            projections,
            (_rank(refused, 1), _rank(allowed, 2)),
        )
        consumer_calls.extend(joined)

        assert tuple(item.projection.candidate_ref for item in joined) == (
            allowed,
            allowed_missing_rank,
        )
        assert tuple(item.fused_rank for item in joined) == (1, 1)
        assert joined[1].rank_evidence is None
        assert all(
            item.projection.candidate_ref != refused for item in consumer_calls
        )
        assert "refused" not in repr(joined)


def test_join_refuses_mismatched_or_duplicate_exact_ref_evidence() -> None:
    allowed = _candidate("allowed")
    same_fragment_other_article = CandidateRef(
        organization_id=allowed.organization_id,
        source_ref=allowed.source_ref,
        resource_ref="resource:different",
        revision_ref=allowed.revision_ref,
        fragment_ref=allowed.fragment_ref,
    )
    with _projections(allowed) as projections:
        joined = join_authorized_ranking(
            projections,
            (_rank(same_fragment_other_article, 1),),
        )
        assert joined[0].rank_evidence is None
        assert joined[0].fused_rank == NEUTRAL_FUSED_RANK


def test_refused_candidate_positions_cannot_change_authorized_selection() -> None:
    allowed_a = _candidate("allowed-a")
    allowed_b = _candidate("allowed-b")
    refused = _candidate("refused")
    budget = PackageBudget(
        max_tokens=len(b"safe body 0"),
        max_provider_calls=1,
        max_cost_microunits=1,
        max_elapsed_ms=1,
    )
    without_refused = (
        CandidateRankEvidence(
            candidate_ref=allowed_a,
            per_ranker=(
                RankerEvidence("one", 1),
                RankerEvidence("two", 2),
            ),
            fused_rank=1,
        ),
        CandidateRankEvidence(
            candidate_ref=allowed_b,
            per_ranker=(RankerEvidence("two", 1),),
            fused_rank=2,
        ),
    )
    with_refused = (
        CandidateRankEvidence(
            candidate_ref=refused,
            per_ranker=(
                RankerEvidence("one", 1),
                RankerEvidence("two", 2),
            ),
            fused_rank=1,
        ),
        CandidateRankEvidence(
            candidate_ref=allowed_b,
            per_ranker=(RankerEvidence("two", 1),),
            fused_rank=2,
        ),
        CandidateRankEvidence(
            candidate_ref=allowed_a,
            per_ranker=(
                RankerEvidence("one", 2),
                RankerEvidence("two", 3),
            ),
            fused_rank=3,
        ),
    )

    with _projections(allowed_a, allowed_b) as projections:
        selected_without = select_authorized_ranking(
            join_authorized_ranking(projections, without_refused),
            budget,
        )
        selected_with = select_authorized_ranking(
            join_authorized_ranking(projections, with_refused),
            budget,
        )

        assert tuple(
            item.projection.candidate_ref for item in selected_without
        ) == tuple(item.projection.candidate_ref for item in selected_with)


def test_neutral_admission_is_neither_best_nor_worst_under_budget() -> None:
    ranked_first = _candidate("ranked-first")
    neutral = _candidate("neutral")
    ranked_last = _candidate("ranked-last")
    budget = PackageBudget(
        max_tokens=2 * len(b"safe body 0"),
        max_provider_calls=1,
        max_cost_microunits=1,
        max_elapsed_ms=1,
    )
    with _projections(ranked_first, neutral, ranked_last) as projections:
        joined = join_authorized_ranking(
            projections,
            (_rank(ranked_first, 1), _rank(ranked_last, 2)),
        )
        selected = select_authorized_ranking(joined, budget)

        assert tuple(item.projection.candidate_ref for item in selected) == (
            ranked_first,
            neutral,
        )
        assert (
            selected[0].fused_rank
            < selected[1].fused_rank
            < joined[2].fused_rank
        )


def test_package_assembly_preserves_authorized_ranking_order() -> None:
    ranked_first = _candidate("z-ranked-first")
    ranked_second = _candidate("a-ranked-second")
    budget = PackageBudget(
        max_tokens=100,
        max_provider_calls=1,
        max_cost_microunits=1,
        max_elapsed_ms=1,
    )
    with _projections(ranked_first, ranked_second) as projections:
        selected = select_authorized_ranking(
            join_authorized_ranking(
                projections,
                (_rank(ranked_first, 1), _rank(ranked_second, 2)),
            ),
            budget,
        )

        assert tuple(item.projection.candidate_ref for item in selected) == (
            ranked_first,
            ranked_second,
        )
        content = construct_package_content(
            tuple(item.projection for item in selected),
        )
        assert tuple(block.body for block in content.blocks) == (
            "safe body 0",
            "safe body 1",
        )
