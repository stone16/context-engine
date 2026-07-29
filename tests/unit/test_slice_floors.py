from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.learning.judges import SliceCaseScore, SliceFloor, evaluate_slice_floors
from engine.learning.thresholds import (
    load_thresholds,
    slice_floors_from_thresholds,
)


def test_slice_floors_are_independent_and_underpopulated_is_insufficient() -> None:
    floors = (
        SliceFloor("single_doc", minimum_cases=2, minimum_score=0.8),
        SliceFloor("cross_doc", minimum_cases=2, minimum_score=0.8),
        SliceFloor("temporal", minimum_cases=2, minimum_score=0.8),
    )
    report = evaluate_slice_floors(
        (
            SliceCaseScore("synthetic-single-a", "single_doc", 1.0),
            SliceCaseScore("synthetic-single-b", "single_doc", 0.8),
            SliceCaseScore("synthetic-cross-a", "cross_doc", 0.7),
            SliceCaseScore("synthetic-cross-b", "cross_doc", 0.8),
            SliceCaseScore("synthetic-temporal-a", "temporal", 1.0),
        ),
        floors,
    )

    assert [(result.slice_name, result.status) for result in report] == [
        ("cross_doc", "fail"),
        ("single_doc", "pass"),
        ("temporal", "insufficient_data"),
    ]
    assert report[2].score == 1.0
    assert report[2].wilson_95_low is not None
    assert report[2].wilson_95_high is not None


def test_slice_report_emits_hand_checked_wilson_interval() -> None:
    report = evaluate_slice_floors(
        tuple(
            SliceCaseScore(f"synthetic-{index}", "single_doc", score)
            for index, score in enumerate((1.0, 1.0, 1.0, 1.0, 0.0))
        ),
        (SliceFloor("single_doc", minimum_cases=5, minimum_score=0.8),),
    )

    assert report[0].case_count == 5
    assert report[0].score == pytest.approx(0.8)
    assert report[0].wilson_95_low == pytest.approx(0.3755, abs=0.0001)
    assert report[0].wilson_95_high == pytest.approx(0.9638, abs=0.0001)


def test_pending_preregistration_is_typed_and_never_coerced_to_pass() -> None:
    thresholds = load_thresholds(
        Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    )

    assert len(thresholds.slice_floors) == 9
    assert all(
        floor.minimum_cases.status == "pending_preregistration"
        and floor.minimum_cases.value is None
        and floor.minimum_score.status == "pending_preregistration"
        and floor.minimum_score.value is None
        for floor in thresholds.slice_floors
    )
    with pytest.raises(ValueError, match="pending preregistration"):
        slice_floors_from_thresholds(thresholds, "retrieval")


@pytest.mark.parametrize(
    "minimum_cases",
    (
        {"status": "configured", "value": 0},
        {"status": "pending_preregistration"},
    ),
)
def test_threshold_loader_refuses_zero_or_absent_pending_value(
    tmp_path: Path,
    minimum_cases: dict[str, object],
) -> None:
    source_path = Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    document = json.loads(source_path.read_text(encoding="utf-8"))
    document["sliceFloors"][0]["minimumCases"] = minimum_cases
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError):
        load_thresholds(path)
