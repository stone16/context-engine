from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

import engine.persistence.membership_context as membership_context_module
from adapters.http.app import create_app
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLAccessPolicyControl,
    PostgreSQLMembershipAuthority,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.scope import EffectiveScope
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    ExactScopeAuthority,
    HostileCandidateIndex,
    OrganizationEvidenceFixture,
    SeededAuthenticator,
    SeededOrganizationAuthority,
    _cleanup_fixture,
    _new_fixture,
    _persistent_content_snapshot,
    _seed_fixture,
)
from tests.support.context_run_operator import exact_test_context_run_operator_read

pytestmark = pytest.mark.integration
QUERY = "same policy epoch revocation probe"
TOKEN = "policy-epoch-integration-token"
REQUEST_ID = "policy-epoch-same-authenticated-request"


def _client(
    *,
    active: OrganizationEvidenceFixture,
    guarded_runtime_engine: Engine,
    index: CandidateIndex,
    query_digest_keyring: QueryDigestKeyring,
) -> TestClient:
    return TestClient(
        create_app(
            authenticator=SeededAuthenticator(active, token=TOKEN),
            organization_authority=SeededOrganizationAuthority(active.organization_id),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=ExactScopeAuthority(active.authorized),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=index,
                clock=lambda: RECEIVED_AT,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: REQUEST_ID,
        )
    )


def _resolve(client: TestClient) -> Response:
    return client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"kind": "acquire", "need": {"query": QUERY}},
    )


def _assert_authorized(
    response: Response,
    fixture: OrganizationEvidenceFixture,
) -> None:
    assert response.status_code == 200
    package = response.json()["package"]
    assert package["coverage"] == {"status": "sufficient"}
    assert len(package["blocks"]) == len(package["evidence"]) == 1
    assert package["blocks"][0]["text"] == fixture.authorized_body
    evidence = package["evidence"][0]
    assert evidence["resourceRef"] == fixture.authorized.resource_ref
    assert evidence["fragmentRef"] == fixture.authorized.fragment_ref
    assert evidence["policyEpoch"] == 1


def _assert_revoked_empty(
    response: Response,
    fixture: OrganizationEvidenceFixture,
) -> None:
    assert response.status_code == 200
    package = response.json()["package"]
    assert package["blocks"] == package["evidence"] == package["gaps"] == []
    assert package["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    assert package["budgetUsage"] == {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    for forbidden in (
        fixture.authorized_body,
        fixture.authorized.source_ref,
        fixture.authorized.resource_ref,
        fixture.authorized.revision_ref,
        fixture.authorized.fragment_ref,
    ):
        assert forbidden not in response.text


@pytest.mark.security_evidence(id="RUNTIME-REVOCATION-006", layer="runtime")
def test_same_http_acquire_revokes_next_delivery_without_candidate_cleanup(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """RUN-006: one committed revoke invalidates the first following Acquire."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    control_engine = create_database_engine(control_configuration)
    org_a_index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    org_b_index = HostileCandidateIndex(
        fixture.org_b,
        cross_organization=fixture.org_a.authorized,
    )
    org_a_client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=org_a_index,
        query_digest_keyring=query_digest_keyring,
    )
    org_b_client = _client(
        active=fixture.org_b,
        guarded_runtime_engine=guarded_runtime_engine,
        index=org_b_index,
        query_digest_keyring=query_digest_keyring,
    )
    try:
        _seed_fixture(migration_engine, fixture)
        persistent_before = _persistent_content_snapshot(migration_engine, fixture)
        assert len(persistent_before) == 4

        _assert_authorized(_resolve(org_a_client), fixture.org_a)
        _assert_authorized(_resolve(org_b_client), fixture.org_b)

        next_epoch = PostgreSQLAccessPolicyControl(control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=fixture.org_a.organization_id,
                resource_ref=fixture.org_a.authorized.resource_ref,
                principal_ref=(f"principal:authorized-evidence:{fixture.org_a.label}"),
                expected_access_version=1,
            )
        )
        assert next_epoch.organization_id == fixture.org_a.organization_id
        assert next_epoch.value == 2

        # Same auth, query, request id, CandidateIndex object, CandidateRef tuple,
        # and persistent Fragment. This is the first request after commit.
        _assert_revoked_empty(_resolve(org_a_client), fixture.org_a)
        _assert_authorized(_resolve(org_b_client), fixture.org_b)

        assert len(org_a_index.calls) == 2
        assert len(org_b_index.calls) == 2
        assert all(call.need.query == QUERY for call in org_a_index.calls)
        assert all(call.need.query == QUERY for call in org_b_index.calls)
        assert _persistent_content_snapshot(migration_engine, fixture) == (
            persistent_before
        )
        with migration_engine.connect() as connection:
            org_a_state = connection.execute(
                text(
                    """
                    SELECT epoch.policy_epoch, access.access_state,
                           membership.status
                    FROM organization_policy_epoch AS epoch
                    JOIN resource_access_policy AS access
                      ON access.organization_id = epoch.organization_id
                    JOIN membership
                      ON membership.organization_id = epoch.organization_id
                    WHERE epoch.organization_id = :organization_id
                      AND access.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": fixture.org_a.organization_id,
                    "resource_ref": fixture.org_a.authorized.resource_ref,
                },
            ).one()
            org_b_epoch = connection.execute(
                text(
                    """
                    SELECT policy_epoch
                    FROM organization_policy_epoch
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": fixture.org_b.organization_id},
            ).scalar_one()
        assert tuple(org_a_state) == (2, "revoked", "active")
        assert org_b_epoch == 1
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            control_engine.dispose()
            migration_engine.dispose()


def test_mid_resolve_revoke_is_visible_despite_repeatable_read_engine_default(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """RUN-006: final epoch read cannot reuse a pre-revoke MVCC snapshot."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    control_engine = create_database_engine(control_configuration)
    runtime_engine = create_database_engine(runtime_configuration)
    drifted_runtime_engine = runtime_engine.execution_options(
        isolation_level="REPEATABLE READ"
    )
    index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    final_read_reached = Event()
    release_final_read = Event()
    read_lock = Lock()
    read_count = 0
    original_read = (
        membership_context_module._PostgreSQLPolicyEpochPort.read_current_epoch
    )

    def synchronized_epoch_read(
        port: membership_context_module._PostgreSQLPolicyEpochPort,
        organization_id: UUID,
    ) -> object:
        nonlocal read_count
        with read_lock:
            read_count += 1
            current_read = read_count
        if current_read == 3:
            final_read_reached.set()
            if not release_final_read.wait(timeout=10):
                raise RuntimeError("final Policy Epoch read was not released")
        return original_read(port, organization_id)

    monkeypatch.setattr(
        membership_context_module._PostgreSQLPolicyEpochPort,
        "read_current_epoch",
        synchronized_epoch_read,
    )
    client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=drifted_runtime_engine,
        index=cast(CandidateIndex, index),
        query_digest_keyring=query_digest_keyring,
    )
    try:
        _seed_fixture(migration_engine, fixture)
        persistent_before = _persistent_content_snapshot(migration_engine, fixture)

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending_response = executor.submit(_resolve, client)
            assert final_read_reached.wait(timeout=10)

            next_epoch = PostgreSQLAccessPolicyControl(control_engine).change_access(
                ResourceAccessRevocation(
                    organization_id=fixture.org_a.organization_id,
                    resource_ref=fixture.org_a.authorized.resource_ref,
                    principal_ref=(
                        f"principal:authorized-evidence:{fixture.org_a.label}"
                    ),
                    expected_access_version=1,
                )
            )
            assert next_epoch.value == 2
            release_final_read.set()
            response = pending_response.result(timeout=10)

        _assert_revoked_empty(response, fixture.org_a)
        decision_ref = response.json()["package"]["decisionRef"]
        with exact_test_context_run_operator_read(
            control_engine=guarded_control_engine,
            operator_engine=guarded_operator_engine,
            organization_id=fixture.org_a.organization_id,
            decision_ref=decision_ref,
            request_id="test:issue-19:mid-resolve-epoch-veto",
            opaque_credential="test:issue-19:mid-resolve-epoch-veto",
            authorized_at=RECEIVED_AT,
        ) as (reader, authorization):
            run = reader.find_by_decision_ref(authorization, decision_ref)
        assert run is not None
        assert run.effective_scope_digest == EffectiveScope(frozenset()).digest
        assert read_count == 3
        assert len(index.calls) == 1
        assert _persistent_content_snapshot(migration_engine, fixture) == (
            persistent_before
        )
    finally:
        release_final_read.set()
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            runtime_engine.dispose()
            control_engine.dispose()
            migration_engine.dispose()
