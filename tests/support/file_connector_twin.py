"""Deterministic offline twin for the admitted File/Obsidian connector."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from adapters.connectors.file import PermissionObservationFailed, VaultSnapshotEntry


class SyntheticVaultTwin:
    """In-memory vault surface with explicit permission-observation failures."""

    def __init__(
        self,
        files: dict[str, bytes],
        *,
        fail_acl_for: set[str] | None = None,
        organization_id: UUID | None = None,
        source_version_id: UUID | None = None,
        worker_job_id: UUID | None = None,
        policy_epoch: int = 1,
        snapshots: tuple[dict[str, bytes], ...] | None = None,
    ) -> None:
        self.organization_id = organization_id or uuid4()
        self.source_version_id = source_version_id or uuid4()
        self.worker_job_id = worker_job_id or uuid4()
        self.policy_epoch = policy_epoch
        self._files = dict(files)
        self._snapshots = tuple(dict(snapshot) for snapshot in snapshots or ())
        self._snapshot_index = 0
        self._fail_acl_for = set(fail_acl_for or ())
        self.filesystem_accesses = 0
        self.credential_accesses = 0
        self.observed_acl_paths: list[str] = []
        self.snapshot_calls = 0
        self._now = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    def snapshot(self) -> tuple[VaultSnapshotEntry, ...]:
        self.snapshot_calls += 1
        if self._snapshots:
            index = min(self._snapshot_index, len(self._snapshots) - 1)
            self._files = dict(self._snapshots[index])
            self._snapshot_index += 1
        return tuple(
            VaultSnapshotEntry(path, self._files[path])
            for path in sorted(self._files, key=lambda value: value.encode("utf-8"))
        )

    def observe_acl(self, path: str) -> None:
        self.observed_acl_paths.append(path)
        if path in self._fail_acl_for:
            raise PermissionObservationFailed("File ACL observation is unavailable")

    def now(self) -> datetime:
        return self._now

    def replace(self, path: str, content: bytes) -> None:
        self._files[path] = content

    def delete(self, path: str) -> None:
        del self._files[path]


__all__ = ["SyntheticVaultTwin"]
