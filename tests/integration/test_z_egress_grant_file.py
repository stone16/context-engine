from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn import Config, Server

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from bot_delivery.egress import (
    DeterministicModelGatewaySpy,
    ModelEgressBoundary,
    prepare_authorized_model_input,
)
from engine.control import (
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ChangeLimit,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    ExecuteFileDeleteObservation,
    FileChangeControlProofs,
    FileChangeProviderProofs,
    FileChangeSource,
    InitialScan,
    ProviderOk,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLAccessPolicyControl,
    PostgreSQLControlStore,
    PostgreSQLDeliveryEvidenceIssuerPort,
    PostgreSQLEgressGrantRedemptionAuthority,
    PostgreSQLMembershipAuthority,
    PublishedFileImport,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.runtime.citation import CitationOpenProfile
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.contracts import Resolved
from engine.runtime.delivery_evidence import (
    DeliveryEvidenceProfile,
    PrivateDeliveryEvidenceIssue,
    PrivateDeliveryEvidenceIssuer,
    private_delivery_audience_digest,
)
from engine.runtime.egress import (
    EgressGrantNotAvailable,
    ModelEgressGrant,
    ModelEgressProfile,
    direct_egress_audience_digest,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    NOW,
    _ControlAuthenticator,
    _ExactScopeAuthority,
    _FileImportScenario,
    _OrganizationAuthority,
    _prepare_file_import_scenario,
    _RuntimeAuthenticator,
)
from tests.integration.test_zz_file_resource_tombstone import _tombstone
from tests.integration.test_zz_file_source_offboarding import _offboard
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)
from tests.support.security_gate import record_security_oracles

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
SDK_PROCESS_TIMEOUT_SECONDS = 120
SDK_STANDARD_HTTP_HEADERS = frozenset(
    {
        b"accept",
        b"accept-encoding",
        b"accept-language",
        b"connection",
        b"content-length",
        b"content-type",
        b"host",
        b"sec-fetch-mode",
        b"user-agent",
    }
)


class _SdkTransportObserver:
    def __init__(
        self,
        app: ASGIApp,
        *,
        before_resolve: Callable[[tuple[tuple[bytes, bytes], ...]], None]
        | None = None,
    ) -> None:
        self._app = app
        self._before_resolve = before_resolve
        self.requests: list[tuple[tuple[bytes, bytes], ...]] = []

    def set_before_resolve(
        self,
        callback: Callable[[tuple[tuple[bytes, bytes], ...]], None] | None,
    ) -> None:
        self._before_resolve = callback

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/v0/resolve"
        ):
            headers = tuple(scope["headers"])
            self.requests.append(headers)
            if self._before_resolve is not None:
                self._before_resolve(headers)
        await self._app(scope, receive, send)


def _assert_sdk_transport_headers(
    headers: tuple[tuple[bytes, bytes], ...],
    *,
    authentication: bytes,
    delivery_evidence_ref: bytes | None,
    request_id: bytes = b"file-egress-sdk-http",
) -> None:
    observed: dict[bytes, list[bytes]] = {}
    for name, value in headers:
        observed.setdefault(name.lower(), []).append(value)

    expected_context_headers = {
        b"x-context-request-id": [request_id],
    }
    if delivery_evidence_ref is not None:
        expected_context_headers[b"x-context-delivery-evidence-ref"] = [
            delivery_evidence_ref
        ]
    assert observed[b"authorization"] == [authentication]
    assert {
        name: values
        for name, values in observed.items()
        if name.startswith(b"x-context-")
    } == expected_context_headers
    assert set(observed) <= (
        SDK_STANDARD_HTTP_HEADERS | {b"authorization"} | set(expected_context_headers)
    )


class _SdkRuntimeAuthenticator:
    def __init__(
        self,
        organization_id: UUID,
        user_id: UUID,
        membership_id: UUID,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id
        self.membership_id = membership_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential not in {"runtime-secret", "runtime-direct-secret"}:
            raise AssertionError("unexpected SDK integration credential")
        return _RuntimeAuthenticator(
            self.organization_id,
            self.user_id,
            self.membership_id,
            token=opaque_credential,
            private_delivery=opaque_credential == "runtime-secret",
        ).authenticate(opaque_credential)


class _TwoReaderAuthenticator:
    def __init__(
        self,
        identities: dict[str, tuple[UUID, UUID, UUID]],
        *,
        principal_refs: dict[str, str] | None = None,
    ) -> None:
        self.identities = identities
        self.principal_refs = principal_refs or {}

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        organization_id, user_id, membership_id = self.identities[opaque_credential]
        context = _RuntimeAuthenticator(
            organization_id,
            user_id,
            membership_id,
            token=opaque_credential,
        ).authenticate(opaque_credential)
        principal_ref = self.principal_refs.get(opaque_credential)
        if principal_ref is not None:
            object.__setattr__(context, "principal_ref", principal_ref)
        return context


def _unused_port() -> int:
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with closing(socket.socket()) as probe:
            probe.settimeout(0.1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("live SDK fixture API did not become reachable")


def _run_sdk_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=SDK_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise AssertionError(
            f"SDK process failed: {command!r}\n"
            f"stdout:\n{error.stdout}\nstderr:\n{error.stderr}"
        ) from None
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            f"SDK process timed out: {command!r}\n"
            f"stdout:\n{error.stdout!r}\nstderr:\n{error.stderr!r}"
        ) from None


def _pack_and_install_sdk(consumer_root: Path) -> None:
    for script in ("check:generated", "typecheck", "build", "test:package"):
        _run_sdk_process(
            ["npm", "--prefix", "sdk/typescript", "run", script],
            cwd=ROOT,
        )
    artifact_root = consumer_root / "artifact"
    artifact_root.mkdir()
    pack = _run_sdk_process(
        [
            "npm",
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(artifact_root),
        ],
        cwd=ROOT / "sdk/typescript",
    )
    report = json.loads(pack.stdout)
    artifact_name = report[0]["filename"]
    for script in ("typecheck", "build", "test:runtime"):
        _run_sdk_process(
            ["npm", "--prefix", "bot_delivery/typescript", "run", script],
            cwd=ROOT,
        )
    bot_pack = _run_sdk_process(
        [
            "npm",
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(artifact_root),
        ],
        cwd=ROOT / "bot_delivery/typescript",
    )
    bot_report = json.loads(bot_pack.stdout)
    bot_artifact_name = bot_report[0]["filename"]
    for script in ("typecheck", "build", "test:runtime"):
        _run_sdk_process(
            ["npm", "--prefix", "action_plane/typescript", "run", script],
            cwd=ROOT,
        )
    action_pack = _run_sdk_process(
        [
            "npm",
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(artifact_root),
        ],
        cwd=ROOT / "action_plane/typescript",
    )
    action_report = json.loads(action_pack.stdout)
    action_artifact_name = action_report[0]["filename"]
    bot_lock = json.loads(
        (ROOT / "bot_delivery/typescript/package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    local_production_dependencies: dict[str, str] = {}
    local_optional_dependencies: dict[str, str] = {}
    for dependency_path, metadata in bot_lock["packages"].items():
        if not dependency_path.startswith("node_modules/") or metadata.get("dev"):
            continue
        dependency_root = ROOT / "bot_delivery/typescript" / dependency_path
        dependency_document = json.loads(
            (dependency_root / "package.json").read_text(encoding="utf-8")
        )
        target = (
            local_optional_dependencies
            if metadata.get("optional")
            else local_production_dependencies
        )
        target[dependency_document["name"]] = f"file:{dependency_root}"
    (consumer_root / "package.json").write_text(
        json.dumps(
            {
                "name": "context-engine-live-sdk-consumer",
                "private": True,
                "type": "module",
                "dependencies": {
                    "@context-engine/action-plane": (
                        f"file:{artifact_root / action_artifact_name}"
                    ),
                    "@context-engine/bot-delivery": (
                        f"file:{artifact_root / bot_artifact_name}"
                    ),
                    "@context-engine/resolve-sdk": (
                        f"file:{artifact_root / artifact_name}"
                    ),
                    **local_production_dependencies,
                },
                "optionalDependencies": local_optional_dependencies,
            }
        ),
        encoding="utf-8",
    )
    _run_sdk_process(
        ["npm", "install", "--ignore-scripts", "--offline"],
        cwd=consumer_root,
    )
    (consumer_root / "live-consumer.mjs").write_bytes(
        (ROOT / "sdk/typescript/test/live-consumer.mjs").read_bytes()
    )
    (consumer_root / "live-empty-consumer.mjs").write_bytes(
        (ROOT / "sdk/typescript/test/live-empty-consumer.mjs").read_bytes()
    )
    (consumer_root / "live-private-flow.mjs").write_bytes(
        (
            ROOT / "bot_delivery/typescript/test/live-private-flow.mjs"
        ).read_bytes()
    )


def _pack_and_install_resolve_sdk(consumer_root: Path) -> None:
    """Install only the public generated SDK for a resolve-only consumer."""

    for script in ("check:generated", "typecheck", "build", "test:package"):
        _run_sdk_process(
            ["npm", "--prefix", "sdk/typescript", "run", script],
            cwd=ROOT,
        )
    artifact_root = consumer_root / "artifact"
    artifact_root.mkdir()
    pack = _run_sdk_process(
        [
            "npm",
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(artifact_root),
        ],
        cwd=ROOT / "sdk/typescript",
    )
    artifact_name = json.loads(pack.stdout)[0]["filename"]
    (consumer_root / "package.json").write_text(
        json.dumps(
            {
                "name": "context-engine-delete-sdk-consumer",
                "private": True,
                "type": "module",
                "dependencies": {
                    "@context-engine/resolve-sdk": (
                        f"file:{artifact_root / artifact_name}"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    _run_sdk_process(
        ["npm", "install", "--ignore-scripts", "--offline"],
        cwd=consumer_root,
    )
    (consumer_root / "live-empty-consumer.mjs").write_bytes(
        (ROOT / "sdk/typescript/test/live-empty-consumer.mjs").read_bytes()
    )


def _run_installed_live_consumer(
    consumer_root: Path,
    *,
    base_url: str,
    delivery_evidence_ref: str,
    citation_delivery_evidence_ref: str,
    egress_database_url: str,
    organization_id: UUID,
) -> dict[str, object]:
    result = _run_sdk_process(
        ["node", "live-consumer.mjs"],
        cwd=consumer_root,
        env={
            **os.environ,
            "CONTEXT_ENGINE_SDK_BASE_URL": base_url,
            "CONTEXT_ENGINE_SDK_DELIVERY_EVIDENCE_REF": delivery_evidence_ref,
            "CONTEXT_ENGINE_SDK_CITATION_DELIVERY_EVIDENCE_REF": (
                citation_delivery_evidence_ref
            ),
            "CONTEXT_ENGINE_SDK_REQUEST_ID": "file-egress-sdk-http",
            "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION": "runtime-secret",
            "CONTEXT_ENGINE_SDK_TEST_DIRECT_AUTHENTICATION": ("runtime-direct-secret"),
            "CONTEXT_ENGINE_MODEL_EGRESS_DATABASE_URL": egress_database_url,
            "CONTEXT_ENGINE_MODEL_EGRESS_ORGANIZATION_ID": str(organization_id),
        },
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


def _run_installed_empty_consumer(
    consumer_root: Path,
    *,
    base_url: str,
) -> dict[str, object]:
    result = _run_sdk_process(
        ["node", "live-empty-consumer.mjs"],
        cwd=consumer_root,
        env={
            **os.environ,
            "CONTEXT_ENGINE_SDK_BASE_URL": base_url,
            "CONTEXT_ENGINE_SDK_REQUEST_ID": "file-delete-sdk-after",
            "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION": "runtime-secret",
        },
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    assert "runtime-secret" not in result.stdout + result.stderr
    return document


def _run_installed_private_bot_flow(
    consumer_root: Path,
    *,
    action_database_url: str,
    audience_digest: str,
    base_url: str,
    citation_delivery_evidence_ref: str,
    egress_database_url: str,
    finalize_delivery_evidence_ref: str,
    followup_delivery_evidence_ref: str,
    membership_id: UUID,
    organization_id: UUID,
    prime_delivery_evidence_ref: str,
    user_id: UUID,
    flow_mode: str = "complete",
    binding_audience_digest: str | None = None,
    binding_policy_epoch: int | None = None,
    event_mode: str = "bound",
    model_mode: str = "generated",
    question: str = "ContextEngine delivers context.",
    request_id: str = "bot-live-finalize",
    sender_mode: str = "applied",
    turn_ref: str = "live-finalize",
) -> dict[str, object]:
    result = _run_sdk_process(
        ["node", "live-private-flow.mjs"],
        cwd=consumer_root,
        env={
            **os.environ,
            "CE_BOT_ACTION_DATABASE_URL": action_database_url,
            "CE_BOT_AUDIENCE_DIGEST": audience_digest,
            "CE_BOT_BINDING_AUDIENCE_DIGEST": (
                binding_audience_digest or audience_digest
            ),
            "CE_BOT_BINDING_POLICY_EPOCH": str(binding_policy_epoch or 1),
            "CE_BOT_CITATION_EVIDENCE_REF": citation_delivery_evidence_ref,
            "CE_BOT_EGRESS_DATABASE_URL": egress_database_url,
            "CE_BOT_FINALIZE_EVIDENCE_REF": finalize_delivery_evidence_ref,
            "CE_BOT_FOLLOWUP_EVIDENCE_REF": followup_delivery_evidence_ref,
            "CE_BOT_FLOW_MODE": flow_mode,
            "CE_BOT_EVENT_MODE": event_mode,
            "CE_BOT_MEMBERSHIP_ID": str(membership_id),
            "CE_BOT_ORGANIZATION_ID": str(organization_id),
            "CE_BOT_MODEL_MODE": model_mode,
            "CE_BOT_PRIME_EVIDENCE_REF": prime_delivery_evidence_ref,
            "CE_BOT_SDK_AUTHENTICATION": "runtime-secret",
            "CE_BOT_SDK_BASE_URL": base_url,
            "CE_BOT_QUESTION": question,
            "CE_BOT_REQUEST_ID": request_id,
            "CE_BOT_SENDER_MODE": sender_mode,
            "CE_BOT_USER_ID": str(user_id),
            "CE_BOT_TURN_REF": turn_ref,
        },
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    process_output = result.stdout + result.stderr
    for secret in (
        action_database_url,
        citation_delivery_evidence_ref,
        egress_database_url,
        finalize_delivery_evidence_ref,
        followup_delivery_evidence_ref,
        prime_delivery_evidence_ref,
        "runtime-secret",
        "71" * 32,
    ):
        assert secret not in process_output
    return document


def _file_model_profile() -> ModelEgressProfile:
    return ModelEgressProfile(
        profile_ref="file-model-egress-integration-v1",
        retention_policy_ref="no-provider-retention-v1",
        sensitivity_policy_ref="authorized-package-only-v1",
        issuer_ref="context-runtime-integration",
        consumer_ref="model-gateway-integration",
        provider_ref="deterministic-provider-spy",
        model_ref="deterministic-model-spy",
        region_ref="local-test-region",
        maximum_ttl=timedelta(minutes=1),
    )


def _file_citation_profile() -> CitationOpenProfile:
    return CitationOpenProfile(
        profile_ref="private-citation-open-v1",
        retention_policy_ref="citation-locator-retention-v1",
        maximum_ttl=timedelta(minutes=10),
        retention_period=timedelta(days=30),
    )


def _accept_published_path_delete_observation(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
) -> tuple[ContextControl, ControlOperatorAuthority, ExecuteFileDeleteObservation]:
    provider_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=lambda: NOW,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        request_id="sdk-zero-effect-activate-v3",
    ) as call:
        v3 = control.activate_file_change_feed(
            call,
            ActivateFileChangeFeed(scenario.source_ref),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS,
        request_id="sdk-zero-effect-activate-v4",
    ) as call:
        v4 = control.activate_file_delete_observations(
            call,
            ActivateFileDeleteObservations(scenario.source_ref),
        )
    assert v4.active_version.version_ref != v3.active_version.version_ref
    provider = FileChangeProvider(
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1_024 * 1_024),
        ),
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    source = FileChangeSource(scenario.organization_id, v4.active_version)
    initial_page = provider.read_changes(source, InitialScan(), ChangeLimit(1))
    assert type(initial_page) is ProviderOk
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="sdk-zero-effect-accept-baseline",
    ) as call:
        control.accept_file_change_page(call, initial_page.value)
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="sdk-zero-effect-read-baseline",
    ) as call:
        progress = control.read_file_source_progress(call, scenario.source_ref)
    assert progress.complete_change_baseline is not None
    (scenario.root / "handbook.md").unlink()
    delete_page = provider.read_changes(
        FileChangeSource(
            scenario.organization_id,
            v4.active_version,
            scan_head=progress.change_scan_head,
            complete_baseline=progress.complete_change_baseline,
        ),
        InitialScan(),
        ChangeLimit(1),
    )
    assert type(delete_page) is ProviderOk
    assert [change.kind.value for change in delete_page.value.changes] == ["delete"]
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="sdk-zero-effect-accept-delete",
    ) as call:
        accepted = control.accept_file_change_page(call, delete_page.value)
    return (
        control,
        authority,
        ExecuteFileDeleteObservation(
            accepted.source_ref,
            accepted.source_version_ref,
            accepted.page_ref,
            1,
        ),
    )


@pytest.fixture
def _published_file_scenario(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    worker_configuration: DatabaseConfiguration,
) -> Iterator[tuple[_FileImportScenario, PublishedFileImport, Engine]]:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    migration_engine = create_database_engine(migration_configuration)
    try:
        worker_database_url = worker_configuration.url.render_as_string(
            hide_password=False
        )
        worker_lease_token = scenario.token.serialize()
        worker_signing_key = bytes(range(32)).hex()
        worker_environment = {
            **os.environ,
            "CONTEXT_ENGINE_WORKER_DATABASE_URL": worker_database_url,
            "CONTEXT_ENGINE_WORKER_FILE_ROOT_PATH": str(scenario.root),
            "CONTEXT_ENGINE_WORKER_FILE_ROOT_REF": scenario.root_ref.value,
            "CONTEXT_ENGINE_WORKER_JOB_ID": str(scenario.prepared.job_id),
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": worker_signing_key,
            "CONTEXT_ENGINE_WORKER_LEASE_TOKEN": worker_lease_token,
            "CONTEXT_ENGINE_WORKER_ORGANIZATION_ID": str(
                scenario.organization_id
            ),
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID": str(
                scenario.receiver.service_principal_id
            ),
            "CONTEXT_ENGINE_WORKER_SOURCE_ID": str(scenario.source_ref.value),
        }
        completed = subprocess.run(
            ["context-engine-worker", "--run-file-job"],
            cwd=ROOT,
            env=worker_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=SDK_PROCESS_TIMEOUT_SECONDS,
        )
        assert completed.stderr == ""
        worker_output = completed.stdout + completed.stderr
        for secret in (
            worker_database_url,
            worker_lease_token,
            worker_signing_key,
        ):
            assert secret not in worker_output
        worker_result = json.loads(completed.stdout)
        assert worker_result["service"] == "context-engine-worker"
        assert worker_result["jobBehavior"] == "file.import"
        assert worker_result["status"] == "complete"
        candidate_documents = worker_result["candidateRefs"]
        assert isinstance(candidate_documents, list)
        published = PublishedFileImport(
            candidate_refs=tuple(
                CandidateRef(
                    organization_id=UUID(candidate["organizationId"]),
                    source_ref=candidate["sourceRef"],
                    resource_ref=candidate["resourceRef"],
                    revision_ref=candidate["revisionRef"],
                    fragment_ref=candidate["fragmentRef"],
                )
                for candidate in candidate_documents
            ),
            acquisition_id=UUID(worker_result["acquisitionId"]),
            content_identity_digest=worker_result["contentIdentityDigest"],
            outcome=worker_result["outcome"],
            reason_digest=worker_result["reasonDigest"],
            effect_count=worker_result["effectCount"],
        )
        ensure_test_runtime_release(
            scenario.organization_id,
            active_revision_refs=(published.candidate_ref.revision_ref,),
        )
        yield scenario, published, migration_engine
    finally:
        clear_test_runtime_release(scenario.organization_id)
        cleanup_triggers = (
            ("action_receipt", "action_receipt_reject_mutation"),
            (
                "file_delete_observation_execution",
                "file_delete_observation_execution_immutable",
            ),
            ("file_source_change", "file_source_change_immutable"),
            ("file_source_change_page", "file_source_change_page_immutable"),
            (
                "file_source_publish_watermark",
                "file_source_publish_watermark_immutable",
            ),
            (
                "file_source_acquisition_checkpoint",
                "file_source_acquisition_checkpoint_immutable",
            ),
            ("file_resource_cleanup_intent", "file_resource_cleanup_intent_immutable"),
            ("file_source_cleanup_intent", "file_source_cleanup_intent_immutable"),
        )
        with migration_engine.begin() as connection:
            for table, trigger in cleanup_triggers:
                connection.execute(
                    text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
                )
        try:
            with migration_engine.begin() as connection:
                for table in (
                    "private_delivery_audit",
                    "action_receipt",
                    "action_reconciliation",
                    "action_provider_attempt",
                    "action_perform_audit",
                    "action_ticket",
                    "action_prepare_audit",
                    "action_delivery_attempt",
                    "citation_open_locator",
                    "decision_audit",
                    "context_run",
                    "model_egress_audit",
                    "egress_audit",
                    "egress_grant",
                    "delivery_evidence",
                    "file_delete_observation_execution",
                    "file_source_publish_watermark",
                    "file_source_acquisition_checkpoint",
                    "file_source_delete_observation_page",
                    "file_source_change",
                    "file_source_change_page",
                    "file_resource_cleanup_intent",
                    "file_source_cleanup_intent",
                ):
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE organization_id = :org"),
                        {"org": scenario.organization_id},
                    )
        finally:
            with migration_engine.begin() as connection:
                for table, trigger in reversed(cleanup_triggers):
                    connection.execute(
                        text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                    )
        migration_engine.dispose()


@pytest.mark.security_evidence(id="RUNTIME-EGRESS-011", layer="runtime")
def test_file_http_package_redeems_exact_model_grant_before_gateway_bytes(
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    guarded_runtime_engine: Engine,
    egress_configuration: DatabaseConfiguration,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    egress_engine = create_database_engine(egress_configuration)
    request_now = datetime.now(UTC).replace(microsecond=0)
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).scalar_one()
        observed: list[Resolved] = []
        response = TestClient(
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
                    published.candidate_ref.source_ref,
                    published.candidate_ref.resource_ref,
                ),
                runtime=Runtime(
                    required_kernel_dependencies(),
                    candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                    egress_profile=_file_model_profile(),
                    clock=lambda: request_now,
                    query_digest_keyring=query_digest_keyring,
                ),
                resolution_observer=observed.append,
                clock=lambda: request_now,
                request_id_factory=lambda: "file-egress-http",
            )
        ).post(
            "/v1/context:resolve",
            headers={"Authorization": "Bearer runtime-secret"},
            json={
                "kind": "acquire",
                "need": {"query": "ContextEngine delivers context."},
            },
        )

        assert response.status_code == 200
        assert response.json()["package"]["blocks"][0]["text"] == (
            "ContextEngine delivers context."
        )
        wire_grant = response.json()["egressGrant"]
        assert wire_grant["kind"] == "model"
        assert len(observed) == 1
        outcome = observed[0]
        assert type(outcome.egress_grant) is ModelEgressGrant
        assert wire_grant["value"] == outcome.egress_grant.value

        audience_digest = direct_egress_audience_digest(
            organization_id=scenario.organization_id,
            membership_id=scenario.membership_id,
            membership_version=1,
            authenticated_application_ref="application:file-tracer",
            delivery_binding_ref="binding:file-tracer",
        )
        gateway = DeterministicModelGatewaySpy(_file_model_profile())
        boundary = ModelEgressBoundary(
            organization_id=scenario.organization_id,
            audience_digest=audience_digest,
            policy_epoch=1,
            profile=_file_model_profile(),
            authority=PostgreSQLEgressGrantRedemptionAuthority(egress_engine),
            gateway=gateway,
        )
        authorized = prepare_authorized_model_input(
            outcome.package,
            outcome.egress_grant,
        )

        boundary.transmit(authorized, outcome.egress_grant)

        assert gateway.request_count == 1
        assert gateway.outbound_bytes > 0
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            boundary.transmit(authorized, outcome.egress_grant)
        assert gateway.request_count == 1
    finally:
        egress_engine.dispose()


@pytest.mark.security_evidence(id="RUNTIME-CITATION-AUTH-010", layer="runtime")
@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-010", layer="runtime")
@pytest.mark.parametrize("denied_principal", (False, True))
def test_file_http_citation_is_not_consumed_by_denied_reader(
    denied_principal: bool,
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    caplog: pytest.LogCaptureFixture,
    record_property: Callable[[str, object], None],
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    denied_user_id = uuid4()
    denied_membership_id = uuid4()
    request_now = datetime.now(UTC)
    with migration_engine.begin() as connection:
        authorized_user_id = connection.execute(
            text(
                "SELECT user_id FROM membership "
                "WHERE organization_id = :organization_id "
                "AND membership_id = :membership_id"
            ),
            {
                "organization_id": scenario.organization_id,
                "membership_id": scenario.membership_id,
            },
        ).scalar_one()
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
            {"user_id": denied_user_id},
        )
        connection.execute(
            text(
                "INSERT INTO membership (organization_id, membership_id, user_id, "
                "status, membership_version, valid_from) VALUES "
                "(:org, :membership, :user_id, 'active', 1, :valid_from)"
            ),
            {
                "org": scenario.organization_id,
                "membership": denied_membership_id,
                "user_id": denied_user_id,
                "valid_from": request_now - timedelta(days=1),
            },
        )

    observed: list[Resolved] = []
    client = TestClient(
        create_app(
            authenticator=_TwoReaderAuthenticator(
                {
                    "reader-a": (
                        scenario.organization_id,
                        authorized_user_id,
                        scenario.membership_id,
                    ),
                    "reader-b": (
                        scenario.organization_id,
                        denied_user_id,
                        denied_membership_id,
                    ),
                },
                principal_refs=(
                    {"reader-b": "principal:file-denied-reader"}
                    if denied_principal
                    else None
                ),
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                published.candidate_ref.source_ref,
                published.candidate_ref.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                egress_profile=_file_model_profile(),
                citation_profile=_file_citation_profile(),
                clock=lambda: request_now,
                query_digest_keyring=query_digest_keyring,
            ),
            resolution_observer=observed.append,
            clock=lambda: request_now,
        )
    )

    acquired = client.post(
        "/v0/resolve",
        headers={
            "Authorization": "Bearer reader-a",
            "X-Context-Request-Id": "citation-reader-a-acquire",
        },
        json={
            "kind": "acquire",
            "need": {"query": "ContextEngine delivers context."},
        },
    )
    assert acquired.status_code == 200
    citation_ref = acquired.json()["package"]["evidence"][0]["citationOpenRef"]
    assert isinstance(citation_ref, str) and citation_ref.startswith("cor_")

    with migration_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT expires_at, retain_until FROM citation_open_locator "
                "WHERE organization_id = :org AND locator_digest = :digest"
            ),
            {
                "org": scenario.organization_id,
                "digest": sha256(citation_ref.encode()).digest(),
            },
        ).one()

    denied = client.post(
        "/v0/resolve",
        headers={
            "Authorization": "Bearer reader-b",
            "X-Context-Request-Id": "citation-reader-b-denied",
        },
        json={"kind": "open_citation", "citationOpenRef": citation_ref},
    )
    assert denied.status_code == 200
    assert denied.json() == {"kind": "citation_not_available"}

    with migration_engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT expires_at, retain_until FROM citation_open_locator "
                "WHERE organization_id = :org AND locator_digest = :digest"
            ),
            {
                "org": scenario.organization_id,
                "digest": sha256(citation_ref.encode()).digest(),
            },
        ).one()
    assert after == before

    reopened = client.post(
        "/v0/resolve",
        headers={
            "Authorization": "Bearer reader-a",
            "X-Context-Request-Id": "citation-reader-a-reopen",
        },
        json={"kind": "open_citation", "citationOpenRef": citation_ref},
    )
    assert reopened.status_code == 200
    assert reopened.json()["kind"] == "resolved"
    assert reopened.json()["package"]["blocks"][0]["text"] == (
        "ContextEngine delivers context."
    )
    assert citation_ref not in json.dumps(reopened.json())
    assert citation_ref not in repr(observed)
    assert citation_ref not in "".join(
        record.getMessage() for record in caplog.records
    )
    assert "sourceUrl" not in json.dumps(reopened.json())
    assert len(observed) == 2
    with migration_engine.connect() as connection:
        denied_run = connection.execute(
            text(
                "SELECT run_ref, decision_ref, outcome, purpose, "
                "authorized_evidence_refs, query_digest FROM context_run "
                "WHERE organization_id = :org AND request_id = :request_id"
            ),
            {
                "org": scenario.organization_id,
                "request_id": "citation-reader-b-denied",
            },
        ).one()
        denied_audit = connection.execute(
            text(
                "SELECT category FROM decision_audit "
                "WHERE organization_id = :org AND run_ref = :run_ref "
                "AND decision_ref = :decision_ref"
            ),
            {
                "org": scenario.organization_id,
                "run_ref": denied_run.run_ref,
                "decision_ref": denied_run.decision_ref,
            },
        ).scalar_one()
    assert denied_run.outcome == "delivered_empty"
    assert denied_run.purpose == "citation.open"
    assert denied_run.authorized_evidence_refs == []
    assert denied_run.query_digest != sha256(citation_ref.encode()).hexdigest()
    assert denied_audit == "no_authorized_evidence"
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-010",
        unauthorized_evidence_count=len(
            denied.json().get("package", {}).get("evidence", [])
        ),
        wrong_organization_effect_count=0,
        missing_context_fallback_count=0,
    )


@pytest.mark.parametrize("target_state", ["revoked", "source_offboarded", "tombstoned"])
def test_file_http_citation_reauthorizes_unavailable_target(
    target_state: str,
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    request_now = datetime.now(UTC)
    with migration_engine.connect() as connection:
        user_id = connection.execute(
            text(
                "SELECT user_id FROM membership "
                "WHERE organization_id = :org AND membership_id = :membership"
            ),
            {"org": scenario.organization_id, "membership": scenario.membership_id},
        ).scalar_one()
    client = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id, user_id, scenario.membership_id
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=_ExactScopeAuthority(
                published.candidate_ref.source_ref,
                published.candidate_ref.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                egress_profile=_file_model_profile(),
                citation_profile=_file_citation_profile(),
                clock=lambda: request_now,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: request_now,
        )
    )
    acquired = client.post(
        "/v0/resolve",
        headers={
            "Authorization": "Bearer runtime-secret",
            "X-Context-Request-Id": f"citation-{target_state}-acquire",
        },
        json={"kind": "acquire", "need": {"query": "ContextEngine delivers context."}},
    )
    citation_ref = acquired.json()["package"]["evidence"][0]["citationOpenRef"]

    if target_state == "revoked":
        PostgreSQLAccessPolicyControl(guarded_control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=scenario.organization_id,
                resource_ref=published.candidate_ref.resource_ref,
                principal_ref="principal:file-reader",
                expected_access_version=1,
            )
        )
    elif target_state == "tombstoned":
        _tombstone(
            scenario,
            guarded_control_engine,
            resource_ref=published.candidate_ref.resource_ref,
            event_ref=f"citation-{target_state}",
            event_sequence=2,
        )
    else:
        _offboard(scenario, guarded_control_engine)

    opened = client.post(
        "/v0/resolve",
        headers={
            "Authorization": "Bearer runtime-secret",
            "X-Context-Request-Id": f"citation-{target_state}-open",
        },
        json={"kind": "open_citation", "citationOpenRef": citation_ref},
    )

    assert opened.status_code == 200
    assert opened.content == b'{"kind":"citation_not_available"}'
    with migration_engine.connect() as connection:
        retained = connection.execute(
            text(
                "SELECT count(*) FROM citation_open_locator "
                "WHERE organization_id = :org AND locator_digest = :digest"
            ),
            {
                "org": scenario.organization_id,
                "digest": sha256(citation_ref.encode()).digest(),
            },
        ).scalar_one()
    assert retained == 1


@pytest.mark.security_evidence(id="HTTP-FILE-DELETE-INVISIBLE-087", layer="runtime")
def test_file_delete_execution_is_immediately_invisible_over_generated_sdk(
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    tmp_path: Path,
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    request_now = datetime.now(UTC).replace(microsecond=0)
    with migration_engine.connect() as connection:
        user_id = connection.execute(
            text(
                "SELECT user_id FROM membership "
                "WHERE organization_id = :org AND membership_id = :membership"
            ),
            {"org": scenario.organization_id, "membership": scenario.membership_id},
        ).scalar_one()
        retained_before = tuple(
            connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_revision
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM context_fragment
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM file_revision_snapshot
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM exact_phrase_candidate
                       WHERE organization_id = :org)
                    """
                ),
                {"org": scenario.organization_id},
            ).one()
        )
    application = create_app(
        authenticator=_RuntimeAuthenticator(
            scenario.organization_id,
            user_id,
            scenario.membership_id,
        ),
        organization_authority=_OrganizationAuthority(),
        membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
        scope_authority=_ExactScopeAuthority(
            published.candidate_ref.source_ref,
            published.candidate_ref.resource_ref,
        ),
        runtime=Runtime(
            required_kernel_dependencies(),
            candidate_index=PostgreSQLExactPhraseCandidateIndex(),
            egress_profile=_file_model_profile(),
            citation_profile=_file_citation_profile(),
            clock=lambda: request_now,
            query_digest_keyring=query_digest_keyring,
        ),
        clock=lambda: request_now,
    )
    control, authority, command = _accept_published_path_delete_observation(
        scenario,
        guarded_control_engine,
    )
    with TestClient(application) as client:
        before = client.post(
            "/v0/resolve",
            headers={
                "Authorization": "Bearer runtime-secret",
                "X-Context-Request-Id": "delete-execution-before",
            },
            json={
                "kind": "acquire",
                "need": {"query": "ContextEngine delivers context."},
            },
        )
    assert before.status_code == 200
    assert before.json()["package"]["evidence"]

    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION,
        request_id="execute-sdk-visible-delete",
    ) as call:
        control.execute_file_delete_observation(call, command)

    consumer_root = tmp_path / "delete-installed-sdk-consumer"
    consumer_root.mkdir()
    _pack_and_install_resolve_sdk(consumer_root)
    port = _unused_port()
    server = Server(
        Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    server_thread = Thread(target=server.run, daemon=True)
    server_thread.start()
    try:
        _wait_for_tcp(port)
        sdk_after = _run_installed_empty_consumer(
            consumer_root,
            base_url=f"http://127.0.0.1:{port}",
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        assert not server_thread.is_alive()
    assert sdk_after["kind"] == "resolved"
    sdk_package = sdk_after["package"]
    assert isinstance(sdk_package, dict)
    assert sdk_package["blocks"] == []
    assert sdk_package["evidence"] == []

    with migration_engine.connect() as connection:
        retained_after = tuple(
            connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM context_revision
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM context_fragment
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM file_revision_snapshot
                       WHERE organization_id = :org),
                      (SELECT count(*) FROM exact_phrase_candidate
                       WHERE organization_id = :org)
                    """
                ),
                {"org": scenario.organization_id},
            ).one()
        )
    assert retained_after == retained_before


@pytest.mark.security_evidence(id="SDK-LIVE-FILE-064", layer="runtime")
@pytest.mark.security_evidence(id="SDK-MODEL-EGRESS-070", layer="runtime")
@pytest.mark.security_evidence(id="PG-MODEL-EGRESS-070", layer="postgres")
def test_packed_typescript_sdk_resolves_authorized_file_package_over_live_http(
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    tmp_path: Path,
    action_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    identity_configuration: DatabaseConfiguration,
    egress_configuration: DatabaseConfiguration,
    learning_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    identity_engine = create_database_engine(identity_configuration)
    operator_engine = create_database_engine(operator_configuration)
    server: Server | None = None
    server_thread: Thread | None = None
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).scalar_one()
            application_roles = (
                action_configuration.expected_role,
                control_configuration.expected_role,
                identity_configuration.expected_role,
                egress_configuration.expected_role,
                learning_configuration.expected_role,
                operator_configuration.expected_role,
                runtime_configuration.expected_role,
                worker_configuration.expected_role,
            )
            privileges = {
                role: tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(:role, 'model_egress_audit', "
                            "'SELECT'), "
                            "has_table_privilege(:role, 'model_egress_audit', "
                            "'INSERT'), "
                            "has_table_privilege(:role, 'model_egress_audit', "
                            "'UPDATE'), "
                            "has_table_privilege(:role, 'model_egress_audit', "
                            "'DELETE'), "
                            "has_function_privilege(:role, "
                            "'context_egress_record_model_outcome(uuid,bytea,"
                            "bytea,bytea,bytea,bytea,text,bigint,bigint,bigint,"
                            "bigint,text,bigint,text)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_security_delete_expired_model_egress_audit("
                            "uuid)', 'EXECUTE')"
                        ),
                        {"role": role},
                    ).one()
                )
                for role in application_roles
            }
            assert privileges[egress_configuration.expected_role] == (
                False,
                False,
                False,
                False,
                True,
                False,
            )
            assert privileges[operator_configuration.expected_role] == (
                False,
                False,
                False,
                False,
                False,
                True,
            )
            for role in set(application_roles) - {
                egress_configuration.expected_role,
                operator_configuration.expected_role,
            }:
                assert privileges[role] == (False,) * 6

        consumer_root = tmp_path / "installed-sdk-consumer"
        consumer_root.mkdir()
        _pack_and_install_sdk(consumer_root)
        _accept_published_path_delete_observation(
            scenario,
            guarded_control_engine,
        )
        with migration_engine.connect() as connection:
            zero_effect = connection.execute(
                text(
                    """
                    SELECT resource.tombstoned, epoch.policy_epoch,
                           (SELECT count(*) FROM file_resource_cleanup_intent
                            WHERE organization_id = :organization_id),
                           (SELECT count(*) FROM file_source_publish_watermark
                            WHERE organization_id = :organization_id
                              AND source_id = :source_id)
                    FROM context_resource AS resource
                    JOIN organization_policy_epoch AS epoch
                      ON epoch.organization_id = resource.organization_id
                    WHERE resource.organization_id = :organization_id
                      AND resource.resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                    "resource_ref": published.candidate_ref.resource_ref,
                },
            ).one()
        assert tuple(zero_effect) == (False, 1, 0, 1)
        request_now = datetime.now(UTC).replace(microsecond=0)

        evidence_issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=15),
            ),
            reference_factory=lambda: "der_"
            + sha256(scenario.organization_id.bytes + b"sdk-http").hexdigest(),
            resolution_ref_factory=lambda: "dlr_"
            + sha256(scenario.organization_id.bytes + b"sdk-result").hexdigest()[:32],
        )
        evidence_ref = evidence_issuer.issue_private(
            PrivateDeliveryEvidenceIssue(
                organization_id=scenario.organization_id,
                user_id=user_id,
                membership_id=scenario.membership_id,
                membership_version=1,
                authenticated_service_ref="application:file-tracer",
                authentication_binding_ref="binding:file-tracer",
                request_id="file-egress-sdk-http",
                destination_ref="private-chat:file-tracer",
                consumer_ref="consumer:file-tracer",
                purpose="context.answer",
                policy_epoch=1,
                issued_at=request_now - timedelta(seconds=1),
                expires_at=request_now + timedelta(minutes=10),
            )
        )
        citation_evidence_ref = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=15),
            ),
            reference_factory=lambda: "der_"
            + sha256(scenario.organization_id.bytes + b"sdk-citation-http").hexdigest(),
            resolution_ref_factory=lambda: "dlr_"
            + sha256(
                scenario.organization_id.bytes + b"sdk-citation-result"
            ).hexdigest()[:32],
        ).issue_private(
            PrivateDeliveryEvidenceIssue(
                organization_id=scenario.organization_id,
                user_id=user_id,
                membership_id=scenario.membership_id,
                membership_version=1,
                authenticated_service_ref="application:file-tracer",
                authentication_binding_ref="binding:file-tracer",
                request_id="file-egress-sdk-http-citation",
                destination_ref="private-chat:file-tracer",
                consumer_ref="consumer:file-tracer",
                purpose="citation.open",
                policy_epoch=1,
                issued_at=request_now - timedelta(seconds=1),
                expires_at=request_now + timedelta(minutes=10),
            )
        )

        observed: list[Resolved] = []
        transport_observer = _SdkTransportObserver(
            create_app(
                authenticator=_SdkRuntimeAuthenticator(
                    scenario.organization_id,
                    user_id,
                    scenario.membership_id,
                ),
                organization_authority=_OrganizationAuthority(),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                scope_authority=_ExactScopeAuthority(
                    published.candidate_ref.source_ref,
                    published.candidate_ref.resource_ref,
                ),
                runtime=Runtime(
                    required_kernel_dependencies(),
                    candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                    egress_profile=_file_model_profile(),
                    citation_profile=_file_citation_profile(),
                    clock=lambda: request_now,
                    query_digest_keyring=query_digest_keyring,
                ),
                resolution_observer=observed.append,
                clock=lambda: request_now,
            )
        )
        port = _unused_port()
        server = Server(
            Config(
                transport_observer,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        _wait_for_tcp(port)

        result = _run_installed_live_consumer(
            consumer_root,
            base_url=f"http://127.0.0.1:{port}",
            delivery_evidence_ref=evidence_ref.evidence_ref,
            citation_delivery_evidence_ref=(citation_evidence_ref.evidence_ref),
            egress_database_url=egress_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            organization_id=scenario.organization_id,
        )

        acquire = result["acquire"]
        assert isinstance(acquire, dict)
        assert acquire["kind"] == "resolved"
        package = acquire["package"]
        assert isinstance(package, dict)
        blocks = package["blocks"]
        assert isinstance(blocks, list)
        assert blocks[0]["text"] == "ContextEngine delivers context."
        evidence = package["evidence"]
        assert isinstance(evidence, list)
        assert evidence[0]["sourceRef"] == published.candidate_ref.source_ref
        assert evidence[0]["resourceRef"] == published.candidate_ref.resource_ref
        assert evidence[0]["revisionRef"] == published.candidate_ref.revision_ref
        assert evidence[0]["fragmentRef"] == "fragment:paragraph:1"
        grant = acquire["egressGrant"]
        assert isinstance(grant, dict)
        assert grant["kind"] == "model"
        assert isinstance(grant["value"], str)
        assert grant["value"]
        assert evidence_ref.evidence_ref not in json.dumps(result)
        generation = result["generation"]
        assert isinstance(generation, dict)
        assert generation["kind"] == "generated"
        assert generation["answer"]["text"] == (
            "ContextEngine delivers authorized Package context."
        )
        assert generation["answer"]["citations"] == [
            {
                "citationOpenRef": evidence[0]["citationOpenRef"],
                "evidenceRef": evidence[0]["evidenceRef"],
            }
        ]
        assert result["generationReplay"] == {"kind": "generation_not_available"}
        gateway = result["gateway"]
        assert isinstance(gateway, dict)
        assert gateway["callCount"] == 1
        assert gateway["outboundBytes"] > 0
        assert gateway["requests"] == [
            {
                "context": [
                    {
                        "evidenceRefs": [evidence[0]["evidenceRef"]],
                        "text": "ContextEngine delivers context.",
                    }
                ],
                "instructions": "Answer only from the supplied Package.",
                "question": "What does ContextEngine deliver?",
            }
        ]
        assert result["continuation"] == {
            "kind": "request_not_available",
            "retryable": False,
        }
        citation = result["citation"]
        assert isinstance(citation, dict)
        assert citation["kind"] == "resolved"
        citation_package = citation["package"]
        assert isinstance(citation_package, dict)
        assert citation_package["purpose"] == "citation.open"
        assert citation_package["packageId"] != package["packageId"]
        assert citation_package["blocks"][0]["text"] == (
            "ContextEngine delivers context."
        )
        assert (
            citation_package["evidence"][0]["citationOpenRef"]
            != (evidence[0]["citationOpenRef"])
        )
        citation_grant = citation["egressGrant"]
        assert isinstance(citation_grant, dict)
        assert citation_grant["kind"] == "model"
        assert isinstance(citation_grant["value"], str)
        assert citation_grant["value"]
        assert citation_grant["value"] != grant["value"]
        assert len(transport_observer.requests) == 3
        _assert_sdk_transport_headers(
            transport_observer.requests[0],
            authentication=b"Bearer runtime-secret",
            delivery_evidence_ref=evidence_ref.evidence_ref.encode("ascii"),
        )
        _assert_sdk_transport_headers(
            transport_observer.requests[1],
            authentication=b"Bearer runtime-direct-secret",
            delivery_evidence_ref=None,
        )
        _assert_sdk_transport_headers(
            transport_observer.requests[2],
            authentication=b"Bearer runtime-secret",
            delivery_evidence_ref=(citation_evidence_ref.evidence_ref.encode("ascii")),
            request_id=b"file-egress-sdk-http-citation",
        )
        assert len(observed) == 2
        assert observed[0].package.decision_ref == package["decisionRef"]
        with migration_engine.begin() as connection:
            audit = connection.execute(
                text(
                    "SELECT grant_digest, package_digest, payload_digest, "
                    "question_digest, answer_payload_digest, outcome_category, "
                    "provider_calls, cost_microunits, elapsed_ms, output_bytes, "
                    "profile_ref, audit_profile_ref, recorded_at, retain_until "
                    "FROM model_egress_audit WHERE organization_id = :org"
                ),
                {"org": scenario.organization_id},
            ).one()
            assert audit.outcome_category == "generated"
            assert audit.provider_calls == 1
            assert audit.cost_microunits == 7
            assert audit.elapsed_ms == 5
            assert audit.output_bytes == len(
                b"ContextEngine delivers authorized Package context."
            )
            assert audit.profile_ref == _file_model_profile().profile_ref
            assert audit.audit_profile_ref == "model-generation-audit-v1"
            assert audit.retain_until - audit.recorded_at == timedelta(days=30)
            assert all(
                len(bytes(digest)) == 32
                for digest in (
                    audit.grant_digest,
                    audit.package_digest,
                    audit.payload_digest,
                    audit.question_digest,
                    audit.answer_payload_digest,
                )
            )
            serialized_audit = repr(audit)
            assert grant["value"] not in serialized_audit
            assert generation["answer"]["text"] not in serialized_audit
            assert blocks[0]["text"] not in serialized_audit
            connection.execute(
                text(
                    "UPDATE model_egress_audit SET "
                    "recorded_at = recorded_at - interval '31 days', "
                    "retain_until = retain_until - interval '31 days' "
                    "WHERE organization_id = :org"
                ),
                {"org": scenario.organization_id},
            )
        with operator_engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT context_security_delete_expired_model_egress_audit(:org)"
                ),
                {"org": uuid4()},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT context_security_delete_expired_model_egress_audit(:org)"
                ),
                {"org": scenario.organization_id},
            ).scalar_one() == 1
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
            assert not server_thread.is_alive()
        identity_engine.dispose()
        operator_engine.dispose()


@pytest.mark.security_evidence(id="SDK-PRIVATE-BOT-FLOW-071", layer="runtime")
@pytest.mark.security_evidence(id="PG-PRIVATE-BOT-FLOW-071", layer="postgres")
def test_installed_private_bot_completes_file_answer_effects_audit_and_citation(
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    tmp_path: Path,
    action_configuration: DatabaseConfiguration,
    identity_configuration: DatabaseConfiguration,
    egress_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario, published, migration_engine = _published_file_scenario
    identity_engine = create_database_engine(identity_configuration)
    server: Server | None = None
    server_thread: Thread | None = None
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership WHERE organization_id = :org "
                    "AND membership_id = :membership"
                ),
                {
                    "org": scenario.organization_id,
                    "membership": scenario.membership_id,
                },
            ).scalar_one()
            other_organization_id = uuid4()
            other_user_id = uuid4()
            other_membership_id = uuid4()
            roles = (
                action_configuration.expected_role,
                identity_configuration.expected_role,
                egress_configuration.expected_role,
            )
            privileges = {
                role: tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(:role, 'private_delivery_audit', "
                            "'SELECT'), has_table_privilege(:role, "
                            "'private_delivery_audit', 'INSERT'), "
                            "has_function_privilege(:role, "
                            "'context_action_record_private_delivery_outcome(uuid,"
                            "text,text,bytea,text,text,text,bigint,text)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_action_bind_private_delivery_effect(bytea,"
                            "bytea,bytea,text,bytea,bytea)', 'EXECUTE')"
                        ),
                        {"role": role},
                    ).one()
                )
                for role in roles
            }
            assert privileges[action_configuration.expected_role] == (
                False,
                False,
                True,
                True,
            )
            for role in set(roles) - {action_configuration.expected_role}:
                assert privileges[role] == (False, False, False, False)
            functions = connection.execute(
                text(
                    "SELECT function.proname, owner.rolname, function.prosecdef, "
                    "function.proconfig "
                    "FROM pg_proc AS function JOIN pg_roles AS owner ON "
                    "owner.oid = function.proowner WHERE function.proname IN "
                    "('context_action_record_private_delivery_outcome', "
                    "'context_action_bind_private_delivery_effect')"
                )
            ).all()
            assert {function.proname for function in functions} == {
                "context_action_bind_private_delivery_effect",
                "context_action_record_private_delivery_outcome",
            }
            for function in functions:
                assert function.rolname == "context_engine_action_execute_definer"
                assert function.prosecdef is True
                assert sorted(function.proconfig) == [
                    "row_security=on",
                    "search_path=pg_catalog, pg_temp",
                ]

        consumer_root = tmp_path / "installed-private-bot-consumer"
        consumer_root.mkdir()
        _pack_and_install_sdk(consumer_root)
        request_now = datetime.now(UTC).replace(microsecond=0)
        issue_specs = (
            ("prime", "bot-live-prime", "context.answer"),
            ("finalize", "bot-live-finalize", "context.answer"),
            ("followup", "bot-live-followup", "context.answer"),
            ("citation", "bot-live-citation", "citation.open"),
            ("revoked", "bot-live-revoked", "context.answer"),
        )
        evidence_refs: dict[str, str] = {}
        issue_documents: dict[str, PrivateDeliveryEvidenceIssue] = {}
        for label, request_id, purpose in issue_specs:
            def evidence_ref_factory(label: str = label) -> str:
                return "der_" + sha256(
                    scenario.organization_id.bytes + label.encode("ascii")
                ).hexdigest()

            def resolution_ref_factory(label: str = label) -> str:
                return "dlr_" + sha256(label.encode("ascii")).hexdigest()[:32]

            issue = PrivateDeliveryEvidenceIssue(
                organization_id=scenario.organization_id,
                user_id=user_id,
                membership_id=scenario.membership_id,
                membership_version=1,
                authenticated_service_ref="application:file-tracer",
                authentication_binding_ref="binding:file-tracer",
                request_id=request_id,
                destination_ref="private-chat:file-tracer",
                consumer_ref="consumer:file-tracer",
                purpose=purpose,
                policy_epoch=1,
                issued_at=request_now - timedelta(seconds=1),
                expires_at=request_now + timedelta(minutes=10),
            )
            issued = PrivateDeliveryEvidenceIssuer(
                PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
                profile=DeliveryEvidenceProfile(
                    profile_ref="private-delivery-evidence-v1",
                    maximum_ttl=timedelta(minutes=15),
                ),
                reference_factory=evidence_ref_factory,
                resolution_ref_factory=resolution_ref_factory,
            ).issue_private(issue)
            evidence_refs[label] = issued.evidence_ref
            issue_documents[label] = issue

        mutation_specs = {
            "wrong_destination": {
                "destination_ref": "private-chat:wrong-destination",
            },
            "wrong_request": {"request_id": "bot-live-wrong-request"},
            "wrong_service": {
                "authenticated_service_ref": "application:wrong-service",
            },
        }
        mutation_refs: dict[str, str] = {}
        mutation_documents: dict[str, PrivateDeliveryEvidenceIssue] = {}
        for label, overrides in mutation_specs.items():
            base = {
                "organization_id": scenario.organization_id,
                "user_id": user_id,
                "membership_id": scenario.membership_id,
                "membership_version": 1,
                "authenticated_service_ref": "application:file-tracer",
                "authentication_binding_ref": "binding:file-tracer",
                "request_id": f"bot-live-{label}",
                "destination_ref": "private-chat:file-tracer",
                "consumer_ref": "consumer:file-tracer",
                "purpose": "context.answer",
                "policy_epoch": 1,
                "issued_at": request_now - timedelta(seconds=1),
                "expires_at": request_now + timedelta(minutes=10),
                **overrides,
            }
            mutation_issue = PrivateDeliveryEvidenceIssue(**base)
            mutation_ref = "der_" + sha256(
                scenario.organization_id.bytes + f"mutation:{label}".encode("ascii")
            ).hexdigest()

            def mutation_evidence_ref_factory(
                value: str = mutation_ref,
            ) -> str:
                return value

            def mutation_resolution_ref_factory(
                value: str = label,
            ) -> str:
                return (
                    "dlr_"
                    + sha256(f"mutation:{value}".encode("ascii")).hexdigest()[:32]
                )

            mutation_issued = PrivateDeliveryEvidenceIssuer(
                PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
                profile=DeliveryEvidenceProfile(
                    profile_ref="private-delivery-evidence-v1",
                    maximum_ttl=timedelta(minutes=15),
                ),
                reference_factory=mutation_evidence_ref_factory,
                resolution_ref_factory=mutation_resolution_ref_factory,
            ).issue_private(mutation_issue)
            mutation_refs[label] = mutation_issued.evidence_ref
            mutation_documents[label] = mutation_issue

        expired_ref = "der_" + sha256(
            scenario.organization_id.bytes + b"mutation:expired"
        ).hexdigest()
        expired_issue = PrivateDeliveryEvidenceIssue(
            organization_id=scenario.organization_id,
            user_id=user_id,
            membership_id=scenario.membership_id,
            membership_version=1,
            authenticated_service_ref="application:file-tracer",
            authentication_binding_ref="binding:file-tracer",
            request_id="bot-live-expired",
            destination_ref="private-chat:file-tracer",
            consumer_ref="consumer:file-tracer",
            purpose="context.answer",
            policy_epoch=1,
            issued_at=request_now - timedelta(seconds=1),
            expires_at=request_now + timedelta(minutes=10),
        )
        PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=15),
            ),
            reference_factory=lambda: expired_ref,
            resolution_ref_factory=lambda: (
                "dlr_" + sha256(b"mutation:expired").hexdigest()[:32]
            ),
        ).issue_private(expired_issue)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE delivery_evidence SET expires_at = :expired "
                    ", issued_at = :issued "
                    "WHERE evidence_digest = digest(:evidence_ref, 'sha256')"
                ),
                {
                    "evidence_ref": expired_ref,
                    "expired": request_now - timedelta(seconds=1),
                    "issued": request_now - timedelta(minutes=2),
                },
            )
        mutation_refs["expired"] = expired_ref
        mutation_documents["expired"] = expired_issue

        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": other_organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": other_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:org, :membership, :user_id, 'active', 1, :valid_from)"
                ),
                {
                    "org": other_organization_id,
                    "membership": other_membership_id,
                    "user_id": other_user_id,
                    "valid_from": request_now - timedelta(days=1),
                },
            )
        wrong_organization_issue = PrivateDeliveryEvidenceIssue(
            organization_id=other_organization_id,
            user_id=other_user_id,
            membership_id=other_membership_id,
            membership_version=1,
            authenticated_service_ref="application:file-tracer",
            authentication_binding_ref="binding:file-tracer",
            request_id="bot-live-finalize",
            destination_ref="private-chat:file-tracer",
            consumer_ref="consumer:file-tracer",
            purpose="context.answer",
            policy_epoch=1,
            issued_at=request_now - timedelta(seconds=1),
            expires_at=request_now + timedelta(minutes=10),
        )
        wrong_organization_ref = "der_" + sha256(
            other_organization_id.bytes + b"mutation:wrong-organization"
        ).hexdigest()
        PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=15),
            ),
            reference_factory=lambda: wrong_organization_ref,
            resolution_ref_factory=lambda: (
                "dlr_" + sha256(b"mutation:wrong-organization").hexdigest()[:32]
            ),
        ).issue_private(wrong_organization_issue)

        action_engine = create_database_engine(action_configuration)
        try:
            with action_engine.connect() as connection:
                bound = connection.execute(
                    text(
                        "SELECT * FROM context_action_bind_private_delivery_effect("
                        "digest(:evidence_ref, 'sha256'), digest(:service, 'sha256'), "
                        "digest(:consumer, 'sha256'), :request_id, "
                        "digest(:destination, 'sha256'), digest(:purpose, 'sha256'))"
                    ),
                    {
                        "consumer": "consumer:file-tracer",
                        "destination": "private-chat:file-tracer",
                        "evidence_ref": evidence_refs["finalize"],
                        "purpose": "context.answer",
                        "request_id": "bot-live-finalize",
                        "service": "application:file-tracer",
                    },
                ).one()
            assert bound.outcome == "bound"
            assert bound.organization_id == scenario.organization_id
        finally:
            action_engine.dispose()

        transport_observer = _SdkTransportObserver(
            create_app(
                authenticator=_SdkRuntimeAuthenticator(
                    scenario.organization_id,
                    user_id,
                    scenario.membership_id,
                ),
                organization_authority=_OrganizationAuthority(),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                scope_authority=_ExactScopeAuthority(
                    published.candidate_ref.source_ref,
                    published.candidate_ref.resource_ref,
                ),
                runtime=Runtime(
                    required_kernel_dependencies(),
                    candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                    egress_profile=_file_model_profile(),
                    citation_profile=_file_citation_profile(),
                    clock=lambda: request_now,
                    query_digest_keyring=query_digest_keyring,
                ),
                clock=lambda: request_now,
            )
        )
        port = _unused_port()
        server = Server(
            Config(
                transport_observer,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off",
            )
        )
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        _wait_for_tcp(port)

        result = _run_installed_private_bot_flow(
            consumer_root,
            action_database_url=action_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            audience_digest=private_delivery_audience_digest(
                issue_documents["finalize"]
            ),
            base_url=f"http://127.0.0.1:{port}",
            citation_delivery_evidence_ref=evidence_refs["citation"],
            egress_database_url=egress_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            finalize_delivery_evidence_ref=evidence_refs["finalize"],
            followup_delivery_evidence_ref=evidence_refs["followup"],
            membership_id=scenario.membership_id,
            organization_id=scenario.organization_id,
            prime_delivery_evidence_ref=evidence_refs["prime"],
            user_id=user_id,
        )

        finalized = result["finalized"]
        followed_up = result["followedUp"]
        citation = result["citation"]
        assert isinstance(finalized, dict)
        assert isinstance(followed_up, dict)
        assert isinstance(citation, dict)
        assert finalized["kind"] == "delivered", result
        assert finalized["finalStatus"] == "finalized"
        assert followed_up["kind"] == "delivered"
        assert followed_up["finalStatus"] == "private_followup"
        gateway = result["gateway"]
        assert isinstance(gateway, dict)
        assert gateway["callCount"] == 2
        assert isinstance(gateway["outboundBytes"], int)
        assert gateway["outboundBytes"] > 0
        assert result["sender"] == {"callCount": 4, "effectCount": 4}
        assert citation["kind"] == "opened"
        assert citation["purpose"] == "citation.open"
        assert isinstance(citation["packageDigest"], str)
        assert len(citation["packageDigest"]) == 64
        assert "sourceUrl" not in json.dumps(citation)
        serialized_result = json.dumps(result)
        assert "ContextEngine delivers authorized Package context." not in (
            json.dumps(finalized) + json.dumps(followed_up)
        )
        for evidence_ref in evidence_refs.values():
            assert evidence_ref not in serialized_result

        assert len(transport_observer.requests) == 4
        for request, label in zip(
            transport_observer.requests,
            ("prime", "finalize", "followup", "citation"),
            strict=True,
        ):
            _assert_sdk_transport_headers(
                request,
                authentication=b"Bearer runtime-secret",
                delivery_evidence_ref=evidence_refs[label].encode("ascii"),
                request_id=f"bot-live-{label}".encode("ascii"),
            )

        with migration_engine.connect() as connection:
            receipts = connection.execute(
                text(
                    "SELECT delivery_attempt_ref, operation, receipt_ref FROM "
                    "action_receipt WHERE organization_id = :org ORDER BY "
                    "delivery_attempt_ref, operation"
                ),
                {"org": scenario.organization_id},
            ).all()
            audits = connection.execute(
                text(
                    "SELECT audit_ref, delivery_attempt_ref, package_digest, "
                    "placeholder_receipt_ref, final_receipt_ref, final_status, "
                    "audit_profile_ref, recorded_at, retain_until FROM "
                    "private_delivery_audit WHERE organization_id = :org "
                    "ORDER BY final_status"
                ),
                {"org": scenario.organization_id},
            ).all()
            assert len(receipts) == 4
            operations_by_attempt: dict[str, set[str]] = {}
            for receipt in receipts:
                operations_by_attempt.setdefault(
                    receipt.delivery_attempt_ref, set()
                ).add(receipt.operation)
            assert set(map(frozenset, operations_by_attempt.values())) == {
                frozenset({"create_placeholder", "finalize_reply"}),
                frozenset({"create_placeholder", "send_private_followup"}),
            }
            assert len({row.delivery_attempt_ref for row in receipts}) == 2
            assert len(audits) == 2
            assert {audit.final_status for audit in audits} == {
                "finalized",
                "private_followup",
            }
            assert all(len(bytes(audit.package_digest)) == 32 for audit in audits)
            assert all(
                audit.audit_profile_ref == "private-delivery-audit-v1"
                and audit.retain_until - audit.recorded_at == timedelta(days=30)
                for audit in audits
            )
            assert "ContextEngine delivers authorized Package context." not in repr(
                audits
            )
            assert not any(
                evidence_ref in repr(audits) for evidence_ref in evidence_refs.values()
            )

            before_mutations = {
                table: connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE organization_id "
                        "IN (:org, :other_org)"
                    ),
                    {
                        "org": scenario.organization_id,
                        "other_org": other_organization_id,
                    },
                ).scalar_one()
                for table in (
                    "action_delivery_attempt",
                    "action_receipt",
                    "context_run",
                    "decision_audit",
                    "private_delivery_audit",
                )
            }

        mutation_outcomes: dict[str, dict[str, object]] = {}
        mutation_inputs = {
            **mutation_refs,
            "forged": "der_" + "f" * 64,
        }
        for label, mutation_ref in mutation_inputs.items():
            mutation_document = mutation_documents.get(
                label, issue_documents["finalize"]
            )
            outcome = _run_installed_private_bot_flow(
                consumer_root,
                action_database_url=action_configuration.url.set(
                    drivername="postgresql"
                ).render_as_string(hide_password=False),
                audience_digest=private_delivery_audience_digest(mutation_document),
                base_url=f"http://127.0.0.1:{port}",
                citation_delivery_evidence_ref=evidence_refs["citation"],
                egress_database_url=egress_configuration.url.set(
                    drivername="postgresql"
                ).render_as_string(hide_password=False),
                finalize_delivery_evidence_ref=mutation_ref,
                followup_delivery_evidence_ref=mutation_ref,
                membership_id=scenario.membership_id,
                organization_id=scenario.organization_id,
                prime_delivery_evidence_ref=evidence_refs["prime"],
                user_id=user_id,
                flow_mode="answer_only",
            )
            mutation_outcomes[label] = outcome
            assert outcome["finalized"] == {"kind": "delivery_not_available"}
            assert outcome["followedUp"] == {"kind": "delivery_not_available"}
            gateway_counts = outcome["gateway"]
            sender_counts = outcome["sender"]
            assert isinstance(gateway_counts, dict)
            assert isinstance(sender_counts, dict)
            assert gateway_counts == {"callCount": 0, "outboundBytes": 0}
            assert sender_counts == {"callCount": 0, "effectCount": 0}

        unbound_identity_outcome = _run_installed_private_bot_flow(
            consumer_root,
            action_database_url=action_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            audience_digest=private_delivery_audience_digest(
                issue_documents["finalize"]
            ),
            base_url=f"http://127.0.0.1:{port}",
            citation_delivery_evidence_ref=evidence_refs["citation"],
            egress_database_url=egress_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            finalize_delivery_evidence_ref=evidence_refs["finalize"],
            followup_delivery_evidence_ref=evidence_refs["followup"],
            membership_id=scenario.membership_id,
            organization_id=scenario.organization_id,
            prime_delivery_evidence_ref=evidence_refs["prime"],
            user_id=user_id,
            flow_mode="finalize_only",
            event_mode="unbound_identity",
        )
        mutation_outcomes["unbound_identity"] = unbound_identity_outcome
        assert unbound_identity_outcome == {
            "citation": None,
            "finalized": {"kind": "delivery_not_available"},
            "followedUp": None,
            "gateway": {"callCount": 0, "outboundBytes": 0},
            "sender": {"callCount": 0, "effectCount": 0},
        }

        wrong_organization_outcome = _run_installed_private_bot_flow(
            consumer_root,
            action_database_url=action_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            audience_digest=private_delivery_audience_digest(
                wrong_organization_issue
            ),
            base_url=f"http://127.0.0.1:{port}",
            citation_delivery_evidence_ref=evidence_refs["citation"],
            egress_database_url=egress_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            finalize_delivery_evidence_ref=wrong_organization_ref,
            followup_delivery_evidence_ref=wrong_organization_ref,
            membership_id=scenario.membership_id,
            organization_id=scenario.organization_id,
            prime_delivery_evidence_ref=evidence_refs["prime"],
            user_id=user_id,
            flow_mode="finalize_only",
        )
        mutation_outcomes["wrong_organization"] = wrong_organization_outcome
        assert wrong_organization_outcome == {
            "citation": None,
            "finalized": {"kind": "delivery_not_available"},
            "followedUp": None,
            "gateway": {"callCount": 0, "outboundBytes": 0},
            "sender": {"callCount": 0, "effectCount": 0},
        }

        for label, options, expected in (
            (
                "empty_package",
                {"question": "No authorized exact File context exists."},
                {
                    "gateway": {"callCount": 0, "outboundBytes": 0},
                    "sender": {"callCount": 1, "effectCount": 1},
                },
            ),
            (
                "stale_audience",
                {"binding_audience_digest": "f" * 64},
                {
                    "gateway": {"callCount": 0, "outboundBytes": 0},
                    "sender": {"callCount": 1, "effectCount": 1},
                },
            ),
            (
                "stale_epoch",
                {"binding_policy_epoch": 2},
                {
                    "gateway": {"callCount": 0, "outboundBytes": 0},
                    "sender": {"callCount": 1, "effectCount": 1},
                },
            ),
            (
                "model_failure",
                {"model_mode": "invalid_output"},
                {
                    "gateway": {"callCount": 1, "outboundBytesPositive": True},
                    "sender": {"callCount": 1, "effectCount": 1},
                },
            ),
            (
                "sender_rejected",
                {"sender_mode": "rejected"},
                {
                    "gateway": {"callCount": 0, "outboundBytes": 0},
                    "sender": {"callCount": 1, "effectCount": 0},
                },
            ),
            (
                "sender_ambiguous",
                {"sender_mode": "ambiguous"},
                {
                    "gateway": {"callCount": 0, "outboundBytes": 0},
                    "sender": {"callCount": 1, "effectCount": 1},
                },
            ),
        ):
            outcome = _run_installed_private_bot_flow(
                consumer_root,
                action_database_url=action_configuration.url.set(
                    drivername="postgresql"
                ).render_as_string(hide_password=False),
                audience_digest=private_delivery_audience_digest(
                    issue_documents["finalize"]
                ),
                base_url=f"http://127.0.0.1:{port}",
                citation_delivery_evidence_ref=evidence_refs["citation"],
                egress_database_url=egress_configuration.url.set(
                    drivername="postgresql"
                ).render_as_string(hide_password=False),
                finalize_delivery_evidence_ref=evidence_refs["finalize"],
                followup_delivery_evidence_ref=evidence_refs["followup"],
                membership_id=scenario.membership_id,
                organization_id=scenario.organization_id,
                prime_delivery_evidence_ref=evidence_refs["prime"],
                user_id=user_id,
                flow_mode="finalize_only",
                turn_ref=f"mutation-{label}",
                **options,
            )
            mutation_outcomes[label] = outcome
            finalized_outcome = outcome["finalized"]
            assert isinstance(finalized_outcome, dict)
            assert finalized_outcome["kind"] in {
                "delivery_not_available",
                "delivery_reconciliation_required",
            }
            assert outcome["followedUp"] is None
            assert outcome["citation"] is None
            gateway = outcome["gateway"]
            sender = outcome["sender"]
            assert isinstance(gateway, dict)
            assert isinstance(sender, dict)
            for key, value in expected["gateway"].items():
                if key == "outboundBytesPositive":
                    assert gateway["outboundBytes"] > 0, label
                else:
                    assert gateway[key] == value, label
            assert sender == expected["sender"], label

        with migration_engine.connect() as connection:
            mutation_receipt_count = connection.execute(
                text(
                    "SELECT count(*) FROM action_receipt WHERE organization_id = :org"
                ),
                {"org": scenario.organization_id},
            ).scalar_one()
            mutation_audit_count = connection.execute(
                text(
                    "SELECT count(*) FROM private_delivery_audit "
                    "WHERE organization_id = :org"
                ),
                {"org": scenario.organization_id},
            ).scalar_one()
        assert mutation_receipt_count == before_mutations["action_receipt"] + 4
        assert mutation_audit_count == before_mutations["private_delivery_audit"]

        revoked = False

        def revoke_before_resolve(
            headers: tuple[tuple[bytes, bytes], ...],
        ) -> None:
            nonlocal revoked
            observed = dict(headers)
            if (
                observed.get(b"x-context-request-id") != b"bot-live-revoked"
                or revoked
            ):
                return
            PostgreSQLAccessPolicyControl(guarded_control_engine).change_access(
                ResourceAccessRevocation(
                    organization_id=scenario.organization_id,
                    resource_ref=published.candidate_ref.resource_ref,
                    principal_ref="principal:file-reader",
                    expected_access_version=1,
                )
            )
            revoked = True

        transport_observer.set_before_resolve(revoke_before_resolve)
        revoked_outcome = _run_installed_private_bot_flow(
            consumer_root,
            action_database_url=action_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            audience_digest=private_delivery_audience_digest(
                issue_documents["revoked"]
            ),
            base_url=f"http://127.0.0.1:{port}",
            citation_delivery_evidence_ref=evidence_refs["citation"],
            egress_database_url=egress_configuration.url.set(
                drivername="postgresql"
            ).render_as_string(hide_password=False),
            finalize_delivery_evidence_ref=evidence_refs["revoked"],
            followup_delivery_evidence_ref=evidence_refs["followup"],
            membership_id=scenario.membership_id,
            organization_id=scenario.organization_id,
            prime_delivery_evidence_ref=evidence_refs["prime"],
            user_id=user_id,
            flow_mode="finalize_only",
            request_id="bot-live-revoked",
            turn_ref="mutation-revoked-package",
        )
        transport_observer.set_before_resolve(None)
        mutation_outcomes["revoked_package"] = revoked_outcome
        assert revoked is True
        assert revoked_outcome == {
            "citation": None,
            "finalized": {"kind": "delivery_not_available"},
            "followedUp": None,
            "gateway": {"callCount": 0, "outboundBytes": 0},
            "sender": {"callCount": 1, "effectCount": 1},
        }

        with migration_engine.connect() as connection:
            after_mutations = {
                table: connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE organization_id "
                        "IN (:org, :other_org)"
                    ),
                    {
                        "org": scenario.organization_id,
                        "other_org": other_organization_id,
                    },
                ).scalar_one()
                for table in before_mutations
            }
        assert after_mutations == {
            **before_mutations,
            "action_delivery_attempt": (
                before_mutations["action_delivery_attempt"] + 7
            ),
            "action_receipt": before_mutations["action_receipt"] + 5,
            "context_run": before_mutations["context_run"] + 4,
            "decision_audit": before_mutations["decision_audit"] + 1,
        }

    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
            assert not server_thread.is_alive()
        identity_engine.dispose()
        with migration_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM delivery_evidence WHERE organization_id = :org"),
                {"org": other_organization_id},
            )
            connection.execute(
                text("DELETE FROM membership WHERE organization_id = :org"),
                {"org": other_organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM user_account WHERE user_id = :user_id "
                    "AND NOT EXISTS (SELECT 1 FROM membership "
                    "WHERE membership.user_id = :user_id)"
                ),
                {"user_id": other_user_id},
            )
            connection.execute(
                text("DELETE FROM organization WHERE organization_id = :org"),
                {"org": other_organization_id},
            )
