from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from engine.learning.backup import (
    MANIFEST_NAME,
    GoldenBackupUnavailable,
    create_backup,
    recover_backup,
    verify_backup,
)
from tests.support.golden_backup import stage_corpus

RECORDED_AT = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _snapshot(tmp_path: Path) -> Path:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    stage_corpus(source_root)
    backup_root.mkdir()
    outcome = create_backup(source_root, backup_root, recorded_at=RECORDED_AT)
    return backup_root / outcome.snapshot


def _rewrite(path: Path, data: bytes) -> None:
    path.chmod(0o600)
    path.write_bytes(data)
    path.chmod(0o600)


def test_verification_accepts_an_untouched_backup(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)

    verification = verify_backup(snapshot)

    assert verification.snapshot == "20260729T120000Z"
    assert verification.recorded_at == "2026-07-29T12:00:00Z"
    assert verification.file_count == 3
    assert len(verification.content_digest) == 64


def test_a_truncated_backup_file_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    target = snapshot / "golden-v1.json"
    _rewrite(target, target.read_bytes()[:-64])

    with pytest.raises(GoldenBackupUnavailable, match="truncated"):
        verify_backup(snapshot)


def test_a_corrupted_backup_file_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    target = snapshot / "golden-v1.json"
    data = bytearray(target.read_bytes())
    data[0] ^= 0xFF
    _rewrite(target, bytes(data))

    with pytest.raises(GoldenBackupUnavailable, match="corrupted"):
        verify_backup(snapshot)


def test_a_missing_backup_file_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    (snapshot / "lineage-map.json").unlink()

    with pytest.raises(GoldenBackupUnavailable, match="missing"):
        verify_backup(snapshot)


def test_an_unexpected_extra_backup_file_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    (snapshot / "injected.json").write_text("{}", encoding="utf-8")
    (snapshot / "injected.json").chmod(0o600)

    with pytest.raises(GoldenBackupUnavailable, match="unexpected"):
        verify_backup(snapshot)


def test_a_manifest_edited_to_match_a_tampered_file_still_fails(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    target = snapshot / "golden-v1.json"
    replacement = b'{"tampered": true}'
    _rewrite(target, replacement)
    manifest_path = snapshot / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in cast(list[dict[str, object]], manifest["files"]):
        if record["path"] == "golden-v1.json":
            record["bytes"] = len(replacement)
            record["sha256"] = (
                "0000000000000000000000000000000000000000000000000000000000000000"
            )
    _rewrite(manifest_path, json.dumps(manifest).encode("utf-8"))

    with pytest.raises(GoldenBackupUnavailable, match="does not match"):
        verify_backup(snapshot)


def test_a_manifest_recorded_at_that_left_its_snapshot_fails_verification(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    manifest_path = snapshot / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["recordedAt"] = "2026-07-28T12:00:00Z"
    _rewrite(manifest_path, json.dumps(manifest).encode("utf-8"))

    with pytest.raises(GoldenBackupUnavailable, match="does not match its snapshot"):
        verify_backup(snapshot)


def test_a_removed_manifest_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    (snapshot / MANIFEST_NAME).unlink()

    with pytest.raises(GoldenBackupUnavailable, match="manifest is unavailable"):
        verify_backup(snapshot)


def test_a_corrupted_backup_is_refused_instead_of_silently_recovered(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    target = snapshot / "golden-v1.json"
    data = bytearray(target.read_bytes())
    data[0] ^= 0xFF
    _rewrite(target, bytes(data))
    destination = tmp_path / "recovered"
    destination.mkdir()

    with pytest.raises(GoldenBackupUnavailable, match="corrupted"):
        recover_backup(snapshot, destination)

    assert list(destination.iterdir()) == []
