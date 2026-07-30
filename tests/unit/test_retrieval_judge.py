from __future__ import annotations

import pytest

from engine.learning.judges import RetrievalCaseInput, judge_retrieval


def test_retrieval_reports_hand_checked_case_hit_macro_and_micro_recall() -> None:
    report = judge_retrieval(
        (
            RetrievalCaseInput(
                case_ref="synthetic-balanced-a",
                expected_evidence=frozenset({"a"}),
                observed_evidence=frozenset({"a"}),
            ),
            RetrievalCaseInput(
                case_ref="synthetic-unbalanced-b",
                expected_evidence=frozenset({"b1", "b2", "b3"}),
                observed_evidence=frozenset({"b1"}),
            ),
        )
    )

    assert report.case_hit == pytest.approx(0.5)
    assert report.macro_evidence_recall == pytest.approx(2 / 3)
    assert report.micro_evidence_recall == pytest.approx(0.5)
    assert report.hit_cases == 1
    assert report.total_cases == 2
    assert report.evidence_hits == 2
    assert report.total_expected_evidence == 4


def test_unanswerable_retrieval_succeeds_only_with_no_observed_evidence() -> None:
    report = judge_retrieval(
        (
            RetrievalCaseInput(
                case_ref="synthetic-correct-empty",
                expected_evidence=frozenset(),
                observed_evidence=frozenset(),
            ),
            RetrievalCaseInput(
                case_ref="synthetic-wrong-evidence",
                expected_evidence=frozenset(),
                observed_evidence=frozenset({"unexpected"}),
            ),
        )
    )

    assert report.case_hit == 0.5
    assert report.macro_evidence_recall == 0.5
    assert report.micro_evidence_recall == 0.5
