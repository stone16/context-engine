"""Authoritative candidate-versus-active Release slice comparison."""

from __future__ import annotations

import json
from hashlib import sha256
from re import fullmatch
from typing import cast

COMPARISON_REPORT_VERSION = "context-engine-release-comparison-v1"
_OBSERVED_STATUSES = frozenset(
    {"PASS", "FAIL", "INSUFFICIENT_DATA", "PENDING_PREREGISTRATION"}
)
_LAYERS = ("answer", "citation", "retrieval")
_SLICES = ("cross_doc", "single_doc", "temporal")
_SLICE_STATUSES = frozenset(
    {"pass", "fail", "insufficient_data", "pending_preregistration"}
)


class EvaluationComparisonUnavailable(RuntimeError):
    """Reports cannot support an authoritative comparison verdict."""


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise EvaluationComparisonUnavailable(f"comparison {name} is unavailable")
    return cast(dict[str, object], value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value.isspace():
        raise EvaluationComparisonUnavailable(f"comparison {name} is unavailable")
    return value


def _digest(document: object) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _require_authoritative(report: object, side: str) -> dict[str, object]:
    document = _object(report, f"{side} report")
    if document.get("reportVersion") != "context-engine-eval-report-v1":
        raise EvaluationComparisonUnavailable(f"{side} report is not authoritative")
    report_digest = document.get("reportDigest")
    digest_input = {
        key: value for key, value in document.items() if key != "reportDigest"
    }
    if (
        type(report_digest) is not str
        or fullmatch(r"[0-9a-f]{64}", report_digest) is None
        or report_digest != _digest(digest_input)
    ):
        raise EvaluationComparisonUnavailable(f"{side} report is not authoritative")
    if document.get("status") not in _OBSERVED_STATUSES:
        raise EvaluationComparisonUnavailable(
            f"{side} report is not authoritative"
        )
    security = _object(document.get("security"), f"{side} security")
    security_state = security.get("observationState")
    counts = tuple(
        security.get(name)
        for name in (
            "missingContextFallbackCount",
            "unauthorizedEvidenceCount",
            "wrongOrganizationEffectCount",
        )
    )
    if (
        security_state not in {"observed_clean", "observed_violation"}
        or any(type(value) is not int or value < 0 for value in counts)
        or (
            security_state == "observed_clean"
            and (security.get("status") != "pass" or counts != (0, 0, 0))
        )
        or (
            security_state == "observed_violation"
            and (
                security.get("status") != "fail"
                or not any(cast(int, value) > 0 for value in counts)
                or document.get("status") != "FAIL"
            )
        )
    ):
        raise EvaluationComparisonUnavailable(
            f"{side} report is not authoritative"
        )
    if document.get("thresholdAuthority") != "tracked":
        raise EvaluationComparisonUnavailable(
            f"{side} report is not authoritative"
        )
    run = _object(document.get("run"), f"{side} run")
    _text(run.get("executedSeamRef"), f"{side} executed seam")
    lineage = _object(document.get("lineageCheck"), f"{side} lineage")
    if lineage.get("ran") is not True or lineage.get("staleCaseCount") != 0:
        raise EvaluationComparisonUnavailable(
            f"{side} lineage is not authoritative"
        )
    release = _object(document.get("release"), f"{side} release")
    _text(release.get("releaseRef"), f"{side} releaseRef")
    return document


def _slice_rows(
    report: dict[str, object],
    side: str,
) -> dict[tuple[str, str], dict[str, object]]:
    slices = _object(report.get("slices"), f"{side} slices")
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for layer in _LAYERS:
        values = slices.get(layer)
        if type(values) is not list:
            raise EvaluationComparisonUnavailable(
                f"comparison {side} {layer} slices are unavailable"
            )
        for value in cast(list[object], values):
            row = _object(value, f"{side} {layer} slice")
            slice_name = row.get("slice_name")
            if slice_name not in _SLICES:
                raise EvaluationComparisonUnavailable(
                    f"comparison {side} slice is unavailable"
                )
            key = (layer, cast(str, slice_name))
            if key in rows:
                raise EvaluationComparisonUnavailable(
                    f"comparison {side} slices are duplicated"
                )
            if type(row.get("case_count")) is not int:
                raise EvaluationComparisonUnavailable(
                    f"comparison {side} slice count is unavailable"
                )
            case_count = cast(int, row["case_count"])
            score = row.get("score")
            status = row.get("status")
            if (
                case_count < 0
                or (score is not None and type(score) is not float)
                or (type(score) is float and not 0.0 <= score <= 1.0)
                or status not in _SLICE_STATUSES
                or (case_count == 0 and score is not None)
                or (status in {"pass", "fail"} and score is None)
            ):
                raise EvaluationComparisonUnavailable(
                    f"comparison {side} slice observation is unavailable"
                )
            rows[key] = row
    expected = frozenset(
        (layer, slice_name) for layer in _LAYERS for slice_name in _SLICES
    )
    if frozenset(rows) != expected:
        raise EvaluationComparisonUnavailable(
            f"comparison {side} slice set is unavailable"
        )
    return rows


def compare_release_evaluations(
    active_report: object,
    candidate_report: object,
) -> dict[str, object]:
    """Compare exact authoritative reports without activating either Release."""

    active = _require_authoritative(active_report, "active")
    candidate = _require_authoritative(candidate_report, "candidate")
    active_golden = _object(active.get("goldenSet"), "active golden set")
    candidate_golden = _object(candidate.get("goldenSet"), "candidate golden set")
    golden_digest = _text(active_golden.get("digest"), "active golden digest")
    if candidate_golden.get("digest") != golden_digest:
        raise EvaluationComparisonUnavailable(
            "comparison reports use different golden sets"
        )
    active_rows = _slice_rows(active, "active")
    candidate_rows = _slice_rows(candidate, "candidate")
    if frozenset(active_rows) != frozenset(candidate_rows):
        raise EvaluationComparisonUnavailable("comparison slice sets differ")
    comparisons: list[dict[str, object]] = []
    for layer, slice_name in sorted(active_rows):
        active_row = active_rows[(layer, slice_name)]
        candidate_row = candidate_rows[(layer, slice_name)]
        if active_row["case_count"] != candidate_row["case_count"]:
            raise EvaluationComparisonUnavailable(
                "comparison reports use different slice populations"
            )
        active_score = active_row["score"]
        candidate_score = candidate_row["score"]
        delta = (
            None
            if active_score is None or candidate_score is None
            else round(cast(float, candidate_score) - cast(float, active_score), 12)
        )
        comparisons.append(
            {
                "activeCaseCount": active_row["case_count"],
                "activeScore": active_score,
                "activeStatus": active_row["status"],
                "candidateCaseCount": candidate_row["case_count"],
                "candidateScore": candidate_score,
                "candidateStatus": candidate_row["status"],
                "delta": delta,
                "layer": layer,
                "slice": slice_name,
            }
        )
    active_release = _object(active["release"], "active release")
    candidate_release = _object(candidate["release"], "candidate release")
    pending = "PENDING_PREREGISTRATION" in {
        active["status"],
        candidate["status"],
    }
    comparison: dict[str, object] = {
        "activeReleaseRef": active_release["releaseRef"],
        "activeReportDigest": active["reportDigest"],
        "activeReportStatus": active["status"],
        "candidateReleaseRef": candidate_release["releaseRef"],
        "candidateReportDigest": candidate["reportDigest"],
        "candidateReportStatus": candidate["status"],
        "goldenSetDigest": golden_digest,
        "schemaVersion": COMPARISON_REPORT_VERSION,
        "slices": comparisons,
        "status": "PENDING_PREREGISTRATION" if pending else "compared",
    }
    comparison["reportDigest"] = _digest(comparison)
    return comparison
