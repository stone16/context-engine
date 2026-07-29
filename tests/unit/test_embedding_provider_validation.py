from __future__ import annotations

import math

import pytest

from engine.supply.embeddings import (
    EmbeddingProfile,
    EmbeddingProviderUnavailable,
    validate_embedding_batch,
)


@pytest.mark.parametrize(
    ("inputs", "vectors"),
    (
        (("first",), ((1.0,),)),
        (("first",), ((1.0, math.nan),)),
        (("first",), ((0.0, 0.0),)),
        (("first", "second"), ((1.0, 1.0),)),
    ),
)
def test_invalid_provider_vectors_fail_closed_as_provider_unavailable(
    inputs: tuple[str, ...],
    vectors: tuple[tuple[float, ...], ...],
) -> None:
    profile = EmbeddingProfile(2)

    with pytest.raises(
        EmbeddingProviderUnavailable,
        match="Embedding provider is unavailable",
    ):
        validate_embedding_batch(inputs, vectors, profile)
