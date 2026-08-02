"""PostgreSQL-backed content-free exact-phrase candidate discovery."""

from __future__ import annotations

from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.contracts import Acquire
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    ExactPhraseDiscoveryRequest,
    _candidate_discovery_ranker_candidates,
)
from engine.runtime.scope import CandidateDiscoveryScope


class PostgreSQLExactPhraseCandidateIndex:
    """Discover content-free candidates within one trusted Organization."""

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> ExactPhraseDiscoveryRequest:
        if type(request) is not Acquire:
            raise TypeError("exact phrase discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError("exact phrase discovery requires CandidateDiscoveryScope")
        return ExactPhraseDiscoveryRequest(exact_phrase_digest(request.need.query))

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> ExactPhraseDiscoveryRequest:
        if type(budget) is not PackageBudgetMeter:
            raise TypeError("exact phrase discovery requires PackageBudgetMeter")
        if (
            type(active_embedding_profile_digest) is not str
            or not active_embedding_profile_digest
        ):
            raise TypeError("exact phrase discovery requires an active profile")
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
            raise TypeError("exact phrase discovery requires Acquire")
        if type(effective_scope) is not CandidateDiscoveryScope:
            raise TypeError("exact phrase discovery requires CandidateDiscoveryScope")
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="lexical",
                    candidates=tuple(
                        RankedCandidate(candidate_ref=candidate)
                        for candidate in _candidate_discovery_ranker_candidates(
                            discovery_session,
                            "exact_phrase",
                        )
                    ),
                ),
            )
        )
