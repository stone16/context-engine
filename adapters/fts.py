"""Native PostgreSQL full-text candidate discovery."""

from __future__ import annotations

from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.contracts import Acquire
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    FtsDiscoveryRequest,
    _candidate_discovery_ranker_candidates,
)
from engine.runtime.scope import CandidateDiscoveryScope

DEFAULT_FTS_CANDIDATE_LIMIT = 64
MAX_FTS_CANDIDATE_LIMIT = 64
POSTGRES_TEXT_SEARCH_CONFIGURATION = "simple"


class PostgreSQLFtsCandidateIndex:
    """Return bounded lexical CandidateRefs on the retained transaction."""

    __slots__ = ("_limit",)

    def __init__(self, *, limit: int = DEFAULT_FTS_CANDIDATE_LIMIT) -> None:
        if type(limit) is not int or not 1 <= limit <= MAX_FTS_CANDIDATE_LIMIT:
            raise ValueError("FTS candidate limit is not available")
        self._limit = limit

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> FtsDiscoveryRequest:
        if type(request) is not Acquire:
            raise TypeError("FTS candidate discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError("FTS candidate discovery requires CandidateDiscoveryScope")
        return FtsDiscoveryRequest(
            query_text=request.need.query,
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

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> FtsDiscoveryRequest:
        if type(budget) is not PackageBudgetMeter:
            raise TypeError("FTS candidate discovery requires PackageBudgetMeter")
        if (
            type(active_embedding_profile_digest) is not str
            or not active_embedding_profile_digest
        ):
            raise TypeError("FTS candidate discovery requires an active profile")
        return self.prepare_discovery(
            request,
            effective_scope=effective_scope,
        )

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        if type(request) is not Acquire:
            raise TypeError("FTS candidate discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError("FTS candidate discovery requires CandidateDiscoveryScope")
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="fts",
                    candidates=tuple(
                        RankedCandidate(candidate_ref=candidate)
                        for candidate in _candidate_discovery_ranker_candidates(
                            discovery_session,
                            "fts",
                        )
                    ),
                ),
            )
        )
