"""Tracked pending/configured thresholds and one-time calibration state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal, NoReturn, cast

from engine.learning.judges import (
    SliceCaseScore,
    SliceFloor,
    SliceFloorResult,
    evaluate_pending_slice_floors,
    evaluate_slice_floors,
)

THRESHOLDS_SCHEMA_VERSION: Final = "context-engine-eval-thresholds-v1"
DEFAULT_THRESHOLDS_PATH: Final = (
    Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
)
type Layer = Literal["retrieval", "citation", "answer"]
type SliceName = Literal["single_doc", "cross_doc", "temporal"]


@dataclass(frozen=True, slots=True)
class PendingValue:
    """A typed non-value that cannot become zero, absent, or permissive."""

    status: Literal["pending_preregistration"] = "pending_preregistration"
    value: None = None


@dataclass(frozen=True, slots=True)
class ConfiguredCount:
    status: Literal["configured"]
    value: int

    def __post_init__(self) -> None:
        if (
            self.status != "configured"
            or type(self.value) is not int
            or self.value <= 0
        ):
            raise ValueError("configured minimumCases must be positive")


@dataclass(frozen=True, slots=True)
class ConfiguredScore:
    status: Literal["configured"]
    value: float

    def __post_init__(self) -> None:
        if (
            self.status != "configured"
            or type(self.value) is not float
            or not 0.0 <= self.value <= 1.0
        ):
            raise ValueError("configured minimumScore must be a fraction")


type CountThreshold = PendingValue | ConfiguredCount
type ScoreThreshold = PendingValue | ConfiguredScore


@dataclass(frozen=True, slots=True)
class LayerSliceThreshold:
    layer: Layer
    slice_name: SliceName
    minimum_cases: CountThreshold
    minimum_score: ScoreThreshold


@dataclass(frozen=True, slots=True)
class _LoadedThresholdConfiguration:
    minimum_answer_score: ScoreThreshold
    minimum_refusal_accuracy: ScoreThreshold
    slice_floors: tuple[LayerSliceThreshold, ...]
    maximum_calibration_events: int
    recorded_calibration_events: tuple[dict[str, object], ...]
    source_authority: Literal["tracked", "non_authoritative"]


@dataclass(frozen=True, slots=True, init=False)
class EvaluationThresholds:
    minimum_answer_score: ScoreThreshold
    minimum_refusal_accuracy: ScoreThreshold
    slice_floors: tuple[LayerSliceThreshold, ...]
    maximum_calibration_events: int
    recorded_calibration_events: tuple[dict[str, object], ...]
    source_authority: Literal["tracked", "non_authoritative"]

    def __init__(
        self,
        configuration: _LoadedThresholdConfiguration,
    ) -> None:
        if type(configuration) is not _LoadedThresholdConfiguration:
            raise TypeError("evaluation thresholds are loader-constructed")
        if (
            type(configuration.minimum_answer_score)
            not in {PendingValue, ConfiguredScore}
            or type(configuration.minimum_refusal_accuracy)
            not in {PendingValue, ConfiguredScore}
            or type(configuration.slice_floors) is not tuple
            or configuration.maximum_calibration_events != 1
            or type(configuration.recorded_calibration_events) is not tuple
            or configuration.source_authority
            not in {"tracked", "non_authoritative"}
        ):
            raise TypeError("evaluation thresholds are malformed")
        _validate_threshold_constructor_values(
            configuration.minimum_answer_score,
            configuration.minimum_refusal_accuracy,
            configuration.slice_floors,
            configuration.recorded_calibration_events,
        )
        object.__setattr__(
            self,
            "minimum_answer_score",
            configuration.minimum_answer_score,
        )
        object.__setattr__(
            self,
            "minimum_refusal_accuracy",
            configuration.minimum_refusal_accuracy,
        )
        object.__setattr__(self, "slice_floors", configuration.slice_floors)
        object.__setattr__(
            self,
            "maximum_calibration_events",
            configuration.maximum_calibration_events,
        )
        object.__setattr__(
            self,
            "recorded_calibration_events",
            configuration.recorded_calibration_events,
        )
        object.__setattr__(
            self,
            "source_authority",
            configuration.source_authority,
        )

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError("evaluation thresholds must not be subclassed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("evaluation thresholds are not serializable")


def require_loaded_thresholds(thresholds: EvaluationThresholds) -> None:
    """Reject structurally forged threshold objects at the report boundary."""

    if type(thresholds) is not EvaluationThresholds:
        raise TypeError("evaluation thresholds must come from the tracked loader")
    _validate_threshold_constructor_values(
        thresholds.minimum_answer_score,
        thresholds.minimum_refusal_accuracy,
        thresholds.slice_floors,
        thresholds.recorded_calibration_events,
    )


def threshold_report_document(
    thresholds: EvaluationThresholds,
) -> dict[str, object]:
    """Render validated threshold facts from the loader-owned configuration."""

    require_loaded_thresholds(thresholds)
    return asdict(thresholds)


def _object(value: object, name: str, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        raise ValueError(f"{name} is malformed")
    return cast(dict[str, object], value)


def _pending_or_count(value: object) -> CountThreshold:
    document = _object(value, "count threshold", frozenset({"status", "value"}))
    if document == {"status": "pending_preregistration", "value": None}:
        return PendingValue()
    if document["status"] == "configured" and type(document["value"]) is int:
        return ConfiguredCount("configured", document["value"])
    raise ValueError("count threshold must be explicitly pending or configured")


def _pending_or_score(value: object) -> ScoreThreshold:
    document = _object(value, "score threshold", frozenset({"status", "value"}))
    if document == {"status": "pending_preregistration", "value": None}:
        return PendingValue()
    raw_value = document["value"]
    if document["status"] == "configured" and type(raw_value) in {int, float}:
        return ConfiguredScore("configured", float(cast(int | float, raw_value)))
    raise ValueError("score threshold must be explicitly pending or configured")


def _layer(value: object) -> Layer:
    if value not in {"retrieval", "citation", "answer"}:
        raise ValueError("threshold layer is unavailable")
    return cast(Layer, value)


def _slice(value: object) -> SliceName:
    if value not in {"single_doc", "cross_doc", "temporal"}:
        raise ValueError("threshold slice is unavailable")
    return cast(SliceName, value)


def _parse_floors(value: object) -> tuple[LayerSliceThreshold, ...]:
    if type(value) is not list:
        raise ValueError("sliceFloors is malformed")
    floors: list[LayerSliceThreshold] = []
    for raw_floor in cast(list[object], value):
        floor = _object(
            raw_floor,
            "slice floor",
            frozenset({"layer", "minimumCases", "minimumScore", "slice"}),
        )
        floors.append(
            LayerSliceThreshold(
                layer=_layer(floor["layer"]),
                slice_name=_slice(floor["slice"]),
                minimum_cases=_pending_or_count(floor["minimumCases"]),
                minimum_score=_pending_or_score(floor["minimumScore"]),
            )
        )
    expected_pairs = {
        (layer, slice_name)
        for layer in ("retrieval", "citation", "answer")
        for slice_name in ("single_doc", "cross_doc", "temporal")
    }
    actual_pairs = {(floor.layer, floor.slice_name) for floor in floors}
    if len(floors) != 9 or actual_pairs != expected_pairs:
        raise ValueError("sliceFloors must cover each layer and slice exactly once")
    return tuple(sorted(floors, key=lambda floor: (floor.layer, floor.slice_name)))


def _threshold_snapshot(value: object, name: str) -> dict[str, object]:
    document = _object(value, name, frozenset({"answer", "sliceFloors"}))
    answer = _object(
        document["answer"],
        f"{name} answer",
        frozenset({"minimumNormalizedScore", "minimumRefusalAccuracy"}),
    )
    _pending_or_score(answer["minimumNormalizedScore"])
    _pending_or_score(answer["minimumRefusalAccuracy"])
    _parse_floors(document["sliceFloors"])
    return document


def _calibration_event(value: object) -> dict[str, object]:
    document = _object(
        value,
        "threshold calibration event",
        frozenset(
            {
                "authority",
                "newValues",
                "oldValues",
                "pilotDigest",
                "reason",
                "recordedAt",
            }
        ),
    )
    if document["authority"] != "maintainer":
        raise ValueError("threshold calibration event authority is unavailable")
    digest = document["pilotDigest"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("threshold calibration event pilot digest is unavailable")
    reason = document["reason"]
    if (
        type(reason) is not str
        or not reason
        or reason.isspace()
        or reason != reason.strip()
        or len(reason) > 1_024
    ):
        raise ValueError("threshold calibration event reason is unavailable")
    recorded_at = document["recordedAt"]
    if type(recorded_at) is not str:
        raise ValueError("threshold calibration event time is unavailable")
    try:
        parsed_time = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("threshold calibration event time is unavailable") from None
    if (
        not recorded_at.endswith("Z")
        or parsed_time.tzinfo is None
        or parsed_time.utcoffset() != timedelta(0)
    ):
        raise ValueError("threshold calibration event time must be aware UTC")
    old_values = _threshold_snapshot(
        document["oldValues"], "threshold calibration old values"
    )
    new_values = _threshold_snapshot(
        document["newValues"], "threshold calibration new values"
    )
    if old_values == new_values:
        raise ValueError("threshold calibration event must record a change")
    return document


def _threshold_value_document(
    value: CountThreshold | ScoreThreshold,
) -> dict[str, object]:
    return {"status": value.status, "value": value.value}


def _validate_threshold_constructor_values(
    minimum_answer_score: ScoreThreshold,
    minimum_refusal_accuracy: ScoreThreshold,
    slice_floors: tuple[LayerSliceThreshold, ...],
    recorded_calibration_events: tuple[dict[str, object], ...],
) -> None:
    expected_pairs = {
        (layer, slice_name)
        for layer in ("retrieval", "citation", "answer")
        for slice_name in ("single_doc", "cross_doc", "temporal")
    }
    if (
        len(slice_floors) != 9
        or any(type(floor) is not LayerSliceThreshold for floor in slice_floors)
        or {(floor.layer, floor.slice_name) for floor in slice_floors}
        != expected_pairs
        or slice_floors
        != tuple(
            sorted(
                slice_floors,
                key=lambda floor: (floor.layer, floor.slice_name),
            )
        )
        or any(
            type(floor.minimum_cases) not in {PendingValue, ConfiguredCount}
            or type(floor.minimum_score) not in {PendingValue, ConfiguredScore}
            for floor in slice_floors
        )
        or len(recorded_calibration_events) > 1
        or any(type(event) is not dict for event in recorded_calibration_events)
    ):
        raise TypeError("evaluation thresholds are malformed")
    try:
        events = tuple(
            _calibration_event(event) for event in recorded_calibration_events
        )
    except ValueError:
        raise TypeError("evaluation thresholds are malformed") from None
    has_configured_value = any(
        type(value) is not PendingValue
        for value in (
            minimum_answer_score,
            minimum_refusal_accuracy,
            *(floor.minimum_cases for floor in slice_floors),
            *(floor.minimum_score for floor in slice_floors),
        )
    )
    if has_configured_value and not events:
        raise TypeError("evaluation thresholds are malformed")
    if events:
        active_values = {
            "answer": {
                "minimumNormalizedScore": _threshold_value_document(
                    minimum_answer_score
                ),
                "minimumRefusalAccuracy": _threshold_value_document(
                    minimum_refusal_accuracy
                ),
            },
            "sliceFloors": [
                {
                    "layer": floor.layer,
                    "minimumCases": _threshold_value_document(floor.minimum_cases),
                    "minimumScore": _threshold_value_document(floor.minimum_score),
                    "slice": floor.slice_name,
                }
                for floor in slice_floors
            ],
        }
        if events[-1]["newValues"] != active_values:
            raise TypeError("evaluation thresholds are malformed")


def load_thresholds(path: Path) -> EvaluationThresholds:
    if not isinstance(path, Path):
        raise TypeError("evaluation thresholds path must be Path")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValueError("evaluation thresholds are unavailable") from None
    document = _object(
        raw,
        "evaluation thresholds",
        frozenset({"answer", "calibration", "schemaVersion", "sliceFloors"}),
    )
    if document["schemaVersion"] != THRESHOLDS_SCHEMA_VERSION:
        raise ValueError("evaluation thresholds version is unavailable")
    answer = _object(
        document["answer"],
        "answer thresholds",
        frozenset({"minimumNormalizedScore", "minimumRefusalAccuracy"}),
    )
    calibration = _object(
        document["calibration"],
        "threshold calibration",
        frozenset({"maximumRecordedEvents", "recordedEvents"}),
    )
    maximum_events = calibration["maximumRecordedEvents"]
    recorded_events = calibration["recordedEvents"]
    if type(maximum_events) is not int or maximum_events != 1:
        raise ValueError("threshold calibration must allow exactly one event")
    if type(recorded_events) is not list or len(recorded_events) > maximum_events:
        raise ValueError("threshold calibration event count is unavailable")
    events = tuple(_calibration_event(event) for event in recorded_events)
    floors = _parse_floors(document["sliceFloors"])
    answer_values = (
        _pending_or_score(answer["minimumNormalizedScore"]),
        _pending_or_score(answer["minimumRefusalAccuracy"]),
    )
    has_configured_value = any(
        not isinstance(value, PendingValue)
        for value in (
            *answer_values,
            *(floor.minimum_cases for floor in floors),
            *(floor.minimum_score for floor in floors),
        )
    )
    if has_configured_value and not events:
        raise ValueError(
            "configured thresholds require a recorded calibration event"
        )
    if events:
        active_values = {
            "answer": document["answer"],
            "sliceFloors": document["sliceFloors"],
        }
        if events[-1]["newValues"] != active_values:
            raise ValueError(
                "threshold calibration event must bind the active configuration"
            )
    return EvaluationThresholds(
        _LoadedThresholdConfiguration(
            minimum_answer_score=answer_values[0],
            minimum_refusal_accuracy=answer_values[1],
            slice_floors=floors,
            maximum_calibration_events=maximum_events,
            recorded_calibration_events=events,
            source_authority=(
                "tracked"
                if path.resolve() == DEFAULT_THRESHOLDS_PATH.resolve()
                else "non_authoritative"
            ),
        )
    )


def slice_floors_from_thresholds(
    thresholds: EvaluationThresholds,
    layer: Layer,
) -> tuple[SliceFloor, ...]:
    """Return one layer's configured floors or refuse any pending value."""

    require_loaded_thresholds(thresholds)
    selected = tuple(floor for floor in thresholds.slice_floors if floor.layer == layer)
    if any(
        isinstance(floor.minimum_cases, PendingValue)
        or isinstance(floor.minimum_score, PendingValue)
        for floor in selected
    ):
        raise ValueError(f"{layer} slice floors are pending preregistration")
    return tuple(
        SliceFloor(
            floor.slice_name,
            cast(ConfiguredCount, floor.minimum_cases).value,
            cast(ConfiguredScore, floor.minimum_score).value,
        )
        for floor in selected
    )


def evaluate_layer_slice_thresholds(
    cases: tuple[SliceCaseScore, ...],
    thresholds: EvaluationThresholds,
    layer: Layer,
) -> tuple[SliceFloorResult, ...]:
    """Evaluate each ``(layer, slice)`` floor without layer-wide collapse."""

    require_loaded_thresholds(thresholds)
    selected = tuple(floor for floor in thresholds.slice_floors if floor.layer == layer)
    if len(selected) != 3:
        raise ValueError("threshold layer must contain exactly three slice floors")
    pending_by_slice = {
        result.slice_name: result for result in evaluate_pending_slice_floors(cases)
    }
    results: list[SliceFloorResult] = []
    for floor in selected:
        if isinstance(floor.minimum_cases, PendingValue) or isinstance(
            floor.minimum_score, PendingValue
        ):
            results.append(pending_by_slice[floor.slice_name])
            continue
        configured = SliceFloor(
            floor.slice_name,
            floor.minimum_cases.value,
            floor.minimum_score.value,
        )
        results.extend(evaluate_slice_floors(cases, (configured,)))
    return tuple(sorted(results, key=lambda result: result.slice_name))
