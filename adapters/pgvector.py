"""PostgreSQL ANN candidate discovery using one retained Runtime transaction."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread
from time import monotonic_ns

from engine.runtime.budget import (
    BudgetUsage,
    PackageBudgetExceeded,
    PackageBudgetMeter,
)
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.content_io import CandidateIndexUnavailable
from engine.runtime.contracts import Acquire
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    VectorDiscoveryRequest,
    _candidate_discovery_ranker_candidates,
)
from engine.runtime.scope import CandidateDiscoveryScope
from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingProviderProfile,
    EmbeddingProviderUnavailable,
    validate_embedding_batch,
)

DEFAULT_VECTOR_CANDIDATE_LIMIT = 16
MAX_VECTOR_CANDIDATE_LIMIT = 64
QUERY_EMBEDDING_MAXIMUM_USAGE = BudgetUsage(
    tokens=0,
    provider_calls=1,
    cost_microunits=1,
    elapsed_ms=5_000,
)


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


class VectorCandidateIndexUnavailable(CandidateIndexUnavailable):
    """Content-free failure of query embedding or ANN candidate discovery."""


class PostgreSQLVectorCandidateIndex:
    """Return bounded content-free pgvector candidates for one Acquire."""

    __slots__ = (
        "_embedding_profile",
        "_embedding_provider",
        "_provider_profile",
        "_limit",
        "_monotonic_ms",
    )

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        limit: int = DEFAULT_VECTOR_CANDIDATE_LIMIT,
        monotonic_ms: Callable[[], int] = _monotonic_ms,
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
        try:
            provider_profile = embedding_provider.provider_profile
        except (AttributeError, EmbeddingProviderUnavailable, TypeError, ValueError):
            raise TypeError(
                "Vector candidate index requires an embedding provider profile"
            ) from None
        if (
            type(provider_profile) is not EmbeddingProviderProfile
            or provider_profile.dimension != embedding_profile.dimension
        ):
            raise TypeError(
                "Vector candidate index requires an embedding provider profile"
            )
        if type(limit) is not int or not 1 <= limit <= MAX_VECTOR_CANDIDATE_LIMIT:
            raise ValueError("Vector candidate limit is not available")
        if not callable(monotonic_ms):
            raise TypeError("Vector candidate monotonic clock is not available")
        self._embedding_provider = embedding_provider
        self._embedding_profile = embedding_profile
        self._provider_profile = provider_profile
        self._limit = limit
        self._monotonic_ms = monotonic_ms

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> VectorDiscoveryRequest:
        if type(request) is not Acquire:
            raise TypeError("Vector candidate discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError(
                "Vector candidate discovery requires CandidateDiscoveryScope"
            )
        try:
            query_embedding = validate_embedding_batch(
                (request.need.query,),
                self._embedding_provider.embed((request.need.query,)),
                self._embedding_profile,
            )[0]
        except EmbeddingProviderUnavailable:
            raise VectorCandidateIndexUnavailable(
                "Vector candidate discovery is unavailable"
            ) from None
        return VectorDiscoveryRequest(
            query_embedding=query_embedding,
            embedding_profile_digest=self._provider_profile.profile_digest,
            limit=self._limit,
            source_refs=(
                request.narrowing.source_refs if request.narrowing is not None else None
            ),
            resource_refs=(
                request.narrowing.resource_refs
                if request.narrowing is not None
                else None
            ),
        )

    def _embed_query_bounded(self, query: str) -> tuple[tuple[float, ...], ...]:
        """Return by the local query deadline even if backend inference hangs."""

        finished = Event()
        outputs: list[tuple[tuple[float, ...], ...]] = []
        failed: list[bool] = []

        def invoke() -> None:
            try:
                vectors = self._embedding_provider.embed((query,))
                if type(vectors) is tuple:
                    outputs.append(vectors)
                else:
                    failed.append(True)
            except Exception:
                failed.append(True)
            finally:
                finished.set()

        Thread(
            target=invoke,
            name="context-engine-query-embedding",
            daemon=True,
        ).start()
        if not finished.wait(QUERY_EMBEDDING_MAXIMUM_USAGE.elapsed_ms / 1_000):
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        if failed or len(outputs) != 1:
            raise EmbeddingProviderUnavailable("Embedding provider is unavailable")
        return outputs[0]

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> VectorDiscoveryRequest:
        if type(request) is not Acquire:
            raise TypeError("Vector candidate discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError(
                "Vector candidate discovery requires CandidateDiscoveryScope"
            )
        if type(budget) is not PackageBudgetMeter:
            raise TypeError("Vector candidate discovery requires PackageBudgetMeter")
        if active_embedding_profile_digest != self._provider_profile.profile_digest:
            raise VectorCandidateIndexUnavailable(
                "Vector candidate discovery is unavailable"
            )
        try:
            reservation = budget._reserve(QUERY_EMBEDDING_MAXIMUM_USAGE)
        except PackageBudgetExceeded:
            raise VectorCandidateIndexUnavailable(
                "Vector candidate discovery is unavailable"
            ) from None
        try:
            started_ms = self._monotonic_ms()
            query_embedding = validate_embedding_batch(
                (request.need.query,),
                self._embed_query_bounded(request.need.query),
                self._embedding_profile,
            )[0]
            request_plan = VectorDiscoveryRequest(
                query_embedding=query_embedding,
                embedding_profile_digest=self._provider_profile.profile_digest,
                limit=self._limit,
                source_refs=(
                    request.narrowing.source_refs
                    if request.narrowing is not None
                    else None
                ),
                resource_refs=(
                    request.narrowing.resource_refs
                    if request.narrowing is not None
                    else None
                ),
            )
            elapsed_ms = self._monotonic_ms() - started_ms
            if not 0 <= elapsed_ms <= QUERY_EMBEDDING_MAXIMUM_USAGE.elapsed_ms:
                raise VectorCandidateIndexUnavailable(
                    "Vector candidate discovery is unavailable"
                )
        except (EmbeddingProviderUnavailable, VectorCandidateIndexUnavailable):
            budget._commit(reservation, QUERY_EMBEDDING_MAXIMUM_USAGE)
            raise VectorCandidateIndexUnavailable(
                "Vector candidate discovery is unavailable"
            ) from None
        budget._commit(
            reservation,
            BudgetUsage(
                tokens=0,
                provider_calls=1,
                cost_microunits=1,
                elapsed_ms=elapsed_ms,
            ),
        )
        return request_plan

    @property
    def embedding_profile_digest(self) -> str:
        return self._provider_profile.profile_digest

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        if type(request) is not Acquire:
            raise TypeError("Vector candidate discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError(
                "Vector candidate discovery requires CandidateDiscoveryScope"
            )
        candidates = _candidate_discovery_ranker_candidates(
            discovery_session,
            "vector",
        )
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="vector",
                    candidates=tuple(
                        RankedCandidate(candidate_ref=candidate)
                        for candidate in candidates
                    ),
                ),
            )
        )
