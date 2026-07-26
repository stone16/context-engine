from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text

import engine.persistence.membership_context as membership_context_module
from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.http.app import HEALTH_RESPONSE, create_app
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLAccessPolicyControl,
    PostgreSQLMembershipAuthority,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedProjectionSession
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    _ExactScopeAuthority,
    _OrganizationAuthority,
    _RuntimeAuthenticator,
)
from tests.integration.test_zz_file_revision_replacement import _scenario_user_id
from tests.support.file_imports import (
    NOW,
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration
QUERY = "ContextEngine delivers context."
TOKEN = "runtime-secret"


class _RecordingVectorCandidateIndex:
    def __init__(self) -> None:
        self.inner = PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin())
        self.calls: list[tuple[CandidateRef, ...]] = []

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        candidates = self.inner.discover(request, projection_session)
        self.calls.append(candidates)
        return candidates


class _BlockingVectorCandidateIndex:
    def __init__(self) -> None:
        self.inner = PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin())
        self.discovered = Event()
        self.release = Event()
        self.calls: list[tuple[CandidateRef, ...]] = []

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        candidates = self.inner.discover(request, projection_session)
        self.calls.append(candidates)
        self.discovered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("vector candidate barrier timed out")
        return candidates


def _published_scenario(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    *,
    label: str,
) -> tuple[FileImportScenario, CandidateRef, UUID]:
    root = tmp_path / label
    root.mkdir()
    scenario = prepare_file_import_scenario(
        root,
        migration_configuration,
        guarded_control_engine,
    )
    request.addfinalizer(
        lambda: _delete_published_scenario(
            migration_configuration, scenario.organization_id
        )
    )
    assert scenario.token is not None
    published = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    ensure_test_runtime_release(
        scenario.organization_id,
        active_revision_refs=(published.candidate_ref.revision_ref,),
    )
    return (
        scenario,
        published.candidate_ref,
        _scenario_user_id(scenario, migration_configuration),
    )


def _delete_published_scenario(
    migration_configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    clear_test_runtime_release(organization_id)
    delete_file_import_scenario(migration_configuration, organization_id)


def _client(
    scenario: FileImportScenario,
    candidate: CandidateRef,
    user_id: UUID,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    index: CandidateIndex,
    *,
    request_id: str,
) -> TestClient:
    return TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=_ExactScopeAuthority(
                candidate.source_ref,
                candidate.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=index,
                clock=lambda: NOW,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: NOW,
            request_id_factory=lambda: request_id,
        )
    )


def _resolve(client: TestClient) -> Response:
    return client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Context-Request-Id": "issue-101-vector-http",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )


def _stable_empty(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocks": package["blocks"],
        "evidence": package["evidence"],
        "gaps": package["gaps"],
        "coverage": package["coverage"],
        "budgetUsage": package["budgetUsage"],
    }


def _assert_empty(response: Response) -> dict[str, Any]:
    assert response.status_code == 200
    package = cast(dict[str, Any], response.json()["package"])
    assert package["blocks"] == package["evidence"] == package["gaps"] == []
    assert package["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    return _stable_empty(package)


def test_vector_candidate_http_chain_is_rls_scoped_content_free_and_bounded(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    org_a, candidate_a, user_a = _published_scenario(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        label="org-a",
    )
    org_b, candidate_b, _user_b = _published_scenario(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        label="org-b",
    )
    index = _RecordingVectorCandidateIndex()
    response = _resolve(
        _client(
            org_a,
            candidate_a,
            user_a,
            guarded_runtime_engine,
            query_digest_keyring,
            index,
            request_id="issue-101-vector-authorized",
        )
    )

    assert response.status_code == 200
    package = response.json()["package"]
    assert [block["text"] for block in package["blocks"]] == [QUERY]
    assert len(package["evidence"]) == 1
    evidence = package["evidence"][0]
    assert evidence["sourceRef"] == candidate_a.source_ref
    assert evidence["resourceRef"] == candidate_a.resource_ref
    assert evidence["revisionRef"] == candidate_a.revision_ref
    assert evidence["fragmentRef"] == candidate_a.fragment_ref
    assert index.calls == [(candidate_a,)]
    assert candidate_b not in index.calls[0]
    assert len(index.calls[0]) <= 16
    assert set(CandidateRef.__dataclass_fields__) == {
        "organization_id",
        "source_ref",
        "resource_ref",
        "revision_ref",
        "fragment_ref",
    }
    for forbidden in (
        str(org_b.organization_id),
        candidate_b.source_ref,
        candidate_b.resource_ref,
        candidate_b.revision_ref,
    ):
        assert forbidden not in response.text


def test_vector_candidate_denials_have_one_non_enumerating_empty_shape(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario, candidate, user_id = _published_scenario(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        label="denials",
    )
    index = _RecordingVectorCandidateIndex()
    client = _client(
        scenario,
        candidate,
        user_id,
        guarded_runtime_engine,
        query_digest_keyring,
        index,
        request_id="issue-101-vector-denials",
    )
    unknown_candidate = CandidateRef(
        organization_id=scenario.organization_id,
        source_ref=candidate.source_ref,
        resource_ref=f"resource:missing:{uuid4()}",
        revision_ref=str(uuid4()),
        fragment_ref="fragment:missing",
    )
    unknown_shape = _assert_empty(
        _resolve(
            _client(
                scenario,
                unknown_candidate,
                user_id,
                guarded_runtime_engine,
                query_digest_keyring,
                index,
                request_id="issue-101-vector-unknown",
            )
        )
    )
    assert index.calls == [(candidate,)]
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE context_resource SET tombstoned = true "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref = :resource_ref"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": candidate.resource_ref,
                },
            )
    finally:
        migration_engine.dispose()

    tombstoned_shape = _assert_empty(_resolve(client))
    assert index.calls == [(candidate,), ()]
    assert tombstoned_shape == unknown_shape

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE membership SET status = 'revoked' "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            )
    finally:
        migration_engine.dispose()

    revoked = _resolve(client)
    assert revoked.status_code == 401
    assert revoked.json() == {"code": "authentication_failed"}
    assert index.calls == [(candidate,), ()]
    for forbidden in (
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    ):
        assert forbidden not in tombstoned_shape
        assert forbidden not in revoked.text


def test_vector_candidate_stale_epoch_vetoes_already_discovered_evidence(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, candidate, user_id = _published_scenario(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        label="stale-epoch",
    )
    index = _BlockingVectorCandidateIndex()
    final_read_reached = Event()
    release_final_read = Event()
    original_read = (
        membership_context_module._PostgreSQLPolicyEpochPort.read_current_epoch
    )
    reads = 0

    def block_final_epoch_read(
        port: membership_context_module._PostgreSQLPolicyEpochPort,
        organization_id: UUID,
    ) -> object:
        nonlocal reads
        reads += 1
        if reads == 3:
            final_read_reached.set()
            if not release_final_read.wait(timeout=10):
                raise RuntimeError("final Policy Epoch read was not released")
        return original_read(port, organization_id)

    monkeypatch.setattr(
        membership_context_module._PostgreSQLPolicyEpochPort,
        "read_current_epoch",
        block_final_epoch_read,
    )
    client = _client(
        scenario,
        candidate,
        user_id,
        guarded_runtime_engine,
        query_digest_keyring,
        index,
        request_id="issue-101-vector-stale-epoch",
    )
    control_engine = create_database_engine(control_configuration)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_resolve, client)
            assert index.discovered.wait(timeout=10)
            index.release.set()
            assert final_read_reached.wait(timeout=10)
            epoch = PostgreSQLAccessPolicyControl(control_engine).change_access(
                ResourceAccessRevocation(
                    organization_id=scenario.organization_id,
                    resource_ref=candidate.resource_ref,
                    principal_ref="principal:file-reader",
                    expected_access_version=1,
                )
            )
            assert epoch.value == 2
            release_final_read.set()
            response = pending.result(timeout=10)
    finally:
        index.release.set()
        release_final_read.set()
        control_engine.dispose()

    _assert_empty(response)
    assert index.calls == [(candidate,)]
    assert reads == 3
    for forbidden in (
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    ):
        assert forbidden not in response.text


def test_default_composition_keeps_vector_retrieval_not_active() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json()["runtime_delivery"] == "NOT_ACTIVE"
    assert HEALTH_RESPONSE["runtime_delivery"] == "NOT_ACTIVE"
