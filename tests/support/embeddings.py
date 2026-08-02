"""Deterministic test providers carrying exact production profile identities."""

from adapters.embeddings import DeterministicEmbeddingTwin
from engine.supply import (
    QWEN3_EMBEDDING_PROFILE,
    EmbeddingProfile,
    EmbeddingProviderProfile,
    EmbeddingVector,
)


class QwenEmbeddingTwin:
    """Network-free deterministic vectors with the exact pinned Qwen identity."""

    def __init__(self) -> None:
        self._twin = DeterministicEmbeddingTwin()
        self.query_calls: list[tuple[str, ...]] = []
        self.document_calls: list[tuple[str, ...]] = []

    @property
    def profile(self) -> EmbeddingProfile:
        return QWEN3_EMBEDDING_PROFILE.vector_profile

    @property
    def provider_profile(self) -> EmbeddingProviderProfile:
        return QWEN3_EMBEDDING_PROFILE

    def embed(self, inputs: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        self.query_calls.append(inputs)
        return self._twin.embed(inputs)

    def embed_documents(
        self,
        inputs: tuple[str, ...],
    ) -> tuple[EmbeddingVector, ...]:
        self.document_calls.append(inputs)
        return self._twin.embed(inputs)
