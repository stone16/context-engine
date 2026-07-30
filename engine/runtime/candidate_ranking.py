"""Content-free ranked-candidate and post-projection ranking contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Final

from engine.runtime.evidence import CandidateRef

__all__ = [
    "DEFAULT_CANDIDATE_SUBMISSION_LIMIT",
    "MAX_CANDIDATE_SUBMISSION_CEILING",
    "MAX_SUBMITTED_RANKED_LISTS",
    "CandidateQuery",
    "CandidateRankEvidence",
    "FusedCandidates",
    "RankedCandidate",
    "RankedCandidateList",
    "RankerEvidence",
    "require_bounded_candidate_submission",
    "require_candidate_submission_limit",
]

MAX_CANDIDATE_SUBMISSION_CEILING: Final = 512
DEFAULT_CANDIDATE_SUBMISSION_LIMIT: Final = 128
MAX_SUBMITTED_RANKED_LISTS: Final = 8


def require_candidate_submission_limit(limit: object) -> int:
    """Validate one server-owned submission bound at configuration time."""

    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATE_SUBMISSION_CEILING:
        raise ValueError(
            "candidate submission limit must be a positive exact integer within "
            "the server ceiling"
        )
    return limit


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
        type(value) not in {int, float} or type(value) is bool or not isfinite(value)
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


def require_bounded_candidate_submission(
    query: CandidateQuery,
    *,
    submission_limit: int,
) -> CandidateQuery:
    """Re-derive the seam contract, then bound one untrusted submission."""

    if type(query) is not CandidateQuery:
        raise TypeError("candidate submission requires CandidateQuery")
    require_candidate_submission_limit(submission_limit)
    query.__post_init__()
    for ranked_list in query.ranked_lists:
        ranked_list.__post_init__()
        for ranked in ranked_list.candidates:
            ranked.__post_init__()
    if len(query.ranked_lists) > MAX_SUBMITTED_RANKED_LISTS:
        raise ValueError("candidate submission exceeded the server ranker bound")
    submitted = sum(len(ranked_list.candidates) for ranked_list in query.ranked_lists)
    if submitted > submission_limit:
        raise ValueError("candidate submission exceeded the server candidate bound")
    return query


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
        if (
            type(self.per_ranker) is not tuple
            or not self.per_ranker
            or any(type(evidence) is not RankerEvidence for evidence in self.per_ranker)
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
