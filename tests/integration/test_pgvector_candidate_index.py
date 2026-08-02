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
from adapters.pgvector import (
    DEFAULT_VECTOR_CANDIDATE_LIMIT,
    PostgreSQLVectorCandidateIndex,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLAccessPolicyControl,
    PostgreSQLMembershipAuthority,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.persistence.membership_context import _VECTOR_CANDIDATE_SQL
from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.candidate_ranking import CandidateQuery
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    CandidateDiscoverySession,
    VectorDiscoveryRequest,
)
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.scope import CandidateDiscoveryScope, EffectiveScope, ScopeTarget
from engine.supply import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    QWEN3_EMBEDDING_PROFILE,
    EmbeddingProfile,
    EmbeddingProviderUnavailable,
)
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
NARROWED_TARGET_CONTENT = "The target handbook passage remains authorized."
TOKEN = "runtime-secret"
_ANN_DISTRACTOR_COUNT = 96
_NARROWING_DISTRACTOR_COUNT = DEFAULT_VECTOR_CANDIDATE_LIMIT + 4


def _candidate_refs(query: CandidateQuery) -> tuple[CandidateRef, ...]:
    return tuple(
        item.candidate_ref
        for ranked_list in query.ranked_lists
        for item in ranked_list.candidates
    )


class _RecordingVectorCandidateIndex:
    def __init__(self) -> None:
        self.inner = PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin())
        self.calls: list[CandidateQuery] = []

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> VectorDiscoveryRequest:
        return self.inner.prepare_discovery(
            request,
            effective_scope=effective_scope,
        )

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> VectorDiscoveryRequest:
        return self.inner.prepare_budgeted_discovery(
            request,
            effective_scope=effective_scope,
            budget=budget,
            active_embedding_profile_digest=active_embedding_profile_digest,
        )

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        candidates = self.inner.discover(
            request,
            discovery_session,
            effective_scope=effective_scope,
        )
        self.calls.append(candidates)
        return candidates


class _BlockingVectorCandidateIndex:
    def __init__(self) -> None:
        self.inner = PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin())
        self.discovered = Event()
        self.release = Event()
        self.calls: list[CandidateQuery] = []

    def prepare_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> VectorDiscoveryRequest:
        return self.inner.prepare_discovery(
            request,
            effective_scope=effective_scope,
        )

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> VectorDiscoveryRequest:
        return self.inner.prepare_budgeted_discovery(
            request,
            effective_scope=effective_scope,
            budget=budget,
            active_embedding_profile_digest=active_embedding_profile_digest,
        )

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        candidates = self.inner.discover(
            request,
            discovery_session,
            effective_scope=effective_scope,
        )
        self.calls.append(candidates)
        self.discovered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("vector candidate barrier timed out")
        return candidates


class _UnavailableEmbeddingProvider:
    profile = EmbeddingProfile(384)
    provider_profile = DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        del inputs
        raise EmbeddingProviderUnavailable("provider detail must not escape")

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(inputs)

    def __init__(self) -> None:
        self.calls = 0


class _RecordingTwinProvider:
    profile = EmbeddingProfile(384)
    provider_profile = DETERMINISTIC_TWIN_EMBEDDING_PROFILE

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, inputs: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return DeterministicEmbeddingTwin().embed(inputs)

    def embed_documents(
        self, inputs: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(inputs)


def _published_scenario(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    *,
    label: str,
    payload: bytes | None = b"# Handbook\n\nContextEngine delivers context.\n",
) -> tuple[FileImportScenario, CandidateRef, UUID]:
    root = tmp_path / label
    root.mkdir()
    scenario = prepare_file_import_scenario(
        root,
        migration_configuration,
        guarded_control_engine,
        payload=payload,
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


def _add_cross_organization_vector_distractors(
    migration_configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
    candidate: CandidateRef,
) -> None:
    embedding = DeterministicEmbeddingTwin().embed((QUERY,))[0]
    parameters = [
        {
            "organization_id": scenario.organization_id,
            "resource_ref": candidate.resource_ref,
            "revision_id": UUID(candidate.revision_ref),
            "fragment_ref": f"fragment:vector-distractor:{ordinal:03d}",
            "ordinal": ordinal,
            "content": QUERY,
            "embedding": "[" + ",".join(repr(value) for value in embedding) + "]",
            "embedding_profile_digest": (
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
            ),
        }
        for ordinal in range(1, _ANN_DISTRACTOR_COUNT + 1)
    ]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content, projection_kind,
                        embedding, embedding_profile_digest
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, :ordinal, :content, 'body',
                        CAST(:embedding AS vector), :embedding_profile_digest
                    )
                    """
                ),
                parameters,
            )
    finally:
        engine.dispose()


def _add_same_organization_narrowing_distractors(
    migration_configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
) -> None:
    embedding = DeterministicEmbeddingTwin().embed((QUERY,))[0]
    parameters = [
        {
            "organization_id": scenario.organization_id,
            "resource_ref": f"resource:vector-narrowing-distractor:{ordinal:03d}",
            "source_ref": str(scenario.source_ref.value),
            "revision_id": uuid4(),
            "fragment_ref": f"fragment:vector-narrowing-distractor:{ordinal:03d}",
            "content": QUERY,
            "embedding": "[" + ",".join(repr(value) for value in embedding) + "]",
            "embedding_profile_digest": (
                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
            ),
            "membership_id": scenario.membership_id,
        }
        for ordinal in range(_NARROWING_DISTRACTOR_COUNT)
    ]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (
                        :organization_id, :resource_ref, :source_ref,
                        :revision_id, false
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content, projection_kind,
                        embedding, embedding_profile_digest
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, :content, 'body',
                        CAST(:embedding AS vector), :embedding_profile_digest
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resource_access_policy (
                        organization_id, resource_ref, principal_ref,
                        access_version, access_state, revoked_at
                    ) VALUES (
                        :organization_id, :resource_ref, 'principal:file-reader',
                        1, 'allowed', NULL
                    )
                    """
                ),
                parameters,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership_resource_field_right (
                        organization_id, membership_id, membership_version,
                        resource_ref, field_ref
                    ) VALUES (
                        :organization_id, :membership_id, 1,
                        :resource_ref, 'body'
                    )
                    """
                ),
                parameters,
            )
    finally:
        engine.dispose()


def _actor_settings(
    scenario: FileImportScenario,
    user_id: UUID,
) -> dict[str, str]:
    return {
        "app.actor_kind": "user",
        "app.authentication_binding_ref": "binding:file-tracer",
        "app.checked_at": NOW.isoformat().replace("+00:00", "Z"),
        "app.membership_id": str(scenario.membership_id),
        "app.membership_version": "1",
        "app.organization_id": str(scenario.organization_id),
        "app.principal_ref": "principal:file-reader",
        "app.request_id": "issue-101-vector-plan",
        "app.user_id": str(user_id),
    }


def _ann_plan_and_exact_result(
    guarded_runtime_engine: Engine,
    scenario: FileImportScenario,
    user_id: UUID,
    *,
    source_refs: tuple[str, ...] | None = None,
    resource_refs: tuple[str, ...] | None = None,
    effective_scope: EffectiveScope | None = None,
) -> tuple[
    str,
    tuple[tuple[object, ...], ...],
    tuple[tuple[object, ...], ...],
]:
    embedding = DeterministicEmbeddingTwin().embed((QUERY,))[0]
    if effective_scope is None:
        effective_scope = EffectiveScope(
            frozenset(
                {
                    ScopeTarget(
                        scenario.organization_id,
                        str(scenario.source_ref.value),
                    )
                }
            )
        )
    resource_targets = tuple(
        target for target in effective_scope.targets if target.resource_ref is not None
    )
    parameters = {
        "embedding_profile_digest": (
            DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
        ),
        "query_embedding": "["
        + ",".join(repr(value) for value in embedding)
        + "]",
        "limit": 1,
        "source_refs": list(source_refs) if source_refs is not None else None,
        "resource_refs": list(resource_refs) if resource_refs is not None else None,
        "scope_resource_organization_ids": [
            target.organization_id for target in resource_targets
        ],
        "scope_resource_source_refs": [
            target.source_ref for target in resource_targets
        ],
        "scope_resource_refs": [target.resource_ref for target in resource_targets],
    }
    with guarded_runtime_engine.begin() as connection:
        for name, value in _actor_settings(scenario, user_id).items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        for name, value in (
            ("hnsw.iterative_scan", "strict_order"),
            ("hnsw.max_scan_tuples", "20000"),
            ("enable_seqscan", "off"),
            ("enable_sort", "off"),
        ):
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        plan = "\n".join(
            str(line)
            for line in connection.execute(
                text(
                    "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) "
                    + _VECTOR_CANDIDATE_SQL
                ),
                parameters,
            ).scalars()
        )
        approximate: tuple[tuple[object, ...], ...] = tuple(
            tuple(row)
            for row in connection.execute(
                text(_VECTOR_CANDIDATE_SQL),
                parameters,
            )
        )
        connection.execute(
            text("SELECT set_config('enable_indexscan', 'off', true)")
        )
        exact: tuple[tuple[object, ...], ...] = tuple(
            tuple(row)
            for row in connection.execute(
                text(_VECTOR_CANDIDATE_SQL),
                parameters,
            )
        )
    return plan, approximate, exact


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


def _resolve(
    client: TestClient,
    *,
    source_refs: tuple[str, ...] | None = None,
    resource_refs: tuple[str, ...] | None = None,
) -> Response:
    body: dict[str, object] = {"kind": "acquire", "need": {"query": QUERY}}
    if source_refs is not None or resource_refs is not None:
        body["requestNarrowing"] = {
            key: value
            for key, value in (
                ("sourceRefs", source_refs),
                ("resourceRefs", resource_refs),
            )
            if value is not None
        }
    return client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Context-Request-Id": "issue-101-vector-http",
        },
        json=body,
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
        payload=f"# Handbook\n\n{NARROWED_TARGET_CONTENT}\n".encode(),
    )
    org_b, candidate_b, _user_b = _published_scenario(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        label="org-b",
    )
    _add_cross_organization_vector_distractors(
        migration_configuration,
        org_b,
        candidate_b,
    )
    _add_same_organization_narrowing_distractors(
        migration_configuration,
        org_a,
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
        ),
    )

    assert response.status_code == 200
    package = response.json()["package"]
    assert [block["text"] for block in package["blocks"]] == [
        NARROWED_TARGET_CONTENT
    ]
    assert len(package["evidence"]) == 1
    evidence = package["evidence"][0]
    assert evidence["sourceRef"] == candidate_a.source_ref
    assert evidence["resourceRef"] == candidate_a.resource_ref
    assert evidence["revisionRef"] == candidate_a.revision_ref
    assert evidence["fragmentRef"] == candidate_a.fragment_ref
    assert tuple(_candidate_refs(query) for query in index.calls) == ((candidate_a,),)
    assert candidate_b not in _candidate_refs(index.calls[0])
    assert len(_candidate_refs(index.calls[0])) <= DEFAULT_VECTOR_CANDIDATE_LIMIT
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
    _unfiltered_plan, unfiltered_approximate, unfiltered_exact = (
        _ann_plan_and_exact_result(
            guarded_runtime_engine,
            org_a,
            user_a,
        )
    )
    target_row = (
        candidate_a.organization_id,
        candidate_a.source_ref,
        candidate_a.resource_ref,
        UUID(candidate_a.revision_ref),
        candidate_a.fragment_ref,
    )
    assert target_row not in unfiltered_approximate
    assert target_row not in unfiltered_exact
    plan, approximate, exact = _ann_plan_and_exact_result(
        guarded_runtime_engine,
        org_a,
        user_a,
        effective_scope=EffectiveScope(
            frozenset(
                {
                    ScopeTarget(
                        candidate_a.organization_id,
                        candidate_a.source_ref,
                        candidate_a.resource_ref,
                    )
                }
            )
        ),
    )
    assert "Index Scan using ix_context_fragment_embedding_hnsw" in plan
    assert approximate == exact
    assert len(approximate) == 1
    assert approximate[0][:5] == target_row


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
    assert tuple(_candidate_refs(query) for query in index.calls) == ((),)
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
    assert tuple(_candidate_refs(query) for query in index.calls) == ((), ())
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
    assert tuple(_candidate_refs(query) for query in index.calls) == ((), ())
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
    assert tuple(_candidate_refs(query) for query in index.calls) == ((candidate,),)
    assert reads == 3
    for forbidden in (
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    ):
        assert forbidden not in response.text


def test_vector_embedding_outage_is_one_content_free_service_unavailable_response(
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
        label="provider-outage",
    )
    client = _client(
        scenario,
        candidate,
        user_id,
        guarded_runtime_engine,
        query_digest_keyring,
        PostgreSQLVectorCandidateIndex(_UnavailableEmbeddingProvider()),
        request_id="issue-101-vector-provider-outage",
    )

    response = _resolve(client)

    assert response.status_code == 503
    assert response.json() == {"code": "service_unavailable"}
    assert "provider detail" not in response.text
    for forbidden in (
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    ):
        assert forbidden not in response.text


def test_mixed_profile_refuses_over_http_before_ann_discovery(
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
        label="mixed-profile",
    )
    provider = _RecordingTwinProvider()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_fragment DISABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "UPDATE context_fragment SET embedding_profile_digest = :digest "
                    "WHERE organization_id = :organization_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "digest": QWEN3_EMBEDDING_PROFILE.profile_digest,
                },
            )
            connection.execute(
                text(
                    "ALTER TABLE context_fragment ENABLE TRIGGER "
                    "context_fragment_reject_mutation"
                )
            )
    finally:
        migration_engine.dispose()

    response = _resolve(
        _client(
            scenario,
            candidate,
            user_id,
            guarded_runtime_engine,
            query_digest_keyring,
            PostgreSQLVectorCandidateIndex(provider),
            request_id="issue-147-mixed-profile",
        )
    )

    assert response.status_code == 503
    assert response.json() == {"code": "service_unavailable"}
    assert provider.calls == 1


def test_heterogeneous_profile_corpus_refuses_over_http_before_ann_discovery(
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
        label="heterogeneous-profile",
    )
    provider = _RecordingTwinProvider()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            original = connection.execute(
                text(
                    "SELECT embedding::text FROM context_fragment "
                    "WHERE organization_id = :organization_id "
                    "AND revision_id = :revision_id LIMIT 1"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "revision_id": UUID(candidate.revision_ref),
                },
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO context_fragment ("
                    "organization_id, resource_ref, revision_id, fragment_ref, "
                    "ordinal, content, projection_kind, embedding, "
                    "embedding_profile_digest) VALUES ("
                    ":organization_id, :resource_ref, :revision_id, "
                    "'fragment:heterogeneous-profile', 1, "
                    "'heterogeneous profile residual', 'body', "
                    "CAST(:embedding AS vector), :digest)"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": candidate.resource_ref,
                    "revision_id": UUID(candidate.revision_ref),
                    "embedding": original,
                    "digest": QWEN3_EMBEDDING_PROFILE.profile_digest,
                },
            )
    finally:
        migration_engine.dispose()

    response = _resolve(
        _client(
            scenario,
            candidate,
            user_id,
            guarded_runtime_engine,
            query_digest_keyring,
            PostgreSQLVectorCandidateIndex(provider),
            request_id="issue-147-heterogeneous-profile",
        )
    )

    assert response.status_code == 503
    assert response.json() == {"code": "service_unavailable"}
    assert provider.calls == 1


def test_query_embedding_budget_exhaustion_refuses_before_provider_call(
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
        label="query-budget",
    )
    provider = _RecordingTwinProvider()
    client = _client(
        scenario,
        candidate,
        user_id,
        guarded_runtime_engine,
        query_digest_keyring,
        PostgreSQLVectorCandidateIndex(provider),
        request_id="issue-147-query-budget",
    )

    response = client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Context-Request-Id": "issue-147-query-budget",
        },
        json={
            "kind": "acquire",
            "need": {"query": QUERY},
            "packageBudget": {"maxElapsedMs": 1},
        },
    )

    assert response.status_code == 503
    assert response.json() == {"code": "service_unavailable"}
    assert provider.calls == 0


def test_default_composition_keeps_vector_retrieval_not_active() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json()["runtime_delivery"] == "NOT_ACTIVE"
    assert HEALTH_RESPONSE["runtime_delivery"] == "NOT_ACTIVE"
