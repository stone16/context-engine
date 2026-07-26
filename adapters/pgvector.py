"""PostgreSQL ANN candidate discovery using one retained Runtime transaction."""

from __future__ import annotations

from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedProjectionSession,
    _discover_materialized_vector,
)
from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingProviderUnavailable,
    validate_embedding_batch,
)

DEFAULT_VECTOR_CANDIDATE_LIMIT = 16
MAX_VECTOR_CANDIDATE_LIMIT = 64


class VectorCandidateIndexUnavailable(RuntimeError):
    """Content-free failure of query embedding or ANN candidate discovery."""


class PostgreSQLVectorCandidateIndex:
    """Return bounded content-free pgvector candidates for one Acquire."""

    __slots__ = ("_embedding_profile", "_embedding_provider", "_limit")

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        limit: int = DEFAULT_VECTOR_CANDIDATE_LIMIT,
    ) -> None:
        try:
            embedding_profile = embedding_provider.profile
        except (AttributeError, TypeError, ValueError):
            raise TypeError(
                "Vector candidate index requires an embedding provider"
            ) from None
        if type(embedding_profile) is not EmbeddingProfile:
            raise TypeError("Vector candidate index requires an embedding provider")
        if embedding_profile.dimension != CONTEXT_FRAGMENT_EMBEDDING_DIMENSION:
            raise ValueError("Vector candidate dimension does not match storage")
        if type(limit) is not int or not 1 <= limit <= MAX_VECTOR_CANDIDATE_LIMIT:
            raise ValueError("Vector candidate limit is not available")
        self._embedding_provider = embedding_provider
        self._embedding_profile = embedding_profile
        self._limit = limit

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        if type(request) is not Acquire:
            raise TypeError("Vector candidate discovery requires Acquire")
        try:
            query_embedding = validate_embedding_batch(
                (request.need.query,),
                self._embedding_provider.embed((request.need.query,)),
                self._embedding_profile,
            )[0]
            return _discover_materialized_vector(
                projection_session,
                query_embedding,
                self._limit,
            )
        except EmbeddingProviderUnavailable:
            raise VectorCandidateIndexUnavailable(
                "Vector candidate discovery is unavailable"
            ) from None
