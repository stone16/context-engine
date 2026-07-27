from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

import engine.persistence.membership_context as membership_context_module
from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.http.dogfood import (
    DOGFOOD_AGENT_ENV,
    DOGFOOD_APPLICATION_ENV,
    DOGFOOD_BINDING_ENV,
    DOGFOOD_COMPOSITION_ENV,
    DOGFOOD_COMPOSITION_VALUE,
    DOGFOOD_EMBEDDING_PROVIDER_ENV,
    DOGFOOD_EMBEDDING_PROVIDER_VALUE,
    DOGFOOD_MEMBERSHIP_ENV,
    DOGFOOD_MEMBERSHIP_VERSION_ENV,
    DOGFOOD_ORGANIZATION_ENV,
    DOGFOOD_PRINCIPAL_ENV,
    DOGFOOD_SECRET_ENV,
    DOGFOOD_USER_ENV,
    DogfoodConfiguration,
    DogfoodConfigurationUnavailable,
    create_dogfood_app,
    create_served_app,
)
from adapters.pgvector import DEFAULT_VECTOR_CANDIDATE_LIMIT
from applications.api import main as api_main
from applications.dogfood_evaluation import (
    EvidenceIdentity,
    GoldenCase,
    GoldenExpectation,
    GoldenSet,
    evaluate_golden_set,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLAccessPolicyControl,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.release_lineage import (
    DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
    DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
    INDEX_PROFILE_DIGEST_V0,
    INDEX_PROFILE_REF_V0,
)
from tests.integration.test_zz_file_revision_replacement import _scenario_user_id
from tests.support.file_imports import (
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
SECRET = "dogfood-secret-with-at-least-thirty-two-bytes"
TARGET_TEXT = "Dogfood delivery reaches the authorized target."
QUERY = "Which dogfood delivery target is authorized?"


def _configuration(
    scenario: FileImportScenario,
    user_id: UUID,
) -> DogfoodConfiguration:
    return DogfoodConfiguration(
        secret=SECRET,
        organization_id=scenario.organization_id,
        user_id=user_id,
        membership_id=scenario.membership_id,
        membership_version=1,
        principal_ref="principal:file-reader",
        agent_version_ref="agent:dogfood-local:v1",
        application_ref="application:dogfood-local:v1",
        authentication_binding_ref="binding:dogfood-local:v1",
        embedding_provider=DOGFOOD_EMBEDDING_PROVIDER_VALUE,
    )


def _environment(
    configuration: DogfoodConfiguration,
    runtime_configuration: DatabaseConfiguration,
) -> dict[str, str]:
    return {
        DOGFOOD_COMPOSITION_ENV: DOGFOOD_COMPOSITION_VALUE,
        DOGFOOD_SECRET_ENV: configuration.secret,
        DOGFOOD_ORGANIZATION_ENV: str(configuration.organization_id),
        DOGFOOD_USER_ENV: str(configuration.user_id),
        DOGFOOD_MEMBERSHIP_ENV: str(configuration.membership_id),
        DOGFOOD_MEMBERSHIP_VERSION_ENV: str(configuration.membership_version),
        DOGFOOD_PRINCIPAL_ENV: configuration.principal_ref,
        DOGFOOD_AGENT_ENV: configuration.agent_version_ref,
        DOGFOOD_APPLICATION_ENV: configuration.application_ref,
        DOGFOOD_BINDING_ENV: configuration.authentication_binding_ref,
        DOGFOOD_EMBEDDING_PROVIDER_ENV: configuration.embedding_provider,
        "CONTEXT_ENGINE_RUNTIME_ROLE": runtime_configuration.expected_role,
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": (
            runtime_configuration.url.render_as_string(hide_password=False)
        ),
    }


def _publish(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    *,
    dogfood_index_profile: bool = True,
) -> tuple[FileImportScenario, UUID, CandidateRef]:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=f"# Dogfood\n\n{TARGET_TEXT}\n".encode(),
    )
    request.addfinalizer(
        lambda: _delete(migration_configuration, scenario.organization_id)
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
        index_profile_ref=(
            DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1
            if dogfood_index_profile
            else INDEX_PROFILE_REF_V0
        ),
        index_profile_digest=(
            DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1
            if dogfood_index_profile
            else INDEX_PROFILE_DIGEST_V0
        ),
    )
    return (
        scenario,
        _scenario_user_id(scenario, migration_configuration),
        published.candidate_ref,
    )


def _delete(
    migration_configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    clear_test_runtime_release(organization_id)
    delete_file_import_scenario(migration_configuration, organization_id)


def _add_policy_out_of_scope_distractors(
    migration_configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
) -> None:
    embedding = DeterministicEmbeddingTwin().embed((QUERY,))[0]
    parameters = [
        {
            "organization_id": scenario.organization_id,
            "membership_id": scenario.membership_id,
            "resource_ref": f"resource:dogfood-distractor:{ordinal:03d}",
            "source_ref": str(scenario.source_ref.value),
            "revision_id": uuid4(),
            "fragment_ref": f"fragment:dogfood-distractor:{ordinal:03d}",
            "embedding": "[" + ",".join(repr(value) for value in embedding) + "]",
        }
        for ordinal in range(DEFAULT_VECTOR_CANDIDATE_LIMIT + 4)
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
                        embedding
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, :fragment_ref, 'body',
                        CAST(:embedding AS vector)
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
                        :organization_id, :resource_ref,
                        'principal:file-reader', 1, 'allowed', NULL
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


def _strictly_closer_distractor_count(
    migration_configuration: DatabaseConfiguration,
    scenario: FileImportScenario,
    target: CandidateRef,
) -> int:
    query_embedding = DeterministicEmbeddingTwin().embed((QUERY,))[0]
    encoded_embedding = "[" + ",".join(repr(value) for value in query_embedding) + "]"
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM context_fragment AS distractor
                        WHERE distractor.organization_id = :organization_id
                          AND distractor.fragment_ref LIKE
                              'fragment:dogfood-distractor:%'
                          AND distractor.embedding <=> CAST(:embedding AS vector)
                              < (
                                SELECT target.embedding <=>
                                       CAST(:embedding AS vector)
                                FROM context_fragment AS target
                                WHERE target.organization_id = :organization_id
                                  AND target.resource_ref = :target_resource_ref
                                  AND target.revision_id = :target_revision_id
                                  AND target.fragment_ref = :target_fragment_ref
                              )
                        """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "embedding": encoded_embedding,
                        "target_resource_ref": target.resource_ref,
                        "target_revision_id": UUID(target.revision_ref),
                        "target_fragment_ref": target.fragment_ref,
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _resolve(client: TestClient, secret: str = SECRET) -> Any:
    return client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {secret}",
            "X-Context-Request-Id": f"dogfood-{uuid4()}",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )


class _PublicTestClientCaller:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def acquire(self, *, query: str, request_id: str) -> dict[str, object]:
        response = self._client.post(
            "/v0/resolve",
            headers={
                "Authorization": f"Bearer {SECRET}",
                "X-Context-Request-Id": request_id,
            },
            json={"kind": "acquire", "need": {"query": query}},
        )
        assert response.status_code == 200
        return cast(dict[str, object], response.json())


@pytest.mark.security_evidence(id="RUNTIME-DOGFOOD-CARRIER-102", layer="runtime")
def test_dogfood_served_composition_delivers_release_scoped_file_evidence_before_limit(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, user_id, target = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    _add_policy_out_of_scope_distractors(migration_configuration, scenario)
    assert (
        _strictly_closer_distractor_count(
            migration_configuration,
            scenario,
            target,
        )
        > DEFAULT_VECTOR_CANDIDATE_LIMIT
    )
    configuration = _configuration(scenario, user_id)
    served: dict[str, object] = {}
    for name, value in _environment(configuration, runtime_configuration).items():
        monkeypatch.setenv(name, value)

    def observe(app: object, **kwargs: object) -> None:
        served["app"] = app
        served.update(kwargs)

    monkeypatch.setattr("applications.api.uvicorn.run", observe)
    api_main(["--host", "127.0.0.1", "--port", "9123"])
    client = TestClient(cast(Any, served["app"]))

    assert client.get("/health").json()["runtime_delivery"] == "ACTIVE"
    assert served["host"] == "127.0.0.1"
    response = _resolve(client)

    assert response.status_code == 200
    package = cast(dict[str, Any], response.json()["package"])
    assert [block["text"] for block in package["blocks"]] == [TARGET_TEXT]
    assert len(package["evidence"]) == 1
    assert package["evidence"][0]["revisionRef"] == target.revision_ref
    assert "dogfood-distractor" not in response.text
    assert package["budgetUsage"] == {
        "tokens": len(TARGET_TEXT.encode()),
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }


def test_dogfood_rejects_an_active_release_with_an_unbound_embedding_profile(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, user_id, _target = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
        dogfood_index_profile=False,
    )
    configuration = _configuration(scenario, user_id)

    with pytest.raises(DogfoodConfigurationUnavailable):
        create_served_app(
            _environment(configuration, runtime_configuration),
            host="127.0.0.1",
        )


def test_dogfood_evaluator_scores_real_public_resolve_evidence(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, user_id, target = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    configuration = _configuration(scenario, user_id)
    client = TestClient(
        create_dogfood_app(
            configuration,
            _environment(configuration, runtime_configuration),
            host="127.0.0.1",
        )
    )
    identity = EvidenceIdentity(
        source_ref=target.source_ref,
        resource_ref=target.resource_ref,
        revision_ref=target.revision_ref,
        fragment_ref=target.fragment_ref,
    )
    golden_set = GoldenSet(
        name="integration-maintainer-notes-v0",
        cases=tuple(
            GoldenCase(
                case_ref=f"integration-{index:02d}",
                query=QUERY,
                expected_evidence=(
                    GoldenExpectation(path="handbook.md", identity=identity),
                ),
            )
            for index in range(20)
        ),
    )

    report = evaluate_golden_set(
        golden_set,
        _PublicTestClientCaller(client),
    )

    quality = report["quality"]
    assert isinstance(quality, dict)
    assert quality["casePassRate"] == 1.0
    assert report["reliability"] == {"status": "not-evaluated"}
    assert report["budget"] == {"status": "not-evaluated"}

    control_engine = create_database_engine(control_configuration)
    try:
        PostgreSQLAccessPolicyControl(control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=scenario.organization_id,
                resource_ref=target.resource_ref,
                principal_ref="principal:file-reader",
                expected_access_version=1,
            )
        )
    finally:
        control_engine.dispose()
    regressed = evaluate_golden_set(
        golden_set,
        _PublicTestClientCaller(client),
    )
    regressed_quality = regressed["quality"]
    assert isinstance(regressed_quality, dict)
    assert regressed_quality["casePassRate"] == 0.0
    recall = regressed_quality["evidenceRecall"]
    assert isinstance(recall, dict)
    assert recall["value"] == 0.0


@pytest.mark.security_evidence(id="RUNTIME-DOGFOOD-AUTH-102", layer="runtime")
def test_dogfood_secret_and_membership_fail_closed_without_secret_retention(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenario, user_id, _revision_ref = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    configuration = _configuration(scenario, user_id)
    client = TestClient(
        create_dogfood_app(
            configuration,
            _environment(configuration, runtime_configuration),
            host="127.0.0.1",
        )
    )
    successful = _resolve(client)
    assert successful.status_code == 200
    assert successful.json()["package"]["evidence"]

    with caplog.at_level(logging.DEBUG):
        responses = (
            client.post(
                "/v0/resolve",
                headers={"X-Context-Request-Id": "dogfood-absent"},
                json={"kind": "acquire", "need": {"query": QUERY}},
            ),
            _resolve(client, "wrong-dogfood-secret"),
            _resolve(client, SECRET[:-1]),
        )
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"code": "authentication_failed"}
        assert SECRET not in response.text

    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE membership
                    SET status = 'revoked'
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            )
    finally:
        engine.dispose()
    revoked = _resolve(client)
    assert revoked.status_code == 401
    assert revoked.json() == {"code": "authentication_failed"}
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE membership
                    SET status = 'active',
                        valid_until = clock_timestamp() - interval '1 second'
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            )
    finally:
        engine.dispose()
    expired = _resolve(client)
    assert expired.status_code == 401
    assert expired.json() == {"code": "authentication_failed"}
    assert SECRET not in caplog.text

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            for table_name in ("context_run", "decision_audit"):
                values = connection.execute(
                    text(f"SELECT row_to_json(row)::text FROM {table_name} AS row")
                ).scalars()
                assert all(SECRET not in value for value in values)
    finally:
        engine.dispose()


@pytest.mark.security_evidence(id="RUNTIME-DOGFOOD-EPOCH-102", layer="runtime")
def test_dogfood_mid_resolve_policy_epoch_change_vetoes_stale_evidence(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, user_id, target = _publish(
        request,
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    configuration = _configuration(scenario, user_id)
    client = TestClient(
        create_dogfood_app(
            configuration,
            _environment(configuration, runtime_configuration),
            host="127.0.0.1",
        )
    )
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
    control_engine = create_database_engine(control_configuration)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_resolve, client)
            assert final_read_reached.wait(timeout=10)
            epoch = PostgreSQLAccessPolicyControl(control_engine).change_access(
                ResourceAccessRevocation(
                    organization_id=scenario.organization_id,
                    resource_ref=target.resource_ref,
                    principal_ref="principal:file-reader",
                    expected_access_version=1,
                )
            )
            assert epoch.value == 2
            release_final_read.set()
            response = pending.result(timeout=10)
    finally:
        release_final_read.set()
        control_engine.dispose()

    assert response.status_code == 200
    package = response.json()["package"]
    assert package["blocks"] == package["evidence"] == []
    assert package["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    assert TARGET_TEXT not in response.text
    assert reads == 3


def test_dogfood_seed_cli_creates_one_idempotent_current_membership(
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver_id = uuid4()
    command = (
        "context-engine-dogfood-seed",
        "--organization-id",
        str(organization_id),
        "--user-id",
        str(user_id),
        "--membership-id",
        str(membership_id),
        "--file-import-service-principal-id",
        str(receiver_id),
    )
    engine = create_database_engine(migration_configuration)
    try:
        first = subprocess.run(command, check=True, capture_output=True, text=True)
        with engine.connect() as connection:
            first_row = connection.execute(
                text(
                    """
                    SELECT user_id, status, membership_version, valid_from,
                           valid_until, xmin::text,
                           (
                             SELECT count(*)
                             FROM service_principal
                             WHERE organization_id = :organization_id
                               AND service_principal_id = :receiver_id
                               AND workload = 'supply.file-import'
                               AND worker_audience = 'context-engine-worker'
                               AND operation = 'file.import'
                               AND enabled IS TRUE
                           ) AS receivers
                    FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "receiver_id": receiver_id,
                },
            ).one()
        second = subprocess.run(command, check=True, capture_output=True, text=True)
        assert first.stdout == second.stdout
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT user_id, status, membership_version, valid_from,
                           valid_until, xmin::text,
                           (
                             SELECT count(*)
                             FROM service_principal
                             WHERE organization_id = :organization_id
                               AND service_principal_id = :receiver_id
                               AND workload = 'supply.file-import'
                               AND worker_audience = 'context-engine-worker'
                               AND operation = 'file.import'
                               AND enabled IS TRUE
                           ) AS receivers
                    FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "receiver_id": receiver_id,
                },
            ).one()
        assert row == first_row
        assert tuple(row)[:3] == (user_id, "active", 1)
        assert row.valid_until is None
        assert row.receivers == 1
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE membership
                    SET valid_from = statement_timestamp() + interval '1 day'
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                },
            )
        future = subprocess.run(command, check=False, capture_output=True, text=True)
        assert future.returncode != 0
        assert "dogfood identity ready" not in future.stdout
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM service_principal
                    WHERE organization_id = :organization_id
                      AND service_principal_id = :receiver_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver_id,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                },
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        engine.dispose()


@pytest.mark.parametrize(
    ("workload", "operation", "enabled"),
    (
        ("supply.file-import", "file.import", False),
        ("supply.noop", "noop.complete", True),
    ),
    ids=("disabled-exact-receiver", "conflicting-receiver-binding"),
)
def test_dogfood_seed_cli_rolls_back_when_file_import_receiver_conflicts(
    migration_configuration: DatabaseConfiguration,
    workload: str,
    operation: str,
    enabled: bool,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver_id = uuid4()
    command = (
        "context-engine-dogfood-seed",
        "--organization-id",
        str(organization_id),
        "--user-id",
        str(user_id),
        "--membership-id",
        str(membership_id),
        "--file-import-service-principal-id",
        str(receiver_id),
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_id)
                    """
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id, service_principal_id, workload,
                        worker_audience, operation, enabled
                    ) VALUES (
                        :organization_id, :receiver_id, :workload,
                        'context-engine-worker', :operation, :enabled
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver_id,
                    "workload": workload,
                    "operation": operation,
                    "enabled": enabled,
                },
            )
        with engine.connect() as connection:
            before = connection.execute(
                text(
                    """
                    SELECT organization.xmin::text AS organization_xmin,
                           principal.xmin::text AS receiver_xmin,
                           principal.workload,
                           principal.worker_audience,
                           principal.operation,
                           principal.enabled
                    FROM organization
                    JOIN service_principal AS principal
                      USING (organization_id)
                    WHERE organization_id = :organization_id
                      AND principal.service_principal_id = :receiver_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver_id,
                },
            ).one()

        refused = subprocess.run(command, check=False, capture_output=True, text=True)

        assert refused.returncode != 0
        assert "dogfood identity ready" not in refused.stdout
        with engine.connect() as connection:
            after = connection.execute(
                text(
                    """
                    SELECT organization.xmin::text AS organization_xmin,
                           principal.xmin::text AS receiver_xmin,
                           principal.workload,
                           principal.worker_audience,
                           principal.operation,
                           principal.enabled
                    FROM organization
                    JOIN service_principal AS principal
                      USING (organization_id)
                    WHERE organization_id = :organization_id
                      AND principal.service_principal_id = :receiver_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver_id,
                },
            ).one()
            attempted_identity_rows = connection.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM user_account
                            WHERE user_id = :user_id
                        ) AS users,
                        (
                            SELECT count(*)
                            FROM membership
                            WHERE organization_id = :organization_id
                              AND membership_id = :membership_id
                        ) AS memberships
                    """
                ),
                {
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "membership_id": membership_id,
                },
            ).one()
        assert after == before
        assert attempted_identity_rows == (0, 0)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM service_principal
                    WHERE organization_id = :organization_id
                      AND service_principal_id = :receiver_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "receiver_id": receiver_id,
                },
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        engine.dispose()
