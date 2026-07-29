"""Content-free ranked-candidate and post-projection ranking contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from engine.runtime.evidence import CandidateRef

__all__ = [
    "CandidateQuery",
    "CandidateRankEvidence",
    "FusedCandidates",
    "RankedCandidate",
    "RankedCandidateList",
    "RankerEvidence",
    "preserve_single_ranker_candidates",
]


def _require_ranker_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 64
        or value[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in value
        )
    ):
        raise ValueError("ranker_ref must be a bounded lowercase identifier")
    return value


def _require_optional_score(value: float | None) -> float | None:
    if value is not None and (
        type(value) not in {int, float}
        or type(value) is bool
        or not isfinite(value)
    ):
        raise ValueError("ranked candidate score must be finite or absent")
    return value


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One opaque candidate at a position in one ranker's output."""

    candidate_ref: CandidateRef = field(repr=False)
    score: float | None = None

    def __post_init__(self) -> None:
        if type(self.candidate_ref) is not CandidateRef:
            raise TypeError("ranked candidate requires CandidateRef")
        _require_optional_score(self.score)


@dataclass(frozen=True, slots=True)
class RankedCandidateList:
    """One named ranker's ordered, content-free candidate output."""

    ranker_ref: str
    candidates: tuple[RankedCandidate, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_ranker_ref(self.ranker_ref)
        if type(self.candidates) is not tuple or any(
            type(candidate) is not RankedCandidate for candidate in self.candidates
        ):
            raise TypeError("ranked list requires exact RankedCandidate values")


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    """Separate ranked outputs returned by the sole candidate-discovery port."""

    ranked_lists: tuple[RankedCandidateList, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.ranked_lists) is not tuple
            or not self.ranked_lists
            or any(
                type(ranked_list) is not RankedCandidateList
                for ranked_list in self.ranked_lists
            )
        ):
            raise ValueError("CandidateQuery requires at least one named ranked list")
        identities = tuple(item.ranker_ref for item in self.ranked_lists)
        if len(identities) != len(set(identities)):
            raise ValueError("CandidateQuery ranker identity must be unique")


@dataclass(frozen=True, slots=True)
class RankerEvidence:
    """Content-free position and optional score from exactly one ranker."""

    ranker_ref: str
    position: int
    score: float | None = None

    def __post_init__(self) -> None:
        _require_ranker_ref(self.ranker_ref)
        if type(self.position) is not int or self.position < 1:
            raise ValueError("rank evidence position must be positive")
        _require_optional_score(self.score)


@dataclass(frozen=True, slots=True)
class CandidateRankEvidence:
    """Retrieval rank facts with no content, ACL claim, or authority."""

    candidate_ref: CandidateRef = field(repr=False)
    per_ranker: tuple[RankerEvidence, ...] = field(repr=False)
    fused_rank: int

    def __post_init__(self) -> None:
        if type(self.candidate_ref) is not CandidateRef:
            raise TypeError("rank evidence requires CandidateRef")
        if type(self.per_ranker) is not tuple or not self.per_ranker or any(
            type(evidence) is not RankerEvidence for evidence in self.per_ranker
        ):
            raise ValueError("candidate rank evidence requires per-ranker evidence")
        identities = tuple(item.ranker_ref for item in self.per_ranker)
        if len(identities) != len(set(identities)):
            raise ValueError("candidate rank evidence rankers must be unique")
        if type(self.fused_rank) is not int or self.fused_rank < 1:
            raise ValueError("fused rank must be positive")


@dataclass(frozen=True, slots=True)
class FusedCandidates:
    """Opaque deduplicated refs and their separate content-free rank evidence."""

    candidate_refs: tuple[CandidateRef, ...] = field(repr=False)
    rank_evidence: tuple[CandidateRankEvidence, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.candidate_refs) is not tuple or any(
            type(candidate) is not CandidateRef for candidate in self.candidate_refs
        ):
            raise TypeError("fused candidates require exact CandidateRef values")
        if type(self.rank_evidence) is not tuple or any(
            type(evidence) is not CandidateRankEvidence
            for evidence in self.rank_evidence
        ):
            raise TypeError("fused candidates require exact rank evidence")
        if tuple(item.candidate_ref for item in self.rank_evidence) != (
            self.candidate_refs
        ):
            raise ValueError("fused candidates and rank evidence must align exactly")


def preserve_single_ranker_candidates(query: CandidateQuery) -> FusedCandidates:
    """Preserve one active ranker without selecting the deferred fusion policy."""

    if type(query) is not CandidateQuery:
        raise TypeError("single-ranker preservation requires CandidateQuery")
    if len(query.ranked_lists) != 1:
        raise ValueError("multi-ranker fusion policy is not active")
    ranked_list = query.ranked_lists[0]
    candidates = []
    evidence = []
    seen: set[CandidateRef] = set()
    for position, ranked in enumerate(ranked_list.candidates, start=1):
        if ranked.candidate_ref in seen:
            continue
        seen.add(ranked.candidate_ref)
        candidates.append(ranked.candidate_ref)
        evidence.append(
            CandidateRankEvidence(
                candidate_ref=ranked.candidate_ref,
                per_ranker=(
                    RankerEvidence(
                        ranker_ref=ranked_list.ranker_ref,
                        position=position,
                        score=ranked.score,
                    ),
                ),
                fused_rank=len(candidates),
            )
        )
    return FusedCandidates(
        candidate_refs=tuple(candidates),
        rank_evidence=tuple(evidence),
    )
