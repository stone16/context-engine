from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from adapters.embeddings import DeterministicEmbeddingTwin
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.materialized import (
    FtsDiscoveryRequest,
    HybridDiscoveryRequest,
    VectorDiscoveryRequest,
    _candidate_discovery_ranker_candidates,
    _construct_candidate_discovery_session,
)
from engine.runtime.scope import EffectiveScope, ScopeTarget
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    RuntimeEvidenceFixture,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)

pytestmark = pytest.mark.integration


def _seed_embeddings(engine: Engine, fixture: RuntimeEvidenceFixture) -> None:
    organizations = (fixture.org_a, fixture.org_b)
    embeddings = DeterministicEmbeddingTwin().embed(
        tuple(
            body
            for organization in organizations
            for body in (organization.authorized_body, organization.denied_body)
        )
    )
    parameters = [
        {
            "organization_id": organization.organization_id,
            "resource_ref": candidate.resource_ref,
            "embedding": "[" + ",".join(repr(item) for item in embedding) + "]",
            "embedding_profile_digest": (
                DeterministicEmbeddingTwin().provider_profile.profile_digest
            ),
        }
        for organization, candidate, embedding in zip(
            (fixture.org_a, fixture.org_a, fixture.org_b, fixture.org_b),
            (
                fixture.org_a.authorized,
                fixture.org_a.denied,
                fixture.org_b.authorized,
                fixture.org_b.denied,
            ),
            embeddings,
            strict=True,
        )
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE context_fragment DISABLE TRIGGER "
                "context_fragment_reject_mutation"
            )
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE context_fragment "
                    "SET embedding = CAST(:embedding AS vector), "
                    "embedding_profile_digest = :embedding_profile_digest "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref = :resource_ref"
                ),
                parameters,
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment ENABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )


def test_fts_and_vector_force_rls_return_nothing_for_foreign_organization(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        _seed_embeddings(migration_engine, fixture)
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        active = fixture.org_a
        foreign = fixture.org_b.authorized
        with authority.current_user_actor(
            MembershipIdentity(
                organization_id=active.organization_id,
                user_id=active.user_id,
                membership_id=active.membership_id,
                membership_version=1,
                principal_ref=f"principal:authorized-evidence:{active.label}",
                request_id="request:fts-foreign-organization",
                authentication_binding_ref="binding:fts-foreign-organization",
                checked_at=RECEIVED_AT,
            )
        ) as actor:
            session = actor.materialized_projection_session
            assert session is not None
            discovery = _construct_candidate_discovery_session(
                session,
                HybridDiscoveryRequest(
                    fts=FtsDiscoveryRequest(
                        query_text=fixture.org_b.authorized_body,
                        limit=64,
                    ),
                    vector=VectorDiscoveryRequest(
                        query_embedding=DeterministicEmbeddingTwin().embed(
                            (fixture.org_b.authorized_body,)
                        )[0],
                        embedding_profile_digest="a" * 64,
                        limit=64,
                    ),
                ),
                effective_scope=EffectiveScope(
                    frozenset(
                        {
                            ScopeTarget(
                                foreign.organization_id,
                                foreign.source_ref,
                                foreign.resource_ref,
                            )
                        }
                    )
                ),
            )
            assert _candidate_discovery_ranker_candidates(discovery, "fts") == ()
            assert _candidate_discovery_ranker_candidates(discovery, "vector") == ()

        with guarded_runtime_engine.begin() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM context_fragment "
                    "WHERE search_vector @@ "
                    "websearch_to_tsquery('simple', 'authorized')"
                ).scalar_one()
                == 0
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM context_fragment WHERE embedding IS NOT NULL"
                ).scalar_one()
                == 0
            )
    finally:
        _cleanup_fixture(migration_engine, fixture)
        migration_engine.dispose()
