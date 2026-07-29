"""Rank evidence rejoin strictly after exact authorization and projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from engine.runtime.candidate_ranking import CandidateRankEvidence
from engine.runtime.evidence import (
    AuthorizedProjection,
    _require_active_authorized_projection,
)

__all__ = [
    "NEUTRAL_FUSED_RANK",
    "AuthorizedRerankItem",
    "join_authorized_ranking",
]

NEUTRAL_FUSED_RANK: Final = (1 << 63) - 1


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
) -> tuple[AuthorizedRerankItem, ...]:
    """Join only admitted projections; evidence without one is discarded."""

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
    return tuple(
        AuthorizedRerankItem(
            projection,
            evidence_by_ref.get(projection.candidate_ref),
        )
        for projection in projections
    )
