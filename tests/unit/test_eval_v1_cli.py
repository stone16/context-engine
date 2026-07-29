from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from applications.eval_v1 import main
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
                "missingContextFallbackCount": 0,
                "observedEvidence": evidence,
                "refused": entry["answerability"] == "unanswerable",
                "unauthorizedEvidenceCount": 0,
                "wrongOrganizationEffectCount": 0,
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
    assert report["citation"]["status"] == "measured"
    assert report["answer"]["status"] == "pending_preregistration"
    assert set(report["slices"]) == {"answer", "citation", "retrieval"}
    assert {
        item["status"]
        for layer in report["slices"].values()
        for item in layer
    } == {"pending_preregistration"}


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
