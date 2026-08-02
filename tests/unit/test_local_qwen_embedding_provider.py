from pathlib import Path
from typing import Any

import pytest

from adapters.embeddings import LocalQwenEmbeddingProvider
from engine.supply import QWEN3_EMBEDDING_PROFILE, EmbeddingProviderUnavailable


class _Model:
    def __init__(self, output: list[list[float]]) -> None:
        self.output = output
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def encode(self, inputs: list[str], **kwargs: object) -> list[list[float]]:
        self.calls.append((inputs, kwargs))
        return self.output


def test_local_qwen_applies_registered_query_prefix_and_reduction(
    monkeypatch: Any,
) -> None:
    model = _Model([[1.0] * 1024])
    monkeypatch.setattr(
        "adapters.embeddings.load_qwen_local_model",
        lambda _model_dir: model,
    )

    provider = LocalQwenEmbeddingProvider(Path("/verified/local/model"))
    vector = provider.embed(("where is the answer?",))[0]

    assert provider.provider_profile == QWEN3_EMBEDDING_PROFILE
    assert len(vector) == 384
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    assert model.calls[0][0] == [
        QWEN3_EMBEDDING_PROFILE.query_prefix + "where is the answer?"
    ]
    assert model.calls[0][1]["normalize_embeddings"] is True

    provider.embed_documents(("document text",))
    assert model.calls[1][0] == ["document text"]


def test_local_qwen_malformed_output_is_generic_unavailability(
    monkeypatch: Any,
) -> None:
    model = _Model([[1.0] * 383])
    monkeypatch.setattr(
        "adapters.embeddings.load_qwen_local_model",
        lambda _model_dir: model,
    )
    provider = LocalQwenEmbeddingProvider(Path("/verified/local/model"))

    with pytest.raises(
        EmbeddingProviderUnavailable,
        match="Embedding provider is unavailable",
    ) as failure:
        provider.embed(("query",))

    assert failure.value.__cause__ is None
