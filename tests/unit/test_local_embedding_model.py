import importlib
import os
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import rfc8785

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
    verification_calls: list[tuple[Path, tuple[tuple[str, str], ...], str]] = []
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
        "verify_model_artifacts",
        lambda path, expected, digest: verification_calls.append(
            (path, expected, digest)
        ),
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
        (model_dir, artifacts, QWEN3_EMBEDDING_PROFILE.artifact_digest),
        (model_dir, artifacts, QWEN3_EMBEDDING_PROFILE.artifact_digest),
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
        local_model.verify_model_artifacts(
            model_dir,
            (("model.safetensors", expected_digest),),
            QWEN3_EMBEDDING_PROFILE.artifact_digest,
        )

    assert failure.value.__cause__ is None


@pytest.mark.parametrize("flag_name", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_local_model_verifier_requires_safe_descriptor_flags_for_flat_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag_name: str,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    content = b"registered bytes"
    (model_dir / "model.safetensors").write_bytes(content)
    expected_digest = sha256(content).hexdigest()
    manifest_digest = sha256(
        rfc8785.dumps([{"path": "model.safetensors", "sha256": expected_digest}])
    ).hexdigest()
    monkeypatch.delattr(os, flag_name)

    with pytest.raises(
        local_model.LocalEmbeddingModelUnavailable,
        match="Local embedding model is unavailable",
    ) as failure:
        local_model.verify_model_artifacts(
            model_dir,
            (("model.safetensors", expected_digest),),
            manifest_digest,
        )

    assert failure.value.__cause__ is None


@pytest.mark.parametrize("extra_kind", ("directory", "symlink", "fifo"))
def test_local_model_verifier_refuses_every_unregistered_file_type(
    tmp_path: Path,
    extra_kind: str,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    artifact = model_dir / "model.safetensors"
    content = b"registered bytes"
    artifact.write_bytes(content)
    expected_digest = sha256(content).hexdigest()
    manifest_digest = sha256(
        rfc8785.dumps([{"path": "model.safetensors", "sha256": expected_digest}])
    ).hexdigest()
    extra = model_dir / "unregistered"
    if extra_kind == "directory":
        extra.mkdir()
    elif extra_kind == "symlink":
        extra.symlink_to(artifact)
    else:
        os.mkfifo(extra)

    with pytest.raises(local_model.LocalEmbeddingModelUnavailable):
        local_model.verify_model_artifacts(
            model_dir,
            (("model.safetensors", expected_digest),),
            manifest_digest,
        )
