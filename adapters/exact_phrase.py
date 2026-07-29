"""PostgreSQL-backed content-free exact-phrase candidate discovery."""

from __future__ import annotations

from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.contracts import Acquire
from engine.runtime.materialized import (
    MaterializedProjectionSession,
    _discover_materialized_exact_phrase,
)
from engine.runtime.scope import EffectiveScope


class PostgreSQLExactPhraseCandidateIndex:
    """Discover content-free candidates within one trusted Organization."""

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
        *,
        effective_scope: EffectiveScope,
    ) -> CandidateQuery:
        if type(request) is not Acquire:
            raise TypeError("exact phrase discovery requires Acquire")
        if type(effective_scope) is not EffectiveScope:
            raise TypeError("exact phrase discovery requires EffectiveScope")
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="lexical",
                    candidates=tuple(
                        RankedCandidate(candidate_ref=candidate)
                        for candidate in _discover_materialized_exact_phrase(
                            projection_session,
                            exact_phrase_digest(request.need.query),
                        )
                    ),
                ),
            )
        )
