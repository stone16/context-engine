"""Materialize one closed registered local model snapshot for operators."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Never, Protocol, Self
from urllib.parse import quote, urljoin, urlsplit

import httpx
import rfc8785

from adapters.local_embedding_model import (
    registered_qwen_snapshot_contract,
    verify_model_artifacts_descriptor,
)

SCHEMA_VERSION = "context-engine-model-materializer-v1"
SERVICE = "context-engine-model-materializer"
_PRODUCTION_ORIGIN = "https://huggingface.co"
_MAX_REDIRECTS = 4
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_READ_SIZE = 1024 * 1024
_IMMUTABLE_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class MaterializationRefused(RuntimeError):
    """Closed operator-visible materialization refusal."""

    def __init__(self, category: str, exit_code: int) -> None:
        super().__init__(category)
        self.category = category
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class RegisteredModelSnapshot:
    model_id: str
    revision: str
    artifact_digest: str
    artifacts: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        model_parts = self.model_id.split("/") if type(self.model_id) is str else []
        if (
            len(model_parts) != 2
            or any(not part or part in {".", ".."} for part in model_parts)
            or type(self.revision) is not str
            or _IMMUTABLE_REVISION.fullmatch(self.revision) is None
            or type(self.artifact_digest) is not str
            or _SHA256_DIGEST.fullmatch(self.artifact_digest) is None
            or type(self.artifacts) is not tuple
            or not self.artifacts
        ):
            raise ValueError("registered model snapshot is unavailable")
        manifest: list[dict[str, str]] = []
        for artifact in self.artifacts:
            if type(artifact) is not tuple or len(artifact) != 2:
                raise ValueError("registered model snapshot is unavailable")
            path, digest = artifact
            parsed = PurePosixPath(path) if type(path) is str else PurePosixPath(".")
            if (
                type(path) is not str
                or not path
                or parsed.is_absolute()
                or str(parsed) != path
                or any(part in {"", ".", ".."} for part in parsed.parts)
                or type(digest) is not str
                or _SHA256_DIGEST.fullmatch(digest) is None
            ):
                raise ValueError("registered model snapshot is unavailable")
            manifest.append({"path": path, "sha256": digest})
        if (
            tuple(path for path, _digest in self.artifacts)
            != tuple(sorted(path for path, _digest in self.artifacts))
            or len({path for path, _digest in self.artifacts}) != len(self.artifacts)
            or hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()
            != self.artifact_digest
        ):
            raise ValueError("registered model snapshot is unavailable")


class ArtifactResponse(Protocol):
    def content_length(self) -> str | None: ...

    def content_encoding(self) -> str | None: ...

    def iter_bytes(self, chunk_size: int) -> Iterator[bytes]: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ArtifactTransport(Protocol):
    def fetch(
        self,
        snapshot: RegisteredModelSnapshot,
        path: str,
    ) -> ArtifactResponse: ...


class HttpArtifactTransport:
    """Bounded HTTP transport with a closed production redirect policy."""

    def __init__(self, origin: str, *, production: bool) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.hostname
            or (
                production
                and (parsed.scheme != "https" or parsed.port not in {None, 443})
            )
            or (
                not production
                and (
                    parsed.scheme != "http"
                    or parsed.hostname != "127.0.0.1"
                    or parsed.port is None
                )
            )
        ):
            raise ValueError("artifact transport is unavailable")
        self._origin = origin.rstrip("/")
        self._production = production

    @classmethod
    def production(cls) -> HttpArtifactTransport:
        return cls(_PRODUCTION_ORIGIN, production=True)

    @classmethod
    def for_test_twin(cls, origin: str) -> HttpArtifactTransport:
        return cls(origin, production=False)

    def _initial_url(self, snapshot: RegisteredModelSnapshot, path: str) -> str:
        model_parts = snapshot.model_id.split("/")
        path_parts = PurePosixPath(path).parts
        encoded = "/".join(
            quote(part, safe="")
            for part in (*model_parts, "resolve", snapshot.revision, *path_parts)
        )
        return f"{self._origin}/{encoded}"

    def _url_allowed(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            if (
                parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or not parsed.hostname
            ):
                return False
            if not self._production:
                origin = urlsplit(self._origin)
                return (
                    parsed.scheme == "http"
                    and parsed.hostname == origin.hostname == "127.0.0.1"
                    and parsed.port == origin.port
                )
            return (
                parsed.scheme == "https"
                and parsed.port in {None, 443}
                and (
                    parsed.hostname == "huggingface.co"
                    or parsed.hostname.endswith(".cdn.hf.co")
                )
            )
        except ValueError:
            return False

    def fetch(self, snapshot: RegisteredModelSnapshot, path: str) -> ArtifactResponse:
        url = self._initial_url(snapshot, path)
        try:
            client = httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(60.0, connect=10.0),
                trust_env=False,
                headers={"Accept": "application/octet-stream"},
            )
            for _redirect in range(_MAX_REDIRECTS + 1):
                if not self._url_allowed(url):
                    client.close()
                    raise MaterializationRefused("transport_refused", 11)
                request = client.build_request("GET", url)
                response = client.send(request, stream=True)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    response.close()
                    if location is None:
                        client.close()
                        raise MaterializationRefused("transport_refused", 11)
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    response.close()
                    client.close()
                    raise MaterializationRefused("transport_refused", 11)
                return _ClosingResponse(response, client)
            client.close()
        except MaterializationRefused:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            pass
        raise MaterializationRefused("transport_refused", 11) from None


class _ClosingResponse:
    def __init__(self, response: httpx.Response, client: httpx.Client) -> None:
        self._response = response
        self._client = client

    def content_length(self) -> str | None:
        value = self._response.headers.get("content-length")
        return value if type(value) is str else None

    def content_encoding(self) -> str | None:
        value = self._response.headers.get("content-encoding")
        return value if type(value) is str else None

    def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
        return self._response.iter_bytes(chunk_size=chunk_size)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._response.close()
        self._client.close()


def load_registered_snapshot(role: str) -> RegisteredModelSnapshot:
    if role != "primary":
        raise MaterializationRefused("registry_unavailable", 10)
    try:
        model_id, revision, artifact_digest, artifacts = (
            registered_qwen_snapshot_contract()
        )
        return RegisteredModelSnapshot(
            model_id=model_id,
            revision=revision,
            artifact_digest=artifact_digest,
            artifacts=artifacts,
        )
    except Exception:
        raise MaterializationRefused("registry_unavailable", 10) from None


def _validate_destination(destination: Path) -> tuple[str, int]:
    parent_descriptor: int | None = None
    try:
        if not destination.name or destination.name in {".", ".."}:
            raise ValueError
        parent_descriptor = os.open(
            destination.parent,
            _directory_flags(),
        )
        if not stat.S_ISDIR(os.fstat(parent_descriptor).st_mode):
            raise ValueError
        try:
            os.stat(
                destination.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return destination.name, parent_descriptor
        else:
            raise MaterializationRefused("destination_exists", 13)
    except MaterializationRefused:
        raise
    except (OSError, RuntimeError, ValueError):
        raise MaterializationRefused("destination_unavailable", 10) from None
    except BaseException:
        if parent_descriptor is not None:
            with suppress(OSError):
                os.close(parent_descriptor)
        raise


def _open_or_create_directory(parent_descriptor: int, name: str) -> int:
    with suppress(FileExistsError):
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_descriptor)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _create_private_directory(
    parent_descriptor: int,
    prefix: str,
) -> tuple[str, int]:
    for _attempt in range(32):
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
            return name, descriptor
        except FileExistsError:
            continue
        except OSError:
            with suppress(OSError):
                os.rmdir(name, dir_fd=parent_descriptor)
            raise
    raise OSError


def _write_artifact(
    staging_descriptor: int,
    relative_path: str,
    response: ArtifactResponse,
) -> None:
    parsed = PurePosixPath(relative_path)
    if (
        parsed.is_absolute()
        or str(parsed) != relative_path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise MaterializationRefused("verification_refused", 12)
    directory_descriptor = os.dup(staging_descriptor)
    try:
        for directory_part in parsed.parts[:-1]:
            child = _open_or_create_directory(directory_descriptor, directory_part)
            os.close(directory_descriptor)
            directory_descriptor = child
        raw_length = response.content_length()
        encoding = response.content_encoding()
        if encoding is not None and encoding.casefold() != "identity":
            raise OSError
        expected_length = int(raw_length) if raw_length is not None else None
        if (
            expected_length is not None
            and not 0 <= expected_length <= _MAX_ARTIFACT_BYTES
        ):
            raise OSError
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(
            parsed.parts[-1],
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        written = 0
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                for chunk in response.iter_bytes(_READ_SIZE):
                    if type(chunk) is not bytes or not chunk:
                        raise OSError
                    written += len(chunk)
                    if written > _MAX_ARTIFACT_BYTES:
                        raise OSError
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
        if expected_length is not None and written != expected_length:
            raise OSError
    except MaterializationRefused:
        raise
    except (httpx.HTTPError, OSError, TypeError, ValueError):
        raise MaterializationRefused("transport_refused", 11) from None
    finally:
        with suppress(OSError):
            os.close(directory_descriptor)


def _fsync_directory_tree(directory_descriptor: int) -> None:
    with os.scandir(directory_descriptor) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        metadata = entry.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        child = os.open(
            entry.name,
            _directory_flags(),
            dir_fd=directory_descriptor,
        )
        try:
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise OSError
            _fsync_directory_tree(child)
        finally:
            os.close(child)
    os.fsync(directory_descriptor)


def _discard_directory_contents(directory_descriptor: int) -> None:
    with os.scandir(directory_descriptor) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        metadata = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                entry.name,
                _directory_flags(),
                dir_fd=directory_descriptor,
            )
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    continue
                _discard_directory_contents(child)
            finally:
                os.close(child)
            with suppress(OSError):
                os.rmdir(entry.name, dir_fd=directory_descriptor)
            continue
        with suppress(OSError):
            os.unlink(entry.name, dir_fd=directory_descriptor)


def _atomic_publish_no_clobber(
    staging_parent_descriptor: int,
    staging_descriptor: int,
    staging_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    try:
        named = os.stat(
            staging_name,
            dir_fd=staging_parent_descriptor,
            follow_symlinks=False,
        )
        retained = os.fstat(staging_descriptor)
        if (
            not stat.S_ISDIR(named.st_mode)
            or (named.st_dev, named.st_ino) != (retained.st_dev, retained.st_ino)
        ):
            raise OSError
    except OSError:
        raise MaterializationRefused("publication_refused", 13) from None
    source = os.fsencode(staging_name)
    target = os.fsencode(destination_name)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        rename = library.renameatx_np
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            staging_parent_descriptor,
            source,
            destination_parent_descriptor,
            target,
            0x00000004,
        )
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            staging_parent_descriptor,
            source,
            destination_parent_descriptor,
            target,
            1,
        )
    else:
        raise MaterializationRefused("publication_refused", 13)
    error = ctypes.get_errno() if result != 0 else 0
    if result == 0:
        try:
            os.fsync(destination_parent_descriptor)
        except OSError:
            raise MaterializationRefused("publication_refused", 13) from None
        return
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise MaterializationRefused("destination_exists", 13)
    raise MaterializationRefused("publication_refused", 13)


SnapshotLoader = Callable[[str], RegisteredModelSnapshot]
Verifier = Callable[[int, tuple[tuple[str, str], ...], str], None]


def materialize_registered_snapshot(
    role: str,
    destination: Path,
    *,
    snapshot_loader: SnapshotLoader,
    transport: ArtifactTransport,
    verifier: Verifier = verify_model_artifacts_descriptor,
) -> None:
    try:
        snapshot = snapshot_loader(role)
    except MaterializationRefused:
        raise
    except Exception:
        raise MaterializationRefused("registry_unavailable", 10) from None
    destination_name, destination_parent_descriptor = _validate_destination(
        destination
    )
    staging_parent_descriptor: int | None = None
    staging_descriptor: int | None = None
    staging_parent_name = ""
    staging_parent_identity: tuple[int, int] | None = None
    try:
        try:
            staging_parent_name, staging_parent_descriptor = (
                _create_private_directory(
                    destination_parent_descriptor,
                    ".context-engine-model-work-",
                )
            )
            os.fchmod(staging_parent_descriptor, 0o300)
            staging_name, staging_descriptor = _create_private_directory(
                staging_parent_descriptor,
                ".snapshot-",
            )
            parent_metadata = os.fstat(staging_parent_descriptor)
            staging_parent_identity = (
                parent_metadata.st_dev,
                parent_metadata.st_ino,
            )
        except OSError:
            raise MaterializationRefused("destination_unavailable", 10) from None
        for relative_path, _digest in snapshot.artifacts:
            with transport.fetch(snapshot, relative_path) as response:
                _write_artifact(
                    staging_descriptor,
                    relative_path,
                    response,
                )
        _fsync_directory_tree(staging_descriptor)
        os.fsync(staging_parent_descriptor)
        try:
            verifier(
                staging_descriptor,
                snapshot.artifacts,
                snapshot.artifact_digest,
            )
        except Exception:
            raise MaterializationRefused("verification_refused", 12) from None
        _atomic_publish_no_clobber(
            staging_parent_descriptor,
            staging_descriptor,
            staging_name,
            destination_parent_descriptor,
            destination_name,
        )
    finally:
        if staging_descriptor is not None:
            with suppress(OSError):
                os.close(staging_descriptor)
        if staging_parent_descriptor is not None:
            with suppress(OSError):
                os.fchmod(staging_parent_descriptor, 0o700)
            with suppress(OSError):
                _discard_directory_contents(staging_parent_descriptor)
            with suppress(OSError):
                os.close(staging_parent_descriptor)
            try:
                named = os.stat(
                    staging_parent_name,
                    dir_fd=destination_parent_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(named.st_mode) and staging_parent_identity == (
                    named.st_dev,
                    named.st_ino,
                ):
                    os.rmdir(
                        staging_parent_name,
                        dir_fd=destination_parent_descriptor,
                    )
            except OSError:
                pass
        with suppress(OSError):
            os.close(destination_parent_descriptor)


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        print(_render("refused", "operation_refused"), flush=True)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _PrivateArgumentParser(
        prog=SERVICE,
        description="materialize one registered local model snapshot",
    )
    parser.add_argument("--role", required=True, choices=("primary",))
    parser.add_argument("--destination", required=True, type=Path)
    return parser


def _render(status: str, category: str) -> str:
    return json.dumps(
        {
            "schemaVersion": SCHEMA_VERSION,
            "service": SERVICE,
            "status": status,
            "category": category,
        },
        separators=(",", ":"),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    snapshot_loader: SnapshotLoader = load_registered_snapshot,
    transport: ArtifactTransport | None = None,
    verifier: Verifier = verify_model_artifacts_descriptor,
) -> None:
    arguments = _parser().parse_args(argv)
    try:
        materialize_registered_snapshot(
            arguments.role,
            arguments.destination,
            snapshot_loader=snapshot_loader,
            transport=transport or HttpArtifactTransport.production(),
            verifier=verifier,
        )
    except MaterializationRefused as refusal:
        print(_render("refused", refusal.category), flush=True)
        raise SystemExit(refusal.exit_code) from None
    except KeyboardInterrupt:
        print(_render("refused", "operation_refused"), flush=True)
        raise SystemExit(14) from None
    except Exception:
        print(_render("refused", "operation_refused"), flush=True)
        raise SystemExit(14) from None
    print(_render("materialized", "ready"), flush=True)


__all__ = [
    "ArtifactTransport",
    "HttpArtifactTransport",
    "MaterializationRefused",
    "RegisteredModelSnapshot",
    "load_registered_snapshot",
    "main",
    "materialize_registered_snapshot",
]
