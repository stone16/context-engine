from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from engine.learning.judges import SliceCaseScore, SliceFloor, evaluate_slice_floors
from engine.learning.thresholds import (
    DEFAULT_THRESHOLDS_PATH,
    EvaluationThresholds,
    LayerSliceThreshold,
    PendingValue,
    _LoadedThresholdConfiguration,
    evaluate_layer_slice_thresholds,
    load_thresholds,
    slice_floors_from_thresholds,
)


def _recorded_mixed_thresholds(tmp_path: Path) -> Path:
    document = json.loads(DEFAULT_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    old_values = {
        "answer": document["answer"],
        "sliceFloors": document["sliceFloors"],
    }
    new_values = json.loads(json.dumps(old_values))
    retrieval = [
        floor
        for floor in new_values["sliceFloors"]
        if floor["layer"] == "retrieval"
    ]
    for floor in retrieval:
        if floor["slice"] != "temporal":
            floor["minimumCases"] = {"status": "configured", "value": 1}
            floor["minimumScore"] = {"status": "configured", "value": 0.61}
    document["answer"] = new_values["answer"]
    document["sliceFloors"] = new_values["sliceFloors"]
    document["calibration"]["recordedEvents"] = [
        {
            "authority": "maintainer",
            "newValues": new_values,
            "oldValues": old_values,
            "pilotDigest": "a" * 64,
            "reason": "synthetic-mixed-floor-contract-fixture",
            "recordedAt": "2026-07-29T12:00:00Z",
        }
    ]
    path = tmp_path / "mixed-thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_slice_floors_are_independent_and_underpopulated_is_insufficient() -> None:
    floors = (
        SliceFloor("single_doc", minimum_cases=2, minimum_score=0.73),
        SliceFloor("cross_doc", minimum_cases=2, minimum_score=0.73),
        SliceFloor("temporal", minimum_cases=2, minimum_score=0.73),
    )
    report = evaluate_slice_floors(
        (
            SliceCaseScore("synthetic-single-a", "single_doc", 1.0),
            SliceCaseScore("synthetic-single-b", "single_doc", 0.74),
            SliceCaseScore("synthetic-cross-a", "cross_doc", 0.72),
            SliceCaseScore("synthetic-cross-b", "cross_doc", 0.73),
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
            for index, score in enumerate((1.0, 1.0, 1.0, 0.0, 0.0))
        ),
        (SliceFloor("single_doc", minimum_cases=5, minimum_score=0.59),),
    )

    assert report[0].case_count == 5
    assert report[0].score == pytest.approx(0.6)
    assert report[0].wilson_95_low == pytest.approx(0.2307, abs=0.0001)
    assert report[0].wilson_95_high == pytest.approx(0.8824, abs=0.0001)


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


def test_threshold_loader_refuses_empty_calibration_event(tmp_path: Path) -> None:
    source_path = Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    document = json.loads(source_path.read_text(encoding="utf-8"))
    document["calibration"]["recordedEvents"] = [{}]
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="calibration event"):
        load_thresholds(path)


def test_threshold_loader_accepts_one_strict_maintainer_calibration_event(
    tmp_path: Path,
) -> None:
    source_path = Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    document = json.loads(source_path.read_text(encoding="utf-8"))
    old_values = {
        "answer": document["answer"],
        "sliceFloors": document["sliceFloors"],
    }
    new_values = json.loads(json.dumps(old_values))
    new_values["answer"]["minimumNormalizedScore"] = {
        "status": "configured",
        "value": 0.75,
    }
    document["answer"] = new_values["answer"]
    document["sliceFloors"] = new_values["sliceFloors"]
    document["calibration"]["recordedEvents"] = [
        {
            "authority": "maintainer",
            "newValues": new_values,
            "oldValues": old_values,
            "pilotDigest": "a" * 64,
            "reason": "synthetic-post-pilot-calibration",
            "recordedAt": "2026-07-29T12:00:00Z",
        }
    ]
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    thresholds = load_thresholds(path)

    assert len(thresholds.recorded_calibration_events) == 1


def test_calibration_event_must_bind_the_active_threshold_configuration(
    tmp_path: Path,
) -> None:
    source_path = Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    document = json.loads(source_path.read_text(encoding="utf-8"))
    snapshot = {
        "answer": document["answer"],
        "sliceFloors": document["sliceFloors"],
    }
    changed = json.loads(json.dumps(snapshot))
    changed["answer"]["minimumNormalizedScore"] = {
        "status": "configured",
        "value": 0.75,
    }
    document["calibration"]["recordedEvents"] = [
        {
            "authority": "maintainer",
            "newValues": changed,
            "oldValues": snapshot,
            "pilotDigest": "a" * 64,
            "reason": "synthetic-post-pilot-calibration",
            "recordedAt": "2026-07-29T12:00:00Z",
        }
    ]
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="active configuration"):
        load_thresholds(path)


def test_any_configured_threshold_requires_a_recorded_preregistration_event(
    tmp_path: Path,
) -> None:
    document = json.loads(DEFAULT_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    document["sliceFloors"][0]["minimumCases"] = {
        "status": "configured",
        "value": 1,
    }
    document["sliceFloors"][0]["minimumScore"] = {
        "status": "configured",
        "value": 0.61,
    }
    path = tmp_path / "unregistered-thresholds.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="recorded calibration"):
        load_thresholds(path)


def test_nontracked_threshold_file_is_explicitly_non_authoritative(
    tmp_path: Path,
) -> None:
    thresholds = load_thresholds(_recorded_mixed_thresholds(tmp_path))

    assert thresholds.source_authority == "non_authoritative"
    assert load_thresholds(DEFAULT_THRESHOLDS_PATH).source_authority == "tracked"


def test_threshold_authority_cannot_be_forged_by_direct_construction() -> None:
    from engine.learning.thresholds import EvaluationThresholds

    with pytest.raises(TypeError, match="loader-constructed"):
        EvaluationThresholds(
            cast(_LoadedThresholdConfiguration, object())
        )


def test_threshold_authority_type_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError, match="must not be subclassed"):

        class _ForgedThresholds(EvaluationThresholds):
            pass


@pytest.mark.parametrize(
    ("floors", "events"),
    (
        (
            tuple(
                LayerSliceThreshold(
                    layer="retrieval",
                    slice_name="single_doc",
                    minimum_cases=PendingValue(),
                    minimum_score=PendingValue(),
                )
                for _ in range(9)
            ),
            (),
        ),
        (
            load_thresholds(DEFAULT_THRESHOLDS_PATH).slice_floors,
            ({"authority": "malformed"},),
        ),
    ),
)
def test_sealed_threshold_constructor_revalidates_load_bearing_inputs(
    floors: tuple[LayerSliceThreshold, ...],
    events: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(TypeError, match="malformed"):
        EvaluationThresholds(
            _LoadedThresholdConfiguration(
                minimum_answer_score=PendingValue(),
                minimum_refusal_accuracy=PendingValue(),
                slice_floors=floors,
                maximum_calibration_events=1,
                recorded_calibration_events=events,
                source_authority="tracked",
            )
        )


def test_one_pending_floor_does_not_hide_configured_sibling_outcomes(
    tmp_path: Path,
) -> None:
    thresholds = load_thresholds(_recorded_mixed_thresholds(tmp_path))
    reports = evaluate_layer_slice_thresholds(
        (
            SliceCaseScore("synthetic-single", "single_doc", 0.0),
            SliceCaseScore("synthetic-cross", "cross_doc", 1.0),
            SliceCaseScore("synthetic-temporal", "temporal", 1.0),
        ),
        thresholds,
        "retrieval",
    )

    assert [(report.slice_name, report.status) for report in reports] == [
        ("cross_doc", "pass"),
        ("single_doc", "fail"),
        ("temporal", "pending_preregistration"),
    ]


def test_v1_sources_do_not_anchor_forbidden_preregistration_values() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    forbidden = re.compile(r"\b0\.(?:8(?:0*|50*)|90*)\b")
    decimal = "0."
    for equivalent_spelling in tuple(
        decimal + suffix
        for suffix in ("8", "80", "800", "85", "850", "9", "90", "900")
    ):
        assert forbidden.fullmatch(equivalent_spelling)
    for allowed_value in tuple(
        decimal + suffix for suffix in ("75", "81", "86", "91")
    ):
        assert forbidden.fullmatch(allowed_value) is None
    sources = {
        repository_root / "applications/eval_v1.py",
        *sorted((repository_root / "engine/learning").glob("*.py")),
        *sorted((repository_root / "eval").rglob("*.json")),
        *sorted((repository_root / "tests/unit").glob("test_golden*.py")),
        *sorted((repository_root / "tests/unit").glob("test_*judge*.py")),
        repository_root / "tests/unit/test_eval_v1_cli.py",
        repository_root / "tests/unit/test_security_veto.py",
        Path(__file__),
    }

    for source in sources:
        assert forbidden.search(source.read_text(encoding="utf-8")) is None, source
