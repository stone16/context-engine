from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from adapters.http.dogfood import (
    DOGFOOD_AGENT_ENV,
    DOGFOOD_APPLICATION_ENV,
    DOGFOOD_BINDING_ENV,
    DOGFOOD_COMPOSITION_ENV,
    DOGFOOD_COMPOSITION_VALUE,
    DOGFOOD_EMBEDDING_MODEL_DIR_ENV,
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
    create_served_app,
)
from applications.api import main as api_main
from applications.operator_authentication import (
    CONTROL_OPERATOR_OPERATIONS_ENV,
    CONTROL_OPERATOR_SECRET_ENV,
    LOCAL_OPERATOR_TTL,
    LOCAL_RELEASE_GRANT_TTL,
    OPERATOR_ORGANIZATION_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
    LocalOperatorConfiguration,
    LocalReleaseOperatorAuthenticator,
)
from applications.operator_authentication import (
    DOGFOOD_SECRET_ENV as OPERATOR_DOGFOOD_SECRET_ENV,
)
from applications.release_promotion import (
    RELEASE_EVALUATION_SIGNING_KEY_ENV,
    RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV,
    promote_release,
)
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileRootRef,
    OffboardFileSource,
    PrepareFileImport,
    RegisterFileSource,
)
from engine.learning import ReleaseOperatorAuthenticationRejected
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    PostgreSQLReleaseCandidateSnapshotStore,
    PostgreSQLWorkerLeaseIssuer,
    create_database_engine,
)
from engine.runtime.release_lineage import QWEN_VECTOR_INDEX_PROFILE_REF_V1
from engine.supply import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    QWEN3_EMBEDDING_PROFILE,
)
from tests.support.embeddings import QwenEmbeddingTwin
from tests.support.file_imports import (
    NOW,
    ControlAuthenticator,
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    prepare_repeat_file_import,
    run_file_import,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
CONTROL_SECRET = "issue-114-control-operator-secret-0001"
RELEASE_SECRET = "issue-114-release-operator-secret-0001"
DOGFOOD_SECRET = "issue-114-dogfood-runtime-secret-0001"
WORKER_KEY = bytes.fromhex("ab" * 32)
EVALUATION_KEY = bytes.fromhex("bc" * 32)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def release_evidence_file(tmp_path: Path) -> Path:
    path = tmp_path / "release-evidence.json"
    path.write_text(
        json.dumps(
            {
                "budget": {"evidenceDigest": _digest("budget"), "status": "pass"},
                "capabilityCoverageDigest": _digest("capability-coverage"),
                "fixtureDigest": _digest("fixture-corpus"),
                "quality": {
                    "evidenceDigest": _digest("quality"),
                    "status": "pass",
                },
                "reliability": {
                    "evidenceDigest": _digest("reliability"),
                    "status": "pass",
                },
                "security": {
                    "evidenceDigest": _digest("security"),
                    "status": "pass",
                },
                "verificationCommands": ["make check"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _run(
    executable: str,
    arguments: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *arguments],
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def _operator_environment(organization_id: UUID) -> dict[str, str]:
    return {
        **os.environ,
        OPERATOR_ORGANIZATION_ENV: str(organization_id),
        CONTROL_OPERATOR_SECRET_ENV: CONTROL_SECRET,
        RELEASE_OPERATOR_SECRET_ENV: RELEASE_SECRET,
        OPERATOR_DOGFOOD_SECRET_ENV: DOGFOOD_SECRET,
        WORKER_SECRET_ENV: WORKER_KEY.hex(),
        CONTROL_OPERATOR_OPERATIONS_ENV: (
            "register_source,read_source,read_source_progress,"
            "activate_file_change_feed,activate_file_delete_observations,"
            "accept_file_change_page,schedule_file_change_page"
        ),
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV: "1",
        RELEASE_EVALUATION_SIGNING_KEY_ENV: EVALUATION_KEY.hex(),
    }


def _dogfood_environment(
    *,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    runtime_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
) -> dict[str, str]:
    return {
        DOGFOOD_COMPOSITION_ENV: DOGFOOD_COMPOSITION_VALUE,
        DOGFOOD_SECRET_ENV: DOGFOOD_SECRET,
        DOGFOOD_ORGANIZATION_ENV: str(organization_id),
        DOGFOOD_USER_ENV: str(user_id),
        DOGFOOD_MEMBERSHIP_ENV: str(membership_id),
        DOGFOOD_MEMBERSHIP_VERSION_ENV: "1",
        DOGFOOD_PRINCIPAL_ENV: "principal:file-reader",
        DOGFOOD_AGENT_ENV: "agent:dogfood-local:v1",
        DOGFOOD_APPLICATION_ENV: "application:dogfood-local:v1",
        DOGFOOD_BINDING_ENV: "binding:dogfood-local:v1",
        DOGFOOD_EMBEDDING_PROVIDER_ENV: DOGFOOD_EMBEDDING_PROVIDER_VALUE,
        DOGFOOD_EMBEDDING_MODEL_DIR_ENV: "/verified/test-qwen-model",
        "CONTEXT_ENGINE_RUNTIME_ROLE": runtime_configuration.expected_role,
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": (
            runtime_configuration.url.render_as_string(hide_password=False)
        ),
        "CONTEXT_ENGINE_CONTROL_ROLE": control_configuration.expected_role,
        "CONTEXT_ENGINE_CONTROL_DATABASE_URL": (
            control_configuration.url.render_as_string(hide_password=False)
        ),
    }


def _dogfood_configuration(
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
) -> DogfoodConfiguration:
    return DogfoodConfiguration(
        secret=DOGFOOD_SECRET,
        organization_id=organization_id,
        user_id=user_id,
        membership_id=membership_id,
        membership_version=1,
        principal_ref="principal:file-reader",
        agent_version_ref="agent:dogfood-local:v1",
        application_ref="application:dogfood-local:v1",
        authentication_binding_ref="binding:dogfood-local:v1",
        embedding_provider=DOGFOOD_EMBEDDING_PROVIDER_VALUE,
    )


def _seed_release_grant(
    *,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    environment: dict[str, str],
) -> None:
    seeded = _run(
        "context-engine-dogfood-seed",
        [
            "--organization-id",
            str(organization_id),
            "--user-id",
            str(user_id),
            "--membership-id",
            str(membership_id),
            "--provision-release-operator-grant",
        ],
        environment=environment,
    )
    assert "release_operator_grant=ready" in seeded.stdout
    assert seeded.stderr == ""


def _promote(
    organization_id: UUID,
    evidence_file: Path,
    environment: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        "context-engine-control",
        [
            "promote-release",
            "--organization-id",
            str(organization_id),
            "--evidence-file",
            str(evidence_file),
        ],
        environment=environment,
        check=check,
    )


def _user_id(
    migration_configuration: DatabaseConfiguration,
    organization_id: UUID,
    membership_id: UUID,
) -> UUID:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            return cast(
                UUID,
                connection.execute(
                    text(
                        "SELECT user_id FROM membership "
                        "WHERE organization_id = :organization_id "
                        "AND membership_id = :membership_id"
                    ),
                    {
                        "organization_id": organization_id,
                        "membership_id": membership_id,
                    },
                ).scalar_one(),
            )
    finally:
        engine.dispose()


def _offboard_source(
    scenario: FileImportScenario,
    guarded_control_engine: Engine,
) -> None:
    authority = ControlOperatorAuthority(
        ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(guarded_control_engine, clock=lambda: NOW),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.OFFBOARD_FILE_SOURCE,
        request_id="issue-114-offboard-before-promotion",
    ) as call:
        result = control.offboard_file_source(
            call,
            OffboardFileSource(source_ref=scenario.source_ref),
        )
    assert result.source_ref == scenario.source_ref


def _publish_second_source(
    scenario: FileImportScenario,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> tuple[FileImportScenario, str]:
    root_ref = FileRootRef(f"root-second-{scenario.organization_id.hex}")
    root = scenario.root.parent / root_ref.value
    root.mkdir()
    (root / "handbook.md").write_bytes(
        b"# Independent source\n\nThis source remains active.\n"
    )
    authority = ControlOperatorAuthority(
        ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="issue-114-register-second-source",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource(
                "Independent source",
                root_ref,
                "issue-114-independent-source",
            ),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="issue-114-import-second-source",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=scenario.membership_id,
                    membership_version=1,
                ),
                idempotency_key="issue-114-independent-source",
            ),
        )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
    ).issue_file_import_lease(prepared)
    second = FileImportScenario(
        organization_id=scenario.organization_id,
        membership_id=scenario.membership_id,
        receiver=scenario.receiver,
        source_ref=source.source_ref,
        prepared=prepared,
        codec=scenario.codec,
        token=token,
        root_ref=root_ref,
        root=root,
    )
    published = run_file_import(
        second,
        prepared,
        token,
        guarded_worker_engine,
        embedding_provider=QwenEmbeddingTwin(),
    )
    return second, published.candidate_ref.revision_ref


def _clear_offboard_intent(
    migration_configuration: DatabaseConfiguration,
    organization_id: UUID,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE file_source_cleanup_intent DISABLE TRIGGER "
                    "file_source_cleanup_intent_immutable"
                )
            )
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM file_source_cleanup_intent "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE file_source_cleanup_intent ENABLE TRIGGER "
                        "file_source_cleanup_intent_immutable"
                    )
                )
    finally:
        engine.dispose()


def test_promote_release_activates_every_current_revision_and_dogfood_runtime(
    tmp_path: Path,
    release_evidence_file: Path,
    migration_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_learning_engine: Engine,
    guarded_release_operator_engine: Engine,
    guarded_worker_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Dogfood\n\nPromoted through the operator process.\n",
    )
    try:
        assert scenario.token is not None
        published = run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
            embedding_provider=QwenEmbeddingTwin(),
        )
        (scenario.root / "second.md").write_bytes(
            b"# Second note\n\nEvery current revision belongs in the release.\n"
        )
        second_prepared, second_token = prepare_repeat_file_import(
            scenario,
            guarded_control_engine,
            idempotency_key="issue-114-second-note",
            path=FileImportPath("second.md"),
        )
        second_published = run_file_import(
            scenario,
            second_prepared,
            second_token,
            guarded_worker_engine,
            embedding_provider=QwenEmbeddingTwin(),
        )
        retained_source, retained_revision_ref = _publish_second_source(
            scenario,
            guarded_control_engine,
            guarded_worker_engine,
        )
        expected_revision_refs = sorted(
            (
                published.candidate_ref.revision_ref,
                second_published.candidate_ref.revision_ref,
                retained_revision_ref,
            )
        )
        user_id = _user_id(
            migration_configuration,
            scenario.organization_id,
            scenario.membership_id,
        )
        environment = _operator_environment(scenario.organization_id)
        _seed_release_grant(
            organization_id=scenario.organization_id,
            user_id=user_id,
            membership_id=scenario.membership_id,
            environment=environment,
        )
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                grant_lifetime = connection.execute(
                    text(
                        "SELECT expires_at - valid_from "
                        "FROM release_operator_grant "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
            assert grant_lifetime == LOCAL_RELEASE_GRANT_TTL
            assert grant_lifetime > LOCAL_OPERATOR_TTL
        finally:
            migration_engine.dispose()
        dogfood_environment = _dogfood_environment(
            organization_id=scenario.organization_id,
            user_id=user_id,
            membership_id=scenario.membership_id,
            runtime_configuration=runtime_configuration,
            control_configuration=control_configuration,
        )
        with pytest.raises(DogfoodConfigurationUnavailable):
            create_served_app(dogfood_environment, host="127.0.0.1")

        failing_evidence = json.loads(release_evidence_file.read_text())
        failing_evidence["budget"]["status"] = "fail"
        failing_evidence_file = tmp_path / "failing-release-evidence.json"
        failing_evidence_file.write_text(
            json.dumps(failing_evidence, sort_keys=True),
            encoding="utf-8",
        )
        refused_gate = _promote(
            scenario.organization_id,
            failing_evidence_file,
            environment,
            check=False,
        )
        assert refused_gate.returncode != 0
        assert refused_gate.stdout == ""
        assert refused_gate.stderr == "context-engine-control: operation refused\n"

        promoted = _promote(
            scenario.organization_id,
            release_evidence_file,
            environment,
        )
        document = json.loads(promoted.stdout)
        assert document == {
            "activeGeneration": 1,
            "activeRevisionCount": 3,
            "indexProfileRef": QWEN_VECTOR_INDEX_PROFILE_REF_V1,
            "manifestRef": document["manifestRef"],
        }
        assert promoted.stderr == ""

        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                durable = connection.execute(
                    text(
                        "SELECT active.active_generation, "
                        "manifest.index_profile_ref, "
                        "manifest.embedding_profile_document, "
                        "manifest.embedding_profile_digest, "
                        "manifest.active_revision_refs "
                        "FROM active_release_manifest AS active "
                        "JOIN release_manifest AS manifest "
                        "ON manifest.organization_id = active.organization_id "
                        "AND manifest.manifest_ref = active.manifest_ref "
                        "AND manifest.manifest_digest = active.manifest_digest "
                        "WHERE active.organization_id = :organization_id"
                    ),
                    {"organization_id": scenario.organization_id},
                ).one()
                assert tuple(durable) == (
                    1,
                    QWEN_VECTOR_INDEX_PROFILE_REF_V1,
                    QWEN3_EMBEDDING_PROFILE.canonical_document(),
                    QWEN3_EMBEDDING_PROFILE.profile_digest,
                    expected_revision_refs,
                )
                residual = connection.execute(
                    text(
                        "SELECT count(*) FROM context_fragment AS fragment "
                        "JOIN context_resource AS resource "
                        "ON resource.organization_id = fragment.organization_id "
                        "AND resource.resource_ref = fragment.resource_ref "
                        "AND resource.active_revision_id = fragment.revision_id "
                        "WHERE resource.organization_id = :organization_id "
                        "AND fragment.embedding_profile_digest "
                        "IS DISTINCT FROM :embedding_profile_digest"
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "embedding_profile_digest": (
                            QWEN3_EMBEDDING_PROFILE.profile_digest
                        ),
                    },
                ).scalar_one()
                assert residual == 0
        finally:
            migration_engine.dispose()

        served: dict[str, object] = {}
        for name, value in dogfood_environment.items():
            monkeypatch.setenv(name, value)

        def observe(app: object, **kwargs: object) -> None:
            served["app"] = app
            served.update(kwargs)

        monkeypatch.setattr("applications.api.uvicorn.run", observe)
        monkeypatch.setattr(
            "adapters.http.dogfood.LocalQwenEmbeddingProvider",
            lambda _path: QwenEmbeddingTwin(),
        )
        api_main(["--host", "127.0.0.1", "--port", "9123"])
        assert served["host"] == "127.0.0.1"
        assert (
            TestClient(cast(Any, served["app"]))
            .get("/health")
            .json()["runtime_delivery"]
            == "ACTIVE"
        )

        repeated = json.loads(
            _promote(
                scenario.organization_id,
                release_evidence_file,
                environment,
            ).stdout
        )
        assert repeated["activeGeneration"] == 2
        assert repeated["manifestRef"] == document["manifestRef"]
        assert repeated["activeRevisionCount"] == 3

        _offboard_source(scenario, guarded_control_engine)
        configuration = LocalOperatorConfiguration.load(environment)
        assert configuration is not None
        release_identity = LocalReleaseOperatorAuthenticator(
            configuration,
            clock=lambda: datetime.now(UTC),
        ).authenticate(RELEASE_SECRET)
        snapshot = PostgreSQLReleaseCandidateSnapshotStore(
            guarded_release_operator_engine
        ).observe_candidate_snapshot(scenario.organization_id, release_identity)
        assert snapshot.expected_active_generation == 2
        assert snapshot.active_revision_refs == (retained_revision_ref,)
        after_offboard = json.loads(
            _promote(
                scenario.organization_id,
                release_evidence_file,
                environment,
            ).stdout
        )
        assert after_offboard["activeGeneration"] == 3
        assert after_offboard["activeRevisionCount"] == 1
        assert after_offboard["manifestRef"] != document["manifestRef"]
        assert retained_source.source_ref != scenario.source_ref
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                active_refs = connection.execute(
                    text(
                        "SELECT manifest.active_revision_refs "
                        "FROM active_release_manifest AS active "
                        "JOIN release_manifest AS manifest "
                        "ON manifest.organization_id = active.organization_id "
                        "AND manifest.manifest_ref = active.manifest_ref "
                        "AND manifest.manifest_digest = active.manifest_digest "
                        "WHERE active.organization_id = :organization_id"
                    ),
                    {"organization_id": scenario.organization_id},
                ).scalar_one()
        finally:
            migration_engine.dispose()
        assert active_refs == [retained_revision_ref]
    finally:
        clear_test_runtime_release(scenario.organization_id)
        _clear_offboard_intent(
            migration_configuration,
            scenario.organization_id,
        )
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


@pytest.fixture
def empty_release_organization(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[UUID, UUID, UUID]]:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    environment = _operator_environment(organization_id)
    try:
        _run(
            "context-engine-dogfood-seed",
            [
                "--organization-id",
                str(organization_id),
                "--user-id",
                str(user_id),
                "--membership-id",
                str(membership_id),
                "--provision-release-operator-grant",
            ],
            environment=environment,
        )
        yield organization_id, user_id, membership_id
    finally:
        clear_test_runtime_release(organization_id)
        engine = create_database_engine(migration_configuration)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM membership "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text("DELETE FROM organization WHERE organization_id = :org"),
                    {"org": organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM user_account WHERE user_id = :user_id "
                        "AND NOT EXISTS (SELECT 1 FROM membership "
                        "WHERE membership.user_id = user_account.user_id)"
                    ),
                    {"user_id": user_id},
                )
        finally:
            engine.dispose()


def test_promote_release_refuses_empty_corpus_and_control_credential(
    release_evidence_file: Path,
    empty_release_organization: tuple[UUID, UUID, UUID],
    migration_configuration: DatabaseConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id, _user_id_value, _membership_id = empty_release_organization
    environment = _operator_environment(organization_id)
    empty = _promote(
        organization_id,
        release_evidence_file,
        environment,
        check=False,
    )
    assert empty.returncode != 0
    assert empty.stdout == ""
    assert empty.stderr == "context-engine-control: operation refused\n"

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            counts = tuple(
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM release_candidate "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM active_release_manifest "
                        " WHERE organization_id = :organization_id), "
                        "(SELECT count(*) FROM release_promotion_audit "
                        " WHERE organization_id = :organization_id)"
                    ),
                    {"organization_id": organization_id},
                ).one()
            )
        assert counts == (0, 0, 0)
    finally:
        migration_engine.dispose()

    configuration = LocalOperatorConfiguration.load(environment)
    assert configuration is not None
    authorities = configuration.authorities()
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(RELEASE_OPERATOR_SECRET_ENV, CONTROL_SECRET)
    with pytest.raises(ReleaseOperatorAuthenticationRejected):
        promote_release(
            organization_id=organization_id,
            evidence_file=release_evidence_file,
            configuration=configuration,
            authorities=authorities,
        )


def test_test_release_helper_replaces_stale_real_corpus_with_exact_sentinel(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    """An emptied corpus never reuses a Release that selected former content."""

    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Retired corpus\n\nThis Revision will be offboarded.\n",
    )
    try:
        assert scenario.token is not None
        published = run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
        )
        initial = ensure_test_runtime_release(scenario.organization_id)
        assert initial.active_revision_refs == (
            published.candidate_ref.revision_ref,
        )

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
                        "resource_ref": published.candidate_ref.resource_ref,
                    },
                )
        finally:
            migration_engine.dispose()
        after_empty = ensure_test_runtime_release(scenario.organization_id)

        assert after_empty.active_revision_refs != initial.active_revision_refs
        assert len(after_empty.active_revision_refs) == 1
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                sentinel_source_ref = connection.execute(
                    text(
                        "SELECT resource.source_ref "
                        "FROM context_resource AS resource "
                        "WHERE resource.organization_id = :organization_id "
                        "AND resource.active_revision_id = :revision_id"
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "revision_id": UUID(after_empty.active_revision_refs[0]),
                    },
                ).scalar_one()
            assert sentinel_source_ref == "source:test-release-sentinel"
        finally:
            migration_engine.dispose()
    finally:
        clear_test_runtime_release(scenario.organization_id)
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


@pytest.mark.parametrize("residual_kind", ["prior_profile", "missing_vector"])
def test_promote_release_refuses_partial_reembed_without_advancing_pointer(
    tmp_path: Path,
    release_evidence_file: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    residual_kind: str,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"# Partial re-embed\n\nPromotion must refuse residuals.\n",
    )
    try:
        assert scenario.token is not None
        published = run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
            embedding_provider=QwenEmbeddingTwin(),
        )
        user_id = _user_id(
            migration_configuration,
            scenario.organization_id,
            scenario.membership_id,
        )
        environment = _operator_environment(scenario.organization_id)
        _seed_release_grant(
            organization_id=scenario.organization_id,
            user_id=user_id,
            membership_id=scenario.membership_id,
            environment=environment,
        )
        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE context_fragment DISABLE TRIGGER "
                        "context_fragment_reject_mutation"
                    )
                )
                if residual_kind == "prior_profile":
                    connection.execute(
                        text(
                            "UPDATE context_fragment "
                            "SET embedding_profile_digest = :digest "
                            "WHERE organization_id = :organization_id "
                            "AND revision_id = :revision_id"
                        ),
                        {
                            "digest": (
                                DETERMINISTIC_TWIN_EMBEDDING_PROFILE.profile_digest
                            ),
                            "organization_id": scenario.organization_id,
                            "revision_id": UUID(published.candidate_ref.revision_ref),
                        },
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE context_fragment SET embedding = NULL, "
                            "embedding_profile_digest = NULL "
                            "WHERE organization_id = :organization_id "
                            "AND revision_id = :revision_id"
                        ),
                        {
                            "organization_id": scenario.organization_id,
                            "revision_id": UUID(published.candidate_ref.revision_ref),
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

        refused = _promote(
            scenario.organization_id,
            release_evidence_file,
            environment,
            check=False,
        )
        assert refused.returncode != 0
        assert refused.stdout == ""
        assert refused.stderr == "context-engine-control: operation refused\n"

        migration_engine = create_database_engine(migration_configuration)
        try:
            with migration_engine.connect() as connection:
                counts = tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "(SELECT count(*) FROM active_release_manifest "
                            " WHERE organization_id = :organization_id), "
                            "(SELECT count(*) FROM release_promotion_audit "
                            " WHERE organization_id = :organization_id)"
                        ),
                        {"organization_id": scenario.organization_id},
                    ).one()
                )
            assert counts == (0, 0)
        finally:
            migration_engine.dispose()
    finally:
        clear_test_runtime_release(scenario.organization_id)
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_learning_role_cannot_read_corpus_or_forge_candidate_snapshot(
    empty_release_organization: tuple[UUID, UUID, UUID],
    guarded_learning_engine: Engine,
    guarded_release_operator_engine: Engine,
) -> None:
    organization_id, _user_id_value, _membership_id = empty_release_organization
    for table_name in ("context_resource", "context_source"):
        with (
            guarded_learning_engine.connect() as connection,
            pytest.raises(DBAPIError) as denied,
        ):
            connection.execute(text(f"SELECT * FROM {table_name}"))  # noqa: S608
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"

    environment = _operator_environment(organization_id)
    configuration = LocalOperatorConfiguration.load(environment)
    assert configuration is not None
    release_identity = LocalReleaseOperatorAuthenticator(
        configuration,
        clock=lambda: datetime.now(UTC),
    ).authenticate(RELEASE_SECRET)
    with (
        guarded_learning_engine.connect() as connection,
        pytest.raises(DBAPIError) as denied,
    ):
        connection.execute(
            text(
                "SELECT * FROM public."
                "context_release_observe_candidate_snapshot("
                ":organization_id, :operator_ref, :binding_ref, "
                ":authority_ref, :authority_digest)"
            ),
            {
                "organization_id": organization_id,
                "operator_ref": release_identity.operator_ref,
                "binding_ref": release_identity.authentication_binding_ref,
                "authority_ref": release_identity.authority_ref,
                "authority_digest": release_identity.authority_digest,
            },
        ).one_or_none()
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"

    for table_name in ("context_resource", "context_source"):
        with (
            guarded_release_operator_engine.connect() as connection,
            pytest.raises(DBAPIError) as denied,
        ):
            connection.execute(text(f"SELECT * FROM {table_name}"))  # noqa: S608
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"

    with guarded_release_operator_engine.begin() as connection:
        forged = connection.execute(
            text(
                "SELECT * FROM public.context_release_observe_candidate_snapshot("
                ":organization_id, :operator_ref, :binding_ref, "
                ":authority_ref, :authority_digest)"
            ),
            {
                "organization_id": organization_id,
                "operator_ref": "operator:forged",
                "binding_ref": "binding:forged",
                "authority_ref": "authority:forged",
                "authority_digest": _digest("forged"),
            },
        ).one_or_none()
    assert forged is None
    snapshot = PostgreSQLReleaseCandidateSnapshotStore(
        guarded_release_operator_engine
    ).observe_candidate_snapshot(organization_id, release_identity)
    assert snapshot.active_revision_refs == ()


def test_release_credential_is_refused_by_every_exposed_control_operation(
    empty_release_organization: tuple[UUID, UUID, UUID],
) -> None:
    organization_id, _user_id_value, _membership_id = empty_release_organization
    environment = _operator_environment(organization_id)
    configuration = LocalOperatorConfiguration.load(environment)
    assert configuration is not None
    authority = configuration.authorities().control
    exposed_operations = (
        ControlOperation.REGISTER_SOURCE,
        ControlOperation.READ_SOURCE,
        ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        ControlOperation.IMPORT_FILE,
        ControlOperation.OFFBOARD_FILE_SOURCE,
        ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        ControlOperation.READ_SOURCE_PROGRESS,
        ControlOperation.TOMBSTONE_FILE_RESOURCE,
    )
    for operation in exposed_operations:
        refused = authority.authorize(
            opaque_credential=RELEASE_SECRET,
            operation=operation,
            request_id=f"issue-114-release-credential-{operation.value}",
        )
        with pytest.raises(ControlOperatorAuthenticationRejected), refused:
            raise AssertionError("release credential entered Control authority")
