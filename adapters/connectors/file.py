"""File/Obsidian connector translated onto the closed Supply execution seam."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from adapters.file_source import FileRootRegistry
from engine.control import FileRootRef
from engine.supply import (
    ConnectorCheckpointBinding,
    SourceAclEvidenceClass,
    SourceAclObservation,
    SupplyChangePage,
    SupplyDocumentDeleteObservation,
    SupplyDocumentEnvelope,
    WorkerLeaseToken,
    deserialize_supply_change_page,
)
from third_party.onyx.connectors.connector_runner import ConnectorRunner
from third_party.onyx.connectors.interfaces import (
    CheckpointedConnectorWithPermSync,
    CheckpointOutput,
)
from third_party.onyx.connectors.models import (
    ConnectorCheckpoint,
    ConnectorFailure,
    DeletedDocument,
    Document,
)

_CHECKPOINT_VERSION = 1
_BATCH_SIZE = 100
_WEAK_ACL_JUSTIFICATION = "local File/Obsidian has no corpus ACL API"


class PermissionObservationFailed(RuntimeError):
    """The source ACL observation failed, so no Article may be emitted."""


@dataclass(frozen=True, slots=True)
class VaultSnapshotEntry:
    """One stable source snapshot entry presented to the connector."""

    path: str
    content: bytes

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or self.path != self.path.strip()
        ):
            raise ValueError("vault snapshot path must be canonical")
        if type(self.content) is not bytes or not self.content:
            raise ValueError("vault snapshot content must be nonempty bytes")


class VaultSource(Protocol):
    def snapshot(self) -> tuple[VaultSnapshotEntry, ...]: ...


class VaultPermissionObserver(Protocol):
    def observe_acl(self, path: str) -> None: ...


class FileConnectorTwin(VaultSource, VaultPermissionObserver, Protocol):
    policy_epoch: int

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FileCheckpoint:
    """Decoded connector state; the engine stores only its opaque bytes."""

    entries: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise TypeError("File checkpoint entries must be a tuple")
        paths: list[str] = []
        for entry in self.entries:
            if type(entry) is not tuple or len(entry) != 2:
                raise TypeError("File checkpoint entries must be exact pairs")
            path, digest = entry
            if type(path) is not str or not path or path != path.strip():
                raise ValueError("File checkpoint path must be canonical")
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("File checkpoint digest must be lowercase SHA-256")
            paths.append(path)
        if paths != sorted(set(paths), key=lambda value: value.encode("utf-8")):
            raise ValueError("File checkpoint paths must be sorted and unique")

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(path for path, _digest in self.entries)


def encode_file_checkpoint(checkpoint: FileCheckpoint) -> bytes:
    if type(checkpoint) is not FileCheckpoint:
        raise TypeError("File checkpoint encoding requires an exact value")
    return json.dumps(
        {"entries": [list(entry) for entry in checkpoint.entries], "version": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def decode_file_checkpoint(payload: bytes) -> FileCheckpoint:
    if type(payload) is not bytes or not payload:
        raise ValueError("File checkpoint must be nonempty bytes")
    try:
        decoded = json.loads(payload)
        if type(decoded) is not dict or set(decoded) != {"entries", "version"}:
            raise ValueError
        if decoded["version"] != _CHECKPOINT_VERSION:
            raise ValueError
        entries = decoded["entries"]
        if type(entries) is not list:
            raise ValueError
        return FileCheckpoint(
            tuple((cast(str, item[0]), cast(str, item[1])) for item in entries)
        )
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("File checkpoint is unavailable") from None


class FileRootVaultSource:
    """Use the existing anchored File registry as the sole filesystem reader."""

    __slots__ = ("_registry", "_root_ref")

    def __init__(self, registry: FileRootRegistry, root_ref: FileRootRef) -> None:
        if type(registry) is not FileRootRegistry or type(root_ref) is not FileRootRef:
            raise TypeError("File vault source requires registered root contracts")
        self._registry = registry
        self._root_ref = root_ref

    def snapshot(self) -> tuple[VaultSnapshotEntry, ...]:
        return tuple(
            VaultSnapshotEntry(path.value, content)
            for path, content in self._registry.observe_markdown_files(self._root_ref)
        )

    def observe_acl(self, path: str) -> None:
        if type(path) is not str or not path:
            raise PermissionObservationFailed("File ACL observation is unavailable")
        # The local source has no ACL endpoint. Successfully classifying that absence
        # is honest Weak evidence; failures must raise instead of falling back here.


class FileConnectorProcessAdapter:
    """Invoke one isolated File scan per engine checkpoint proposal."""

    __slots__ = (
        "_checkpoint",
        "_policy_epoch",
        "_root_path",
        "_root_ref",
        "_service_actor_expires_at",
        "_service_principal_id",
        "_idempotency_key",
        "_worker_lease",
    )

    def __init__(
        self,
        root_ref: FileRootRef,
        root_path: Path,
        *,
        policy_epoch: int,
        worker_lease: WorkerLeaseToken,
        service_principal_id: UUID,
        idempotency_key: str,
        service_actor_expires_at: datetime,
    ) -> None:
        if type(root_ref) is not FileRootRef:
            raise TypeError("File connector process requires FileRootRef")
        if not isinstance(root_path, Path) or not root_path.is_absolute():
            raise ValueError("File connector process requires an absolute root")
        if type(policy_epoch) is not int or policy_epoch < 1:
            raise ValueError("File connector process Policy Epoch must be positive")
        if type(worker_lease) is not WorkerLeaseToken:
            raise TypeError("File connector process requires WorkerLeaseToken")
        if type(service_principal_id) is not UUID:
            raise TypeError("File connector process requires ServiceActor UUID")
        if (
            type(idempotency_key) is not str
            or len(idempotency_key) != 64
            or any(character not in "0123456789abcdef" for character in idempotency_key)
        ):
            raise ValueError("File connector process requires an idempotency digest")
        if (
            type(service_actor_expires_at) is not datetime
            or service_actor_expires_at.tzinfo is None
            or service_actor_expires_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("File connector process requires a UTC actor expiry")
        self._root_ref = root_ref
        self._root_path = root_path
        self._policy_epoch = policy_epoch
        self._worker_lease = worker_lease
        self._service_principal_id = service_principal_id
        self._idempotency_key = idempotency_key
        self._service_actor_expires_at = service_actor_expires_at
        self._checkpoint: bytes | None = None

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None:
        if opaque_checkpoint is not None:
            decode_file_checkpoint(opaque_checkpoint)
        self._checkpoint = opaque_checkpoint

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def _run(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("File connector process requires exact binding")
        request = json.dumps(
            {
                "opaque_checkpoint": (
                    None
                    if self._checkpoint is None
                    else base64.b64encode(self._checkpoint).decode("ascii")
                ),
                "idempotency_key": self._idempotency_key,
                "organization_id": str(binding.organization_id),
                "policy_epoch": self._policy_epoch,
                "root_path": str(self._root_path),
                "root_ref": self._root_ref.value,
                "service_actor_expires_at": self._service_actor_expires_at.isoformat(),
                "service_principal_id": str(self._service_principal_id),
                "source_version_id": str(binding.source_version_id),
                "worker_job_id": str(binding.worker_job_id),
                "worker_lease": self._worker_lease.serialize(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "applications.connector_runner", "--scan-file"],
                input=request,
                capture_output=True,
                check=False,
                timeout=30.0,
            )
        except Exception:
            raise RuntimeError("File connector process is unavailable") from None
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError("File connector process is unavailable")
        try:
            page = deserialize_supply_change_page(completed.stdout)
        except ValueError:
            raise RuntimeError("File connector process output is unavailable") from None
        if page.binding != binding:
            raise RuntimeError("File connector process binding is unavailable")
        return page


class _VaultConnector(CheckpointedConnectorWithPermSync):
    """Native File source logic executed through the registered Onyx runner shape."""

    __slots__ = ("_permission_observer", "_source")

    def __init__(
        self,
        source: VaultSource,
        permission_observer: VaultPermissionObserver,
    ) -> None:
        self._source = source
        self._permission_observer = permission_observer

    def build_dummy_checkpoint(self) -> ConnectorCheckpoint:
        return ConnectorCheckpoint(encode_file_checkpoint(FileCheckpoint(())))

    def validate_checkpoint(self, payload: bytes) -> ConnectorCheckpoint:
        decode_file_checkpoint(payload)
        return ConnectorCheckpoint(payload)

    def load_from_checkpoint(
        self,
        checkpoint: ConnectorCheckpoint,
    ) -> CheckpointOutput:
        return self._generate(checkpoint, include_permissions=False)

    def load_from_checkpoint_with_perm_sync(
        self,
        checkpoint: ConnectorCheckpoint,
    ) -> CheckpointOutput:
        return self._generate(checkpoint, include_permissions=True)

    def _generate(
        self,
        checkpoint: ConnectorCheckpoint,
        *,
        include_permissions: bool,
    ) -> CheckpointOutput:
        prior = decode_file_checkpoint(checkpoint.payload)
        snapshot = self._source.snapshot()
        paths = tuple(item.path for item in snapshot)
        if paths != tuple(sorted(set(paths), key=lambda value: value.encode("utf-8"))):
            raise RuntimeError("vault snapshot must be sorted and unique")
        current_entries = tuple(
            (item.path, hashlib.sha256(item.content).hexdigest()) for item in snapshot
        )
        current_by_path = dict(current_entries)
        prior_by_path = dict(prior.entries)
        changed_paths = tuple(
            path
            for path in sorted(
                set(prior_by_path) | set(current_by_path),
                key=lambda value: value.encode("utf-8"),
            )
            if prior_by_path.get(path) != current_by_path.get(path)
        )
        selected_paths = changed_paths[:_BATCH_SIZE]
        snapshot_by_path = {item.path: item for item in snapshot}
        next_entries = dict(prior_by_path)
        for path in selected_paths:
            item = snapshot_by_path.get(path)
            if item is None:
                if include_permissions:
                    self._permission_observer.observe_acl(path)
                next_entries.pop(path, None)
                yield DeletedDocument(_document_ref(path))
                continue
            if include_permissions:
                self._permission_observer.observe_acl(item.path)
            next_entries[item.path] = current_by_path[item.path]
            yield Document(
                document_id=_document_ref(item.path),
                content=item.content,
                content_type="text/markdown",
                metadata=(
                    ("content_sha256", current_by_path[item.path]),
                    ("path", item.path),
                ),
            )
        return ConnectorCheckpoint(
            encode_file_checkpoint(
                FileCheckpoint(
                    tuple(
                        sorted(
                            next_entries.items(),
                            key=lambda item: item[0].encode("utf-8"),
                        )
                    )
                )
            )
        )


class FileConnectorAdapter:
    """Translate admitted runner outputs into CE Supply envelopes and deletes."""

    __slots__ = (
        "_checkpoint",
        "_clock",
        "_connector",
        "_policy_epoch",
        "emitted_pages",
        "last_emitted_page",
    )

    def __init__(
        self,
        source: VaultSource,
        permission_observer: VaultPermissionObserver,
        *,
        policy_epoch: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(policy_epoch) is not int or policy_epoch < 1:
            raise ValueError("File connector Policy Epoch must be positive")
        if not callable(clock or _utc_now):
            raise TypeError("File connector clock must be callable")
        self._connector = _VaultConnector(source, permission_observer)
        self._policy_epoch = policy_epoch
        self._clock = clock or _utc_now
        self._checkpoint: ConnectorCheckpoint | None = None
        self.emitted_pages: list[SupplyChangePage] = []
        self.last_emitted_page: SupplyChangePage | None = None

    @classmethod
    def from_twin(cls, twin: FileConnectorTwin) -> FileConnectorAdapter:
        return cls(twin, twin, policy_epoch=twin.policy_epoch, clock=twin.now)

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None:
        self.last_emitted_page = None
        self._checkpoint = (
            self._connector.build_dummy_checkpoint()
            if opaque_checkpoint is None
            else self._connector.validate_checkpoint(opaque_checkpoint)
        )

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if self._checkpoint is None:
            raise RuntimeError("File connector checkpoint was not loaded")
        return self._run(binding)

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if self._checkpoint is None:
            raise RuntimeError("File connector checkpoint was not loaded")
        return self._run(binding)

    def _run(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("File connector requires an exact checkpoint binding")
        assert self._checkpoint is not None
        documents: list[Document] = []
        deleted: list[DeletedDocument] = []
        failures: list[ConnectorFailure] = []
        proposed: ConnectorCheckpoint | None = None
        for batch in ConnectorRunner(
            self._connector,
            self._checkpoint,
            batch_size=_BATCH_SIZE,
            include_permissions=True,
        ).run():
            documents.extend(batch.documents)
            deleted.extend(batch.deleted_documents)
            failures.extend(batch.failures)
            if batch.checkpoint is not None:
                if proposed is not None:
                    raise RuntimeError("File connector returned multiple checkpoints")
                proposed = batch.checkpoint
        if failures or proposed is None:
            raise RuntimeError("File connector output is unavailable")
        observed_at = self._clock()
        acl = _weak_acl(binding, observed_at, self._policy_epoch)
        page = SupplyChangePage(
            binding=binding,
            page_ref=_page_ref(
                binding,
                self._checkpoint.payload,
                proposed.payload,
            ),
            documents=tuple(
                SupplyDocumentEnvelope(
                    organization_id=binding.organization_id,
                    source_version_id=binding.source_version_id,
                    worker_job_id=binding.worker_job_id,
                    document_ref=document.document_id,
                    content=document.content,
                    content_type=document.content_type,
                    acl_observation=acl,
                    metadata=document.metadata,
                )
                for document in documents
            ),
            deleted_document_refs=tuple(
                SupplyDocumentDeleteObservation(
                    document_ref=document.document_id,
                    acl_observation=acl,
                )
                for document in deleted
            ),
            checkpoint_proposal=proposed.payload,
            terminal=not documents and not deleted,
        )
        self.emitted_pages.append(page)
        self.last_emitted_page = page
        return page


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _document_ref(path: str) -> str:
    return f"file:{hashlib.sha256(path.encode('utf-8')).hexdigest()}"


def _page_ref(
    binding: ConnectorCheckpointBinding,
    prior: bytes,
    proposed: bytes,
) -> str:
    digest = hashlib.sha256()
    digest.update(binding.organization_id.bytes)
    digest.update(binding.source_version_id.bytes)
    digest.update(binding.worker_job_id.bytes)
    digest.update(hashlib.sha256(prior).digest())
    digest.update(hashlib.sha256(proposed).digest())
    return f"file-page:{digest.hexdigest()}"


def _weak_acl(
    binding: ConnectorCheckpointBinding,
    observed_at: datetime,
    policy_epoch: int,
) -> SourceAclObservation:
    return SourceAclObservation(
        organization_id=binding.organization_id,
        observed_at=observed_at,
        policy_epoch=policy_epoch,
        evidence_class=SourceAclEvidenceClass.WEAK,
        source_lacks_stronger_acl=_WEAK_ACL_JUSTIFICATION,
    )


__all__ = [
    "FileCheckpoint",
    "FileConnectorAdapter",
    "FileConnectorProcessAdapter",
    "FileRootVaultSource",
    "PermissionObservationFailed",
    "VaultSnapshotEntry",
    "decode_file_checkpoint",
    "encode_file_checkpoint",
]
