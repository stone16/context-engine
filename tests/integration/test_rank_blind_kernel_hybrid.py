from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.http.app import create_app
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.authorized_ranking import HYBRID_RANKER_WEIGHTS
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    ExactPhraseDiscoveryRequest,
)
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.scope import CandidateDiscoveryScope
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    ExactScopeAuthority,
    OrganizationEvidenceFixture,
    RuntimeEvidenceFixture,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)
from tests.support.releases import ensure_test_runtime_release
from tests.unit import test_kernel_rank_blind as rank_blind_oracles

pytestmark = pytest.mark.integration


def test_hybrid_kernel_decision_is_byte_identical_across_rankings() -> None:
    rank_blind_oracles.test_permuting_rank_evidence_cannot_change_kernel_decision()


class _PermutedHybridEvidenceIndex:
    """Submit the same exact refs with two rank-evidence permutations."""

    def __init__(
        self,
        active: OrganizationEvidenceFixture,
        foreign: CandidateRef,
        *,
        permutation: int,
    ) -> None:
        self._active = active
        self._foreign = foreign
        self._permutation = permutation

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> ExactPhraseDiscoveryRequest:
        return PostgreSQLExactPhraseCandidateIndex().prepare_discovery(
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
        del request, discovery_session, effective_scope
        denied = self._active.denied
        allowed = self._active.authorized
        if self._permutation == 0:
            lexical = (denied, allowed, self._foreign)
            vector = (self._foreign, denied, allowed)
        else:
            lexical = (self._foreign, denied, allowed)
            vector = (denied, allowed, self._foreign)
        return CandidateQuery(
            ranked_lists=(
                RankedCandidateList(
                    ranker_ref="fts",
                    candidates=tuple(RankedCandidate(item) for item in lexical),
                ),
                RankedCandidateList(
                    ranker_ref="vector",
                    candidates=tuple(RankedCandidate(item) for item in vector),
                ),
            )
        )


@contextmanager
def _fixture(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[RuntimeEvidenceFixture, Engine]]:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        yield fixture, migration_engine
    finally:
        _cleanup_fixture(migration_engine, fixture)
        migration_engine.dispose()


def _resolve(
    fixture: RuntimeEvidenceFixture,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    *,
    permutation: int,
) -> dict[str, object]:
    active = fixture.org_a
    token = f"hybrid-rank-blind-token:{permutation}"
    request_id = f"request:hybrid-rank-blind:{permutation}"
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(
            CandidateIndex,
            _PermutedHybridEvidenceIndex(
                active,
                fixture.org_b.authorized,
                permutation=permutation,
            ),
        ),
        candidate_submission_limit=128,
        ranker_weights=HYBRID_RANKER_WEIGHTS,
        clock=lambda: RECEIVED_AT,
        query_digest_keyring=query_digest_keyring,
    )
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(active, token=token),
            organization_authority=SeededOrganizationAuthority(active.organization_id),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=ExactScopeAuthority(active.authorized),
            runtime=runtime,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: request_id,
        )
    )
    response = client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Context-Request-Id": request_id,
        },
        json={"kind": "acquire", "need": {"query": "hybrid rank blind"}},
    )
    assert response.status_code == 200
    return cast(dict[str, object], response.json()["package"])


def _tenant_visible(package: dict[str, object]) -> dict[str, object]:
    blocks = cast(list[dict[str, object]], package["blocks"])
    evidence = cast(list[dict[str, object]], package["evidence"])
    return {
        "blocks": [{"text": block["text"]} for block in blocks],
        "evidence": [
            {
                key: item[key]
                for key in (
                    "sourceRef",
                    "resourceRef",
                    "revisionRef",
                    "fragmentRef",
                    "authorizationAsOf",
                )
            }
            for item in evidence
        ],
        "gaps": package["gaps"],
        "coverage": package["coverage"],
        "budgetUsage": package["budgetUsage"],
    }


def test_hybrid_rank_permutation_and_refusals_are_tenant_invisible(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    with _fixture(migration_configuration) as (fixture, _migration_engine):
        first = _resolve(
            fixture,
            guarded_runtime_engine,
            query_digest_keyring,
            permutation=0,
        )
        second = _resolve(
            fixture,
            guarded_runtime_engine,
            query_digest_keyring,
            permutation=1,
        )

        assert _tenant_visible(first) == _tenant_visible(second)
        assert [
            item["text"] for item in cast(list[dict[str, object]], first["blocks"])
        ] == [fixture.org_a.authorized_body]
        visible = repr(_tenant_visible(first)) + repr(_tenant_visible(second))
        for refused in (fixture.org_a.denied, fixture.org_b.authorized):
            assert refused.source_ref not in visible
            assert refused.resource_ref not in visible
            assert refused.revision_ref not in visible
            assert refused.fragment_ref not in visible
