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

    assert lock_document["history"] == [
        {
            "authority": "maintainer",
            "digest": original.pilot_digest,
            "reason": "initial-pilot-lock",
            "recordedAt": "2026-07-29T12:00:00.000000Z",
        },
        {
            "authority": "maintainer",
            "digest": relocked.pilot_digest,
            "reason": "recorded-correction",
            "recordedAt": "2026-07-29T12:05:00.000000Z",
        },
    ]


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
