from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import rfc8785

from applications import embedding_benchmark as cli
from eval.embedding_benchmark import (
    BenchmarkUnavailable,
    ModelIdentity,
    ModelTransformationPipeline,
)

PRIMARY_PIPELINE = "l2 -> truncate 1024->384 -> l2"
BASELINE_PIPELINE = "l2 -> keep native 384 -> l2"


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
            "pooling": "last_token",
            "precision": "float32",
            "queryPrefix": (
                "Instruct: Given a web search query, retrieve relevant passages "
                "that answer the query\nQuery:"
            ),
            "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            "transformationPipeline": PRIMARY_PIPELINE,
        },
    }
    baseline: dict[str, Any] = deepcopy(primary)
    baseline["identity"]["modelId"] = "intfloat/multilingual-e5-small"
    baseline["identity"]["revision"] = (
        "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    )
    baseline["identity"]["pooling"] = "mean"
    baseline["identity"]["queryPrefix"] = "query: "
    baseline["identity"]["documentPrefix"] = "passage: "
    baseline["identity"]["transformationPipeline"] = BASELINE_PIPELINE
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


def _identity_digest(identity: dict[str, object]) -> str:
    digestable = {
        key: value for key, value in identity.items() if key != "artifactDigest"
    }
    return hashlib.sha256(rfc8785.dumps(cast(Any, digestable))).hexdigest()


def test_model_artifact_must_match_the_tracked_registry_digest(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    actual = b"untracked artifact bytes"
    (model_dir / "model.safetensors").write_bytes(actual)
    registry_path = tmp_path / "model-registry.json"
    registry = _registry(
        expected_files={"model.safetensors": b"tracked artifact bytes"},
    )
    models = registry["models"]
    assert isinstance(models, dict)
    primary = models["primary"]
    assert isinstance(primary, dict)
    identity = primary["identity"]
    assert isinstance(identity, dict)
    identity["artifactDigest"] = _expected_directory_digest(
        {"model.safetensors": actual}
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
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


@pytest.mark.parametrize("role", ("primary", "baseline"))
def test_loader_round_trips_every_registered_identity_field(
    tmp_path: Path,
    monkeypatch: Any,
    role: str,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    registry = _registry(expected_files={"model.safetensors": artifact})
    registry_path = tmp_path / "model-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    identity = cli.load_registered_model_identity(
        role=role,
        backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
        model_dir=model_dir,
    )

    models = registry["models"]
    assert isinstance(models, dict)
    model = models[role]
    assert isinstance(model, dict)
    assert identity.public_document() == model["identity"]


def test_loader_reads_dimension_from_the_validated_registry(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    registry = _registry(expected_files={"model.safetensors": artifact})
    models = registry["models"]
    assert isinstance(models, dict)
    primary = models["primary"]
    assert isinstance(primary, dict)
    identity_document = primary["identity"]
    assert isinstance(identity_document, dict)
    identity_document["dimension"] = 385
    registry_path = tmp_path / "model-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(cli, "validate_json_schema_document", lambda *_args: None)
    monkeypatch.setitem(
        cli._REGISTERED_IDENTITY_DIGESTS,
        "primary",
        _identity_digest(identity_document),
    )
    captured: dict[str, object] = {}

    def capture_identity(**values: object) -> Any:
        captured.update(values)
        return SimpleNamespace(**values)

    monkeypatch.setattr(cli, "ModelIdentity", capture_identity)

    cli.load_registered_model_identity(
        role="primary",
        backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
        model_dir=model_dir,
    )

    assert captured["dimension"] == 385


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
    artifact = b"same bytes under the wrong path"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "renamed.safetensors").write_bytes(artifact)
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": artifact},
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


@pytest.mark.parametrize(
    ("model_id", "pipeline", "raw_dimension"),
    (
        ("Qwen/Qwen3-Embedding-0.6B", PRIMARY_PIPELINE, 1024),
        ("intfloat/multilingual-e5-small", BASELINE_PIPELINE, 384),
    ),
)
def test_every_emitted_vector_is_unit_norm_after_the_declared_pipeline(
    model_id: str,
    pipeline: str,
    raw_dimension: int,
    monkeypatch: Any,
) -> None:
    raw_value = 1.0 / math.sqrt(raw_dimension)
    encode_arguments: list[dict[str, object]] = []

    class FakeModel:
        def encode(self, _values: list[str], **arguments: object) -> list[list[float]]:
            encode_arguments.append(arguments)
            return [[raw_value] * raw_dimension]

    class FakeBackend:
        @staticmethod
        def SentenceTransformer(*_args: object, **_kwargs: object) -> FakeModel:
            return FakeModel()

    monkeypatch.setattr(cli, "_load_sentence_transformers", lambda: FakeBackend)
    identity = ModelIdentity(
        model_id=model_id,
        revision="a" * 40,
        artifact_digest="b" * 64,
        dimension=384,
        transformation_pipeline=ModelTransformationPipeline(pipeline),
        pooling="mean",
        query_prefix="query: ",
        document_prefix="passage: ",
        precision="float32",
        batch_size=8,
    )
    provider = cli.SentenceTransformersProvider(
        identity=identity,
        model_dir=Path("unused"),
    )

    vector = provider.embed_queries(("query",))[0]

    assert len(vector) == 384
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(
        1.0, abs=1e-6
    )
    assert encode_arguments[0]["normalize_embeddings"] is True


def test_registry_pipeline_round_trips_into_the_report_identity(
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

    assert identity.public_document()["transformationPipeline"] == PRIMARY_PIPELINE


def test_registry_declaring_a_pipeline_the_model_code_does_not_perform_is_refused(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    registry = _registry(
        expected_files={"model.safetensors": artifact},
    )
    models = registry["models"]
    assert isinstance(models, dict)
    primary = models["primary"]
    assert isinstance(primary, dict)
    identity = primary["identity"]
    assert isinstance(identity, dict)
    identity["transformationPipeline"] = BASELINE_PIPELINE
    monkeypatch.setitem(
        cli._REGISTERED_IDENTITY_DIGESTS,
        "primary",
        _identity_digest(identity),
    )
    registry_path = tmp_path / "model-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("revision", "f" * 40),
        ("pooling", "mean"),
    ),
)
def test_loader_refuses_valid_but_unapproved_non_pipeline_identity_mutations(
    tmp_path: Path,
    monkeypatch: Any,
    field_name: str,
    changed_value: str,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(artifact)
    registry = _registry(expected_files={"model.safetensors": artifact})
    models = registry["models"]
    assert isinstance(models, dict)
    primary = models["primary"]
    assert isinstance(primary, dict)
    identity = primary["identity"]
    assert isinstance(identity, dict)
    identity[field_name] = changed_value
    registry_path = tmp_path / "model-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )


def test_artifacts_are_reverified_after_the_backend_loads(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    artifact = b"tracked artifact bytes"
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    artifact_path = model_dir / "model.safetensors"
    artifact_path.write_bytes(artifact)
    registry_path = tmp_path / "model-registry.json"
    _write_registry(
        registry_path,
        expected_files={"model.safetensors": artifact},
    )
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    class SwappingSentenceTransformer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            artifact_path.write_bytes(b"swapped after verification")

    class FakeBackend:
        SentenceTransformer = SwappingSentenceTransformer

    monkeypatch.setattr(cli, "_load_sentence_transformers", lambda: FakeBackend)

    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        cli.build_local_provider(
            cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            "primary",
            model_dir,
        )


def test_tracked_registry_pins_every_load_bearing_identity_field() -> None:
    registry = json.loads(cli.MODEL_REGISTRY_PATH.read_text(encoding="utf-8"))

    primary_digest = (
        "8cb25677d5be69ce6ac88ebbdfb5dad30980fee39c35c6324a583e325917eddc"
    )
    baseline_digest = (
        "961e0953328daf4df7916c1406751c5c63fe19e14a5c7e977d9f8b38964c5cf3"
    )
    qwen_query_prefix = (
        "Instruct: Given a web search query, retrieve relevant passages "
        "that answer the query\nQuery:"
    )
    assert registry["models"]["primary"]["identity"] == {
        "artifactDigest": primary_digest,
        "batchSize": 8,
        "dimension": 384,
        "documentPrefix": "",
        "modelId": "Qwen/Qwen3-Embedding-0.6B",
        "pooling": "last_token",
        "precision": "float32",
        "queryPrefix": qwen_query_prefix,
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "transformationPipeline": PRIMARY_PIPELINE,
    }
    assert registry["models"]["baseline"]["identity"] == {
        "artifactDigest": baseline_digest,
        "batchSize": 8,
        "dimension": 384,
        "documentPrefix": "passage: ",
        "modelId": "intfloat/multilingual-e5-small",
        "pooling": "mean",
        "precision": "float32",
        "queryPrefix": "query: ",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "transformationPipeline": BASELINE_PIPELINE,
    }
    assert hashlib.sha256(
        rfc8785.dumps(registry["models"]["primary"])
    ).hexdigest() == "c6fd8959cf770e872ca441c8b48fec7109eef681af5a7d9f47d08e26f045e152"
    assert hashlib.sha256(
        rfc8785.dumps(registry["models"]["baseline"])
    ).hexdigest() == "ac247591224422998acfe77af86320dcc440d63476a8fe8cd96f40bf8ba0a693"


@pytest.mark.parametrize(
    "invalid_json",
    (
        '{"models": 9223372036854775808}',
        '{"models": 1e1000000}',
        '{"models": NaN}',
        "[" * 80 + "0" + "]" * 80,
    ),
)
def test_model_registry_pathologies_fail_as_typed_unresolved_identity(
    tmp_path: Path,
    monkeypatch: Any,
    invalid_json: str,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    registry_path = tmp_path / "model-registry.json"
    registry_path.write_text(invalid_json, encoding="utf-8")
    monkeypatch.setattr(cli, "MODEL_REGISTRY_PATH", registry_path)

    with pytest.raises(BenchmarkUnavailable):
        cli.load_registered_model_identity(
            role="primary",
            backend=cli.SupportedBackend.SENTENCE_TRANSFORMERS,
            model_dir=model_dir,
        )
