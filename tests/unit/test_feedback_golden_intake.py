from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.learning.curation_candidate import (
    EvaluationCaseIntake,
    build_curation_candidate,
    curation_candidate_document,
)
from engine.learning.golden import (
    GoldenSetUnavailable,
    create_golden_lock,
    load_golden_case,
    load_golden_set,
)
from engine.learning.golden_intake import admit_evaluation_case
from tests.support.golden import golden_case, valid_composed_entries, write_golden
from tests.unit.test_feedback_curation_candidate import _triaged_feedback


def _intake_case(*, partition: str = "dev") -> dict[str, object]:
    case = golden_case(
        "synthetic-feedback-intake",
        partition=partition,
        answerability="unanswerable",
    )
    case["hardNegativeEvidence"] = []
    return case


def _locked_corpus(tmp_path: Path) -> tuple[Path, Path]:
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    write_golden(golden_path, valid_composed_entries())
    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        golden_set,
        lock_path,
        authority="maintainer",
        reason="synthetic-initial-lock",
        recorded_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    return golden_path, lock_path


def _write_candidate(path: Path, case: dict[str, object]) -> None:
    candidate = build_curation_candidate(
        _triaged_feedback(),
        EvaluationCaseIntake(
            case=load_golden_case(case),
            synthetic=True,
        ),
        proposed_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
    )
    path.write_text(
        json.dumps(curation_candidate_document(candidate)),
        encoding="utf-8",
    )


def test_private_candidate_case_enters_only_the_private_durable_corpus(
    tmp_path: Path,
) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    candidate_path = tmp_path / "private-candidate.json"
    private_case = _intake_case()
    private_case["caseRef"] = "private-feedback-intake"
    private_case["query"] = "Where is my private evaluation input?"
    private_case["expectedAnswer"] = "The private durable corpus owns it."
    private_case["topicCluster"] = "private-feedback-topic"
    candidate = build_curation_candidate(
        _triaged_feedback(),
        EvaluationCaseIntake(
            case=load_golden_case(private_case),
            synthetic=False,
        ),
        proposed_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
    )
    candidate_path.write_text(
        json.dumps(curation_candidate_document(candidate)),
        encoding="utf-8",
    )

    receipt = admit_evaluation_case(
        candidate_path,
        golden_path=golden_path,
        lock_path=lock_path,
    )

    assert receipt.case_ref == "private-feedback-intake"
    assert "private-feedback-intake" in golden_path.read_text(encoding="utf-8")


def test_intake_appends_a_schema_valid_dev_case_through_existing_lock(
    tmp_path: Path,
) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    case_path = tmp_path / "intake.json"
    _write_candidate(case_path, _intake_case())

    outcome = admit_evaluation_case(
        case_path,
        golden_path=golden_path,
        lock_path=lock_path,
    )
    loaded = load_golden_set(golden_path, lock_path=lock_path)

    assert outcome.case_ref == "synthetic-feedback-intake"
    assert outcome.case_count == 71
    assert len(loaded.cases) == 71
    assert loaded.pilot_digest == outcome.pilot_digest
    assert loaded.cases[-1] == EvaluationCaseIntake(
        case=loaded.cases[-1],
        synthetic=True,
    ).case
    assert stat.S_IMODE(golden_path.stat().st_mode) == 0o600


def test_intake_file_permissions_do_not_depend_on_process_umask(
    tmp_path: Path,
) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    case_path = tmp_path / "intake.json"
    _write_candidate(case_path, _intake_case())
    previous = os.umask(0o000)
    try:
        admit_evaluation_case(
            case_path,
            golden_path=golden_path,
            lock_path=lock_path,
        )
    finally:
        os.umask(previous)

    assert stat.S_IMODE(golden_path.stat().st_mode) == 0o600


def test_intake_refuses_unlocked_or_schema_invalid_case_without_writing(
    tmp_path: Path,
) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    before = golden_path.read_bytes()
    unlocked = tmp_path / "unlocked.json"
    write_golden(unlocked, valid_composed_entries())
    case_path = tmp_path / "intake.json"
    invalid = _intake_case()
    invalid["slice"] = "unknown"
    candidate = curation_candidate_document(
        build_curation_candidate(
            _triaged_feedback(),
            EvaluationCaseIntake(
                case=load_golden_case(_intake_case()),
                synthetic=True,
            ),
            proposed_at=datetime(2026, 7, 31, 1, tzinfo=UTC),
        )
    )
    candidate["evaluationCase"] = invalid
    case_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises((GoldenSetUnavailable, RuntimeError)):
        admit_evaluation_case(
            case_path,
            golden_path=golden_path,
            lock_path=lock_path,
        )
    assert golden_path.read_bytes() == before

    _write_candidate(case_path, _intake_case())
    with pytest.raises(GoldenSetUnavailable, match="lock"):
        admit_evaluation_case(
            case_path,
            golden_path=unlocked,
            lock_path=tmp_path / "missing.lock.json",
        )


def test_intake_refuses_a_pilot_case_because_relock_is_a_separate_ceremony(
    tmp_path: Path,
) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    case_path = tmp_path / "intake.json"
    _write_candidate(case_path, _intake_case(partition="pilot"))

    with pytest.raises(GoldenSetUnavailable, match="dev"):
        admit_evaluation_case(
            case_path,
            golden_path=golden_path,
            lock_path=lock_path,
        )


def test_intake_refuses_a_tampered_candidate_without_writing(tmp_path: Path) -> None:
    golden_path, lock_path = _locked_corpus(tmp_path)
    before = golden_path.read_bytes()
    candidate_path = tmp_path / "candidate.json"
    _write_candidate(candidate_path, _intake_case())
    document = json.loads(candidate_path.read_text(encoding="utf-8"))
    document["feedbackBinding"]["releaseGeneration"] = 8
    candidate_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="identity"):
        admit_evaluation_case(
            candidate_path,
            golden_path=golden_path,
            lock_path=lock_path,
        )

    assert golden_path.read_bytes() == before
