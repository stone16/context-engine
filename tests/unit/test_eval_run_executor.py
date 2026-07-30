from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from socket import socket
from typing import cast

import pytest

from adapters.http.dogfood import DOGFOOD_SECRET_ENV
from applications.dogfood_evaluation import DOGFOOD_BASE_URL_ENV
from applications.eval_executor import (
    TRACKED_RUN_SEAM_REF,
    AnswerJudgments,
    execute_evaluation_report,
    load_answer_judgments,
)
from engine.learning.eval_run import EvaluationRunUnavailable
from engine.learning.golden import GoldenSet, load_golden_set
from engine.learning.thresholds import DEFAULT_THRESHOLDS_PATH, load_thresholds
from tests.support.eval_seam import (
    QUERY_PREFIX,
    SECRET,
    block,
    case_ref_of,
    clean_responder,
    evidence,
    judgment_document,
    resolved,
    serving,
)
from tests.support.golden import golden_case, write_golden

GENERATED_AT = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _entries() -> list[dict[str, object]]:
    return [
        golden_case("dev-00"),
        golden_case("dev-01"),
        golden_case("dev-02", answerability="unanswerable"),
    ]


def _golden(tmp_path: Path, entries: list[dict[str, object]]) -> GoldenSet:
    path = tmp_path / "golden.json"
    write_golden(path, entries)
    return load_golden_set(path, validate_set_composition=False)


def _clean(query: str) -> dict[str, object]:
    return clean_responder(_entries())(query)


def _judgments(tmp_path: Path, entries: list[dict[str, object]]) -> AnswerJudgments:
    path = tmp_path / "judgments.json"
    path.write_text(json.dumps(judgment_document(entries)), encoding="utf-8")
    return load_answer_judgments(path)


def _execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[str], dict[str, object]] | None = None,
    entries: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    selected = _entries() if entries is None else entries
    seam = clean_responder(selected) if responder is None else responder
    with serving(seam, monkeypatch):
        return execute_evaluation_report(
            _golden(tmp_path, selected),
            _judgments(tmp_path, selected),
            load_thresholds(DEFAULT_THRESHOLDS_PATH),
            generated_at=GENERATED_AT,
        )


def test_executed_clean_run_is_the_only_path_to_an_observed_clean_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _execute(tmp_path, monkeypatch)

    assert report["security"] == {
        "missingContextFallbackCount": 0,
        "observationState": "observed_clean",
        "status": "pass",
        "unauthorizedEvidenceCount": 0,
        "wrongOrganizationEffectCount": 0,
    }
    assert report["status"] == "PENDING_PREREGISTRATION"
    assert report["run"] == {"executedSeamRef": TRACKED_RUN_SEAM_REF}
    retrieval = cast(dict[str, object], report["retrieval"])
    citation = cast(dict[str, object], report["citation"])
    assert retrieval["macro_evidence_recall"] == 1.0
    assert citation["status"] == "pass"


def test_one_observed_unauthorized_evidence_fails_the_report_at_perfect_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref != "dev-01":
            return _clean(query)
        unbound = evidence("unbound-extra", decisionRef="dec_other-decision")
        return resolved(
            [evidence(case_ref), unbound],
            [block(f"ev-{case_ref}")],
        )

    report = _execute(tmp_path, monkeypatch, respond)

    retrieval = cast(dict[str, object], report["retrieval"])
    citation = cast(dict[str, object], report["citation"])
    assert retrieval["macro_evidence_recall"] == 1.0
    assert citation["status"] == "pass"
    assert report["security"] == {
        "missingContextFallbackCount": 0,
        "observationState": "observed_violation",
        "status": "fail",
        "unauthorizedEvidenceCount": 1,
        "wrongOrganizationEffectCount": 0,
    }
    assert report["status"] == "FAIL"


@pytest.mark.parametrize(
    "field",
    (
        "decisionRef",
        "policyEpoch",
        "policySnapshotRef",
        "purpose",
        "runRef",
        "authorizationAsOf",
        "projectedFields",
        "sourceAclEvidence",
    ),
)
def test_evidence_without_its_complete_decision_binding_is_unauthorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref != "dev-01":
            return _clean(query)
        stripped = evidence(case_ref)
        del stripped[field]
        return resolved([stripped], [block(f"ev-{case_ref}")])

    report = _execute(tmp_path, monkeypatch, respond)
    security = cast(dict[str, object], report["security"])

    assert security["unauthorizedEvidenceCount"] == 1
    assert report["status"] == "FAIL"


def test_content_not_grounded_in_delivered_evidence_is_a_missing_context_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref != "dev-00":
            return _clean(query)
        return resolved(
            [evidence(case_ref)],
            [block(f"ev-{case_ref}"), block("ev-never-delivered")],
        )

    report = _execute(tmp_path, monkeypatch, respond)

    assert report["security"] == {
        "missingContextFallbackCount": 1,
        "observationState": "observed_violation",
        "status": "fail",
        "unauthorizedEvidenceCount": 0,
        "wrongOrganizationEffectCount": 0,
    }
    assert report["status"] == "FAIL"


def test_content_delivered_under_empty_coverage_is_a_missing_context_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref != "dev-00":
            return _clean(query)
        outcome = resolved([evidence(case_ref)], [block(f"ev-{case_ref}")])
        package = cast(dict[str, object], outcome["package"])
        package["coverage"] = {
            "status": "empty",
            "reason": "no_authorized_evidence",
        }
        return outcome

    report = _execute(tmp_path, monkeypatch, respond)
    security = cast(dict[str, object], report["security"])

    assert security["missingContextFallbackCount"] == 1
    assert report["status"] == "FAIL"


def test_a_second_audience_binding_in_one_run_is_a_wrong_organization_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        if case_ref != "dev-01":
            return _clean(query)
        return resolved(
            [evidence(case_ref)],
            [block(f"ev-{case_ref}")],
            audienceDigest="b" * 64,
        )

    report = _execute(tmp_path, monkeypatch, respond)

    assert report["security"] == {
        "missingContextFallbackCount": 0,
        "observationState": "observed_violation",
        "status": "fail",
        "unauthorizedEvidenceCount": 0,
        "wrongOrganizationEffectCount": 1,
    }
    assert report["status"] == "FAIL"


def test_refusal_is_observed_from_the_package_and_overrides_the_blind_judgment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(query: str) -> dict[str, object]:
        case_ref = case_ref_of(query)
        return resolved([], []) if case_ref == "dev-00" else _clean(query)

    report = _execute(tmp_path, monkeypatch, respond)
    answer = cast(dict[str, object], report["answer"])
    cases = cast(list[dict[str, object]], answer["cases"])
    refused = next(case for case in cases if case["case_ref"] == "dev-00")

    assert refused["refused"] is True
    assert refused["normalized_score"] == 0.0
    assert cast(dict[str, object], report["security"])["status"] == "pass"


@pytest.mark.parametrize(
    "field",
    (
        "missingContextFallbackCount",
        "observedEvidence",
        "refused",
        "securityObservation",
        "unauthorizedEvidenceCount",
        "wrongOrganizationEffectCount",
    ),
)
def test_judgments_cannot_claim_anything_the_executed_run_observes(
    tmp_path: Path,
    field: str,
) -> None:
    entries = _entries()
    document = judgment_document(entries)
    cases = cast(list[dict[str, object]], document["cases"])
    cases[0][field] = 0
    path = tmp_path / "judgments.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(EvaluationRunUnavailable, match="executed run observes"):
        load_answer_judgments(path)


def test_executor_refuses_when_the_tracked_seam_is_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DOGFOOD_BASE_URL_ENV, raising=False)
    monkeypatch.delenv(DOGFOOD_SECRET_ENV, raising=False)
    entries = _entries()

    with pytest.raises(EvaluationRunUnavailable, match="seam is unavailable"):
        execute_evaluation_report(
            _golden(tmp_path, entries),
            _judgments(tmp_path, entries),
            load_thresholds(DEFAULT_THRESHOLDS_PATH),
            generated_at=GENERATED_AT,
        )


def test_executor_refuses_when_the_tracked_seam_does_not_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    monkeypatch.setenv(DOGFOOD_SECRET_ENV, SECRET)
    monkeypatch.setenv(DOGFOOD_BASE_URL_ENV, f"http://127.0.0.1:{port}")
    entries = _entries()

    with pytest.raises(EvaluationRunUnavailable, match="seam is unavailable"):
        execute_evaluation_report(
            _golden(tmp_path, entries),
            _judgments(tmp_path, entries),
            load_thresholds(DEFAULT_THRESHOLDS_PATH),
            generated_at=GENERATED_AT,
        )


def test_executor_refuses_a_judgment_set_that_does_not_match_the_golden_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _entries()
    judgments = _judgments(tmp_path, entries[:2])

    with (
        serving(clean_responder(entries), monkeypatch),
        pytest.raises(EvaluationRunUnavailable, match="exactly match"),
    ):
        execute_evaluation_report(
            _golden(tmp_path, entries),
            judgments,
            load_thresholds(DEFAULT_THRESHOLDS_PATH),
            generated_at=GENERATED_AT,
        )


def test_executor_refuses_a_golden_set_that_retains_the_configured_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _entries()
    entries[0]["query"] = f"{QUERY_PREFIX}dev-00 never retain {SECRET}"

    with pytest.raises(EvaluationRunUnavailable, match="secret material"):
        _execute(tmp_path, monkeypatch, entries=entries)
