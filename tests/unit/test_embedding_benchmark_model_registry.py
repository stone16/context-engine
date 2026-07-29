from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from applications import embedding_benchmark as cli
from eval.embedding_benchmark import BenchmarkUnavailable


def _registry(
    *, expected_files: dict[str, bytes], include_primary: bool = True
) -> dict[str, object]:
    artifacts = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(expected_files.items())
    ]
    artifact_digest = hashlib.sha256(rfc8785.dumps(artifacts)).hexdigest()
    primary: dict[str, Any] = {
        "artifacts": artifacts,
        "backend": "sentence-transformers",
        "identity": {
            "artifactDigest": artifact_digest,
            "batchSize": 8,
            "dimension": 384,
            "documentPrefix": "",
            "modelId": "Qwen/Qwen3-Embedding-0.6B",
            "normalization": "l2",
            "pooling": "last_token",
            "precision": "float32",
            "queryPrefix": "Instruct: retrieve relevant passages\nQuery:",
            "reduction": "matryoshka_truncate_384",
            "revision": "a" * 40,
        },
    }
    baseline: dict[str, Any] = deepcopy(primary)
    baseline["identity"]["modelId"] = "intfloat/multilingual-e5-small"
    models: dict[str, object] = {"baseline": baseline}
    if include_primary:
        models["primary"] = primary
    return {
        "models": models,
        "schemaVersion": "context-engine-embedding-model-registry-v1",
    }


def _write_registry(
    path: Path,
    *,
    expected_files: dict[str, bytes],
    include_primary: bool = True,
) -> None:
    path.write_text(
        json.dumps(
            _registry(
                expected_files=expected_files,
                include_primary=include_primary,
            )
        ),
        encoding="utf-8",
    )


def _expected_directory_digest(files: dict[str, bytes]) -> str:
    manifest = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(files.items())
    ]
    return hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()


def test_model_artifact_must_match_the_tracked_registry_digest(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"untracked artifact bytes")
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": b"tracked artifact bytes"},
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


def test_model_absent_from_the_tracked_registry_is_refused(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"artifact")
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": b"artifact"},
        include_primary=False,
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


def test_local_input_cannot_override_a_tracked_identity_field(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    (model_dir / "benchmark-model-identity.json").write_text(
        json.dumps({"revision": "f" * 40}),
        encoding="utf-8",
    )
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": artifact},
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="identity override"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


def test_matching_artifact_uses_only_the_tracked_identity(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": artifact},
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    identity = cli.load_registered_model_identity(
        role="primary",
        backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
        model_dir=model_dir,
    )

    assert identity.model_id == "Qwen/Qwen3-Embedding-0.6B"
    assert identity.artifact_digest == _expected_directory_digest(
        {"model.safetensors": artifact}
    )


def test_complete_registered_sentence_transformer_layout_is_accepted(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    files = {
        "1_Pooling/config.json": b"pooling",
        "config.json": b"model config",
        "model.safetensors": b"weights",
        "modules.json": b"modules",
        "tokenizer.json": b"tokenizer",
    }
    model_dir = tmp_path / "model"
    for relative_path, content in files.items():
        path = model_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    registry_path = tmp_path / "model-registry.json"
    _write_registry(registry_path, expected_files=files)
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    loaded_paths: list[Path] = []

    class FakeSentenceTransformer:
        def __init__(
            self,
            model_path: str,
            *,
            local_files_only: bool,
            trust_remote_code: bool,
        ) -> None:
            assert local_files_only is True
            assert trust_remote_code is False
            loaded_paths.append(Path(model_path))

    class FakeBackend:
        SentenceTransformer = FakeSentenceTransformer

    monkeypatch.setattr(cli, "_load_sentence_transformers", lambda: FakeBackend)

    provider = cli.build_local_provider(
        cli.SupportedBackend.SENTENCE_TRANSFORMERS,
        "primary",
        model_dir,
    )

    assert provider.identity.artifact_digest == _expected_directory_digest(files)
    assert loaded_paths == [model_dir]


def test_complete_registered_layout_refuses_an_extra_untracked_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    files = {
        "config.json": b"model config",
        "model.safetensors": b"weights",
        "modules.json": b"modules",
    }
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for relative_path, content in files.items():
        (model_dir / relative_path).write_bytes(content)
    (model_dir / "caller-identity.json").write_text("{}", encoding="utf-8")
    registry_path = tmp_path / "model-registry.json"
    _write_registry(registry_path, expected_files=files)
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )
