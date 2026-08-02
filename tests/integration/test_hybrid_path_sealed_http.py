from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

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
        active = fixture.org_a
        first_body = f"{active.authorized_body} {active.authorized_body}"
        second_body = active.authorized_body
        second_admitted = CandidateRef(
            organization_id=active.authorized.organization_id,
            source_ref=active.authorized.source_ref,
            resource_ref=active.authorized.resource_ref,
            revision_ref=active.authorized.revision_ref,
            fragment_ref=f"fragment:000-ranked-second:{uuid4()}",
        )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id,
                        resource_ref,
                        revision_id,
                        fragment_ref,
                        ordinal,
                        content
                    ) VALUES (
                        :organization_id,
                        :resource_ref,
                        :revision_id,
                        :fragment_ref,
                        1,
                        :content
                    )
                    """
                ),
                {
                    "organization_id": second_admitted.organization_id,
                    "resource_ref": second_admitted.resource_ref,
                    "revision_id": UUID(second_admitted.revision_ref),
                    "fragment_ref": second_admitted.fragment_ref,
                    "content": second_body,
                },
            )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment DISABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
        try:
            with migration_engine.begin() as connection:
                provider = DeterministicEmbeddingTwin()
                for fragment_ref, body in (
                    (active.authorized.fragment_ref, first_body),
                    (second_admitted.fragment_ref, second_body),
                ):
                    embedding = provider.embed_documents((body,))[0]
                    connection.execute(
                        text(
                            "UPDATE context_fragment "
                                "SET content = :content, "
                                "embedding = CAST(:embedding AS vector), "
                                "embedding_profile_digest = "
                                ":embedding_profile_digest "
                            "WHERE organization_id = :organization_id "
                            "AND resource_ref = :resource_ref "
                            "AND fragment_ref = :fragment_ref"
                        ),
                        {
                            "embedding": "["
                            + ",".join(repr(item) for item in embedding)
                            + "]",
                            "content": body,
                            "embedding_profile_digest": (
                                provider.provider_profile.profile_digest
                            ),
                            "organization_id": active.organization_id,
                            "resource_ref": active.authorized.resource_ref,
                            "fragment_ref": fragment_ref,
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
                "need": {"query": first_body},
            },
        )

        assert response.status_code == 200
        assert [item["text"] for item in response.json()["package"]["blocks"]] == [
            first_body,
            second_body,
        ]
        assert tuple(item.projection.candidate_ref for item in consumed) == (
            second_admitted,
            active.authorized,
        )
        assert all(
            tuple(
                ranker.ranker_ref
                for ranker in item.rank_evidence.per_ranker  # type: ignore[union-attr]
            )
            == ("fts", "vector")
            for item in consumed
        )
        record_property("hybrid_candidate_kernel_projection", "PASS")
    finally:
        _cleanup_fixture(migration_engine, fixture)
        migration_engine.dispose()
