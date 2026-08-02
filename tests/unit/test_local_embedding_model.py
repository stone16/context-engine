import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import adapters.local_embedding_model as local_model
from engine.supply import QWEN3_EMBEDDING_PROFILE


def test_local_model_registry_resolves_exact_qwen_artifact_manifest() -> None:
    artifacts = local_model._registered_qwen_artifacts()

    assert len(artifacts) == 10
    assert artifacts == tuple(sorted(artifacts))
    assert artifacts[-1][0] == "vocab.json"
    assert all(len(digest) == 64 for _path, digest in artifacts)


def test_local_model_load_verifies_bytes_before_and_after_backend_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = Path("/verified/qwen")
    artifacts = (("model.safetensors", "a" * 64),)
    verification_calls: list[tuple[Path, tuple[tuple[str, str], ...]]] = []
    constructed: list[tuple[str, bool, bool]] = []
    model = object()

    class _Backend:
        @staticmethod
        def SentenceTransformer(
            path: str,
            *,
            local_files_only: bool,
            trust_remote_code: bool,
        ) -> object:
            constructed.append((path, local_files_only, trust_remote_code))
            return model

    monkeypatch.setattr(local_model, "_registered_qwen_artifacts", lambda: artifacts)
    monkeypatch.setattr(
        local_model,
        "_verify_model_artifacts",
        lambda path, expected: verification_calls.append((path, expected)),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(SentenceTransformer=_Backend.SentenceTransformer)
        if name == "sentence_transformers"
        else None,
    )

    assert local_model.load_qwen_local_model(model_dir) is model
    assert verification_calls == [
        (model_dir, artifacts),
        (model_dir, artifacts),
    ]
    assert constructed == [(str(model_dir), True, False)]


def test_local_model_refuses_changed_or_extra_artifacts(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    artifact = model_dir / "model.safetensors"
    artifact.write_bytes(b"changed bytes")
    expected_digest = "a" * 64
    monkeypatch.setattr(
        local_model,
        "QWEN3_EMBEDDING_PROFILE",
        QWEN3_EMBEDDING_PROFILE,
    )

    with pytest.raises(
        local_model.LocalEmbeddingModelUnavailable,
        match="Local embedding model is unavailable",
    ) as failure:
        local_model._verify_model_artifacts(
            model_dir,
            (("model.safetensors", expected_digest),),
        )

    assert failure.value.__cause__ is None
