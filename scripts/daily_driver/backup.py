"""Owner-only PostgreSQL logical backup and scratch-restore drill."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Final

from engine.learning.golden_storage import (
    require_durable_golden_path,
    require_durable_storage_root,
)
from scripts.daily_driver.environment import EnvironmentRefused, load_owner_environment

BACKUP_DIRECTORY_MODE: Final = 0o700
BACKUP_FILE_MODE: Final = 0o600
_SCRATCH_DATABASE = re.compile(r"context_engine_restore_[a-z0-9_]+")
_CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}")


class BackupRefused(ValueError):
    """The backup could not be created or restored without weakening safety."""


@dataclass(frozen=True)
class BackupOutcome:
    """Path-free callers may inspect whether an exact backup was newly recorded."""

    path: Path
    created: bool


PgDump = Callable[[tuple[str, ...], BinaryIO], int]


def require_safe_backup_root(root: Path) -> Path:
    """Reuse the golden-storage durable-root convention and seal permissions."""

    try:
        resolved = require_durable_storage_root(root)
    except ValueError as error:
        raise BackupRefused(str(error)) from None
    if resolved.stat().st_uid != os.getuid():
        raise BackupRefused("backup root must be owned by the current user")
    os.chmod(resolved, BACKUP_DIRECTORY_MODE)
    return resolved


def _database_environment(checkout: Path) -> tuple[Path, dict[str, str]]:
    environment_path = checkout / ".context-engine" / "database.env"
    try:
        loaded = load_owner_environment(
            environment_path,
            required=(
                "CONTEXT_ENGINE_COMPOSE_PROJECT",
                "POSTGRES_USER",
                "POSTGRES_DB",
            ),
        )
    except EnvironmentRefused as error:
        raise BackupRefused(str(error)) from None
    return environment_path, dict(loaded)


def _compose_command(
    *,
    docker_executable: str,
    checkout: Path,
    environment_path: Path,
    project: str,
    postgres_arguments: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        docker_executable,
        "compose",
        "--project-directory",
        str(checkout),
        "--env-file",
        str(environment_path),
        "--project-name",
        project,
        "exec",
        "-T",
        "postgres",
        *postgres_arguments,
    )


def _run_dump(command: tuple[str, ...], output: BinaryIO) -> int:
    return subprocess.run(command, stdout=output, check=False).returncode


def create_database_backup(
    *,
    checkout: Path,
    backup_root: Path,
    recorded_at: datetime | None = None,
    pg_dump: PgDump = _run_dump,
    docker_executable: str = "docker",
) -> BackupOutcome:
    """Stage one custom dump, fsync it, then atomically publish it."""

    root = require_safe_backup_root(backup_root)
    environment_path, environment = _database_environment(checkout.resolve())
    instant = datetime.now(UTC) if recorded_at is None else recorded_at
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise BackupRefused("backup instant must be timezone-aware")
    recorded_utc = instant.astimezone(UTC)
    target = root / f"context-engine-{recorded_utc:%Y%m%dT%H%M%SZ}.dump"
    try:
        require_durable_golden_path(target, root=root)
    except ValueError as error:
        raise BackupRefused(str(error).replace("golden corpus", "backup")) from None
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise BackupRefused("recorded backup target is unsafe")
        if target.stat().st_mode & 0o777 != BACKUP_FILE_MODE:
            raise BackupRefused("recorded backup must have mode 0600")
        return BackupOutcome(path=target, created=False)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=root,
        prefix=".context-engine-backup.partial-",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, BACKUP_FILE_MODE)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            command = _compose_command(
                docker_executable=docker_executable,
                checkout=checkout,
                environment_path=environment_path,
                project=environment["CONTEXT_ENGINE_COMPOSE_PROJECT"],
                postgres_arguments=(
                    "pg_dump",
                    "--format=custom",
                    f"--username={environment['POSTGRES_USER']}",
                    f"--dbname={environment['POSTGRES_DB']}",
                ),
            )
            if pg_dump(command, output) != 0:
                raise BackupRefused("pg_dump failed; no backup was published")
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size == 0:
            raise BackupRefused("pg_dump produced no backup")
        os.replace(temporary, target)
        os.chmod(target, BACKUP_FILE_MODE)
        _fsync_directory(root)
        return BackupOutcome(path=target, created=True)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def restore_database_backup(
    *,
    checkout: Path,
    dump_path: Path,
    scratch_database: str,
) -> None:
    """Restore an owner-only dump into one exact, disposable scratch database."""

    if _SCRATCH_DATABASE.fullmatch(scratch_database) is None:
        raise BackupRefused("scratch database name is outside the restore namespace")
    root = require_safe_backup_root(dump_path.parent)
    try:
        require_durable_golden_path(dump_path, root=root)
    except ValueError as error:
        raise BackupRefused(str(error).replace("golden corpus", "backup")) from None
    if (
        dump_path.is_symlink()
        or not dump_path.is_file()
        or dump_path.stat().st_mode & 0o777 != BACKUP_FILE_MODE
    ):
        raise BackupRefused("restore input must be an owner-only regular backup")
    environment_path, environment = _database_environment(checkout.resolve())
    if scratch_database == environment["POSTGRES_DB"]:
        raise BackupRefused("restore drill may not target the live database")

    project = environment["CONTEXT_ENGINE_COMPOSE_PROJECT"]
    user = environment["POSTGRES_USER"]
    _checked_compose(
        _compose_command(
            docker_executable="docker",
            checkout=checkout,
            environment_path=environment_path,
            project=project,
            postgres_arguments=(
                "dropdb",
                "--if-exists",
                "--force",
                f"--username={user}",
                scratch_database,
            ),
        ),
        checkout=checkout,
    )
    _checked_compose(
        _compose_command(
            docker_executable="docker",
            checkout=checkout,
            environment_path=environment_path,
            project=project,
            postgres_arguments=("createdb", f"--username={user}", scratch_database),
        ),
        checkout=checkout,
    )
    container_id = _postgres_container_id(
        checkout=checkout,
        environment_path=environment_path,
        project=project,
    )
    restore_command = (
        "docker",
        "exec",
        "--interactive",
        container_id,
        "pg_restore",
        "--exit-on-error",
        "--username",
        user,
        "--dbname",
        scratch_database,
    )
    with dump_path.open("rb") as dump:
        result = subprocess.run(
            restore_command,
            cwd=checkout,
            stdin=dump,
            check=False,
        )
    if result.returncode != 0:
        raise BackupRefused("pg_restore failed")
    _verify_restored_schema(
        checkout=checkout,
        environment_path=environment_path,
        project=project,
        user=user,
        scratch_database=scratch_database,
    )


def _checked_compose(command: tuple[str, ...], *, checkout: Path) -> None:
    if subprocess.run(command, cwd=checkout, check=False).returncode != 0:
        raise BackupRefused("scratch database preparation failed")


def _postgres_container_id(
    *,
    checkout: Path,
    environment_path: Path,
    project: str,
) -> str:
    result = subprocess.run(
        (
            "docker",
            "compose",
            "--project-directory",
            str(checkout),
            "--env-file",
            str(environment_path),
            "--project-name",
            project,
            "ps",
            "--quiet",
            "postgres",
        ),
        cwd=checkout,
        check=False,
        capture_output=True,
        text=True,
    )
    container_id = result.stdout.strip()
    if result.returncode != 0 or _CONTAINER_ID.fullmatch(container_id) is None:
        raise BackupRefused("PostgreSQL container is unavailable")
    return container_id


def _verify_restored_schema(
    *,
    checkout: Path,
    environment_path: Path,
    project: str,
    user: str,
    scratch_database: str,
) -> None:
    command = _compose_command(
        docker_executable="docker",
        checkout=checkout,
        environment_path=environment_path,
        project=project,
        postgres_arguments=(
            "psql",
            "--tuples-only",
            "--no-align",
            "--username",
            user,
            "--dbname",
            scratch_database,
            "--command",
            "SELECT to_regclass('public.alembic_version') IS NOT NULL",
        ),
    )
    result = subprocess.run(
        command,
        cwd=checkout,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "t":
        raise BackupRefused("restored database failed its schema check")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
