from __future__ import annotations

import json
from hashlib import sha256
from typing import cast

import pytest

from engine.learning.comparison import (
    EvaluationComparisonUnavailable,
    compare_release_evaluations,
)


def _report(release_ref: str, *, status: str = "PASS") -> dict[str, object]:
    def row(slice_name: str) -> dict[str, object]:
        return {
            "case_count": 10,
            "score": 0.8,
            "slice_name": slice_name,
            "status": "pass",
            "wilson_95_high": 0.95,
            "wilson_95_low": 0.65,
        }

    report: dict[str, object] = {
        "goldenSet": {"digest": "a" * 64},
        "lineageCheck": {
            "ran": True,
            "staleCaseCount": 0,
            "totalCaseCount": 10,
        },
        "release": {"releaseRef": release_ref},
        "reportVersion": "context-engine-eval-report-v1",
        "run": {"executedSeamRef": "dogfood-loopback-resolve-acquire-v1"},
        "security": {
            "missingContextFallbackCount": 0,
            "observationState": "observed_clean",
            "status": "pass",
            "unauthorizedEvidenceCount": 0,
            "wrongOrganizationEffectCount": 0,
        },
        "slices": {
            layer: [
                row(slice_name)
                for slice_name in ("cross_doc", "single_doc", "temporal")
            ]
            for layer in ("answer", "citation", "retrieval")
        },
        "status": status,
        "thresholdAuthority": "tracked",
    }
    report["reportDigest"] = _digest(report)
    return report


def _digest(document: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _rebind(report: dict[str, object]) -> None:
    report.pop("reportDigest", None)
    report["reportDigest"] = _digest(report)


def test_comparison_reports_candidate_delta_per_slice() -> None:
    active = _report("rel-active-1")
    candidate = _report("rel-candidate-2")
    candidate["slices"]["retrieval"][1]["score"] = 0.9  # type: ignore[index]
    _rebind(candidate)

    comparison = compare_release_evaluations(active, candidate)
    slices = cast(list[dict[str, object]], comparison["slices"])

    retrieval = next(
        item
        for item in slices
        if item["layer"] == "retrieval" and item["slice"] == "single_doc"
    )
    assert comparison["status"] == "compared"
    assert comparison["activeReleaseRef"] == "rel-active-1"
    assert comparison["candidateReleaseRef"] == "rel-candidate-2"
    assert retrieval == {
        "activeCaseCount": 10,
        "activeScore": 0.8,
        "activeStatus": "pass",
        "candidateCaseCount": 10,
        "candidateScore": 0.9,
        "candidateStatus": "pass",
        "delta": 0.1,
        "layer": "retrieval",
        "slice": "single_doc",
    }
    assert comparison["schemaVersion"] == "context-engine-release-comparison-v1"
    digest = cast(str, comparison.pop("reportDigest"))
    assert digest == _digest(comparison)


@pytest.mark.parametrize("status", ("REFUSED", "NON_AUTHORITATIVE"))
def test_non_authoritative_report_is_never_presented_as_a_verdict(
    status: str,
) -> None:
    with pytest.raises(EvaluationComparisonUnavailable, match="authoritative"):
        compare_release_evaluations(
            _report("rel-active-1", status=status),
            _report("rel-candidate-2"),
        )


def test_security_failure_remains_authoritative_but_never_offset_by_scores() -> None:
    active = _report("rel-active-1", status="FAIL")
    active["security"] = {
        "missingContextFallbackCount": 0,
        "observationState": "observed_violation",
        "status": "fail",
        "unauthorizedEvidenceCount": 1,
        "wrongOrganizationEffectCount": 0,
    }
    _rebind(active)

    comparison = compare_release_evaluations(
        active,
        _report("rel-candidate-2", status="PASS"),
    )

    assert comparison["activeReportStatus"] == "FAIL"
    assert comparison["candidateReportStatus"] == "PASS"


def test_pending_reports_compare_slices_without_claiming_a_verdict() -> None:
    comparison = compare_release_evaluations(
        _report("rel-active-1", status="PENDING_PREREGISTRATION"),
        _report("rel-candidate-2", status="PENDING_PREREGISTRATION"),
    )

    assert comparison["status"] == "PENDING_PREREGISTRATION"
    assert "verdict" not in comparison
    assert cast(list[object], comparison["slices"])


def test_comparison_refuses_different_golden_set_or_missing_lineage_check() -> None:
    active = _report("rel-active-1")
    candidate = _report("rel-candidate-2")
    candidate["goldenSet"] = {"digest": "d" * 64}
    _rebind(candidate)
    with pytest.raises(EvaluationComparisonUnavailable, match="golden"):
        compare_release_evaluations(active, candidate)

    candidate = _report("rel-candidate-2")
    candidate["lineageCheck"] = {
        "ran": False,
        "staleCaseCount": None,
        "totalCaseCount": 10,
    }
    _rebind(candidate)
    with pytest.raises(EvaluationComparisonUnavailable, match="lineage"):
        compare_release_evaluations(active, candidate)


def test_comparison_refuses_tampered_digest_or_incomplete_slice_set() -> None:
    candidate = _report("rel-candidate-2")
    candidate["status"] = "FAIL"
    with pytest.raises(EvaluationComparisonUnavailable, match="authoritative"):
        compare_release_evaluations(_report("rel-active-1"), candidate)

    candidate = _report("rel-candidate-2")
    cast(dict[str, object], candidate["slices"])["answer"] = []
    _rebind(candidate)
    with pytest.raises(EvaluationComparisonUnavailable, match="slice set"):
        compare_release_evaluations(_report("rel-active-1"), candidate)
