from __future__ import annotations

from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from adapters.http.app import create_app
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_runtime_authorized_evidence_integration import (
    ORG_A_DENIED_BODY,
    ORG_B_AUTHORIZED_BODY,
    RECEIVED_AT,
    ExactScopeAuthority,
    HostileCandidateIndex,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)
from tests.support.releases import ensure_test_runtime_release

pytestmark = pytest.mark.integration
TOKEN = "authorized-hit-test-secret"


def test_hit_test_shows_only_authorized(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """Mixed PG candidates disclose only post-Kernel authorized hit facts."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        index = HostileCandidateIndex(
            fixture.org_a,
            cross_organization=fixture.org_b.authorized,
        )
        app = create_app(
            authenticator=SeededAuthenticator(fixture.org_a, token=TOKEN),
            organization_authority=SeededOrganizationAuthority(
                fixture.org_a.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=ExactScopeAuthority(fixture.org_a.authorized),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=cast(CandidateIndex, index),
                clock=lambda: RECEIVED_AT,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: RECEIVED_AT,
            ui_bearer_token=TOKEN,
        )

        response = TestClient(app).post(
            "/ui/hit-test",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert fixture.org_a.authorized_body in response.text
        assert fixture.org_a.authorized.resource_ref in response.text
        assert fixture.org_a.authorized.revision_ref in response.text
        assert fixture.org_a.authorized.fragment_ref in response.text
        assert "Authorized hit 1" in response.text
        _assert_refused_candidates_are_unobservable(response.text, fixture)
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()


def test_hit_test_empty_and_error_states_do_not_reveal_refused_candidates(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        app = create_app(
            authenticator=SeededAuthenticator(fixture.org_a, token=TOKEN),
            organization_authority=SeededOrganizationAuthority(
                fixture.org_a.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=ExactScopeAuthority(fixture.org_a.authorized),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=cast(
                    CandidateIndex,
                    HostileCandidateIndex(
                        fixture.org_a,
                        cross_organization=fixture.org_b.authorized,
                    ),
                ),
                clock=lambda: RECEIVED_AT,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: RECEIVED_AT,
        )
        client = TestClient(app)

        refusal = client.post(
            "/ui/hit-test",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        invalid = client.post(
            "/ui/hit-test",
            content="query=",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert refusal.status_code == 401
        assert "Request refused" in refusal.text
        assert "No hits" not in refusal.text
        assert invalid.status_code == 422
        for document in (refusal.text, invalid.text):
            _assert_refused_candidates_are_unobservable(document, fixture)
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()


def _assert_refused_candidates_are_unobservable(
    document: str,
    fixture: object,
) -> None:
    runtime_fixture = fixture
    # Attribute access remains explicit so each forbidden fact is independently
    # visible in this security oracle rather than hidden in a helper computation.
    org_a = runtime_fixture.org_a  # type: ignore[attr-defined]
    org_b = runtime_fixture.org_b  # type: ignore[attr-defined]
    forbidden = (
        ORG_A_DENIED_BODY,
        ORG_B_AUTHORIZED_BODY,
        org_a.denied.source_ref,
        org_a.denied.resource_ref,
        org_a.denied.revision_ref,
        org_a.denied.fragment_ref,
        org_b.authorized.source_ref,
        org_b.authorized.resource_ref,
        org_b.authorized.revision_ref,
        org_b.authorized.fragment_ref,
        "rank 3",
        "filtered 2",
        "2 refused",
    )
    assert all(value not in document for value in forbidden)
