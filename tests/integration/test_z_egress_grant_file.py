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
    PostgreSQLAccessPolicyControl,
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
        cleanup_triggers = (
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
                    "citation_open_locator",
                    "decision_audit",
                    "context_run",
                    "model_egress_audit",
                    "egress_audit",
                    "egress_grant",
                    "delivery_evidence",
                    "file_source_publish_watermark",
                    "file_source_acquisition_checkpoint",
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
