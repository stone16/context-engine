from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from eval.embedding_benchmark import BenchmarkUnavailable, validate_report_document

REPORT_SCHEMA = Path("eval/embedding-benchmark/report.schema.json")


def _identity(model_id: str, *, pooling: str) -> dict[str, object]:
    return {
        "artifactDigest": "b" * 64,
        "batchSize": 8,
        "dimension": 384,
        "documentPrefix": "",
        "modelId": model_id,
        "normalization": "l2",
        "pooling": pooling,
        "precision": "float32",
        "queryPrefix": "query: ",
        "reduction": "none_native_384",
        "revision": "a" * 40,
    }


def _metrics() -> dict[str, object]:
    return {
        "caseHit": {"hits": 2, "totalCases": 2, "value": 1.0},
        "evidenceRecall": {
            "macro": {"value": 0.75},
            "micro": {"hits": 3, "totalExpected": 4, "value": 0.75},
        },
        "perSlice": {
            "single_doc": {
                "caseHit": {"hits": 2, "totalCases": 2, "value": 1.0},
                "evidenceRecall": {
                    "macro": {"value": 0.75},
                    "micro": {
                        "hits": 3,
                        "totalExpected": 4,
                        "value": 0.75,
                    },
                },
            }
        },
    }


def _report() -> dict[str, object]:
    model = {
        "identity": _identity("Qwen/Qwen3-Embedding-0.6B", pooling="last_token"),
        "metrics": _metrics(),
        "timing": {
            "documentCount": 4,
            "perDocumentEmbedMilliseconds": 1.25,
            "wallClockMilliseconds": 8.0,
        },
    }
    baseline = deepcopy(model)
    baseline["identity"] = _identity(
        "intfloat/multilingual-e5-small", pooling="mean"
    )
    return {
        "comparison": {
            "metricDeltas": {
                "caseHit": 0.0,
                "macroEvidenceRecall": 0.0,
                "microEvidenceRecall": 0.0,
            },
            "primaryAgainstModelBaseline": "tie",
            "primaryAgainstStandingTwinBaseline": "win",
        },
        "models": {"baseline": baseline, "primary": model},
        "run": {
            "datasetDigest": "c" * 64,
            "runIdentity": "d" * 64,
            "topK": 10,
        },
        "schemaVersion": "context-engine-embedding-benchmark-report-v1",
        "standingTwinBaseline": {"caseHitValue": 0.038},
    }


def _set_model_metric_values(
    report: dict[str, Any],
    *,
    primary: tuple[float, float, float],
    baseline: tuple[float, float, float],
    declared: str,
) -> None:
    for role, values in (("primary", primary), ("baseline", baseline)):
        metrics = report["models"][role]["metrics"]
        metrics["caseHit"]["value"] = values[0]
        metrics["evidenceRecall"]["macro"]["value"] = values[1]
        metrics["evidenceRecall"]["micro"]["value"] = values[2]
    report["comparison"] = {
        "metricDeltas": {
            "caseHit": primary[0] - baseline[0],
            "macroEvidenceRecall": primary[1] - baseline[1],
            "microEvidenceRecall": primary[2] - baseline[2],
        },
        "primaryAgainstModelBaseline": declared,
        "primaryAgainstStandingTwinBaseline": (
            "win" if primary[0] > 0.038 else "lose"
        ),
    }
def test_report_validates_against_the_tracked_closed_schema() -> None:
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))

    validate_report_document(_report(), schema_path=REPORT_SCHEMA)

    assert schema["additionalProperties"] is False
    assert schema["$id"].endswith("/embedding-benchmark/report.schema.json")


def test_validator_uses_the_tracked_schema_as_its_authority(tmp_path: Path) -> None:
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    schema["properties"]["schemaVersion"]["const"] = "future-version"
    changed_schema = tmp_path / "report.schema.json"
    changed_schema.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(BenchmarkUnavailable, match="report schema"):
        validate_report_document(_report(), schema_path=changed_schema)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda report: report["comparison"].update(
            {"primaryAgainstModelBaseline": "preferred"}
        ),
        lambda report: report.update({"queries": []}),
        lambda report: report["models"]["primary"]["identity"].update(
            {"nickname": "qwen"}
        ),
    ),
)
def test_report_refuses_values_or_fields_outside_the_tracked_schema(
    mutate: Any,
) -> None:
    report: dict[str, Any] = _report()
    mutate(report)

    with pytest.raises(BenchmarkUnavailable, match="report schema"):
        validate_report_document(report, schema_path=REPORT_SCHEMA)


@pytest.mark.parametrize(
    "remove",
    (
        ("models", "primary", "identity", "revision"),
        ("models", "primary", "metrics", "caseHit"),
        ("models", "baseline", "metrics", "evidenceRecall", "micro"),
        ("models", "baseline", "timing", "perDocumentEmbedMilliseconds"),
    ),
)
def test_report_refuses_a_missing_identity_metric_or_cost_field(
    remove: tuple[str, ...],
) -> None:
    report: dict[str, Any] = _report()
    parent: dict[str, Any] = report
    for key in remove[:-1]:
        parent = parent[key]
    del parent[remove[-1]]

    with pytest.raises(BenchmarkUnavailable, match="report schema"):
        validate_report_document(report, schema_path=REPORT_SCHEMA)


@pytest.mark.parametrize(
    ("primary", "baseline"),
    (
        ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        ((1.0, 0.0, 0.5), (0.0, 1.0, 0.5)),
    ),
)
def test_report_refuses_a_claimed_win_not_derived_from_recorded_metrics(
    primary: tuple[float, float, float],
    baseline: tuple[float, float, float],
) -> None:
    report: dict[str, Any] = _report()
    _set_model_metric_values(
        report,
        primary=primary,
        baseline=baseline,
        declared="win",
    )

    with pytest.raises(BenchmarkUnavailable, match="report verdict"):
        validate_report_document(report, schema_path=REPORT_SCHEMA)


@pytest.mark.parametrize(
    ("primary", "baseline", "declared"),
    (
        ((1.0, 1.0, 1.0), (0.0, 0.5, 1.0), "win"),
        ((0.0, 0.5, 1.0), (1.0, 1.0, 1.0), "lose"),
        ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), "tie"),
        ((1.0, 0.0, 0.5), (0.0, 1.0, 0.5), "inconclusive"),
    ),
)
def test_report_accepts_each_self_consistent_typed_pareto_outcome(
    primary: tuple[float, float, float],
    baseline: tuple[float, float, float],
    declared: str,
) -> None:
    report: dict[str, Any] = _report()
    _set_model_metric_values(
        report,
        primary=primary,
        baseline=baseline,
        declared=declared,
    )

    validate_report_document(report, schema_path=REPORT_SCHEMA)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda report: report["comparison"]["metricDeltas"].update(
            {"microEvidenceRecall": 0.25}
        ),
        lambda report: report["comparison"].update(
            {"primaryAgainstStandingTwinBaseline": "lose"}
        ),
    ),
)
def test_report_refuses_each_derived_comparison_field_when_fabricated(
    mutate: Any,
) -> None:
    report: dict[str, Any] = _report()
    mutate(report)

    with pytest.raises(BenchmarkUnavailable, match="report verdict"):
        validate_report_document(report, schema_path=REPORT_SCHEMA)
