from __future__ import annotations

from threading import Event
from typing import cast
from uuid import UUID

import pytest

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.pgvector import (
    MAX_VECTOR_CANDIDATE_LIMIT,
    PostgreSQLVectorCandidateIndex,
    VectorCandidateIndexUnavailable,
)
from engine.runtime.budget import BudgetUsage, PackageBudget, PackageBudgetMeter
from engine.runtime.contracts import Acquire, ContextNeed, RequestNarrowing
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedProjectionPort,
    _close_candidate_discovery_session,
    _close_materialized_projection_scope,
    _construct_candidate_discovery_session,
    _construct_materialized_projection_session,
    _open_materialized_projection_scope,
)
from engine.runtime.scope import (
    CandidateDiscoveryScope,
    EffectiveScope,
    ScopeTarget,
)
from engine.supply import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    EmbeddingProfile,
    EmbeddingProviderUnavailable,
)


class _RecordingPort:
    def __init__(self, candidates: tuple[CandidateRef, ...]) -> None:
        self.candidates = candidates
        self.calls: list[
            tuple[
                tuple[float, ...],
                str,
                int,
                tuple[str, ...] | None,
                tuple[str, ...] | None,
                frozenset[ScopeTarget],
            ]
        ] = []

    def discover_vector(
        self,
        query_embedding: tuple[float, ...],
        embedding_profile_digest: str,
        limit: int,
        source_refs: tuple[str, ...] | None,
        resource_refs: tuple[str, ...] | None,
        effective_scope: frozenset[ScopeTarget],
    ) -> tuple[CandidateRef, ...]:
        self.calls.append(
            (
                query_embedding,
                embedding_profile_digest,
                limit,
                source_refs,
                resource_refs,
                effective_scope,
            )
        )
        return self.candidates[:limit]

    def discover_exact_phrase(self, phrase_digest: str) -> tuple[()]:
        del phrase_digest
        return ()

    def source_is_active(self, source_ref: UUID) -> bool:
        del source_ref
        return True

    def observe_publication(self, candidate_ref: CandidateRef) -> None:
        del candidate_ref

    def locate(self, candidate_ref: CandidateRef) -> None:
        del candidate_ref

    def project(self, locator: object) -> None:
        del locator


class _UnavailableProvider:
    profile = EmbeddingProfile(384)
    provider_profile = DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del inputs
        raise EmbeddingProviderUnavailable("provider detail")

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(inputs)


class _RecordingProvider:
    profile = EmbeddingProfile(384)
    provider_profile = DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return DeterministicEmbeddingTwin().embed(inputs)

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(inputs)


class _HangingProvider(_RecordingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = Event()

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del inputs
        self.calls += 1
        self.release.wait()
        return ()


def _candidate() -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:vector",
        resource_ref="resource:vector",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref="fragment:vector",
    )


def _effective_scope() -> EffectiveScope:
    candidate = _candidate()
    return EffectiveScope(
        frozenset(
            {
                ScopeTarget(
                    candidate.organization_id,
                    candidate.source_ref,
                    candidate.resource_ref,
                )
            }
        )
    )


def _discovery_scope() -> CandidateDiscoveryScope:
    return CandidateDiscoveryScope(_effective_scope().digest)


def test_vector_index_embeds_query_and_returns_only_bounded_candidate_refs() -> None:
    port = _RecordingPort((_candidate(),))
    scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, port),
    )
    plan = PostgreSQLVectorCandidateIndex(
        DeterministicEmbeddingTwin(),
        limit=1,
    ).prepare_discovery(
        Acquire(need=ContextNeed(query="semantic query")),
        effective_scope=_discovery_scope(),
    )
    discovery_session = _construct_candidate_discovery_session(
        session,
        plan,
        effective_scope=_effective_scope(),
    )
    try:
        candidate_query = PostgreSQLVectorCandidateIndex(
            DeterministicEmbeddingTwin(),
            limit=1,
        ).discover(
            Acquire(need=ContextNeed(query="semantic query")),
            discovery_session,
            effective_scope=_discovery_scope(),
        )
    finally:
        _close_candidate_discovery_session(discovery_session)
        _close_materialized_projection_scope(scope)

    assert tuple(
        item.candidate_ref
        for item in candidate_query.ranked_lists[0].candidates
    ) == (_candidate(),)
    assert len(port.calls) == 1
    (
        query_embedding,
        embedding_profile_digest,
        limit,
        source_refs,
        resource_refs,
        effective_scope,
    ) = port.calls[0]
    assert len(query_embedding) == 384
    assert embedding_profile_digest == (
        DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
    )
    assert limit == 1
    assert source_refs is None
    assert resource_refs is None
    assert effective_scope == _effective_scope().targets
    assert set(CandidateRef.__dataclass_fields__) == {
        "organization_id",
        "source_ref",
        "resource_ref",
        "revision_ref",
        "fragment_ref",
    }


def test_vector_index_applies_request_narrowing_before_ann_limit() -> None:
    port = _RecordingPort((_candidate(),))
    scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(MaterializedProjectionPort, port),
    )
    request = Acquire(
        need=ContextNeed(query="semantic query"),
        narrowing=RequestNarrowing(
            source_refs=("source:vector",),
            resource_refs=("resource:vector",),
        ),
    )
    index = PostgreSQLVectorCandidateIndex(
        DeterministicEmbeddingTwin(),
        limit=1,
    )
    discovery_session = _construct_candidate_discovery_session(
        session,
        index.prepare_discovery(request, effective_scope=_discovery_scope()),
        effective_scope=_effective_scope(),
    )
    try:
        index.discover(
            request,
            discovery_session,
            effective_scope=_discovery_scope(),
        )
    finally:
        _close_candidate_discovery_session(discovery_session)
        _close_materialized_projection_scope(scope)

    assert port.calls[0][3:5] == (
        ("source:vector",),
        ("resource:vector",),
    )


def test_vector_index_genericizes_query_embedding_failure_before_database_io() -> None:
    port = _RecordingPort((_candidate(),))
    with pytest.raises(
        VectorCandidateIndexUnavailable,
        match="Vector candidate discovery is unavailable",
    ) as failure:
        PostgreSQLVectorCandidateIndex(_UnavailableProvider()).prepare_discovery(
            Acquire(need=ContextNeed(query="semantic query")),
            effective_scope=_discovery_scope(),
        )

    assert failure.value.__cause__ is None
    assert port.calls == []


def test_budgeted_query_embedding_debits_actual_internal_usage() -> None:
    budget = PackageBudgetMeter(PackageBudget(1, 1, 1, 5_000))
    observed_times = iter((25, 32))

    PostgreSQLVectorCandidateIndex(
        DeterministicEmbeddingTwin(), monotonic_ms=lambda: next(observed_times)
    ).prepare_budgeted_discovery(
        Acquire(need=ContextNeed(query="semantic query")),
        effective_scope=_discovery_scope(),
        budget=budget,
        active_embedding_profile_digest=(
            DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
        ),
    )

    assert budget.usage.provider_calls == 1
    assert budget.usage.cost_microunits == 1
    assert budget.usage.elapsed_ms == 7


def test_budgeted_query_embedding_refuses_exhaustion_before_provider_call() -> None:
    provider = _RecordingProvider()
    budget = PackageBudgetMeter(
        PackageBudget(1, 1, 1, 1),
    )

    with pytest.raises(
        VectorCandidateIndexUnavailable,
        match="Vector candidate discovery is unavailable",
    ):
        PostgreSQLVectorCandidateIndex(provider).prepare_budgeted_discovery(
            Acquire(need=ContextNeed(query="semantic query")),
            effective_scope=_discovery_scope(),
            budget=budget,
            active_embedding_profile_digest=(
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
            ),
        )

    assert provider.calls == 0
    assert budget.usage.provider_calls == 0


def test_failed_query_embedding_charges_reserved_maximum_after_provider_call() -> None:
    budget = PackageBudgetMeter(PackageBudget(1, 1, 1, 5_000))

    with pytest.raises(VectorCandidateIndexUnavailable):
        PostgreSQLVectorCandidateIndex(_UnavailableProvider()).prepare_budgeted_discovery(
            Acquire(need=ContextNeed(query="semantic query")),
            effective_scope=_discovery_scope(),
            budget=budget,
            active_embedding_profile_digest=(
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
            ),
        )

    assert budget.usage.provider_calls == 1
    assert budget.usage.cost_microunits == 1
    assert budget.usage.elapsed_ms == 5_000


def test_hanging_query_embedding_returns_by_deadline_and_charges_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _HangingProvider()
    budget = PackageBudgetMeter(PackageBudget(1, 1, 1, 5_000))
    monkeypatch.setattr(
        "adapters.pgvector.QUERY_EMBEDDING_MAXIMUM_USAGE",
        BudgetUsage(tokens=0, provider_calls=1, cost_microunits=1, elapsed_ms=10),
    )

    try:
        with pytest.raises(VectorCandidateIndexUnavailable):
            PostgreSQLVectorCandidateIndex(provider).prepare_budgeted_discovery(
                Acquire(need=ContextNeed(query="semantic query")),
                effective_scope=_discovery_scope(),
                budget=budget,
                active_embedding_profile_digest=(
                    DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
                ),
            )
    finally:
        provider.release.set()

    assert provider.calls == 1
    assert budget.usage == BudgetUsage(0, 1, 1, 10)


def test_query_provider_profile_must_equal_active_release_before_call() -> None:
    provider = _UnavailableProvider()
    budget = PackageBudgetMeter(PackageBudget(1, 1, 1, 5_000))

    with pytest.raises(VectorCandidateIndexUnavailable):
        PostgreSQLVectorCandidateIndex(provider).prepare_budgeted_discovery(
            Acquire(need=ContextNeed(query="semantic query")),
            effective_scope=_discovery_scope(),
            budget=budget,
            active_embedding_profile_digest="f" * 64,
        )

    assert budget.usage.provider_calls == 0


@pytest.mark.parametrize("limit", [0, MAX_VECTOR_CANDIDATE_LIMIT + 1, True])
def test_vector_index_refuses_unbounded_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="limit is not available"):
        PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin(), limit=limit)
