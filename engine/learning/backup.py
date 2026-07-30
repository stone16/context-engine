"""Atomic, verifiable, owner-only snapshots of the private golden corpus.

The corpus is the project's only quality authority and lives outside every
disposable worktree. A snapshot is written into a staging directory, verified
byte for byte, and only then renamed into place, so an interrupted run leaves
no partially recorded backup behind.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final, Literal, cast

BACKUP_MANIFEST_VERSION: Final = "context-engine-golden-backup-v1"
MANIFEST_NAME: Final = "backup-manifest.json"
SNAPSHOT_DIRECTORY_MODE: Final = 0o700
SNAPSHOT_FILE_MODE: Final = 0o600
MAXIMUM_BACKUP_BYTES: Final = 512 * 1024 * 1024
_SNAPSHOT_NAME_FORMAT: Final = "%Y%m%dT%H%M%SZ"
_STAGING_PREFIX: Final = ".staging-"
_MANIFEST_FIELDS: Final = frozenset(
    {"contentDigest", "files", "recordedAt", "schemaVersion"}
)
_RECORD_FIELDS: Final = frozenset({"bytes", "path", "sha256"})


class GoldenBackupUnavailable(RuntimeError):
    """A partial, corrupted, or out-of-order backup is refused, never used."""


def _instant(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise GoldenBackupUnavailable("backup time must be aware UTC")
    return value.astimezone(UTC).replace(microsecond=0)


def _recorded_at(value: datetime) -> str:
    return _instant(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _snapshot_name(value: datetime) -> str:
    return _instant(value).strftime(_SNAPSHOT_NAME_FORMAT)


def _snapshot_instant(name: str) -> datetime:
    try:
        return datetime.strptime(name, _SNAPSHOT_NAME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise GoldenBackupUnavailable("backup snapshot name is unavailable") from None


def _recorded_instant(value: object) -> datetime:
    if type(value) is not str:
        raise GoldenBackupUnavailable("backup time is unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GoldenBackupUnavailable("backup time is unavailable") from None
    instant = _instant(parsed)
    if _recorded_at(instant) != value:
        raise GoldenBackupUnavailable("backup time is unavailable")
    return instant


def _relative_name(value: object) -> str:
    if type(value) is not str or not value or len(value) > 1_024:
        raise GoldenBackupUnavailable("backup content path is unavailable")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or str(parsed) != value
        or "\\" in value
        or value == MANIFEST_NAME
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise GoldenBackupUnavailable("backup content path is unavailable")
    return value


def _hex_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GoldenBackupUnavailable("backup digest is unavailable")
    return value


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class BackupFileRecord:
    """One backed-up file's relative name, exact size, and content digest."""

    path: str
    digest: str
    size: int

    def __post_init__(self) -> None:
        _relative_name(self.path)
        _hex_digest(self.digest)
        if type(self.size) is not int or self.size < 0:
            raise GoldenBackupUnavailable("backup content size is unavailable")

    def document(self) -> dict[str, object]:
        return {"bytes": self.size, "path": self.path, "sha256": self.digest}


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """The complete recorded content of one snapshot."""

    recorded_at: str
    files: tuple[BackupFileRecord, ...] = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.files) is not tuple or not self.files:
            raise GoldenBackupUnavailable("backup content is unavailable")
        paths = [record.path for record in self.files]
        if len(paths) != len(set(paths)) or paths != sorted(paths):
            raise GoldenBackupUnavailable("backup content is unavailable")
        _recorded_instant(self.recorded_at)
        object.__setattr__(
            self,
            "content_digest",
            _digest([record.document() for record in self.files]),
        )

    @property
    def total_bytes(self) -> int:
        return sum(record.size for record in self.files)

    def document(self) -> dict[str, object]:
        return {
            "contentDigest": self.content_digest,
            "files": [record.document() for record in self.files],
            "recordedAt": self.recorded_at,
            "schemaVersion": BACKUP_MANIFEST_VERSION,
        }


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    """Content-free result of one backup attempt."""

    status: Literal["created", "unchanged"]
    snapshot: str
    recorded_at: str
    file_count: int
    total_bytes: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class BackupVerification:
    """Content-free result of verifying one recorded snapshot."""

    snapshot: str
    recorded_at: str
    file_count: int
    total_bytes: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class _SourceFile:
    record: BackupFileRecord
    data: bytes = field(repr=False)


def _require_durable_directory(path: Path, name: str) -> Path:
    if not isinstance(path, Path):
        raise TypeError(f"{name} must be Path")
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise GoldenBackupUnavailable(f"{name} is unavailable")
    return path.resolve(strict=True)


def _require_separate_roots(source_root: Path, backup_root: Path) -> tuple[Path, Path]:
    source = _require_durable_directory(source_root, "golden corpus root")
    backup = _require_durable_directory(backup_root, "golden backup root")
    if source.is_relative_to(backup) or backup.is_relative_to(source):
        raise GoldenBackupUnavailable(
            "golden backup root must not contain or live inside the corpus root"
        )
    return source, backup


def _make_private_directory(path: Path) -> None:
    path.mkdir(mode=SNAPSHOT_DIRECTORY_MODE)
    os.chmod(path, SNAPSHOT_DIRECTORY_MODE)


def _make_private_parents(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if not current.exists():
            _make_private_directory(current)


def _write_private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        SNAPSHOT_FILE_MODE,
    )
    try:
        os.fchmod(descriptor, SNAPSHOT_FILE_MODE)
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GoldenBackupUnavailable("golden corpus entry is not a regular file")
    try:
        return path.read_bytes()
    except OSError:
        raise GoldenBackupUnavailable("golden corpus entry is unreadable") from None


def _collect(source_root: Path) -> tuple[_SourceFile, ...]:
    sources: list[_SourceFile] = []
    total = 0
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            raise GoldenBackupUnavailable("golden corpus symlink is refused")
        if path.is_dir():
            continue
        data = _read_regular_file(path)
        total += len(data)
        if total > MAXIMUM_BACKUP_BYTES:
            raise GoldenBackupUnavailable("golden corpus exceeds the backup ceiling")
        relative = path.relative_to(source_root).as_posix()
        sources.append(
            _SourceFile(
                record=BackupFileRecord(
                    path=_relative_name(relative),
                    digest=sha256(data).hexdigest(),
                    size=len(data),
                ),
                data=data,
            )
        )
    if not sources:
        raise GoldenBackupUnavailable("golden corpus is empty")
    return tuple(sorted(sources, key=lambda source: source.record.path))


def snapshot_names(backup_root: Path) -> tuple[str, ...]:
    """List recorded snapshots oldest first; staging and foreign entries ignored."""

    root = _require_durable_directory(backup_root, "golden backup root")
    names = []
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink() or path.name.startswith("."):
            continue
        try:
            _snapshot_instant(path.name)
        except GoldenBackupUnavailable:
            continue
        names.append(path.name)
    return tuple(sorted(names))


def latest_snapshot(backup_root: Path) -> str | None:
    """The snapshot recovery restores by default: the newest recorded one."""

    names = snapshot_names(backup_root)
    return names[-1] if names else None


def _manifest(document: object) -> BackupManifest:
    if type(document) is not dict or frozenset(document) != _MANIFEST_FIELDS:
        raise GoldenBackupUnavailable("backup manifest is malformed")
    manifest_document = cast(dict[str, object], document)
    if manifest_document["schemaVersion"] != BACKUP_MANIFEST_VERSION:
        raise GoldenBackupUnavailable("backup manifest version is unavailable")
    files = manifest_document["files"]
    if type(files) is not list:
        raise GoldenBackupUnavailable("backup manifest is malformed")
    records: list[BackupFileRecord] = []
    for item in cast(list[object], files):
        if type(item) is not dict or frozenset(item) != _RECORD_FIELDS:
            raise GoldenBackupUnavailable("backup manifest is malformed")
        record = cast(dict[str, object], item)
        records.append(
            BackupFileRecord(
                path=cast(str, record["path"]),
                digest=cast(str, record["sha256"]),
                size=cast(int, record["bytes"]),
            )
        )
    recorded_at = manifest_document["recordedAt"]
    if type(recorded_at) is not str:
        raise GoldenBackupUnavailable("backup manifest is malformed")
    manifest = BackupManifest(recorded_at=recorded_at, files=tuple(records))
    if manifest_document["contentDigest"] != manifest.content_digest:
        raise GoldenBackupUnavailable(
            "backup manifest does not match its recorded content"
        )
    return manifest


def read_manifest(snapshot: Path) -> BackupManifest:
    """Load one snapshot's manifest and refuse any edited content record."""

    try:
        document = json.loads((snapshot / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise GoldenBackupUnavailable("backup manifest is unavailable") from None
    return _manifest(document)


def _require_owner_only(path: Path, expected: int) -> None:
    if path.stat().st_mode & 0o777 != expected:
        raise GoldenBackupUnavailable("backup permissions are not restrictive")


def _verify_content(root: Path, manifest: BackupManifest) -> None:
    expected = {record.path: record for record in manifest.files}
    present: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise GoldenBackupUnavailable("backup snapshot symlink is refused")
        if path.is_dir():
            _require_owner_only(path, SNAPSHOT_DIRECTORY_MODE)
            continue
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST_NAME:
            _require_owner_only(path, SNAPSHOT_FILE_MODE)
            continue
        if relative not in expected:
            raise GoldenBackupUnavailable("backup snapshot has unexpected content")
        present.add(relative)
        _require_owner_only(path, SNAPSHOT_FILE_MODE)
        data = _read_regular_file(path)
        record = expected[relative]
        if len(data) != record.size:
            raise GoldenBackupUnavailable("backup content is truncated or extended")
        if sha256(data).hexdigest() != record.digest:
            raise GoldenBackupUnavailable("backup content is corrupted")
    if present != set(expected):
        raise GoldenBackupUnavailable("backup snapshot is missing content")


def verify_backup(snapshot: Path) -> BackupVerification:
    """Refuse truncation, corruption, partial writes, and loosened permissions."""

    root = _require_durable_directory(snapshot, "backup snapshot")
    _require_owner_only(root, SNAPSHOT_DIRECTORY_MODE)
    manifest = read_manifest(root)
    if _snapshot_instant(root.name) != _recorded_instant(manifest.recorded_at):
        raise GoldenBackupUnavailable(
            "backup manifest time does not match its snapshot"
        )
    _verify_content(root, manifest)
    return BackupVerification(
        snapshot=root.name,
        recorded_at=manifest.recorded_at,
        file_count=len(manifest.files),
        total_bytes=manifest.total_bytes,
        content_digest=manifest.content_digest,
    )


def _write_snapshot(
    staging: Path,
    sources: tuple[_SourceFile, ...],
    manifest: BackupManifest,
) -> None:
    _make_private_directory(staging)
    for source in sources:
        target = staging / PurePosixPath(source.record.path)
        _make_private_parents(staging, target.parent)
        _write_private_file(target, source.data)
        if sha256(_read_regular_file(target)).hexdigest() != source.record.digest:
            raise GoldenBackupUnavailable("backup copy differs from its source")
    _write_private_file(
        staging / MANIFEST_NAME,
        json.dumps(
            manifest.document(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n",
    )
    _sync_directory(staging)


def create_backup(
    source_root: Path,
    backup_root: Path,
    *,
    recorded_at: datetime,
    allow_older: bool = False,
) -> BackupOutcome:
    """Record one immutable snapshot, or refuse; never write a partial one."""

    if type(allow_older) is not bool:
        raise TypeError("allow_older must be bool")
    source, backup = _require_separate_roots(source_root, backup_root)
    if any(path.name.startswith(_STAGING_PREFIX) for path in backup.iterdir()):
        raise GoldenBackupUnavailable("an interrupted backup staging entry remains")
    instant = _instant(recorded_at)
    sources = _collect(source)
    manifest = BackupManifest(
        recorded_at=_recorded_at(instant),
        files=tuple(item.record for item in sources),
    )
    latest = latest_snapshot(backup)
    if latest is not None:
        recorded = read_manifest(backup / latest)
        if recorded.content_digest == manifest.content_digest:
            return BackupOutcome(
                status="unchanged",
                snapshot=latest,
                recorded_at=recorded.recorded_at,
                file_count=len(recorded.files),
                total_bytes=recorded.total_bytes,
                content_digest=recorded.content_digest,
            )
        if instant <= _snapshot_instant(latest) and not allow_older:
            raise GoldenBackupUnavailable(
                "backup is older than the latest recorded snapshot"
            )
    name = _snapshot_name(instant)
    target = backup / name
    if target.exists():
        raise GoldenBackupUnavailable("backup snapshot is already recorded")
    staging = backup / f"{_STAGING_PREFIX}{name}"
    try:
        _write_snapshot(staging, sources, manifest)
    except BaseException as error:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(error, OSError):
            raise GoldenBackupUnavailable("backup write did not complete") from None
        raise
    os.rename(staging, target)
    _sync_directory(backup)
    return BackupOutcome(
        status="created",
        snapshot=name,
        recorded_at=manifest.recorded_at,
        file_count=len(manifest.files),
        total_bytes=manifest.total_bytes,
        content_digest=manifest.content_digest,
    )


def recover_backup(snapshot: Path, destination: Path) -> BackupVerification:
    """Restore a verified snapshot into an empty durable destination."""

    verification = verify_backup(snapshot)
    root = _require_durable_directory(snapshot, "backup snapshot")
    target = _require_durable_directory(destination, "recovery destination")
    if root.is_relative_to(target) or target.is_relative_to(root):
        raise GoldenBackupUnavailable(
            "recovery destination must be outside the backup snapshot"
        )
    if any(target.iterdir()):
        raise GoldenBackupUnavailable("recovery destination must be empty")
    os.chmod(target, SNAPSHOT_DIRECTORY_MODE)
    manifest = read_manifest(root)
    try:
        for record in manifest.files:
            restored = target / PurePosixPath(record.path)
            _make_private_parents(target, restored.parent)
            _write_private_file(restored, _read_regular_file(root / record.path))
        _sync_directory(target)
    except OSError:
        raise GoldenBackupUnavailable("recovery did not complete") from None
    _verify_content(target, manifest)
    return verification
