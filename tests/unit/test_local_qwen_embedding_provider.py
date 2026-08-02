from pathlib import Path
from threading import Event
from time import monotonic
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


def test_local_qwen_timeout_is_bounded_and_does_not_accumulate_calls(
    monkeypatch: Any,
) -> None:
    release = Event()

    class _HangingModel:
        def __init__(self) -> None:
            self.calls = 0

        def encode(self, _inputs: list[str], **_kwargs: object) -> list[list[float]]:
            self.calls += 1
            release.wait()
            return [[1.0] * 1024]

    model = _HangingModel()
    monkeypatch.setattr(
        "adapters.embeddings.load_qwen_local_model",
        lambda _model_dir: model,
    )
    monkeypatch.setattr("adapters.embeddings._LOCAL_EMBEDDING_TIMEOUT_SECONDS", 0.01)
    provider = LocalQwenEmbeddingProvider(Path("/verified/local/model"))

    started = monotonic()
    with pytest.raises(EmbeddingProviderUnavailable):
        provider.embed_documents(("document text",))
    first_elapsed = monotonic() - started

    started = monotonic()
    with pytest.raises(EmbeddingProviderUnavailable):
        provider.embed_documents(("document text",))
    second_elapsed = monotonic() - started
    release.set()

    assert first_elapsed < 0.5
    assert second_elapsed < 0.5
    assert model.calls == 1


def test_local_qwen_thread_start_failure_releases_inference_lock(
    monkeypatch: Any,
) -> None:
    model = _Model([[1.0] * 1024])
    monkeypatch.setattr(
        "adapters.embeddings.load_qwen_local_model",
        lambda _model_dir: model,
    )
    provider = LocalQwenEmbeddingProvider(Path("/verified/local/model"))

    starts = 0

    def refuse_start(_thread: object) -> None:
        nonlocal starts
        starts += 1
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(
        "adapters._bounded_call.Thread.start",
        refuse_start,
    )
    with pytest.raises(EmbeddingProviderUnavailable):
        provider.embed(("first query",))
    with pytest.raises(EmbeddingProviderUnavailable):
        provider.embed(("second query",))

    assert starts == 2
    assert model.calls == []
