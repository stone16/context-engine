"""Supply-only embedding contracts for immutable Fragment publication."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, cast

CONTEXT_FRAGMENT_EMBEDDING_DIMENSION = 384
type EmbeddingVector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Closed vector shape stored by the current PostgreSQL schema."""

    dimension: int

    def __post_init__(self) -> None:
        if type(self.dimension) is not int or not 1 <= self.dimension <= 4096:
            raise ValueError("Embedding dimension is not available")


class EmbeddingProviderUnavailable(RuntimeError):
    """Content-free transient provider failure."""


class EmbeddingProvider(Protocol):
    """Batch embedding seam used only by Supply publication."""

    @property
    def profile(self) -> EmbeddingProfile: ...

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]: ...


def validate_embedding_batch(
    inputs: tuple[str, ...],
    vectors: Sequence[Sequence[object]],
    profile: EmbeddingProfile,
) -> tuple[EmbeddingVector, ...]:
    """Validate one provider response before any vector crosses persistence."""

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
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            value = float(cast(int | float, raw_value))
            if not isfinite(value) or abs(value) > 1.0e30:
                raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
            vector.append(value)
        if not any(value != 0.0 for value in vector):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        validated.append(tuple(vector))
    return tuple(validated)
