"""Hash-verified local model loading for the activated Qwen adapter."""

from __future__ import annotations

import importlib
import json
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast

import rfc8785

from engine.supply import (
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


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _registered_qwen_artifacts() -> tuple[tuple[str, str], ...]:
    try:
        metadata = MODEL_REGISTRY_PATH.stat()
        if (
            not MODEL_REGISTRY_PATH.is_file()
            or not 0 < metadata.st_size <= _MAX_REGISTRY_BYTES
        ):
            raise ValueError
        raw = MODEL_REGISTRY_PATH.read_bytes()
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
                    character not in "0123456789abcdef"
                    for character in expected_digest
                )
            ):
                raise ValueError
            artifacts.append((relative_path, expected_digest))
        if artifacts != sorted(artifacts):
            raise ValueError
        return tuple(artifacts)
    except LocalEmbeddingModelUnavailable:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        MemoryError,
    ):
        raise LocalEmbeddingModelUnavailable(
            "Local embedding model is unavailable"
        ) from None


def _verify_model_artifacts(
    model_dir: Path,
    expected_artifacts: tuple[tuple[str, str], ...],
) -> None:
    try:
        root = model_dir.resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        files = tuple(sorted(path for path in root.rglob("*") if path.is_file()))
        if (
            not files
            or any(path.is_symlink() for path in files)
            or any(not path.resolve().is_relative_to(root) for path in files)
            or tuple(path.relative_to(root).as_posix() for path in files)
            != tuple(path for path, _digest in expected_artifacts)
        ):
            raise ValueError
        manifest: list[dict[str, str]] = []
        for path, (relative_path, expected_digest) in zip(
            files,
            expected_artifacts,
            strict=True,
        ):
            digest = sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            actual_digest = digest.hexdigest()
            if actual_digest != expected_digest:
                raise ValueError
            manifest.append({"path": relative_path, "sha256": actual_digest})
        if sha256(rfc8785.dumps(manifest)).hexdigest() != (
            QWEN3_EMBEDDING_PROFILE.artifact_digest
        ):
            raise ValueError
    except (OSError, TypeError, ValueError):
        raise LocalEmbeddingModelUnavailable(
            "Local embedding model is unavailable"
        ) from None


def load_qwen_local_model(model_dir: Path) -> Any:
    """Load only the registry-pinned local Qwen bytes without network access."""

    if not isinstance(model_dir, Path):
        raise TypeError("Local embedding model requires a model directory")
    expected_artifacts = _registered_qwen_artifacts()
    _verify_model_artifacts(model_dir, expected_artifacts)
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
    _verify_model_artifacts(model_dir, expected_artifacts)
    return model


__all__ = ["LocalEmbeddingModelUnavailable", "load_qwen_local_model"]
