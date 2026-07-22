"""Explicit host bindings for registered logical File roots."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from engine.control import FileImportPath, FileRootRef

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
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > self._limits.max_file_bytes
            ):
                raise LookupError(
                    "File target is not a regular configured-root file"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read(self._limits.max_file_bytes + 1)
            if len(payload) > self._limits.max_file_bytes:
                raise LookupError("File target exceeds the configured byte ceiling")
            return payload
        finally:
            os.close(descriptor)

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
