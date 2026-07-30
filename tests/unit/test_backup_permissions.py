from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.learning.backup import (
    SNAPSHOT_DIRECTORY_MODE,
    SNAPSHOT_FILE_MODE,
    GoldenBackupUnavailable,
    create_backup,
    recover_backup,
    verify_backup,
)
from engine.learning.golden_storage import (
    GOLDEN_BACKUP_ROOT_ENV,
    GOLDEN_ROOT_ENV,
    durable_backup_root,
)
from tests.support.golden_backup import stage_corpus

RECORDED_AT = datetime(2026, 7, 29, 12, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _entries(root: Path) -> Iterator[Path]:
    yield from sorted(root.rglob("*"))


def _snapshot(tmp_path: Path) -> Path:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    stage_corpus(source_root)
    (source_root / "nested").mkdir()
    (source_root / "nested/report.json").write_text("{}", encoding="utf-8")
    backup_root.mkdir()
    outcome = create_backup(source_root, backup_root, recorded_at=RECORDED_AT)
    return backup_root / outcome.snapshot


def test_every_snapshot_file_and_directory_is_owner_only(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)

    assert _mode(snapshot) == SNAPSHOT_DIRECTORY_MODE
    for path in _entries(snapshot):
        expected = (
            SNAPSHOT_DIRECTORY_MODE if path.is_dir() else SNAPSHOT_FILE_MODE
        )
        assert _mode(path) == expected, path.name


def test_snapshot_permissions_do_not_depend_on_the_process_umask(
    tmp_path: Path,
) -> None:
    previous = os.umask(0o000)
    try:
        snapshot = _snapshot(tmp_path)
    finally:
        os.umask(previous)

    assert _mode(snapshot) == SNAPSHOT_DIRECTORY_MODE
    for path in _entries(snapshot):
        expected = (
            SNAPSHOT_DIRECTORY_MODE if path.is_dir() else SNAPSHOT_FILE_MODE
        )
        assert _mode(path) == expected, path.name


def test_a_group_readable_backup_file_fails_verification(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    (snapshot / "golden-v1.json").chmod(0o640)

    with pytest.raises(GoldenBackupUnavailable, match="permissions"):
        verify_backup(snapshot)


def test_a_world_readable_snapshot_directory_fails_verification(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    snapshot.chmod(0o755)

    with pytest.raises(GoldenBackupUnavailable, match="permissions"):
        verify_backup(snapshot)


def test_recovered_files_and_directories_are_owner_only(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    destination = tmp_path / "recovered"
    destination.mkdir(mode=0o755)

    recover_backup(snapshot, destination)

    assert _mode(destination) == SNAPSHOT_DIRECTORY_MODE
    for path in _entries(destination):
        expected = (
            SNAPSHOT_DIRECTORY_MODE if path.is_dir() else SNAPSHOT_FILE_MODE
        )
        assert _mode(path) == expected, path.name


def test_a_backup_root_inside_a_git_worktree_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = tmp_path / "worktree"
    subprocess.run(("git", "init", "--quiet", str(worktree)), check=True)
    inside = worktree / "backups"
    inside.mkdir()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(inside))

    with pytest.raises(ValueError, match="outside every git worktree"):
        durable_backup_root()


def test_a_backup_root_inside_the_repository_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(REPOSITORY_ROOT / "eval"))

    with pytest.raises(ValueError, match="outside every git worktree"):
        durable_backup_root()


def test_a_backup_root_that_shares_the_corpus_deletion_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    inside = corpus / "backups"
    inside.mkdir(parents=True)
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(inside))

    with pytest.raises(ValueError, match="inside the golden root"):
        durable_backup_root()


def test_a_backup_root_that_contains_the_corpus_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer = tmp_path / "durable"
    corpus = outer / "corpus"
    corpus.mkdir(parents=True)
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(outer))

    with pytest.raises(ValueError, match="inside the golden root"):
        durable_backup_root()


def test_backup_and_corpus_roots_must_not_be_the_same_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(corpus))

    with pytest.raises(ValueError, match="inside the golden root"):
        durable_backup_root()


def test_a_backup_root_that_is_a_symlink_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    real = tmp_path / "real-backups"
    corpus.mkdir()
    real.mkdir()
    link = tmp_path / "linked-backups"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(corpus))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(link))

    with pytest.raises(ValueError, match="unavailable"):
        durable_backup_root()


def test_a_corpus_symlink_is_refused_rather_than_followed_into_a_backup(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    stage_corpus(source_root)
    backup_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text("{}", encoding="utf-8")
    (source_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(GoldenBackupUnavailable, match="symlink"):
        create_backup(source_root, backup_root, recorded_at=RECORDED_AT)
