from __future__ import annotations

import json
from typing import cast
from urllib.request import Request

import pytest

from adapters.embeddings import (
    DeterministicEmbeddingTwin,
    ExternalEmbeddingConfiguration,
    ExternalEmbeddingProvider,
)
from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProfile,
    EmbeddingProviderUnavailable,
    validate_embedding_batch,
)


def test_deterministic_twin_is_content_derived_and_fixed_dimension() -> None:
    provider = DeterministicEmbeddingTwin()

    first = provider.embed(("same fragment", "different fragment"))
    replay = provider.embed(("same fragment", "different fragment"))

    assert first == replay
    assert first[0] != first[1]
    assert all(len(vector) == CONTEXT_FRAGMENT_EMBEDDING_DIMENSION for vector in first)
    assert all(any(value != 0.0 for value in vector) for vector in first)


def test_external_provider_binds_model_dimension_and_input_without_leaking_key() -> (
    None
):
    observed: list[tuple[Request, float, int]] = []

    def transport(request: Request, timeout: float, maximum_bytes: int) -> bytes:
        observed.append((request, timeout, maximum_bytes))
        inputs = json.loads(cast(bytes, request.data or b"{}"))["input"]
        return json.dumps(
            {
                "data": [
                    {
                        "index": index,
                        "embedding": [0.25] * CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
                    }
                    for index, _value in enumerate(inputs)
                ]
            }
        ).encode("utf-8")

    configuration = ExternalEmbeddingConfiguration(
        endpoint="https://embedding.invalid/v1/embeddings",
        model="configured-model",
        api_key="credential-value",
        dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    )
    provider = ExternalEmbeddingProvider(configuration, transport=transport)

    vectors = provider.embed(("first", "second"))

    assert len(vectors) == 2
    request, timeout, maximum_bytes = observed[0]
    payload = json.loads(cast(bytes, request.data or b"{}"))
    assert payload == {
        "dimensions": CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
        "encoding_format": "float",
        "input": ["first", "second"],
        "model": "configured-model",
    }
    assert request.get_header("Authorization") == "Bearer credential-value"
    assert timeout == configuration.timeout_seconds
    assert maximum_bytes > 0
    assert "credential-value" not in repr(configuration)
    assert "embedding.invalid" not in repr(configuration)
    assert "credential-value" not in repr(provider)
    assert "embedding.invalid" not in repr(provider)


def test_external_provider_replaces_transport_details_with_generic_failure() -> None:
    def transport(_request: Request, _timeout: float, _maximum_bytes: int) -> bytes:
        raise OSError("credential-value and response content")

    provider = ExternalEmbeddingProvider(
        ExternalEmbeddingConfiguration(
            endpoint="https://embedding.invalid/v1/embeddings",
            model="configured-model",
            api_key="credential-value",
            dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
        ),
        transport=transport,
    )

    with pytest.raises(
        EmbeddingProviderUnavailable,
        match="Embedding provider is unavailable",
    ) as failure:
        provider.embed(("content",))

    assert "credential-value" not in str(failure.value)
    assert failure.value.__cause__ is None


@pytest.mark.parametrize(
    ("endpoint", "model", "api_key"),
    [
        ("http://embedding.invalid/v1/embeddings", "model", "key"),
        ("https://embedding.invalid/v1/embeddings?token=value", "model", "key"),
        ("https://embedding.invalid/v1/embeddings", " model", "key"),
        ("https://embedding.invalid/v1/embeddings", "model", " key"),
    ],
)
def test_external_configuration_refuses_unsafe_or_ambiguous_values(
    endpoint: str,
    model: str,
    api_key: str,
) -> None:
    with pytest.raises(ValueError, match="configuration is not available"):
        ExternalEmbeddingConfiguration(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.0e31, 0.0])
def test_embedding_validation_refuses_unstorable_or_zero_vectors(
    value: float,
) -> None:
    with pytest.raises(EmbeddingProviderUnavailable):
        validate_embedding_batch(
            ("content",),
            ((value,),),
            EmbeddingProfile(1),
        )
