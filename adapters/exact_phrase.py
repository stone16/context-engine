"""PostgreSQL-backed content-free exact-phrase candidate discovery."""

from __future__ import annotations

from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
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
    ) -> tuple[CandidateRef, ...]:
        if type(request) is not Acquire:
            raise TypeError("exact phrase discovery requires Acquire")
        if type(effective_scope) is not EffectiveScope:
            raise TypeError("exact phrase discovery requires EffectiveScope")
        return _discover_materialized_exact_phrase(
            projection_session,
            exact_phrase_digest(request.need.query),
        )
