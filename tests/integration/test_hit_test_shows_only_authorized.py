from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Never, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.http.app import create_app
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.citation import PRIVATE_FILE_CITATION_OPEN_PROFILE
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex, CandidateIndexUnavailable
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_runtime_authorized_evidence_integration import (
    ORG_A_DENIED_BODY,
    ORG_B_AUTHORIZED_BODY,
    RECEIVED_AT,
    ExactScopeAuthority,
    HostileCandidateIndex,
    RuntimeEvidenceFixture,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)
from tests.support.releases import ensure_test_runtime_release
from tests.support.ui import authenticate_ui

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
    request_now = datetime.now(UTC).replace(microsecond=0)
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
                citation_profile=PRIVATE_FILE_CITATION_OPEN_PROFILE,
                clock=lambda: request_now,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: request_now,
            ui_bearer_token=TOKEN,
        )

        client = TestClient(app)
        authenticate_ui(client, TOKEN)
        response = client.post(
            "/ui/hit-test",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert fixture.org_a.authorized_body in response.text
        assert fixture.org_a.authorized.resource_ref in response.text
        assert fixture.org_a.authorized.revision_ref in response.text
        assert fixture.org_a.authorized.fragment_ref in response.text
        assert "Authorized hit 1" in response.text
        assert "not_exposed_by_rank_free_public_contract" in response.text
        _assert_refused_candidates_are_unobservable(response.text, fixture)

        answer = client.post(
            "/ui/ask",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert answer.status_code == 200, answer.text
        assert fixture.org_a.authorized_body in answer.text
        assert "citation_unavailable" not in answer.text
        _assert_refused_candidates_are_unobservable(answer.text, fixture)
    finally:
        try:
            _cleanup_citation_lineage(migration_engine, fixture)
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
        empty_app = create_app(
            authenticator=SeededAuthenticator(fixture.org_a, token=TOKEN),
            organization_authority=SeededOrganizationAuthority(
                fixture.org_a.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            # The scope names no discovered candidate, so every mixed candidate
            # reaches the Kernel and the public result is authorized-empty.
            scope_authority=ExactScopeAuthority(fixture.org_b.denied),
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
            ui_bearer_token=TOKEN,
        )
        empty_client = TestClient(empty_app)
        authenticate_ui(empty_client, TOKEN)

        empty = empty_client.post(
            "/ui/hit-test",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        unavailable_index = HostileCandidateIndex(
            fixture.org_a,
            cross_organization=fixture.org_b.authorized,
        )

        def unavailable_discover(*args: object, **kwargs: object) -> Never:
            del args, kwargs
            raise CandidateIndexUnavailable

        cast(Any, unavailable_index).discover = unavailable_discover
        error_app = create_app(
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
                candidate_index=cast(CandidateIndex, unavailable_index),
                clock=lambda: RECEIVED_AT,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: RECEIVED_AT,
            ui_bearer_token=TOKEN,
        )
        error_client = TestClient(error_app)
        authenticate_ui(error_client, TOKEN)
        error = error_client.post(
            "/ui/hit-test",
            content="query=hostile+rank",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert empty.status_code == 200
        assert "No authorized evidence" in empty.text
        assert "<h3>Authorized hit" not in empty.text
        assert error.status_code == 503
        assert "Request refused" in error.text
        for document in (empty.text, error.text):
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


def _cleanup_citation_lineage(
    migration_engine: Engine,
    fixture: RuntimeEvidenceFixture,
) -> None:
    org_a = fixture.org_a
    org_b = fixture.org_b
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM citation_open_locator
                WHERE organization_id IN (:org_a_id, :org_b_id)
                """
            ),
            {
                "org_a_id": org_a.organization_id,
                "org_b_id": org_b.organization_id,
            },
        )
