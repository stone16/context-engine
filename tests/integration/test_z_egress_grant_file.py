from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Thread
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn import Config, Server

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from bot_delivery.egress import (
    DeterministicModelGatewaySpy,
    ModelEgressBoundary,
    prepare_authorized_model_input,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLDeliveryEvidenceIssuerPort,
    PostgreSQLEgressGrantRedemptionAuthority,
    PostgreSQLMembershipAuthority,
    PublishedFileImport,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.contracts import Resolved
from engine.runtime.delivery_evidence import (
    DeliveryEvidenceProfile,
    PrivateDeliveryEvidenceIssue,
    PrivateDeliveryEvidenceIssuer,
)
from engine.runtime.egress import (
    EgressGrantNotAvailable,
    ModelEgressGrant,
    ModelEgressProfile,
    direct_egress_audience_digest,
)
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_file_import_tracer import (
    NOW,
    _ExactScopeAuthority,
    _FileImportScenario,
    _OrganizationAuthority,
    _prepare_file_import_scenario,
    _run_file_import,
    _RuntimeAuthenticator,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

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
    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self.requests: list[tuple[tuple[bytes, bytes], ...]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/v0/resolve"
        ):
            self.requests.append(tuple(scope["headers"]))
        await self._app(scope, receive, send)


def _assert_sdk_transport_headers(
    headers: tuple[tuple[bytes, bytes], ...],
    *,
    authentication: bytes,
    delivery_evidence_ref: bytes | None,
) -> None:
    observed: dict[bytes, list[bytes]] = {}
    for name, value in headers:
        observed.setdefault(name.lower(), []).append(value)

    expected_context_headers = {
        b"x-context-request-id": [b"file-egress-sdk-http"],
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
        SDK_STANDARD_HTTP_HEADERS
        | {b"authorization"}
        | set(expected_context_headers)
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
    (consumer_root / "package.json").write_text(
        json.dumps(
            {
                "name": "context-engine-live-sdk-consumer",
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
    (consumer_root / "live-consumer.mjs").write_bytes(
        (ROOT / "sdk/typescript/test/live-consumer.mjs").read_bytes()
    )


def _run_installed_live_consumer(
    consumer_root: Path,
    *,
    base_url: str,
    delivery_evidence_ref: str,
) -> dict[str, object]:
    result = _run_sdk_process(
        ["node", "live-consumer.mjs"],
        cwd=consumer_root,
        env={
            **os.environ,
            "CONTEXT_ENGINE_SDK_BASE_URL": base_url,
            "CONTEXT_ENGINE_SDK_DELIVERY_EVIDENCE_REF": delivery_evidence_ref,
            "CONTEXT_ENGINE_SDK_REQUEST_ID": "file-egress-sdk-http",
            "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION": "runtime-secret",
            "CONTEXT_ENGINE_SDK_TEST_DIRECT_AUTHENTICATION": (
                "runtime-direct-secret"
            ),
        },
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
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


@pytest.fixture
def _published_file_scenario(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> Iterator[tuple[_FileImportScenario, PublishedFileImport, Engine]]:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    migration_engine = create_database_engine(migration_configuration)
    try:
        published = _run_file_import(
            scenario,
            scenario.prepared,
            scenario.token,
            guarded_worker_engine,
        )
        ensure_test_runtime_release(
            scenario.organization_id,
            active_revision_refs=(published.candidate_ref.revision_ref,),
        )
        yield scenario, published, migration_engine
    finally:
        clear_test_runtime_release(scenario.organization_id)
        with migration_engine.begin() as connection:
            for table in (
                "decision_audit",
                "context_run",
                "egress_audit",
                "egress_grant",
                "delivery_evidence",
            ):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :org"),
                    {"org": scenario.organization_id},
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
                    clock=lambda: NOW,
                    query_digest_keyring=query_digest_keyring,
                ),
                resolution_observer=observed.append,
                clock=lambda: NOW,
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


@pytest.mark.security_evidence(id="SDK-LIVE-FILE-064", layer="runtime")
def test_packed_typescript_sdk_resolves_authorized_file_package_over_live_http(
    _published_file_scenario: tuple[_FileImportScenario, PublishedFileImport, Engine],
    tmp_path: Path,
    identity_configuration: DatabaseConfiguration,
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
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).scalar_one()

        consumer_root = tmp_path / "installed-sdk-consumer"
        consumer_root.mkdir()
        _pack_and_install_sdk(consumer_root)
        request_now = datetime.now(UTC).replace(microsecond=0)

        evidence_ref = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=15),
            ),
            reference_factory=lambda: "der_"
            + sha256(scenario.organization_id.bytes + b"sdk-http").hexdigest(),
            resolution_ref_factory=lambda: "dlr_"
            + sha256(scenario.organization_id.bytes + b"sdk-result").hexdigest()[:32],
        ).issue_private(
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
        assert result["continuation"] == {
            "kind": "request_not_available",
            "retryable": False,
        }
        assert result["citation"] == {"kind": "citation_not_available"}
        assert len(transport_observer.requests) == 3
        _assert_sdk_transport_headers(
            transport_observer.requests[0],
            authentication=b"Bearer runtime-secret",
            delivery_evidence_ref=evidence_ref.evidence_ref.encode("ascii"),
        )
        for direct_request_headers in transport_observer.requests[1:]:
            _assert_sdk_transport_headers(
                direct_request_headers,
                authentication=b"Bearer runtime-direct-secret",
                delivery_evidence_ref=None,
            )
        assert len(observed) == 1
        assert observed[0].package.decision_ref == package["decisionRef"]
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
            assert not server_thread.is_alive()
        identity_engine.dispose()
