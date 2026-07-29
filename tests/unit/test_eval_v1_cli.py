from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from applications.eval_v1 import main
from engine.learning.eval_run import (
    SecurityObservationNotObserved,
    load_evaluation_run,
)
from engine.learning.golden import create_golden_lock, load_golden_set
from tests.support.golden import valid_composed_entries, write_golden


def _run_document(entries: list[dict[str, object]]) -> dict[str, object]:
    cases = []
    for entry in entries:
        expected_evidence = cast(list[dict[str, str]], entry["expectedEvidence"])
        required_claims = cast(list[dict[str, object]], entry["requiredClaims"])
        evidence = [
            {key: value for key, value in item.items() if key != "path"}
            for item in expected_evidence
        ]
        claims = [
            {"claimRef": claim["claimRef"], "citedEvidence": evidence}
            for claim in required_claims
        ]
        cases.append(
            {
                "blindScore": 0 if entry["answerability"] == "unanswerable" else 2,
                "caseRef": entry["caseRef"],
                "claims": claims,
                "criticalContradiction": False,
                "observedEvidence": evidence,
                "refused": entry["answerability"] == "unanswerable",
                "securityObservation": {
                    "events": [],
                    "status": "observed",
                },
            }
        )
    return {
        "answerJudge": {
            "modelRef": "synthetic-blind-judge-model",
            "profileRef": "synthetic-answer-judge-v1",
        },
        "cases": cases,
        "schemaVersion": "context-engine-eval-run-v1",
    }


def test_cli_emits_layered_report_but_pending_slice_gate_is_not_green(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / ".context-engine/eval/report.json"
    output_path.parents[1].mkdir()
    write_golden(golden_path, entries)
    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        golden_set,
        lock_path,
        authority="maintainer",
        reason="synthetic-test-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    run_path.write_text(json.dumps(_run_document(entries)), encoding="utf-8")

    main(
        [
            "report",
            "--golden-set",
            str(golden_path),
            "--lock",
            str(lock_path),
            "--run",
            str(run_path),
            "--output",
            str(output_path),
            "--generated-at",
            "2026-07-29T12:00:00Z",
        ]
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["status"] == "PENDING_PREREGISTRATION"
    assert report["retrieval"]["status"] == "measured"
    assert report["citation"]["status"] == "pass"
    assert report["answer"]["status"] == "pending_preregistration"
    assert report["security"] == {
        "missingContextFallbackCount": 0,
        "status": "pass",
        "unauthorizedEvidenceCount": 0,
        "wrongOrganizationEffectCount": 0,
    }
    assert set(report["slices"]) == {"answer", "citation", "retrieval"}
    assert {
        item["status"]
        for layer in report["slices"].values()
        for item in layer
    } == {"pending_preregistration"}
    citation_single_doc = next(
        item
        for item in report["slices"]["citation"]
        if item["slice_name"] == "single_doc"
    )
    assert citation_single_doc["case_count"] == len(entries)


def test_cli_validate_refuses_pilot_without_lock(tmp_path: Path) -> None:
    golden_path = tmp_path / "golden.json"
    write_golden(golden_path, valid_composed_entries())

    try:
        main(["validate", "--golden-set", str(golden_path)])
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("unlocked pilot must be refused")


def test_cli_refuses_report_output_outside_ignored_directory(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    run_path = tmp_path / "run.json"
    write_golden(golden_path, entries)
    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        golden_set,
        lock_path,
        authority="maintainer",
        reason="synthetic-test-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    run_path.write_text(json.dumps(_run_document(entries)), encoding="utf-8")

    try:
        main(
            [
                "report",
                "--golden-set",
                str(golden_path),
                "--lock",
                str(lock_path),
                "--run",
                str(run_path),
                "--output",
                str(tmp_path / "tracked-report.json"),
                "--generated-at",
                "2026-07-29T12:00:00Z",
            ]
        )
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("tracked report output must be refused")


def test_cli_refuses_output_traversal_from_ignored_directory(tmp_path: Path) -> None:
    ignored = tmp_path / ".context-engine"
    ignored.mkdir()

    with pytest.raises(ValueError, match="ignored .context-engine"):
        from applications.eval_v1 import _require_ignored_output

        _require_ignored_output(ignored / ".." / "tracked-report.json")


def test_cli_refuses_symlink_escape_from_ignored_directory(tmp_path: Path) -> None:
    ignored = tmp_path / ".context-engine"
    outside = tmp_path / "outside"
    ignored.mkdir()
    outside.mkdir()
    (ignored / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="ignored .context-engine"):
        from applications.eval_v1 import _require_ignored_output

        _require_ignored_output(ignored / "escape" / "report.json")


def test_cli_creates_exact_ignored_root_in_clean_checkout(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / ".context-engine/eval/report.json"
    write_golden(golden_path, entries)
    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        golden_set,
        lock_path,
        authority="maintainer",
        reason="synthetic-test-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    run_path.write_text(json.dumps(_run_document(entries)), encoding="utf-8")

    _run_report(golden_path, lock_path, run_path, output_path)

    assert output_path.is_file()


def test_cli_unresolvable_citation_fails_whole_report(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    claims = cast(list[dict[str, object]], cases[5]["claims"])
    claims[0]["citedEvidence"] = [
        {
            "fragmentRef": "synthetic-fragment-unresolvable",
            "resourceRef": "synthetic-resource-unresolvable",
            "revisionRef": "synthetic-revision-unresolvable",
            "sourceRef": "synthetic-source-unresolvable",
        }
    ]
    run_path.write_text(json.dumps(run), encoding="utf-8")

    _run_report(golden_path, lock_path, run_path, output_path)
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["citation"]["status"] == "fail"
    assert report["status"] == "FAIL"


def test_cli_all_unanswerable_run_keeps_every_case_in_citation_slices(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    for entry in entries:
        entry["answerability"] = "unanswerable"
        entry["expectedEvidence"] = []
        entry["requiredClaims"] = []
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )

    _run_report(golden_path, lock_path, run_path, output_path)
    report = json.loads(output_path.read_text(encoding="utf-8"))
    single_doc = next(
        item
        for item in report["slices"]["citation"]
        if item["slice_name"] == "single_doc"
    )

    assert report["citation"]["status"] == "pass"
    assert single_doc["case_count"] == len(entries)


def test_cli_refuses_missing_security_observation_instead_of_defaulting_clean(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    del cases[0]["securityObservation"]
    run_path.write_text(json.dumps(run), encoding="utf-8")

    _assert_report_refused(golden_path, lock_path, run_path, output_path)


def test_cli_refuses_not_observed_security_sentinel_as_a_distinct_type(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    cases[0]["securityObservation"] = {"status": "not_observed"}
    run_path.write_text(json.dumps(run), encoding="utf-8")

    loaded = load_evaluation_run(run_path)
    assert isinstance(
        loaded.cases[0].security_observation,
        SecurityObservationNotObserved,
    )
    _assert_report_refused(golden_path, lock_path, run_path, output_path)


def test_cli_refuses_malformed_security_observation_instead_of_coercing_it(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    cases[0]["securityObservation"] = {
        "events": "not-an-observation-list",
        "status": "observed",
    }
    run_path.write_text(json.dumps(run), encoding="utf-8")

    _assert_report_refused(golden_path, lock_path, run_path, output_path)


def test_cli_refuses_legacy_caller_authored_security_counts(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    cases[0]["unauthorizedEvidenceCount"] = 0
    cases[0]["wrongOrganizationEffectCount"] = 0
    cases[0]["missingContextFallbackCount"] = 0
    run_path.write_text(json.dumps(run), encoding="utf-8")

    _assert_report_refused(golden_path, lock_path, run_path, output_path)


@pytest.mark.parametrize(
    "kind",
    (
        "unauthorized_evidence",
        "wrong_organization_effect",
        "missing_context_fallback",
    ),
)
def test_cli_one_observed_security_violation_vetoes_the_whole_report(
    tmp_path: Path,
    kind: str,
) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    run = _run_document(entries)
    cases = cast(list[dict[str, object]], run["cases"])
    cases[0]["securityObservation"] = {
        "events": [
            {
                "kind": kind,
                "observationRef": f"synthetic-{kind}-observation",
            }
        ],
        "status": "observed",
    }
    run_path.write_text(json.dumps(run), encoding="utf-8")

    _run_report(golden_path, lock_path, run_path, output_path)
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["status"] == "FAIL"
    assert report["security"]["status"] == "fail"


def test_cli_refuses_calibration_for_a_different_pilot_digest(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    golden_path, lock_path, run_path, output_path = _write_report_inputs(
        tmp_path, entries
    )
    source_path = Path(__file__).resolve().parents[2] / "eval/thresholds/v1.json"
    thresholds = json.loads(source_path.read_text(encoding="utf-8"))
    old_values = {
        "answer": thresholds["answer"],
        "sliceFloors": thresholds["sliceFloors"],
    }
    new_values = json.loads(json.dumps(old_values))
    new_values["answer"]["minimumNormalizedScore"] = {
        "status": "configured",
        "value": 0.75,
    }
    thresholds["answer"] = new_values["answer"]
    thresholds["sliceFloors"] = new_values["sliceFloors"]
    thresholds["calibration"]["recordedEvents"] = [
        {
            "authority": "maintainer",
            "newValues": new_values,
            "oldValues": old_values,
            "pilotDigest": "a" * 64,
            "reason": "synthetic-other-pilot-calibration",
            "recordedAt": "2026-07-29T12:00:00Z",
        }
    ]
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(
            [
                "report",
                "--golden-set",
                str(golden_path),
                "--lock",
                str(lock_path),
                "--run",
                str(run_path),
                "--thresholds",
                str(thresholds_path),
                "--output",
                str(output_path),
                "--generated-at",
                "2026-07-29T12:00:00Z",
            ]
        )
    assert error.value.code == 1
    assert not output_path.exists()


def _write_report_inputs(
    tmp_path: Path,
    entries: list[dict[str, object]],
) -> tuple[Path, Path, Path, Path]:
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    run_path = tmp_path / "run.json"
    output_path = tmp_path / ".context-engine/eval/report.json"
    output_path.parents[1].mkdir()
    write_golden(golden_path, entries)
    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        golden_set,
        lock_path,
        authority="maintainer",
        reason="synthetic-test-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    run_path.write_text(json.dumps(_run_document(entries)), encoding="utf-8")
    return golden_path, lock_path, run_path, output_path


def _run_report(
    golden_path: Path,
    lock_path: Path,
    run_path: Path,
    output_path: Path,
) -> None:
    main(
        [
            "report",
            "--golden-set",
            str(golden_path),
            "--lock",
            str(lock_path),
            "--run",
            str(run_path),
            "--output",
            str(output_path),
            "--generated-at",
            "2026-07-29T12:00:00Z",
        ]
    )


def _assert_report_refused(
    golden_path: Path,
    lock_path: Path,
    run_path: Path,
    output_path: Path,
) -> None:
    with pytest.raises(SystemExit) as error:
        _run_report(golden_path, lock_path, run_path, output_path)
    assert error.value.code == 1
    assert not output_path.exists()
