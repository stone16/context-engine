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
    DEFAULT_FILE_CHANGE_BASELINE_SIZE,
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE,
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
    ProviderScanBoundExceeded,
    ProviderUnsupported,
    SourceChange,
)

MAX_CONFIGURED_FILE_BYTES = 64 * 1024 * 1024


def _required_open_flag(name: str) -> int:
    """Return one mandatory descriptor-safety flag or refuse this adapter."""

    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise RuntimeError(f"File provider requires platform open flag {name}")
    return value


_O_DIRECTORY = _required_open_flag("O_DIRECTORY")
_O_NOFOLLOW = _required_open_flag("O_NOFOLLOW")
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class FileReadLimits:
    """Server-owned hard ceiling for one acquired File payload."""

    max_file_bytes: int
    max_baseline_entries: int = DEFAULT_FILE_CHANGE_BASELINE_SIZE

    def __post_init__(self) -> None:
        if (
            type(self.max_file_bytes) is not int
            or not 1 <= self.max_file_bytes <= MAX_CONFIGURED_FILE_BYTES
        ):
            raise ValueError("File byte ceiling must be a bounded positive integer")
        if (
            type(self.max_baseline_entries) is not int
            or not 1
            <= self.max_baseline_entries
            <= MAX_CONFIGURED_FILE_CHANGE_BASELINE_SIZE
        ):
            raise ValueError("File scan bound must be a bounded positive integer")


@dataclass(frozen=True, slots=True)
class _AnchoredRoot:
    """One configured root retained as an open directory capability."""

    display_path: Path
    descriptor: int
    curated_subtree: tuple[str, ...] | None


class _FileScanBoundExceeded(LookupError):
    """Internal traversal signal for the configured all-or-none scan fence."""


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    """Stable identity fields required for one directory or file observation."""

    device: int
    inode: int
    mode_type: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _DirectoryEntry:
    """One relevant entry captured from a stable directory snapshot."""

    name: str
    relative_path: str
    identity: _FileIdentity
    is_directory: bool


def _open_anchored_directory(path: Path) -> tuple[Path, int]:
    """Open every absolute path component without following any symlink."""

    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
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
    """Resolve one logical root and symlink-safe relative Markdown paths."""

    __slots__ = ("_limits", "_roots")

    def __init__(
        self,
        roots: Mapping[FileRootRef, Path],
        *,
        limits: FileReadLimits,
        curated_subtrees: Mapping[FileRootRef, str] | None = None,
    ) -> None:
        if not isinstance(roots, Mapping) or not roots:
            raise ValueError("File root registry requires explicit bindings")
        if type(limits) is not FileReadLimits:
            raise TypeError("File root registry requires FileReadLimits")
        selections = {} if curated_subtrees is None else curated_subtrees
        if not isinstance(selections, Mapping) or any(
            type(root_ref) is not FileRootRef
            or root_ref not in roots
            or not _canonical_relative_directory(value)
            for root_ref, value in selections.items()
        ):
            raise ValueError("File curated subtrees require canonical root bindings")
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
                selection = selections.get(root_ref)
                copied[root_ref] = _AnchoredRoot(
                    display_path,
                    descriptor,
                    None if selection is None else tuple(selection.split("/")),
                )
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
        return anchored.display_path.joinpath(*path.value.split("/"))

    def read(self, root_ref: FileRootRef, path: FileImportPath) -> bytes:
        """Read one regular file without following a final symlink."""

        payload, _metadata = self._read_regular(root_ref, path)
        return payload

    def observe_markdown_files(
        self,
        root_ref: FileRootRef,
    ) -> tuple[tuple[FileImportPath, bytes], ...]:
        """Expose the one anchored acquisition truth to admitted File consumers."""

        return self._observe_markdown_files(root_ref)

    def _read_regular(
        self, root_ref: FileRootRef, path: FileImportPath
    ) -> tuple[bytes, os.stat_result]:
        """Read one stable descriptor and return its exact observed identity."""

        self.resolve(root_ref, path)
        anchored = self._roots[root_ref]
        components = path.value.split("/")
        parent_descriptor = os.dup(anchored.descriptor)
        try:
            for component in components[:-1]:
                try:
                    next_descriptor = os.open(
                        component,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=parent_descriptor,
                    )
                except OSError:
                    raise LookupError(
                        "File target is not a regular configured-root file"
                    ) from None
                os.close(parent_descriptor)
                parent_descriptor = next_descriptor
                if not stat.S_ISDIR(os.fstat(parent_descriptor).st_mode):
                    raise LookupError(
                        "File target is not a regular configured-root file"
                    )
            return self._read_regular_at(parent_descriptor, components[-1])
        finally:
            os.close(parent_descriptor)

    def _read_regular_at(
        self,
        parent_descriptor: int,
        name: str,
    ) -> tuple[bytes, os.stat_result]:
        """Read one stable regular file relative to an already-open parent."""

        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | _O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
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
        """Read one stable recursive Markdown snapshot through anchored dirs."""

        if type(root_ref) is not FileRootRef:
            raise TypeError("File observation requires FileRootRef")
        anchored = self._roots.get(root_ref)
        if anchored is None:
            raise LookupError("File root is not configured")
        observed: list[tuple[FileImportPath, bytes]] = []
        snapshots: dict[str, tuple[_DirectoryEntry, ...]] = {}
        descriptor = os.dup(anchored.descriptor)
        relative_prefix = ""
        try:
            for component in anchored.curated_subtree or ():
                try:
                    child = os.open(
                        component,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=descriptor,
                    )
                except OSError:
                    raise LookupError(
                        "File curated subtree is not available"
                    ) from None
                os.close(descriptor)
                descriptor = child
                relative_prefix = (
                    f"{relative_prefix}/{component}"
                    if relative_prefix
                    else component
                )
            self._observe_directory(
                descriptor,
                relative_prefix=relative_prefix,
                observed=observed,
                snapshots=snapshots,
            )
            self._revalidate_directory_tree(
                descriptor,
                relative_prefix=relative_prefix,
                snapshots=snapshots,
            )
        finally:
            os.close(descriptor)
        return tuple(sorted(observed, key=lambda item: item[0].value.encode("utf-8")))

    def _curated_subtree_prefix(self, root_ref: FileRootRef) -> str | None:
        anchored = self._roots.get(root_ref)
        if anchored is None:
            raise LookupError("File root is not configured")
        if anchored.curated_subtree is None:
            return None
        return "/".join(anchored.curated_subtree) + "/"

    def _observe_directory(
        self,
        descriptor: int,
        *,
        relative_prefix: str,
        observed: list[tuple[FileImportPath, bytes]],
        snapshots: dict[str, tuple[_DirectoryEntry, ...]],
    ) -> None:
        """Descend one already-opened directory and verify its stable snapshot."""

        initial = _directory_snapshot(descriptor, relative_prefix)
        if relative_prefix in snapshots:
            raise RuntimeError("File root observation is unstable")
        snapshots[relative_prefix] = initial
        for entry in initial:
            if entry.is_directory:
                try:
                    child = os.open(
                        entry.name,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=descriptor,
                    )
                except OSError:
                    raise RuntimeError("File root observation is unstable") from None
                try:
                    if _file_identity(os.fstat(child)) != entry.identity:
                        raise RuntimeError("File root observation is unstable")
                    self._observe_directory(
                        child,
                        relative_prefix=entry.relative_path,
                        observed=observed,
                        snapshots=snapshots,
                    )
                finally:
                    os.close(child)
                try:
                    after = os.stat(
                        entry.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError:
                    raise RuntimeError("File root observation is unstable") from None
                if _file_identity(after) != entry.identity:
                    raise RuntimeError("File root observation is unstable")
                continue
            path = _file_import_path_or_none(entry.relative_path)
            if path is None or not stat.S_ISREG(entry.identity.mode_type):
                continue
            try:
                payload, opened = self._read_regular_at(descriptor, entry.name)
                after = os.stat(
                    entry.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except (LookupError, OSError):
                raise RuntimeError("File root observation is unstable") from None
            if not (
                entry.identity == _file_identity(opened) == _file_identity(after)
            ):
                raise RuntimeError("File root observation is unstable")
            observed.append((path, payload))
            if len(observed) > self._limits.max_baseline_entries:
                raise _FileScanBoundExceeded
        if _directory_snapshot(descriptor, relative_prefix) != initial:
            raise RuntimeError("File root observation is unstable")

    def _revalidate_directory_tree(
        self,
        descriptor: int,
        *,
        relative_prefix: str,
        snapshots: Mapping[str, tuple[_DirectoryEntry, ...]],
    ) -> None:
        """Revalidate every directory and file after the whole traversal."""

        expected = snapshots.get(relative_prefix)
        if (
            expected is None
            or _directory_snapshot(descriptor, relative_prefix) != expected
        ):
            raise RuntimeError("File root observation is unstable")
        for entry in expected:
            if not entry.is_directory:
                continue
            try:
                child = os.open(
                    entry.name,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=descriptor,
                )
            except OSError:
                raise RuntimeError("File root observation is unstable") from None
            try:
                if _file_identity(os.fstat(child)) != entry.identity:
                    raise RuntimeError("File root observation is unstable")
                self._revalidate_directory_tree(
                    child,
                    relative_prefix=entry.relative_path,
                    snapshots=snapshots,
                )
            finally:
                os.close(child)

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


def _file_identity(value: os.stat_result) -> _FileIdentity:
    """Project one stat result onto the fields used for stability checks."""

    return _FileIdentity(
        device=value.st_dev,
        inode=value.st_ino,
        mode_type=stat.S_IFMT(value.st_mode),
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
    )


def _file_import_path_or_none(name: str) -> FileImportPath | None:
    """Return a validated canonical Markdown path or ignore the candidate."""

    try:
        return FileImportPath(name)
    except ValueError:
        return None


def _directory_snapshot(
    descriptor: int,
    relative_prefix: str,
) -> tuple[_DirectoryEntry, ...]:
    """Classify only recursive directories and Markdown path candidates."""

    try:
        names = os.listdir(descriptor)
    except OSError:
        raise RuntimeError("File root observation is unstable") from None
    entries: list[_DirectoryEntry] = []
    safe_names = tuple(name for name in names if _safe_directory_component(name))
    for name in sorted(safe_names, key=lambda item: item.encode("utf-8")):
        relative_path = f"{relative_prefix}/{name}" if relative_prefix else name
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError:
            raise RuntimeError("File root observation is unstable") from None
        if stat.S_ISDIR(metadata.st_mode) and len(relative_path) + len("/.md") <= 255:
            is_directory = True
        elif _file_import_path_or_none(relative_path) is not None:
            is_directory = False
        else:
            continue
        entries.append(
            _DirectoryEntry(
                name=name,
                relative_path=relative_path,
                identity=_file_identity(metadata),
                is_directory=is_directory,
            )
        )
    return tuple(sorted(entries, key=lambda item: item.name.encode("utf-8")))


def _safe_directory_component(name: object) -> bool:
    """Return whether one observed name is safe to use as a path component."""

    return (
        type(name) is str
        and bool(name)
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and not any(ord(character) < 0x20 for character in name)
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in name)
    )


def _canonical_relative_directory(value: object) -> bool:
    """Return whether configuration names one nonempty root-relative directory."""

    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and not value.startswith("/")
        and "\\" not in value
        and all(component not in {"", ".", ".."} for component in value.split("/"))
        and not any(ord(character) < 0x20 for character in value)
    )


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
    """Deterministic recursive File `readChanges` Provider implementation."""

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
            selection_prefix = self._registry._curated_subtree_prefix(
                source.source_version.root_ref
            )
            if (
                selection_prefix is not None
                and source.complete_baseline is not None
                and any(
                    entry.kind is FileChangeKind.UPSERT
                    and not entry.path.value.startswith(selection_prefix)
                    for entry in source.complete_baseline.entries
                )
            ):
                return ProviderGenericDenied()
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
        except _FileScanBoundExceeded:
            return ProviderScanBoundExceeded(
                scan_bound=self._registry._limits.max_baseline_entries
            )
        except LookupError:
            return ProviderGenericDenied()
        except RuntimeError:
            return ProviderRetryableUnavailable(timedelta(seconds=1))
        changes, baseline_ref = self._changes(source, observed)
        if len(changes) > self._registry._limits.max_baseline_entries:
            return ProviderScanBoundExceeded(
                scan_bound=self._registry._limits.max_baseline_entries
            )
        scan_ref = self._scan_ref(
            source,
            changes,
            baseline_ref,
            scan_bound=self._registry._limits.max_baseline_entries,
        )
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
                scan_bound=self._registry._limits.max_baseline_entries,
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
            scan_bound=self._registry._limits.max_baseline_entries,
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
                # Reuse the prior comparison input only while the complete
                # baseline is also the durable head. An incomplete newer head
                # must be superseded by a scan bound to this complete baseline.
                comparison_baseline_ref = (
                    baseline.reference.comparison_baseline_ref
                    if FileChangeProvider._baseline_is_current_head(
                        source,
                        baseline.reference,
                    )
                    else baseline.reference
                )
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
                    comparison_baseline_ref,
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
    def _baseline_is_current_head(
        source: FileChangeSource,
        baseline_ref: FileChangeBaselineRef,
    ) -> bool:
        head = source.scan_head
        return (
            head is not None
            and head.complete
            and (
                head.source_version_ref,
                head.scan_ref,
                head.scan_epoch,
                head.page_ref,
                head.checkpoint_ref,
                head.sequence,
            )
            == (
                baseline_ref.source_version_ref,
                baseline_ref.scan_ref,
                baseline_ref.scan_epoch,
                baseline_ref.page_ref,
                baseline_ref.checkpoint_ref,
                baseline_ref.sequence,
            )
        )

    @staticmethod
    def _scan_ref(
        source: FileChangeSource,
        observed: tuple[_ObservedChange, ...],
        baseline_ref: FileChangeBaselineRef | None,
        *,
        scan_bound: int = DEFAULT_FILE_CHANGE_BASELINE_SIZE,
    ) -> str:
        document: dict[str, object] = {
            "organizationId": str(source.organization_id),
            "sourceId": str(source.source_version.source_ref.value),
            "sourceVersionId": str(source.source_version.version_ref),
            "scanBound": scan_bound,
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
                    "scanBound": baseline_ref.scan_bound,
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
            "scanBound": self._registry._limits.max_baseline_entries,
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
                    "scanBound",
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
        scan_bound: int,
    ) -> bool:
        offset = claims.get("offset")
        return (
            claims.get("domain") == _CURSOR_DOMAIN
            and claims.get("version") == 1
            and claims.get("organizationId") == str(source.organization_id)
            and claims.get("sourceId") == str(source.source_version.source_ref.value)
            and claims.get("sourceVersionId") == str(source.source_version.version_ref)
            and claims.get("scanRef") == scan_ref
            and claims.get("scanBound") == scan_bound
            and claims.get("scanEpoch") == str(scan_epoch)
            and claims.get("limit") == limit.value
            and type(offset) is int
            and 1 <= offset < observed_count
        )
