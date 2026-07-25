"""Explicit host bindings for registered logical File roots."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import rfc8785

from engine._opaque import decode_base64url, encode_base64url
from engine.control import (
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    CapabilityStatus,
    ChangeCursor,
    ChangeLimit,
    ChangePage,
    FileChangeBaselineRef,
    FileChangeKind,
    FileChangeProviderOutcome,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportPath,
    FileRootRef,
    InitialScan,
    PendingChangeCursor,
    ProviderGenericDenied,
    ProviderInvalidCheckpoint,
    ProviderOk,
    ProviderRetryableUnavailable,
    ProviderUnsupported,
    SourceChange,
)

MAX_CONFIGURED_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileReadLimits:
    """Server-owned hard ceiling for one acquired File payload."""

    max_file_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.max_file_bytes) is not int
            or not 1 <= self.max_file_bytes <= MAX_CONFIGURED_FILE_BYTES
        ):
            raise ValueError("File byte ceiling must be a bounded positive integer")


@dataclass(frozen=True, slots=True)
class _AnchoredRoot:
    display_path: Path
    descriptor: int


def _open_anchored_directory(path: Path) -> tuple[Path, int]:
    """Open every absolute path component without following any symlink."""

    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags | no_follow)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component,
                flags | no_follow,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise NotADirectoryError
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


class FileRootRegistry:
    """Resolve a logical root and closed filename without discovering files."""

    __slots__ = ("_limits", "_roots")

    def __init__(
        self,
        roots: Mapping[FileRootRef, Path],
        *,
        limits: FileReadLimits,
    ) -> None:
        if not isinstance(roots, Mapping) or not roots:
            raise ValueError("File root registry requires explicit bindings")
        if type(limits) is not FileReadLimits:
            raise TypeError("File root registry requires FileReadLimits")
        copied: dict[FileRootRef, _AnchoredRoot] = {}
        try:
            for root_ref, root_path in roots.items():
                if type(root_ref) is not FileRootRef or not isinstance(
                    root_path, Path
                ):
                    raise TypeError(
                        "File root bindings require FileRootRef and Path"
                    )
                try:
                    display_path, descriptor = _open_anchored_directory(root_path)
                except OSError:
                    raise ValueError(
                        "File root must be an existing non-symlink directory"
                    ) from None
                copied[root_ref] = _AnchoredRoot(display_path, descriptor)
        except Exception:
            for root in copied.values():
                os.close(root.descriptor)
            raise
        self._roots = MappingProxyType(copied)
        self._limits = limits

    def resolve(self, root_ref: FileRootRef, path: FileImportPath) -> Path:
        if type(root_ref) is not FileRootRef or type(path) is not FileImportPath:
            raise TypeError("File root resolution requires exact contracts")
        anchored = self._roots.get(root_ref)
        if anchored is None:
            raise LookupError("File root is not configured")
        target = anchored.display_path / path.value
        if target.parent != anchored.display_path:
            raise LookupError("File target is outside the configured root")
        return target

    def read(self, root_ref: FileRootRef, path: FileImportPath) -> bytes:
        """Read one regular file without following a final symlink."""

        payload, _metadata = self._read_regular(root_ref, path)
        return payload

    def _read_regular(
        self, root_ref: FileRootRef, path: FileImportPath
    ) -> tuple[bytes, os.stat_result]:
        """Read one stable descriptor and return its exact observed identity."""

        self.resolve(root_ref, path)
        anchored = self._roots[root_ref]
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path.value, flags, dir_fd=anchored.descriptor)
        except OSError:
            raise LookupError(
                "File target is not a regular configured-root file"
            ) from None
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > self._limits.max_file_bytes
            ):
                raise LookupError(
                    "File target is not a regular configured-root file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read(self._limits.max_file_bytes + 1)
            if len(payload) > self._limits.max_file_bytes:
                raise LookupError("File target exceeds the configured byte ceiling")
            after = os.fstat(descriptor)
            if _file_identity(before) != _file_identity(after):
                raise LookupError("File target changed while it was read")
            return payload, after
        finally:
            os.close(descriptor)

    def _observe_markdown_files(
        self, root_ref: FileRootRef
    ) -> tuple[tuple[FileImportPath, bytes], ...]:
        """Read one stable shallow Markdown snapshot through the anchored root."""

        if type(root_ref) is not FileRootRef:
            raise TypeError("File observation requires FileRootRef")
        anchored = self._roots.get(root_ref)
        if anchored is None:
            raise LookupError("File root is not configured")
        observed: list[tuple[FileImportPath, bytes]] = []
        initial_identities: dict[
            FileImportPath, tuple[int, int, int, int, int, int]
        ] = {}
        try:
            names = os.listdir(anchored.descriptor)
        except OSError:
            raise RuntimeError("File root observation is unstable") from None
        accepted_names = tuple(
            (name, path)
            for name in names
            if (path := _file_import_path_or_none(name)) is not None
        )
        ordered_names = tuple(
            sorted(accepted_names, key=lambda item: item[0].encode("utf-8"))
        )
        for name, path in ordered_names:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=anchored.descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise RuntimeError("File root observation is unstable") from None
            initial_identities[path] = _file_identity(metadata)
            if not stat.S_ISREG(metadata.st_mode):
                continue
            try:
                payload, opened = self._read_regular(root_ref, path)
                after = os.stat(
                    name,
                    dir_fd=anchored.descriptor,
                    follow_symlinks=False,
                )
            except (LookupError, OSError):
                raise RuntimeError("File root observation is unstable") from None
            if not (
                _file_identity(metadata)
                == _file_identity(opened)
                == _file_identity(after)
            ):
                raise RuntimeError("File root observation is unstable")
            observed.append((path, payload))
        try:
            final_names = tuple(
                sorted(
                    (
                        (name, path)
                        for name in os.listdir(anchored.descriptor)
                        if (path := _file_import_path_or_none(name)) is not None
                    ),
                    key=lambda item: item[0].encode("utf-8"),
                )
            )
        except OSError:
            raise RuntimeError("File root observation is unstable") from None
        if final_names != ordered_names:
            raise RuntimeError("File root observation is unstable")
        for path, identity in initial_identities.items():
            try:
                final_metadata = os.stat(
                    path.value,
                    dir_fd=anchored.descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise RuntimeError("File root observation is unstable") from None
            if _file_identity(final_metadata) != identity:
                raise RuntimeError("File root observation is unstable")
        return tuple(observed)

    def close(self) -> None:
        """Release the server-owned directory capabilities."""

        roots = self._roots
        self._roots = MappingProxyType({})
        for root in roots.values():
            os.close(root.descriptor)

    def __enter__(self) -> FileRootRegistry:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _file_import_path_or_none(name: str) -> FileImportPath | None:
    try:
        return FileImportPath(name)
    except ValueError:
        return None


_CURSOR_DOMAIN = "context-engine.file-change-cursor.v1"
_SCAN_DOMAIN = b"context-engine.file-change-scan.v1\x00"


@dataclass(frozen=True, slots=True)
class _ObservedFile:
    path: FileImportPath
    content_sha256: str
    content_length: int


@dataclass(frozen=True, slots=True)
class _ObservedChange:
    kind: FileChangeKind
    path: FileImportPath
    content_sha256: str
    content_length: int


class FileChangeProvider:
    """Deterministic shallow File `readChanges` Provider implementation."""

    __slots__ = ("_proofs", "_registry")

    def __init__(
        self,
        registry: FileRootRegistry,
        *,
        proofs: FileChangeProviderProofs,
    ) -> None:
        if type(registry) is not FileRootRegistry:
            raise TypeError("FileChangeProvider requires FileRootRegistry")
        if type(proofs) is not FileChangeProviderProofs:
            raise TypeError("FileChangeProvider requires FileChangeProviderProofs")
        self._registry = registry
        self._proofs = proofs

    def describe_capabilities(
        self, source: FileChangeSource
    ) -> ProviderOk[Any] | ProviderUnsupported | ProviderGenericDenied:
        if type(source) is not FileChangeSource:
            return ProviderGenericDenied()
        capabilities = source.source_version.capabilities
        if capabilities.describe_capabilities is not CapabilityStatus.AVAILABLE:
            return ProviderUnsupported("describeCapabilities")
        return ProviderOk(capabilities)

    def read_changes(
        self,
        source: FileChangeSource,
        cursor: InitialScan | ChangeCursor,
        limit: ChangeLimit,
    ) -> FileChangeProviderOutcome:
        if (
            type(source) is not FileChangeSource
            or type(cursor) not in {InitialScan, ChangeCursor}
            or type(limit) is not ChangeLimit
        ):
            return ProviderGenericDenied()
        capabilities = source.source_version.capabilities
        if (
            capabilities
            not in {
                FILE_CHANGE_CAPABILITY_MANIFEST,
                FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
            }
            or capabilities.read_changes is not CapabilityStatus.AVAILABLE
        ):
            return ProviderUnsupported("readChanges")
        try:
            observed = tuple(
                _ObservedFile(
                    path=path,
                    content_sha256=hashlib.sha256(payload).hexdigest(),
                    content_length=len(payload),
                )
                for path, payload in self._registry._observe_markdown_files(
                    source.source_version.root_ref
                )
            )
        except LookupError:
            return ProviderGenericDenied()
        except RuntimeError:
            return ProviderRetryableUnavailable(timedelta(seconds=1))
        changes, baseline_ref = self._changes(source, observed)
        scan_ref = self._scan_ref(source, changes, baseline_ref)
        requested_limit = limit
        if (
            type(cursor) is InitialScan
            and source.scan_head is not None
            and source.scan_head.source_version_ref
            == source.source_version.version_ref
            and source.scan_head.scan_ref == scan_ref
        ):
            requested_limit = ChangeLimit(source.scan_head.page_limit)
        offset = 0
        predecessor_page_ref: str | None = None
        predecessor_checkpoint_ref: str | None = None
        predecessor_sequence: int | None = None
        scan_epoch, superseded_scan_epoch = self._scan_epoch(source, scan_ref)
        if type(cursor) is ChangeCursor:
            accepted = self._proofs._unwrap_cursor(cursor)
            if accepted is None:
                return ProviderInvalidCheckpoint()
            head = source.scan_head
            if (
                head is None
                or head.source_version_ref != source.source_version.version_ref
                or head.scan_ref != accepted.scan_ref
                or head.scan_epoch != accepted.scan_epoch
                or accepted.sequence > head.sequence
            ):
                return ProviderInvalidCheckpoint()
            scan_epoch = accepted.scan_epoch
            claims = self._decode_cursor(accepted.pending_cursor)
            if claims is None or not self._cursor_matches(
                claims,
                source=source,
                scan_ref=scan_ref,
                scan_epoch=scan_epoch,
                limit=requested_limit,
                observed_count=len(changes),
            ):
                return ProviderInvalidCheckpoint()
            offset = cast(int, claims["offset"])
            predecessor_page_ref = accepted.page_ref
            predecessor_checkpoint_ref = accepted.checkpoint_ref
            predecessor_sequence = accepted.sequence
            superseded_scan_epoch = None
        selected = changes[offset : offset + requested_limit.value]
        next_offset = offset + len(selected)
        complete = next_offset == len(changes)
        next_cursor = (
            None
            if complete
            else self._encode_cursor(
                source=source,
                scan_ref=scan_ref,
                scan_epoch=scan_epoch,
                offset=next_offset,
                limit=requested_limit,
            )
        )
        unsigned = ChangePage(
            organization_id=source.organization_id,
            source_ref=source.source_version.source_ref.value,
            source_version_ref=source.source_version.version_ref,
            scan_ref=scan_ref,
            scan_epoch=scan_epoch,
            page_limit=requested_limit.value,
            predecessor_page_ref=predecessor_page_ref,
            predecessor_checkpoint_ref=predecessor_checkpoint_ref,
            predecessor_sequence=predecessor_sequence,
            superseded_scan_epoch=superseded_scan_epoch,
            baseline_ref=baseline_ref,
            changes=tuple(
                SourceChange(
                    organization_id=source.organization_id,
                    source_ref=source.source_version.source_ref.value,
                    source_version_ref=source.source_version.version_ref,
                    scan_ref=scan_ref,
                    kind=item.kind,
                    path=item.path,
                    content_sha256=item.content_sha256,
                    content_length=item.content_length,
                )
                for item in selected
            ),
            next_cursor=next_cursor,
            complete=complete,
            provider_proof="A" * 86,
            capability_version=capabilities.declaration_version,
        )
        return ProviderOk(
            replace(unsigned, provider_proof=self._proofs._seal_page(unsigned))
        )

    @staticmethod
    def _changes(
        source: FileChangeSource,
        observed: tuple[_ObservedFile, ...],
    ) -> tuple[tuple[_ObservedChange, ...], FileChangeBaselineRef | None]:
        baseline = source.complete_baseline
        current = {
            item.path: (item.content_sha256, item.content_length)
            for item in observed
        }
        if baseline is not None:
            active = {
                entry.path: (entry.content_sha256, entry.content_length)
                for entry in baseline.entries
                if entry.kind is FileChangeKind.UPSERT
            }
            deleted = {
                entry.path
                for entry in baseline.entries
                if entry.kind is FileChangeKind.DELETE
            }
            if current == active and deleted.isdisjoint(current):
                return (
                    tuple(
                        _ObservedChange(
                            kind=entry.kind,
                            path=entry.path,
                            content_sha256=entry.content_sha256,
                            content_length=entry.content_length,
                        )
                        for entry in baseline.entries
                    ),
                    baseline.reference.comparison_baseline_ref,
                )
        changes = [
            _ObservedChange(
                kind=FileChangeKind.UPSERT,
                path=item.path,
                content_sha256=item.content_sha256,
                content_length=item.content_length,
            )
            for item in observed
        ]
        if baseline is not None:
            changes.extend(
                _ObservedChange(
                    kind=FileChangeKind.DELETE,
                    path=entry.path,
                    content_sha256=entry.content_sha256,
                    content_length=entry.content_length,
                )
                for entry in baseline.entries
                if entry.kind is FileChangeKind.UPSERT
                and entry.path not in current
            )
        changes.sort(key=lambda item: item.path.value.encode("utf-8"))
        return tuple(changes), None if baseline is None else baseline.reference

    @staticmethod
    def _scan_ref(
        source: FileChangeSource,
        observed: tuple[_ObservedChange, ...],
        baseline_ref: FileChangeBaselineRef | None,
    ) -> str:
        document: dict[str, object] = {
            "organizationId": str(source.organization_id),
            "sourceId": str(source.source_version.source_ref.value),
            "sourceVersionId": str(source.source_version.version_ref),
            "entries": [
                {
                    "contentLength": item.content_length,
                    "contentSha256": item.content_sha256,
                    **(
                        {"kind": item.kind.value}
                        if source.source_version.capabilities
                        is FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
                        else {}
                    ),
                    "path": item.path.value,
                }
                for item in observed
            ],
        }
        if (
            source.source_version.capabilities
            is FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
        ):
            document["baseline"] = (
                None
                if baseline_ref is None
                else {
                    "checkpointRef": baseline_ref.checkpoint_ref,
                    "pageRef": baseline_ref.page_ref,
                    "scanEpoch": str(baseline_ref.scan_epoch),
                    "scanRef": baseline_ref.scan_ref,
                    "sequence": baseline_ref.sequence,
                    "sourceVersionId": str(baseline_ref.source_version_ref),
                }
            )
        return hashlib.sha256(
            _SCAN_DOMAIN + rfc8785.dumps(cast(Any, document))
        ).hexdigest()

    @staticmethod
    def _scan_epoch(
        source: FileChangeSource, scan_ref: str
    ) -> tuple[UUID, UUID | None]:
        head = source.scan_head
        if (
            head is not None
            and head.source_version_ref == source.source_version.version_ref
            and head.scan_ref == scan_ref
        ):
            return head.scan_epoch, head.superseded_scan_epoch
        predecessor = None if head is None else head.scan_epoch
        document = {
            "domain": "context-engine.file-change-scan-epoch.v1",
            "organizationId": str(source.organization_id),
            "predecessor": (
                None
                if head is None
                else {
                    "checkpointRef": head.checkpoint_ref,
                    "pageRef": head.page_ref,
                    "scanEpoch": str(head.scan_epoch),
                    "sequence": head.sequence,
                }
            ),
            "scanRef": scan_ref,
            "sourceId": str(source.source_version.source_ref.value),
            "sourceVersionId": str(source.source_version.version_ref),
            "version": 1,
        }
        digest = hashlib.sha256(rfc8785.dumps(cast(Any, document))).digest()
        return UUID(bytes=digest[:16]), predecessor

    def _encode_cursor(
        self,
        *,
        source: FileChangeSource,
        scan_ref: str,
        scan_epoch: UUID,
        offset: int,
        limit: ChangeLimit,
    ) -> PendingChangeCursor:
        document = {
            "domain": _CURSOR_DOMAIN,
            "limit": limit.value,
            "offset": offset,
            "organizationId": str(source.organization_id),
            "scanEpoch": str(scan_epoch),
            "scanRef": scan_ref,
            "sourceId": str(source.source_version.source_ref.value),
            "sourceVersionId": str(source.source_version.version_ref),
            "version": 1,
        }
        payload = rfc8785.dumps(cast(Any, document))
        signature = self._proofs._sign_pending_payload(payload)
        return PendingChangeCursor(
            f"{encode_base64url(payload)}.{encode_base64url(signature)}"
        )

    def _decode_cursor(
        self, cursor: PendingChangeCursor
    ) -> dict[str, object] | None:
        try:
            encoded_payload, encoded_signature = cursor.value.split(".")
            payload = decode_base64url(encoded_payload)
            signature = decode_base64url(encoded_signature)
            expected = self._proofs._sign_pending_payload(payload)
            if not hmac.compare_digest(signature, expected):
                return None
            document = json.loads(payload)
            if (
                type(document) is not dict
                or rfc8785.dumps(cast(Any, document)) != payload
                or set(document) != {
                    "domain",
                    "limit",
                    "offset",
                    "organizationId",
                    "scanEpoch",
                    "scanRef",
                    "sourceId",
                    "sourceVersionId",
                    "version",
                }
            ):
                return None
            return cast(dict[str, object], document)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _cursor_matches(
        claims: dict[str, object],
        *,
        source: FileChangeSource,
        scan_ref: str,
        scan_epoch: UUID,
        limit: ChangeLimit,
        observed_count: int,
    ) -> bool:
        offset = claims.get("offset")
        return (
            claims.get("domain") == _CURSOR_DOMAIN
            and claims.get("version") == 1
            and claims.get("organizationId") == str(source.organization_id)
            and claims.get("sourceId") == str(source.source_version.source_ref.value)
            and claims.get("sourceVersionId") == str(source.source_version.version_ref)
            and claims.get("scanRef") == scan_ref
            and claims.get("scanEpoch") == str(scan_epoch)
            and claims.get("limit") == limit.value
            and type(offset) is int
            and 1 <= offset < observed_count
        )
