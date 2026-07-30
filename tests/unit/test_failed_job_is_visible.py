from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.daily_driver.jobs import run_visible_job


def test_a_failed_scheduled_job_leaves_a_durable_non_silent_signal(
    tmp_path: Path,
) -> None:
    signal_root = tmp_path / "signals"

    exit_code = run_visible_job(
        job="backup",
        signal_root=signal_root,
        action=lambda: 23,
        recorded_at=datetime(2026, 7, 30, 19, 45, tzinfo=UTC),
    )

    assert exit_code == 23
    markers = list((signal_root / "backup").glob("*.json"))
    assert len(markers) == 1
    marker = markers[0]
    assert marker.stat().st_mode & 0o777 == 0o600
    assert signal_root.stat().st_mode & 0o777 == 0o700
    assert marker.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "attemptedAt": "2026-07-30T19:45:00Z",
        "exitCode": 23,
        "job": "backup",
        "status": "FAILED",
    }


def test_a_successful_job_does_not_fabricate_a_failure_signal(
    tmp_path: Path,
) -> None:
    signal_root = tmp_path / "signals"

    assert (
        run_visible_job(
            job="health",
            signal_root=signal_root,
            action=lambda: 0,
            recorded_at=datetime(2026, 7, 30, 19, 45, tzinfo=UTC),
        )
        == 0
    )
    assert not signal_root.exists()


def test_repeated_failures_each_leave_a_durable_signal(tmp_path: Path) -> None:
    signal_root = tmp_path / "signals"

    first = run_visible_job(
        job="backup",
        signal_root=signal_root,
        action=lambda: 7,
        recorded_at=datetime(2026, 7, 30, 19, 45, tzinfo=UTC),
    )
    second = run_visible_job(
        job="backup",
        signal_root=signal_root,
        action=lambda: 9,
        recorded_at=datetime(2026, 7, 30, 19, 45, tzinfo=UTC),
    )

    assert (first, second) == (7, 9)
    markers = sorted((signal_root / "backup").glob("*.json"))
    assert len(markers) == 2
    assert {
        json.loads(path.read_text())["exitCode"] for path in markers
    } == {7, 9}
