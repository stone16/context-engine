from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.http.app import create_app
from adapters.hybrid import PostgreSQLHybridCandidateIndex
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.authorized_ranking import (
    HYBRID_RANKER_WEIGHTS,
    AuthorizedRerankItem,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.evidence import AuthorizedProjection, CandidateRef
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    ExactScopeAuthority,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)
from tests.support.releases import ensure_test_runtime_release

pytestmark = pytest.mark.integration


def test_hybrid_http_keeps_raw_candidates_before_the_kernel(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    consumed: list[AuthorizedRerankItem] = []
    original_init = AuthorizedRerankItem.__init__

    def observe(
        self: AuthorizedRerankItem,
        projection: AuthorizedProjection,
        rank_evidence: object = None,
    ) -> None:
        assert type(projection) is AuthorizedProjection
        assert not isinstance(projection, CandidateRef)
        original_init(self, projection, rank_evidence)  # type: ignore[arg-type]
        consumed.append(self)

    monkeypatch.setattr(AuthorizedRerankItem, "__init__", observe)
    try:
        _seed_fixture(migration_engine, fixture)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment DISABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
        try:
            with migration_engine.begin() as connection:
                embedding = DeterministicEmbeddingTwin().embed(
                    (fixture.org_a.authorized_body,)
                )[0]
                connection.execute(
                    text(
                        "UPDATE context_fragment "
                        "SET embedding = CAST(:embedding AS vector) "
                        "WHERE organization_id = :organization_id "
                        "AND resource_ref = :resource_ref"
                    ),
                    {
                        "embedding": "["
                        + ",".join(repr(item) for item in embedding)
                        + "]",
                        "organization_id": fixture.org_a.organization_id,
                        "resource_ref": fixture.org_a.authorized.resource_ref,
                    },
                )
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment ENABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
        ensure_test_runtime_release(fixture.org_a.organization_id)
        runtime = Runtime(
            required_kernel_dependencies(),
            candidate_index=PostgreSQLHybridCandidateIndex(
                DeterministicEmbeddingTwin()
            ),
            candidate_submission_limit=128,
            ranker_weights=HYBRID_RANKER_WEIGHTS,
            clock=lambda: RECEIVED_AT,
            query_digest_keyring=query_digest_keyring,
        )
        active = fixture.org_a
        client = TestClient(
            create_app(
                authenticator=SeededAuthenticator(active, token="hybrid-token"),
                organization_authority=SeededOrganizationAuthority(
                    active.organization_id
                ),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                scope_authority=ExactScopeAuthority(active.authorized),
                runtime=runtime,
                clock=lambda: RECEIVED_AT,
                request_id_factory=lambda: "request:hybrid-http",
            )
        )
        response = client.post(
            "/v0/resolve",
            headers={
                "Authorization": "Bearer hybrid-token",
                "X-Context-Request-Id": "request:hybrid-http",
            },
            json={
                "kind": "acquire",
                "need": {"query": active.authorized_body},
            },
        )

        assert response.status_code == 200
        assert [item["text"] for item in response.json()["package"]["blocks"]] == [
            active.authorized_body
        ]
        assert len(consumed) == 1
        assert consumed[0].projection.candidate_ref == active.authorized
        assert tuple(
            item.ranker_ref
            for item in consumed[0].rank_evidence.per_ranker  # type: ignore[union-attr]
        ) == ("fts", "vector")
        record_property("hybrid_candidate_kernel_projection", "PASS")
    finally:
        _cleanup_fixture(migration_engine, fixture)
        migration_engine.dispose()
