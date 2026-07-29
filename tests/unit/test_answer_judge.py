from __future__ import annotations

import pytest

from engine.learning.judges import (
    AnswerCaseInput,
    AnswerJudgeProfile,
    judge_answers,
)

PROFILE = AnswerJudgeProfile(
    model_ref="synthetic-blind-judge-model",
    profile_ref="synthetic-answer-judge-v1",
)


def test_answer_scores_are_normalized_from_blind_zero_one_two_scores() -> None:
    report = judge_answers(
        (
            AnswerCaseInput("synthetic-zero", "answerable", 0),
            AnswerCaseInput("synthetic-one", "answerable", 1),
            AnswerCaseInput("synthetic-two", "answerable", 2),
        ),
        PROFILE,
    )

    by_ref = {case.case_ref: case.normalized_score for case in report.cases}
    assert by_ref == {
        "synthetic-one": 0.5,
        "synthetic-two": 1.0,
        "synthetic-zero": 0.0,
    }
    assert report.normalized_score == pytest.approx(0.5)
    assert report.model_ref == "synthetic-blind-judge-model"
    assert report.profile_ref == "synthetic-answer-judge-v1"


def test_critical_contradiction_forces_answer_score_to_zero() -> None:
    report = judge_answers(
        (
            AnswerCaseInput(
                "synthetic-contradiction",
                "answerable",
                2,
                critical_contradiction=True,
            ),
        ),
        PROFILE,
    )

    assert report.cases[0].normalized_score == 0.0
    assert report.critical_contradictions == 1


def test_correct_unanswerable_refusal_scores_as_success() -> None:
    report = judge_answers(
        (
            AnswerCaseInput(
                "synthetic-correct-refusal",
                "unanswerable",
                0,
                refused=True,
            ),
        ),
        PROFILE,
    )

    assert report.cases[0].normalized_score == 1.0
    assert report.refusal_accuracy == 1.0


def test_wrong_refusal_on_answerable_case_scores_zero() -> None:
    report = judge_answers(
        (
            AnswerCaseInput(
                "synthetic-wrong-refusal",
                "answerable",
                2,
                refused=True,
            ),
        ),
        PROFILE,
    )

    assert report.cases[0].normalized_score == 0.0
