"""Hash-verified local model loading for the activated Qwen adapter."""

from __future__ import annotations

import importlib
import json
import os
import stat
from contextlib import suppress
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, cast

import rfc8785

from engine.embedding_profiles import (
    QWEN3_EMBEDDING_PROFILE,
    registered_embedding_provider_profile,
)

MODEL_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "eval"
    / "embedding-benchmark"
    / "model-registry.json"
)
_MAX_REGISTRY_BYTES = 1024 * 1024
_MAX_ARTIFACTS = 256
_SHA256_DIGEST_LENGTH = sha256().digest_size * 2


class LocalEmbeddingModelUnavailable(RuntimeError):
    """The pinned local model identity or backend could not be resolved."""


ModelReadinessCategory = Literal[
    "model_manifest_unavailable",
    "model_manifest_invalid",
    "model_artifacts_unavailable",
    "model_artifacts_invalid",
]


class LocalEmbeddingModelReadinessError(LocalEmbeddingModelUnavailable):
    """A content-free, operator-actionable local-model readiness refusal."""

    readiness_category: ClassVar[ModelReadinessCategory]

    def __init__(self) -> None:
        super().__init__("Local embedding model is unavailable")


class LocalEmbeddingModelManifestUnavailable(LocalEmbeddingModelReadinessError):
    readiness_category = "model_manifest_unavailable"


class LocalEmbeddingModelManifestInvalid(LocalEmbeddingModelReadinessError):
    readiness_category = "model_manifest_invalid"


class LocalEmbeddingModelArtifactsUnavailable(LocalEmbeddingModelReadinessError):
    readiness_category = "model_artifacts_unavailable"


class LocalEmbeddingModelArtifactsInvalid(LocalEmbeddingModelReadinessError):
    readiness_category = "model_artifacts_invalid"


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def registered_qwen_snapshot_contract() -> tuple[
    str, str, str, tuple[tuple[str, str], ...]
]:
    """Load exact Qwen identity and artifacts from the tracked registry."""

    try:
        metadata = MODEL_REGISTRY_PATH.stat()
    except (OSError, MemoryError):
        raise LocalEmbeddingModelManifestUnavailable from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not 0 < metadata.st_size <= _MAX_REGISTRY_BYTES
    ):
        raise LocalEmbeddingModelManifestInvalid from None
    try:
        raw = MODEL_REGISTRY_PATH.read_bytes()
    except (OSError, MemoryError):
        raise LocalEmbeddingModelManifestUnavailable from None
    try:
        if len(raw) != metadata.st_size:
            raise ValueError
        root = json.loads(raw, parse_constant=_reject_json_constant)
        if type(root) is not dict or set(root) != {"schemaVersion", "models"}:
            raise ValueError
        if root["schemaVersion"] != "context-engine-embedding-model-registry-v1":
            raise ValueError
        models = root["models"]
        if type(models) is not dict or set(models) != {"primary", "baseline"}:
            raise ValueError
        primary = models["primary"]
        if type(primary) is not dict or set(primary) != {
            "artifacts",
            "backend",
            "identity",
        }:
            raise ValueError
        if primary["backend"] != "sentence-transformers":
            raise ValueError
        identity = primary["identity"]
        if type(identity) is not dict:
            raise ValueError
        canonical_identity = rfc8785.dumps(cast(Any, identity)).decode("utf-8")
        registered_embedding_provider_profile(
            canonical_identity,
            QWEN3_EMBEDDING_PROFILE.profile_digest,
        )
        if canonical_identity != QWEN3_EMBEDDING_PROFILE.canonical_json():
            raise ValueError
        raw_artifacts = primary["artifacts"]
        if (
            type(raw_artifacts) is not list
            or not raw_artifacts
            or len(raw_artifacts) > _MAX_ARTIFACTS
        ):
            raise ValueError
        artifacts: list[tuple[str, str]] = []
        for raw_artifact in raw_artifacts:
            if type(raw_artifact) is not dict or set(raw_artifact) != {
                "path",
                "sha256",
            }:
                raise ValueError
            relative_path = raw_artifact["path"]
            expected_digest = raw_artifact["sha256"]
            if type(relative_path) is not str or type(expected_digest) is not str:
                raise ValueError
            parsed_path = PurePosixPath(relative_path)
            if (
                not relative_path
                or parsed_path.is_absolute()
                or str(parsed_path) != relative_path
                or any(part in {"", ".", ".."} for part in parsed_path.parts)
                or len(expected_digest) != _SHA256_DIGEST_LENGTH
                or any(
                    character not in "0123456789abcdef" for character in expected_digest
                )
            ):
                raise ValueError
            artifacts.append((relative_path, expected_digest))
        registered_paths = {path for path, _digest in artifacts}
        if artifacts != sorted(artifacts) or len(registered_paths) != len(artifacts):
            raise ValueError
        return (
            cast(str, identity["modelId"]),
            cast(str, identity["revision"]),
            cast(str, identity["artifactDigest"]),
            tuple(artifacts),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        raise LocalEmbeddingModelManifestInvalid from None


def _registered_qwen_artifacts() -> tuple[tuple[str, str], ...]:
    return registered_qwen_snapshot_contract()[3]


def _required_descriptor_flags() -> tuple[int, int]:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if (
        type(directory_flag) is not int
        or directory_flag == 0
        or type(no_follow_flag) is not int
        or no_follow_flag == 0
    ):
        raise ValueError
    return directory_flag, no_follow_flag


def verify_model_artifacts(
    model_dir: Path,
    expected_artifacts: tuple[tuple[str, str], ...],
    expected_artifact_digest: str,
) -> None:
    """Verify one exact artifact tree through no-follow directory descriptors."""

    try:
        directory_flag, no_follow_flag = _required_descriptor_flags()
        flags = os.O_RDONLY | directory_flag | no_follow_flag
        root_descriptor = os.open(model_dir, flags)
    except (OSError, MemoryError, TypeError, ValueError):
        raise LocalEmbeddingModelArtifactsUnavailable from None
    try:
        try:
            verify_model_artifacts_descriptor(
                root_descriptor,
                expected_artifacts,
                expected_artifact_digest,
            )
        finally:
            os.close(root_descriptor)
    except (OSError, MemoryError):
        raise LocalEmbeddingModelArtifactsUnavailable from None
    except (TypeError, ValueError):
        raise LocalEmbeddingModelArtifactsInvalid from None


def verify_model_artifacts_descriptor(
    root_descriptor: int,
    expected_artifacts: tuple[tuple[str, str], ...],
    expected_artifact_digest: str,
) -> None:
    try:
        _required_descriptor_flags()
    except ValueError:
        raise LocalEmbeddingModelArtifactsUnavailable from None
    if type(root_descriptor) is not int or root_descriptor < 0:
        raise ValueError
    root_metadata = os.fstat(root_descriptor)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError
    expected = dict(expected_artifacts)
    if len(expected) != len(expected_artifacts):
        raise ValueError
    observed, directories = _artifact_tree_from_descriptor(root_descriptor)
    expected_directories = frozenset(
        parent.as_posix()
        for relative_path in expected
        for parent in PurePosixPath(relative_path).parents
        if parent != PurePosixPath(".")
    )
    if directories != expected_directories:
        for descriptor in observed.values():
            os.close(descriptor)
        raise ValueError
    if tuple(sorted(observed)) != tuple(path for path, _digest in expected_artifacts):
        for descriptor in observed.values():
            os.close(descriptor)
        raise ValueError
    manifest: list[dict[str, str]] = []
    try:
        for relative_path, expected_digest in expected_artifacts:
            descriptor = observed[relative_path]
            digest = sha256()
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError
            with os.fdopen(os.dup(descriptor), "rb", closefd=True) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or digest.hexdigest() != expected_digest:
                raise ValueError
            manifest.append({"path": relative_path, "sha256": expected_digest})
    finally:
        for descriptor in observed.values():
            with suppress(OSError):
                os.close(descriptor)
    if sha256(rfc8785.dumps(manifest)).hexdigest() != expected_artifact_digest:
        raise ValueError


def _artifact_tree_from_descriptor(
    root_descriptor: int,
) -> tuple[dict[str, int], frozenset[str]]:
    files: dict[str, int] = {}
    directories: set[str] = set()

    def walk(directory_descriptor: int, prefix: PurePosixPath | None) -> None:
        with os.scandir(directory_descriptor) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            name = entry.name
            if type(name) is not str or name in {"", ".", ".."} or "/" in name:
                raise ValueError
            relative = PurePosixPath(name) if prefix is None else prefix / name
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative.as_posix())
                directory_flag, no_follow_flag = _required_descriptor_flags()
                flags = os.O_RDONLY | directory_flag | no_follow_flag
                child = os.open(name, flags, dir_fd=directory_descriptor)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (
                        metadata.st_dev,
                        metadata.st_ino,
                    ):
                        raise ValueError
                    walk(child, relative)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError
            _directory_flag, no_follow_flag = _required_descriptor_flags()
            flags = os.O_RDONLY | no_follow_flag
            descriptor = os.open(name, flags, dir_fd=directory_descriptor)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                os.close(descriptor)
                raise ValueError
            files[relative.as_posix()] = descriptor

    try:
        walk(root_descriptor, None)
        return files, frozenset(directories)
    except BaseException:
        for descriptor in files.values():
            with suppress(OSError):
                os.close(descriptor)
        raise


def load_qwen_local_model(model_dir: Path) -> Any:
    """Load only the registry-pinned local Qwen bytes without network access."""

    if not isinstance(model_dir, Path):
        raise TypeError("Local embedding model requires a model directory")
    expected_artifacts = _registered_qwen_artifacts()
    verify_model_artifacts(
        model_dir,
        expected_artifacts,
        QWEN3_EMBEDDING_PROFILE.artifact_digest,
    )
    try:
        backend = importlib.import_module("sentence_transformers")
        model = backend.SentenceTransformer(
            str(model_dir),
            local_files_only=True,
            trust_remote_code=False,
        )
    except Exception:
        raise LocalEmbeddingModelUnavailable(
            "Local embedding model is unavailable"
        ) from None
    verify_model_artifacts(
        model_dir,
        expected_artifacts,
        QWEN3_EMBEDDING_PROFILE.artifact_digest,
    )
    return model


def verify_registered_qwen_artifacts(model_dir: Path) -> None:
    """Verify the pinned artifact set without importing or constructing a backend."""

    if not isinstance(model_dir, Path):
        raise TypeError("Local embedding model requires a model directory")
    verify_model_artifacts(
        model_dir,
        _registered_qwen_artifacts(),
        QWEN3_EMBEDDING_PROFILE.artifact_digest,
    )


__all__ = [
    "LocalEmbeddingModelArtifactsInvalid",
    "LocalEmbeddingModelArtifactsUnavailable",
    "LocalEmbeddingModelManifestInvalid",
    "LocalEmbeddingModelManifestUnavailable",
    "LocalEmbeddingModelReadinessError",
    "LocalEmbeddingModelUnavailable",
    "QWEN3_EMBEDDING_PROFILE",
    "load_qwen_local_model",
    "registered_qwen_snapshot_contract",
    "verify_model_artifacts",
    "verify_model_artifacts_descriptor",
    "verify_registered_qwen_artifacts",
]
