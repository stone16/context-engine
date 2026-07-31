from __future__ import annotations

from typing import cast

from engine.runtime.authorized_ranking import RankerWeights
from engine.runtime.budget import PackageBudget
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, ContextNeed, Resolved
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedOneHopCandidate
from engine.runtime.scope import ScopeSet, ScopeTarget
from tests.support.context_run import TEST_QUERY_DIGEST_KEYRING
from tests.unit.test_runtime_authorized_evidence import (
    AS_OF,
    AUTHORIZED,
    AUTHORIZED_SECOND,
    DENIED,
    HostileCandidateIndex,
    RecordingMaterializedPort,
    locator,
    trusted_operands,
)

SAME_ARTICLE = CandidateRef(
    organization_id=AUTHORIZED.organization_id,
    source_ref=AUTHORIZED.source_ref,
    resource_ref=AUTHORIZED.resource_ref,
    revision_ref=AUTHORIZED.revision_ref,
    fragment_ref="fragment:same-article-neighbour",
)
SUPERSEDED_SAME_ARTICLE = CandidateRef(
    organization_id=AUTHORIZED.organization_id,
    source_ref=AUTHORIZED.source_ref,
    resource_ref=AUTHORIZED.resource_ref,
    revision_ref="99999999-9999-4999-8999-999999999999",
    fragment_ref="fragment:superseded-neighbour",
)


class OneHopMaterializedPort(RecordingMaterializedPort):
    def __init__(
        self,
        candidates: tuple[CandidateRef, ...] = (AUTHORIZED_SECOND, DENIED),
    ) -> None:
        super().__init__()
        self.candidates = candidates
        self.graph_calls: list[
            tuple[tuple[CandidateRef, ...], int, int]
        ] = []

    def discover_one_hop(
        self,
        anchors: tuple[CandidateRef, ...],
        limit: int,
        offset: int,
    ) -> tuple[MaterializedOneHopCandidate, ...]:
        self.graph_calls.append((anchors, limit, offset))
        return tuple(
            MaterializedOneHopCandidate(
                anchor_ref=anchors[0],
                candidate_ref=candidate,
            )
            for candidate in self.candidates[offset : offset + limit]
        )


def _allow_articles(invocation: object, *candidates: CandidateRef) -> None:
    allowed = ScopeSet(
        frozenset(
            ScopeTarget(
                candidate.organization_id,
                candidate.source_ref,
                candidate.resource_ref,
            )
            for candidate in candidates
        )
    )
    snapshot = invocation.trusted_scope_snapshot  # type: ignore[attr-defined]
    for operand_name in (
        "organization_boundary",
        "membership_rights",
        "principal_grants",
        "agent_ceiling",
        "source_native_acl",
        "resource_acl",
        "purpose_policy",
    ):
        object.__setattr__(snapshot, operand_name, allowed)


def test_one_hop_runs_only_from_authorized_main_results_and_reauthorizes() -> None:
    index = HostileCandidateIndex((AUTHORIZED, DENIED))
    port = OneHopMaterializedPort()
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="synthetic graph query")),
        )

    assert type(outcome) is Resolved
    assert port.graph_calls == [((AUTHORIZED,), 64, 0)]
    assert [block.body for block in outcome.package.blocks] == ["A-safe"]
    assert locator(AUTHORIZED_SECOND) not in port.body_calls
    assert locator(DENIED) not in port.body_calls


def test_refused_neighbours_cannot_consume_the_authorized_expansion_bound() -> None:
    scope_refused = tuple(
        CandidateRef(
            organization_id=AUTHORIZED.organization_id,
            source_ref=AUTHORIZED.source_ref,
            resource_ref=f"resource:denied-{index:03d}",
            revision_ref=f"{index + 10:08x}-0000-4000-8000-000000000000",
            fragment_ref=f"fragment:denied-{index:03d}",
        )
        for index in range(32)
    )
    projection_refused = tuple(
        CandidateRef(
            organization_id=AUTHORIZED.organization_id,
            source_ref=AUTHORIZED.source_ref,
            resource_ref=f"resource:projection-refused-{index:03d}",
            revision_ref=f"{index + 100:08x}-0000-4000-8000-000000000000",
            fragment_ref=f"fragment:projection-refused-{index:03d}",
        )
        for index in range(32)
    )
    index = HostileCandidateIndex((AUTHORIZED,))
    port = OneHopMaterializedPort(
        (*scope_refused, *projection_refused, AUTHORIZED_SECOND)
    )
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        ranker_weights=RankerWeights({"hostile": 1.0, "graph": 2.0}),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        _allow_articles(invocation, AUTHORIZED, *projection_refused, AUTHORIZED_SECOND)
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="Z safe")),
        )

    assert type(outcome) is Resolved
    assert "Z-safe" in repr(outcome)
    assert all(
        locator(candidate) not in port.body_calls for candidate in scope_refused
    )
    assert all(
        locator(candidate) in port.body_calls for candidate in projection_refused
    )
    assert port.graph_calls == [
        ((AUTHORIZED,), 64, 0),
        ((AUTHORIZED,), 64, 64),
    ]


def test_authorized_expansion_competes_and_is_not_auto_included() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = OneHopMaterializedPort((AUTHORIZED_SECOND,))
    port.body_by_candidate[AUTHORIZED_SECOND] = "synthetic graph answer"
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        server_budget=PackageBudget(
            max_tokens=len(b"synthetic graph answer"),
            max_provider_calls=1,
            max_cost_microunits=1,
            max_elapsed_ms=1,
        ),
        ranker_weights=RankerWeights({"hostile": 1.0, "graph": 2.0}),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        _allow_articles(invocation, AUTHORIZED, AUTHORIZED_SECOND)
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="synthetic graph query")),
        )

    assert type(outcome) is Resolved
    assert [block.body for block in outcome.package.blocks] == [
        "synthetic graph answer"
    ]
    assert "A-safe" not in repr(outcome)


def test_weakly_relevant_authorized_expansion_is_not_selected() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = OneHopMaterializedPort((AUTHORIZED_SECOND,))
    port.body_by_candidate[AUTHORIZED_SECOND] = "weak graph signal"
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        _allow_articles(invocation, AUTHORIZED, AUTHORIZED_SECOND)
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="weak unrelated query terms")),
        )

    assert type(outcome) is Resolved
    assert [block.body for block in outcome.package.blocks] == ["A-safe"]
    assert locator(AUTHORIZED_SECOND) in port.body_calls
    assert "weak graph signal" not in repr(outcome)


def test_tokenless_query_cannot_auto_include_an_authorized_expansion() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = OneHopMaterializedPort((AUTHORIZED_SECOND,))
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        _allow_articles(invocation, AUTHORIZED, AUTHORIZED_SECOND)
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="---")),
        )

    assert type(outcome) is Resolved
    assert [block.body for block in outcome.package.blocks] == ["A-safe"]
    assert "Z-safe" not in repr(outcome)


def test_same_article_current_revision_inherits_only_after_lineage_lookup() -> None:
    index = HostileCandidateIndex((AUTHORIZED,))
    port = OneHopMaterializedPort((SAME_ARTICLE, SUPERSEDED_SAME_ARTICLE))
    port.body_by_candidate[SAME_ARTICLE] = "synthetic inherited graph"
    port.body_by_candidate[SUPERSEDED_SAME_ARTICLE] = "STALE-MUST-NOT-BE-READ"
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        ranker_weights=RankerWeights({"hostile": 1.0, "graph": 2.0}),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="synthetic inherited graph")),
        )

    assert type(outcome) is Resolved
    assert locator(SAME_ARTICLE) in port.body_calls
    assert locator(SUPERSEDED_SAME_ARTICLE) not in port.body_calls
    assert "synthetic inherited graph" in repr(outcome)
    assert "STALE-MUST-NOT-BE-READ" not in repr(outcome)


def test_same_article_inheritance_refuses_a_mismatched_materialized_locator() -> None:
    class MismatchedLocatorPort(OneHopMaterializedPort):
        def locate(self, candidate_ref: CandidateRef):  # type: ignore[no-untyped-def]
            if candidate_ref == SAME_ARTICLE:
                self.locator_calls.append(candidate_ref)
                return locator(AUTHORIZED_SECOND)
            return super().locate(candidate_ref)

    index = HostileCandidateIndex((AUTHORIZED,))
    port = MismatchedLocatorPort((SAME_ARTICLE,))
    port.body_by_candidate[SAME_ARTICLE] = "MISMATCHED-MUST-NOT-BE-READ"
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )

    with trusted_operands(port) as (invocation, delivery):
        outcome = runtime.resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="mismatched graph locator")),
        )

    assert type(outcome) is Resolved
    assert port.body_calls == [locator(AUTHORIZED)]
    assert "MISMATCHED-MUST-NOT-BE-READ" not in repr(outcome)
