from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest

from scripts.daily_driver.backup import (
    BackupRefused,
    create_database_backup,
    require_safe_backup_root,
)


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    state = checkout / ".context-engine"
    state.mkdir(parents=True)
    (checkout / ".git").mkdir()
    environment = state / "database.env"
    environment.write_text(
        "CONTEXT_ENGINE_COMPOSE_PROJECT=synthetic-project\n"
        "POSTGRES_USER=synthetic-user\n"
        "POSTGRES_DB=synthetic-database\n",
        encoding="utf-8",
    )
    environment.chmod(0o600)
    return checkout


def test_backup_root_is_absolute_owner_only_and_outside_every_worktree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backups"
    root.mkdir(mode=0o755)

    resolved = require_safe_backup_root(root)

    assert resolved == root.resolve()
    assert resolved.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("unsafe_part", (".context-engine", "nested/.context-engine"))
def test_backup_root_under_context_engine_state_is_refused(
    tmp_path: Path,
    unsafe_part: str,
) -> None:
    root = tmp_path / unsafe_part / "backups"
    root.mkdir(parents=True)

    with pytest.raises(BackupRefused, match=r"durable .*root"):
        require_safe_backup_root(root)


def test_backup_root_inside_a_git_worktree_is_refused(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(("git", "init", "--quiet", str(worktree)), check=True)
    root = worktree / "backups"
    root.mkdir()

    with pytest.raises(BackupRefused, match="outside every git worktree"):
        require_safe_backup_root(root)


def test_backup_root_symlink_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(BackupRefused, match=r"durable .*root"):
        require_safe_backup_root(linked)


def test_pg_dump_is_staged_owner_only_then_atomically_published(
    tmp_path: Path,
) -> None:
    checkout = _checkout(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    def pg_dump(command: tuple[str, ...], output: BinaryIO) -> int:
        assert command[-4:] == (
            "pg_dump",
            "--format=custom",
            "--username=synthetic-user",
            "--dbname=synthetic-database",
        )
        output.write(b"synthetic custom dump")
        return 0

    outcome = create_database_backup(
        checkout=checkout,
        backup_root=backup_root,
        recorded_at=datetime(2026, 7, 30, 18, 0, tzinfo=UTC),
        pg_dump=pg_dump,
    )

    assert outcome.path.parent == backup_root
    assert outcome.path.name == "context-engine-20260730T180000Z.dump"
    assert outcome.path.read_bytes() == b"synthetic custom dump"
    assert outcome.path.stat().st_mode & 0o777 == 0o600
    assert not tuple(backup_root.glob("*.partial-*"))


def test_failed_pg_dump_leaves_no_partial_or_published_dump(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    def failing_dump(command: tuple[str, ...], output: BinaryIO) -> int:
        del command
        output.write(b"partial secret content")
        return 9

    with pytest.raises(BackupRefused, match="pg_dump failed"):
        create_database_backup(
            checkout=checkout,
            backup_root=backup_root,
            recorded_at=datetime(2026, 7, 30, 18, 0, tzinfo=UTC),
            pg_dump=failing_dump,
        )

    assert list(backup_root.iterdir()) == []


def test_backup_refuses_a_group_readable_database_environment(
    tmp_path: Path,
) -> None:
    checkout = _checkout(tmp_path)
    (checkout / ".context-engine" / "database.env").chmod(0o640)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    with pytest.raises(BackupRefused, match="mode 0600"):
        create_database_backup(
            checkout=checkout,
            backup_root=backup_root,
            recorded_at=datetime.now(UTC),
            pg_dump=lambda _command, _output: os.EX_OK,
        )
