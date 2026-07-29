"""Report-level gate status with non-negotiable security vetoes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def _fraction(field_name: str, value: object) -> float:
    if type(value) is not float or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be a fraction")
    return value


@dataclass(frozen=True, slots=True)
class EvaluationScores:
    """Already-thresholded independent quality layer inputs."""

    retrieval: float
    citation: float
    answer: float
    slice_statuses: tuple[
        Literal["pass", "fail", "insufficient_data", "pending_preregistration"],
        ...,
    ]

    def __post_init__(self) -> None:
        _fraction("retrieval", self.retrieval)
        _fraction("citation", self.citation)
        _fraction("answer", self.answer)
        if type(self.slice_statuses) is not tuple or not self.slice_statuses:
            raise ValueError("evaluation requires slice statuses")
        if any(
            status
            not in {
                "pass",
                "fail",
                "insufficient_data",
                "pending_preregistration",
            }
            for status in self.slice_statuses
        ):
            raise ValueError("slice status is unavailable")


@dataclass(frozen=True, slots=True)
class CaseSecurityObservation:
    """Per-case counts for the three AGENTS.md hard oracles."""

    case_ref: str
    unauthorized_evidence_count: int
    wrong_organization_effect_count: int
    missing_context_fallback_count: int

    def __post_init__(self) -> None:
        if type(self.case_ref) is not str or not self.case_ref:
            raise ValueError("security observation case_ref is unavailable")
        for field_name, value in (
            ("unauthorized Evidence", self.unauthorized_evidence_count),
            ("wrong-Organization effect", self.wrong_organization_effect_count),
            ("missing-context fallback", self.missing_context_fallback_count),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} count must be nonnegative")

    @property
    def violates_veto(self) -> bool:
        return any(
            (
                self.unauthorized_evidence_count,
                self.wrong_organization_effect_count,
                self.missing_context_fallback_count,
            )
        )


def final_report_status(
    scores: EvaluationScores,
    security_observations: tuple[CaseSecurityObservation, ...],
) -> Literal[
    "PASS", "FAIL", "INSUFFICIENT_DATA", "PENDING_PREREGISTRATION"
]:
    """Render a whole-report status; one hard-oracle violation always fails."""

    if type(scores) is not EvaluationScores:
        raise TypeError("scores must be EvaluationScores")
    if type(security_observations) is not tuple or not security_observations:
        raise ValueError("security observations are required")
    if any(
        type(observation) is not CaseSecurityObservation
        for observation in security_observations
    ):
        raise TypeError("security observations must be CaseSecurityObservation")
    if any(observation.violates_veto for observation in security_observations):
        return "FAIL"
    if any(status == "fail" for status in scores.slice_statuses):
        return "FAIL"
    if any(
        score != 1.0 for score in (scores.retrieval, scores.citation, scores.answer)
    ):
        return "FAIL"
    if any(status == "insufficient_data" for status in scores.slice_statuses):
        return "INSUFFICIENT_DATA"
    if any(status == "pending_preregistration" for status in scores.slice_statuses):
        return "PENDING_PREREGISTRATION"
    return "PASS"
