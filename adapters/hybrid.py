"""PostgreSQL FTS + existing pgvector rankers behind the candidate port."""

from __future__ import annotations

from adapters.fts import PostgreSQLFtsCandidateIndex
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.candidate_ranking import CandidateQuery
from engine.runtime.contracts import Acquire
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    HybridDiscoveryRequest,
)
from engine.runtime.scope import CandidateDiscoveryScope
from engine.supply import EmbeddingProvider


class PostgreSQLHybridCandidateIndex:
    """Compose both native PostgreSQL rankers without duplicating either."""

    __slots__ = ("_fts", "_vector")

    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self._fts = PostgreSQLFtsCandidateIndex()
        self._vector = PostgreSQLVectorCandidateIndex(embedding_provider)

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> HybridDiscoveryRequest:
        return HybridDiscoveryRequest(
            fts=self._fts.prepare_discovery(
                request,
                effective_scope=effective_scope,
            ),
            vector=self._vector.prepare_discovery(
                request,
                effective_scope=effective_scope,
            ),
        )

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> HybridDiscoveryRequest:
        return HybridDiscoveryRequest(
            fts=self._fts.prepare_discovery(
                request,
                effective_scope=effective_scope,
            ),
            vector=self._vector.prepare_budgeted_discovery(
                request,
                effective_scope=effective_scope,
                budget=budget,
                active_embedding_profile_digest=active_embedding_profile_digest,
            ),
        )

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        lexical = self._fts.discover(
            request,
            discovery_session,
            effective_scope=effective_scope,
        )
        vector = self._vector.discover(
            request,
            discovery_session,
            effective_scope=effective_scope,
        )
        return CandidateQuery(
            ranked_lists=lexical.ranked_lists + vector.ranked_lists,
        )
