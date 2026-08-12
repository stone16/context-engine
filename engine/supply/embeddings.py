"""Embedding contracts for Fragment publication and content-free discovery."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from struct import Struct
from struct import error as StructError
from typing import Protocol, cast

from engine.embedding_profiles import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION as CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
)
from engine.embedding_profiles import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE as DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
)
from engine.embedding_profiles import (
    QWEN3_EMBEDDING_PROFILE as QWEN3_EMBEDDING_PROFILE,
)
from engine.embedding_profiles import (
    EmbeddingProfile as EmbeddingProfile,
)
from engine.embedding_profiles import (
    EmbeddingProviderProfile as EmbeddingProviderProfile,
)
from engine.embedding_profiles import (
    registered_embedding_provider_profile as registered_embedding_provider_profile,
)

type EmbeddingVector = tuple[float, ...]
_FLOAT32 = Struct("!f")


class EmbeddingProviderUnavailable(RuntimeError):
    """Content-free transient provider failure."""


class EmbeddingDocumentRefused(EmbeddingProviderUnavailable):
    """Content-free closed refusal for one document outside provider bounds."""


class EmbeddingProvider(Protocol):
    """Batch embedding seam shared by Supply publication and query discovery."""

    @property
    def profile(self) -> EmbeddingProfile: ...

    @property
    def provider_profile(self) -> EmbeddingProviderProfile: ...

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]: ...

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[EmbeddingVector, ...]: ...


def validate_embedding_batch(
    inputs: tuple[str, ...],
    vectors: Sequence[Sequence[object]],
    profile: EmbeddingProfile,
) -> tuple[EmbeddingVector, ...]:
    """Validate one provider response before any vector crosses persistence."""

    try:
        if (
            type(inputs) is not tuple
            or not inputs
            or any(type(value) is not str or not value for value in inputs)
            or len(vectors) != len(inputs)
        ):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        validated: list[EmbeddingVector] = []
        for raw_vector in vectors:
            if len(raw_vector) != profile.dimension:
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            vector: list[float] = []
            for raw_value in raw_vector:
                if type(raw_value) not in {int, float}:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                value = float(cast(int | float, raw_value))
                if not isfinite(value) or abs(value) > 1.0e30:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                stored_value = _FLOAT32.unpack(_FLOAT32.pack(value))[0]
                if not isfinite(stored_value) or abs(stored_value) > 1.0e30:
                    raise EmbeddingProviderUnavailable(
                        "Embedding provider is unavailable"
                    )
                vector.append(stored_value)
            if not any(value != 0.0 for value in vector):
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            validated.append(tuple(vector))
        return tuple(validated)
    except (TypeError, ValueError, OverflowError, StructError):
        raise EmbeddingProviderUnavailable(
            "Embedding provider is unavailable"
        ) from None
