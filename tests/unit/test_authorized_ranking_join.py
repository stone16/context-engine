from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

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
                    source_acl_projection_ref="sourceacl_projection:ranking",
                    source_acl_as_of=datetime(2026, 7, 29, tzinfo=UTC),
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
        assert all(item.projection.candidate_ref != refused for item in consumer_calls)
        assert "refused" not in repr(joined)


def test_join_discards_refused_ranker_payload_without_reading_it() -> None:
    """Only the exact ref may be read to discard a refused evidence record."""

    allowed = _candidate("allowed")
    refused = _candidate("refused")
    refused_evidence = _rank(refused, 1)

    class _UnreadableRankerPayload:
        def __iter__(self) -> Iterator[RankerEvidence]:
            raise AssertionError("refused per-ranker evidence was read")

        def __repr__(self) -> str:
            raise AssertionError("refused per-ranker evidence was rendered")

    object.__setattr__(
        refused_evidence,
        "per_ranker",
        cast("tuple[RankerEvidence, ...]", _UnreadableRankerPayload()),
    )

    with _projections(allowed) as projections:
        joined = join_authorized_ranking(
            projections,
            (refused_evidence, _rank(allowed, 1)),
        )

        assert tuple(item.projection.candidate_ref for item in joined) == (allowed,)
        assert joined[0].rank_evidence is not None
        assert joined[0].rank_evidence.candidate_ref == allowed


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


def test_tied_ranker_positions_have_a_stable_total_order() -> None:
    canonical_first = _candidate("a-tied")
    canonical_second = _candidate("z-tied")
    tied_evidence = (
        _rank(canonical_second, 1),
        _rank(canonical_first, 1),
    )
    observed_orders: list[tuple[CandidateRef, ...]] = []

    for run in range(10):
        input_order = (
            (canonical_first, canonical_second)
            if run % 2 == 0
            else (canonical_second, canonical_first)
        )
        with _projections(*input_order) as projections:
            joined = join_authorized_ranking(projections, tied_evidence)
            observed_orders.append(
                tuple(item.projection.candidate_ref for item in joined)
            )

    assert (
        observed_orders
        == [
            (canonical_first, canonical_second),
        ]
        * 10
    )


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
        assert selected[0].fused_rank < selected[1].fused_rank < joined[2].fused_rank


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


def _multi_rank(
    candidate: CandidateRef,
    positions: dict[str, int],
    fused_rank: int,
) -> CandidateRankEvidence:
    return CandidateRankEvidence(
        candidate_ref=candidate,
        per_ranker=tuple(
            RankerEvidence(ranker_ref=ranker_ref, position=position)
            for ranker_ref, position in positions.items()
        ),
        fused_rank=fused_rank,
    )


def test_rerank_item_refuses_rank_evidence_bound_to_another_candidate() -> None:
    allowed = _candidate("allowed")
    other = _candidate("other")

    with (
        _projections(allowed) as (projection,),
        pytest.raises(ValueError, match="exact CandidateRef"),
    ):
        AuthorizedRerankItem(projection, _rank(other, 1))


def test_rerank_item_refuses_duck_typed_rank_evidence() -> None:
    """A shape-compatible imposter must be refused on nominal type, not on fit."""

    allowed = _candidate("allowed")

    class _ImposterEvidence:
        def __init__(self, candidate_ref: CandidateRef) -> None:
            self.candidate_ref = candidate_ref
            self.fused_rank = 1

    with (
        _projections(allowed) as (projection,),
        pytest.raises(TypeError, match="wrong nominal type"),
    ):
        AuthorizedRerankItem(
            projection,
            cast("CandidateRankEvidence", _ImposterEvidence(allowed)),
        )


def test_join_refuses_two_rank_records_for_one_candidate() -> None:
    """Last-wins would let a buggy fuser silently replace a candidate's rank."""

    allowed = _candidate("allowed")

    with (
        _projections(allowed) as projections,
        pytest.raises(ValueError, match="unique exact CandidateRef"),
    ):
        join_authorized_ranking(
            projections,
            (_rank(allowed, 1), _rank(allowed, 2)),
        )


def test_pre_kernel_fused_rank_cannot_influence_delivered_order() -> None:
    """Only positions recomputed over admitted candidates may order delivery."""

    first_by_position = _candidate("a-first-by-position")
    second_by_position = _candidate("b-second-by-position")
    inverted_pre_kernel_rank = (
        _multi_rank(first_by_position, {"lexical": 1}, fused_rank=99),
        _multi_rank(second_by_position, {"lexical": 2}, fused_rank=1),
    )

    with _projections(first_by_position, second_by_position) as projections:
        joined = join_authorized_ranking(projections, inverted_pre_kernel_rank)

        assert tuple(item.projection.candidate_ref for item in joined) == (
            first_by_position,
            second_by_position,
        )


def test_join_output_is_the_single_downstream_source_of_order() -> None:
    """The post-projection stage, not a later consumer, owns retrieval order."""

    ranked_first = _candidate("z-ranked-first")
    ranked_second = _candidate("a-ranked-second")
    evidence = (
        _rank(ranked_first, 1),
        _rank(ranked_second, 2),
    )

    with _projections(ranked_second, ranked_first) as projections:
        joined = join_authorized_ranking(projections, evidence)

        assert tuple(item.projection.candidate_ref for item in joined) == (
            ranked_first,
            ranked_second,
        )


def test_budget_packing_preserves_the_authorized_stage_order() -> None:
    """Selection consumes the stage order instead of deriving another one."""

    ranked_first = _candidate("ranked-first")
    ranked_second = _candidate("ranked-second")
    budget = PackageBudget(
        max_tokens=100,
        max_provider_calls=1,
        max_cost_microunits=1,
        max_elapsed_ms=1,
    )
    with _projections(ranked_first, ranked_second) as projections:
        joined = join_authorized_ranking(
            projections,
            (_rank(ranked_first, 1), _rank(ranked_second, 2)),
        )
        supplied_order = tuple(reversed(joined))

        assert select_authorized_ranking(supplied_order, budget) == supplied_order


def test_authorized_fusion_weights_reorder_only_admitted_candidates() -> None:
    """#148's weighting has exactly one legal home: after authorization."""

    lexical_favoured = _candidate("a-lexical-favoured")
    vector_favoured = _candidate("b-vector-favoured")
    refused = _candidate("refused")
    evidence = (
        _multi_rank(lexical_favoured, {"lexical": 1, "vector": 2}, fused_rank=1),
        _multi_rank(vector_favoured, {"lexical": 2, "vector": 1}, fused_rank=2),
        _multi_rank(refused, {"lexical": 1, "vector": 1}, fused_rank=1),
    )

    def _order(**weights: float) -> tuple[CandidateRef, ...]:
        with _projections(lexical_favoured, vector_favoured) as projections:
            joined = join_authorized_ranking(
                projections,
                evidence,
                ranker_weights=dict(weights) or None,
            )
            return tuple(item.projection.candidate_ref for item in joined)

    assert _order() == (lexical_favoured, vector_favoured)
    assert _order(lexical=1.0, vector=1.0) == (lexical_favoured, vector_favoured)
    assert _order(lexical=1.0, vector=4.0) == (vector_favoured, lexical_favoured)


def test_authorized_fusion_weights_must_be_server_owned_and_complete() -> None:
    allowed = _candidate("allowed")
    evidence = (_multi_rank(allowed, {"lexical": 1, "vector": 1}, fused_rank=1),)

    with _projections(allowed) as projections:
        with pytest.raises(ValueError, match="cover every admitted ranker"):
            join_authorized_ranking(
                projections,
                evidence,
                ranker_weights={"lexical": 1.0},
            )
        for weight in (0.0, -1.0, float("inf"), float("nan")):
            with pytest.raises(ValueError, match="positive finite floats"):
                join_authorized_ranking(
                    projections,
                    evidence,
                    ranker_weights={"lexical": 1.0, "vector": weight},
                )
