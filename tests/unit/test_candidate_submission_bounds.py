from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest

from engine.runtime.candidate_ranking import (
    DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
    MAX_CANDIDATE_SUBMISSION_CEILING,
    MAX_SUBMITTED_RANKED_LISTS,
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
    require_bounded_candidate_submission,
)
from engine.runtime.construction import (
    Runtime,
    RuntimeConfigurationError,
    required_kernel_dependencies,
)
from engine.runtime.content_io import CandidateIndex, exact_phrase_digest
from engine.runtime.contracts import Acquire, ContextNeed, Resolved
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    CandidateDiscoveryRequest,
    CandidateDiscoverySession,
    ExactPhraseDiscoveryRequest,
    VectorDiscoveryRequest,
    _construct_candidate_discovery_session,
    _construct_materialized_projection_session,
    _discover_materialized_candidates,
    _open_materialized_projection_scope,
)
from engine.runtime.scope import EffectiveScope
from tests.support.context_run import TEST_QUERY_DIGEST_KEYRING
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    RecordingMaterializedPort,
    trusted_operands,
)

REPRODUCED_HOSTILE_SUBMISSION = 5001


def _fabricated(ordinal: int) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:fabricated",
        resource_ref=f"resource:fabricated-{ordinal}",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref=f"fragment:fabricated-{ordinal}",
    )


class _SubmittingIndex:
    """Submit an exact caller-chosen ranked shape at the replaceable seam."""

    def __init__(
        self,
        ranked_lists: tuple[RankedCandidateList, ...],
        *,
        prepared_limit: int | None = None,
        forge_query: bool = False,
    ) -> None:
        self.ranked_lists = ranked_lists
        self.prepared_limit = prepared_limit
        self.forge_query = forge_query

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: object,
    ) -> CandidateDiscoveryRequest:
        del effective_scope
        if self.prepared_limit is None:
            return ExactPhraseDiscoveryRequest(
                exact_phrase_digest(request.need.query)
            )
        return VectorDiscoveryRequest(
            query_embedding=(1.0,),
            embedding_profile_digest="a" * 64,
            limit=self.prepared_limit,
        )

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: object,
        budget: object,
        active_embedding_profile_digest: str,
    ) -> CandidateDiscoveryRequest:
        del budget, active_embedding_profile_digest
        return self.prepare_discovery(request, effective_scope=effective_scope)

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: object,
    ) -> CandidateQuery:
        del request, discovery_session, effective_scope
        if not self.forge_query:
            return CandidateQuery(ranked_lists=self.ranked_lists)
        forged = object.__new__(CandidateQuery)
        object.__setattr__(forged, "ranked_lists", self.ranked_lists)
        return forged


def _single_list(*candidates: CandidateRef) -> tuple[RankedCandidateList, ...]:
    return (
        RankedCandidateList(
            ranker_ref="hostile",
            candidates=tuple(
                RankedCandidate(candidate_ref=candidate) for candidate in candidates
            ),
        ),
    )


def _resolve_with(
    index: object,
    port: RecordingMaterializedPort,
    *,
    candidate_submission_limit: int = DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
) -> Resolved:
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        candidate_submission_limit=candidate_submission_limit,
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )
    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="bounded candidate submission")),
        )
    assert type(outcome) is Resolved
    return outcome


def test_unbounded_submission_is_refused_before_any_locator_round_trip() -> None:
    """The reviewer's 5001 fabricated refs must cost zero database round trips."""

    index = _SubmittingIndex(
        _single_list(
            *(_fabricated(ordinal) for ordinal in range(REPRODUCED_HOSTILE_SUBMISSION))
        )
    )
    port = RecordingMaterializedPort()

    with pytest.raises(ValueError) as refusal:
        _resolve_with(index, port)

    assert port.locator_calls == []
    assert port.body_calls == []
    message = str(refusal.value)
    assert "fabricated" not in message
    assert str(REPRODUCED_HOSTILE_SUBMISSION) not in message


def test_forged_candidate_query_cannot_bypass_the_submission_bound() -> None:
    """A hostile index skipping __post_init__ is still bounded by Runtime."""

    index = _SubmittingIndex(
        _single_list(
            *(_fabricated(ordinal) for ordinal in range(REPRODUCED_HOSTILE_SUBMISSION))
        ),
        forge_query=True,
    )
    port = RecordingMaterializedPort()

    with pytest.raises(ValueError, match="server candidate bound"):
        _resolve_with(index, port)

    assert port.locator_calls == []


def test_submission_exactly_at_the_configured_bound_still_resolves() -> None:
    index = _SubmittingIndex(
        _single_list(AUTHORIZED, *(_fabricated(ordinal) for ordinal in range(7)))
    )
    port = RecordingMaterializedPort()

    outcome = _resolve_with(index, port, candidate_submission_limit=8)

    assert len(port.locator_calls) == 8
    assert tuple(block.body for block in outcome.package.blocks) == ("A-safe",)


def test_one_candidate_over_the_configured_bound_is_refused() -> None:
    index = _SubmittingIndex(
        _single_list(AUTHORIZED, *(_fabricated(ordinal) for ordinal in range(8)))
    )
    port = RecordingMaterializedPort()

    with pytest.raises(ValueError, match="server candidate bound"):
        _resolve_with(index, port, candidate_submission_limit=8)

    assert port.locator_calls == []


def test_ranker_count_is_bounded_independently_of_candidate_count() -> None:
    index = _SubmittingIndex(
        tuple(
            RankedCandidateList(
                ranker_ref=f"ranker-{ordinal}",
                candidates=(RankedCandidate(candidate_ref=_fabricated(ordinal)),),
            )
            for ordinal in range(MAX_SUBMITTED_RANKED_LISTS + 1)
        )
    )
    port = RecordingMaterializedPort()

    with pytest.raises(ValueError, match="server ranker bound"):
        _resolve_with(index, port)

    assert port.locator_calls == []


def test_prepared_discovery_above_the_configured_bound_never_reaches_the_database(
) -> None:
    index = _SubmittingIndex(
        _single_list(AUTHORIZED),
        prepared_limit=DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
    )
    port = RecordingMaterializedPort()

    with pytest.raises(ValueError, match="prepared candidate discovery"):
        _resolve_with(index, port, candidate_submission_limit=8)

    assert port.locator_calls == []


def test_discovery_request_limits_are_bounded_by_the_server_ceiling() -> None:
    for limit in (0, -1, MAX_CANDIDATE_SUBMISSION_CEILING + 1):
        with pytest.raises(ValueError, match="within the server"):
            VectorDiscoveryRequest(
                query_embedding=(1.0,),
                embedding_profile_digest="a" * 64,
                limit=limit,
            )
    assert (
        VectorDiscoveryRequest(
            query_embedding=(1.0,),
            embedding_profile_digest="a" * 64,
            limit=MAX_CANDIDATE_SUBMISSION_CEILING,
        ).limit
        == MAX_CANDIDATE_SUBMISSION_CEILING
    )


def test_candidate_submission_limit_is_validated_at_configuration_time() -> None:
    for limit in (0, -1, MAX_CANDIDATE_SUBMISSION_CEILING + 1, True):
        with pytest.raises(RuntimeConfigurationError, match="submission limit"):
            Runtime(
                required_kernel_dependencies(),
                candidate_index=cast(
                    CandidateIndex,
                    _SubmittingIndex(_single_list(AUTHORIZED)),
                ),
                candidate_submission_limit=limit,
                query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
            )


def test_trusted_vector_discovery_is_bounded_by_its_own_request() -> None:
    class _OverreturningPort(RecordingMaterializedPort):
        def discover_vector(  # type: ignore[override]
            self,
            query_embedding: tuple[float, ...],
            embedding_profile_digest: str,
            limit: int,
            source_refs: tuple[str, ...] | None,
            resource_refs: tuple[str, ...] | None,
            effective_scope: object,
        ) -> tuple[CandidateRef, ...]:
            del (
                query_embedding,
                embedding_profile_digest,
                source_refs,
                resource_refs,
                effective_scope,
            )
            return tuple(_fabricated(ordinal) for ordinal in range(limit + 1))

    scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(Any, _OverreturningPort()),
    )

    with pytest.raises(TypeError, match="bounded candidates"):
        _construct_candidate_discovery_session(
            session,
            VectorDiscoveryRequest(
                query_embedding=(1.0,),
                embedding_profile_digest="a" * 64,
                limit=4,
            ),
            effective_scope=EffectiveScope(frozenset()),
        )


def test_trusted_exact_phrase_discovery_stays_complete() -> None:
    """#138 forbids hiding an exact match; the seam bound is not a truncation."""

    discovered = tuple(_fabricated(ordinal) for ordinal in range(65))

    class _CompletePort(RecordingMaterializedPort):
        def discover_exact_phrase(  # type: ignore[override]
            self,
            phrase_digest: str,
        ) -> tuple[CandidateRef, ...]:
            del phrase_digest
            return discovered

    scope = _open_materialized_projection_scope()
    session = _construct_materialized_projection_session(
        authority_scope=scope,
        port=cast(Any, _CompletePort()),
    )

    discovery_session = _construct_candidate_discovery_session(
        session,
        ExactPhraseDiscoveryRequest("digest"),
        effective_scope=EffectiveScope(frozenset()),
    )
    assert _discover_materialized_candidates(discovery_session) == discovered


def test_submission_bound_helper_requires_a_server_owned_limit() -> None:
    query = CandidateQuery(ranked_lists=_single_list(AUTHORIZED))

    for limit in (0, -1, MAX_CANDIDATE_SUBMISSION_CEILING + 1):
        with pytest.raises(ValueError, match="candidate submission limit"):
            require_bounded_candidate_submission(query, submission_limit=limit)
    assert (
        require_bounded_candidate_submission(
            query,
            submission_limit=DEFAULT_CANDIDATE_SUBMISSION_LIMIT,
        )
        is query
    )
