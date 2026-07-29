from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.learning.golden import (
    GoldenSetUnavailable,
    create_golden_lock,
    load_golden_set,
    relock_golden_set,
)
from tests.support.golden import valid_composed_entries, write_golden


def test_editing_locked_pilot_case_is_refused_and_relock_is_recorded(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    entries = valid_composed_entries()
    write_golden(golden_path, entries)
    recorded_at = datetime(2026, 7, 29, 12, tzinfo=UTC)
    original = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        original,
        lock_path,
        authority="maintainer",
        reason="initial-pilot-lock",
        recorded_at=recorded_at,
    )

    entries[-1]["expectedAnswer"] = "synthetic-edited-expected-answer"
    write_golden(golden_path, entries)

    with pytest.raises(GoldenSetUnavailable, match="locked pilot digest"):
        load_golden_set(golden_path, lock_path=lock_path)

    relock_golden_set(
        golden_path,
        lock_path,
        authority="maintainer",
        reason="recorded-correction",
        recorded_at=recorded_at + timedelta(minutes=5),
    )
    relocked = load_golden_set(golden_path, lock_path=lock_path)
    lock_document = json.loads(lock_path.read_text(encoding="utf-8"))

    first, second = lock_document["history"]
    assert first == {
        "authority": "maintainer",
        "digest": original.pilot_digest,
        "entryDigest": first["entryDigest"],
        "previousEntryDigest": None,
        "reason": "initial-pilot-lock",
        "recordedAt": "2026-07-29T12:00:00.000000Z",
    }
    assert second == {
        "authority": "maintainer",
        "digest": relocked.pilot_digest,
        "entryDigest": second["entryDigest"],
        "previousEntryDigest": first["entryDigest"],
        "reason": "recorded-correction",
        "recordedAt": "2026-07-29T12:05:00.000000Z",
    }
    assert len(first["entryDigest"]) == 64
    assert len(second["entryDigest"]) == 64


def test_pilot_cannot_load_without_a_lock_outside_initial_lock_flow(
    tmp_path: Path,
) -> None:
    golden_path = tmp_path / "golden.json"
    write_golden(golden_path, valid_composed_entries())

    with pytest.raises(GoldenSetUnavailable, match="requires a lock"):
        load_golden_set(golden_path)


def test_lock_active_digest_cannot_change_without_history_entry(
    tmp_path: Path,
) -> None:
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
        reason="synthetic-test-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    document["activePilotDigest"] = "0" * 64
    lock_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(GoldenSetUnavailable, match="history"):
        load_golden_set(golden_path, lock_path=lock_path)


def test_lock_governance_states_its_accident_detection_boundary_honestly() -> None:
    readme = (
        Path(__file__).resolve().parents[2] / "eval/README.md"
    ).read_text(encoding="utf-8").lower()

    assert "accidental-edit detection" in readme
    assert "not forgery-proof" in readme
    assert "co-located" in readme


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("authority", "rewritten-maintainer"),
        ("digest", "0" * 64),
        ("reason", "rewritten-history-reason"),
        ("recordedAt", "2026-07-29T11:59:00.000000Z"),
    ),
)
def test_rewritten_prior_lock_history_entry_is_refused(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    golden_path = tmp_path / "golden.json"
    lock_path = tmp_path / "golden.lock.json"
    entries = valid_composed_entries()
    write_golden(golden_path, entries)
    initial_time = datetime(2026, 7, 29, 12, tzinfo=UTC)
    original = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    create_golden_lock(
        original,
        lock_path,
        authority="maintainer",
        reason="initial-pilot-lock",
        recorded_at=initial_time,
    )
    entries[-1]["expectedAnswer"] = "synthetic-recorded-correction"
    write_golden(golden_path, entries)
    relock_golden_set(
        golden_path,
        lock_path,
        authority="maintainer",
        reason="recorded-correction",
        recorded_at=initial_time + timedelta(minutes=5),
    )
    lock_document = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_document["history"][0][field] = replacement
    lock_path.write_text(json.dumps(lock_document), encoding="utf-8")

    with pytest.raises(GoldenSetUnavailable, match="history entry"):
        load_golden_set(golden_path, lock_path=lock_path)
