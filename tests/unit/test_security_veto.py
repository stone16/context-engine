from __future__ import annotations

import pytest

from engine.learning.eval_report import (
    CaseSecurityObservation,
    EvaluationScores,
    final_report_status,
)


@pytest.mark.parametrize(
    "counts",
    (
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    ),
)
def test_one_hard_oracle_violation_forces_entire_report_to_fail(
    counts: tuple[int, int, int],
) -> None:
    scores = EvaluationScores(
        retrieval=1.0,
        citation=1.0,
        answer=1.0,
        slice_statuses=("pass", "pass", "pass"),
    )

    status = final_report_status(
        scores,
        (
            CaseSecurityObservation("synthetic-safe", 0, 0, 0),
            CaseSecurityObservation("synthetic-unsafe", *counts),
        ),
    )

    assert status == "FAIL"
