from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.learning import backup as backup_module
from engine.learning.backup import (
    GoldenBackupUnavailable,
    create_backup,
    latest_snapshot,
    read_manifest,
    snapshot_names,
    verify_backup,
)
from tests.support.golden_backup import stage_corpus

FIRST = datetime(2026, 7, 29, 12, tzinfo=UTC)
SECOND = datetime(2026, 7, 30, 12, tzinfo=UTC)
EARLIER = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    stage_corpus(source_root)
    backup_root.mkdir()
    return source_root, backup_root


def _touch_corpus(source_root: Path) -> None:
    path = source_root / "judge-observations.json"
    path.write_text(json.dumps({"note": "synthetic-observation"}), encoding="utf-8")


def test_repeating_a_backup_of_unchanged_content_records_no_second_snapshot(
    tmp_path: Path,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    first = create_backup(source_root, backup_root, recorded_at=FIRST)

    second = create_backup(source_root, backup_root, recorded_at=SECOND)

    assert first.status == "created"
    assert second.status == "unchanged"
    assert second.snapshot == first.snapshot
    assert second.content_digest == first.content_digest
    assert snapshot_names(backup_root) == (first.snapshot,)


def test_changed_content_records_a_new_snapshot_beside_the_previous_one(
    tmp_path: Path,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    first = create_backup(source_root, backup_root, recorded_at=FIRST)
    _touch_corpus(source_root)

    second = create_backup(source_root, backup_root, recorded_at=SECOND)

    assert second.status == "created"
    assert second.content_digest != first.content_digest
    assert snapshot_names(backup_root) == (first.snapshot, second.snapshot)
    assert verify_backup(backup_root / first.snapshot).content_digest == (
        first.content_digest
    )


def test_a_backup_older_than_the_latest_snapshot_is_refused_by_default(
    tmp_path: Path,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    create_backup(source_root, backup_root, recorded_at=SECOND)
    _touch_corpus(source_root)

    with pytest.raises(GoldenBackupUnavailable, match="older"):
        create_backup(source_root, backup_root, recorded_at=EARLIER)

    assert snapshot_names(backup_root) == ("20260730T120000Z",)


def test_an_explicitly_allowed_older_backup_never_becomes_the_recovery_source(
    tmp_path: Path,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    newest = create_backup(source_root, backup_root, recorded_at=SECOND)
    _touch_corpus(source_root)

    older = create_backup(
        source_root,
        backup_root,
        recorded_at=EARLIER,
        allow_older=True,
    )

    assert older.status == "created"
    assert latest_snapshot(backup_root) == newest.snapshot
    assert read_manifest(backup_root / newest.snapshot).content_digest == (
        newest.content_digest
    )


def test_an_interrupted_copy_refuses_instead_of_recording_a_partial_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    written: list[Path] = []
    original = backup_module._write_private_file

    def _fail_after_first(path: Path, data: bytes) -> None:
        if written:
            raise OSError("synthetic interrupted write")
        written.append(path)
        original(path, data)

    monkeypatch.setattr(backup_module, "_write_private_file", _fail_after_first)

    with pytest.raises(GoldenBackupUnavailable, match="did not complete"):
        create_backup(source_root, backup_root, recorded_at=FIRST)

    assert snapshot_names(backup_root) == ()
    assert list(backup_root.iterdir()) == []


def test_an_interrupted_backup_leaves_the_previous_snapshot_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    recorded = create_backup(source_root, backup_root, recorded_at=FIRST)
    _touch_corpus(source_root)

    def _always_fail(path: Path, data: bytes) -> None:
        raise OSError("synthetic interrupted write")

    monkeypatch.setattr(backup_module, "_write_private_file", _always_fail)

    with pytest.raises(GoldenBackupUnavailable, match="did not complete"):
        create_backup(source_root, backup_root, recorded_at=SECOND)

    assert snapshot_names(backup_root) == (recorded.snapshot,)
    assert verify_backup(backup_root / recorded.snapshot).content_digest == (
        recorded.content_digest
    )


def test_a_leftover_staging_entry_refuses_the_next_backup(tmp_path: Path) -> None:
    source_root, backup_root = _roots(tmp_path)
    (backup_root / ".staging-20260729T120000Z").mkdir()

    with pytest.raises(GoldenBackupUnavailable, match="staging"):
        create_backup(source_root, backup_root, recorded_at=SECOND)


def test_an_already_recorded_snapshot_instant_is_never_overwritten(
    tmp_path: Path,
) -> None:
    source_root, backup_root = _roots(tmp_path)
    recorded = create_backup(source_root, backup_root, recorded_at=FIRST)
    _touch_corpus(source_root)

    with pytest.raises(GoldenBackupUnavailable, match="already recorded"):
        create_backup(
            source_root,
            backup_root,
            recorded_at=FIRST,
            allow_older=True,
        )

    assert read_manifest(backup_root / recorded.snapshot).content_digest == (
        recorded.content_digest
    )


def test_an_empty_corpus_is_refused_rather_than_recorded_as_a_backup(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    source_root.mkdir()
    backup_root.mkdir()

    with pytest.raises(GoldenBackupUnavailable, match="empty"):
        create_backup(source_root, backup_root, recorded_at=FIRST)
