from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from mcp import Client, ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import TypeAdapter

from adapters.http.app import create_app
from adapters.http.authentication import DogfoodAuthenticator
from adapters.http.contracts import AcquireWire, ResolutionOutcomeWire
from adapters.http.dogfood_client import DOGFOOD_BASE_URL_ENV, DOGFOOD_SECRET_ENV
from adapters.mcp.server import MCP_TOOL_NAME, ResolveCaller, create_mcp_server
from engine.runtime import Runtime
from engine.runtime.capabilities import RuntimeCapability
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.package_digest import context_package_digest
from tests.support.context_run import TEST_QUERY_DIGEST_KEYRING
from tests.support.resolve_parity import without_request_scoped_resolve_fields
from tests.unit.test_http_effective_scope import DeterministicScopeAuthority, operands
from tests.unit.test_http_trust_boundary import (
    RECEIVED_AT,
    VALID_TOKEN,
    DeterministicAuthenticator,
    DeterministicMembershipAuthority,
    DeterministicOrganizationAuthority,
    DownstreamContentIoSpy,
    RejectingTestMembershipAuthority,
    RejectingTestOrganizationAuthority,
    UnavailableTestMembershipAuthority,
)

SECRET = "mcp-secret-with-at-least-thirty-two-bytes"


def _package(
    *,
    text: str | None,
    reason: str | None = None,
    gap: str | None = None,
) -> dict[str, object]:
    evidence_ref = "ev_" + "a" * 64
    blocks: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    if text is not None:
        blocks.append(
            {
                "blockId": "block_" + "a" * 64,
                "text": text,
                "evidenceRefs": [evidence_ref],
            }
        )
        evidence.append(
            {
                "evidenceRef": evidence_ref,
                "sourceRef": "source:file:maintainer",
                "resourceRef": "resource:file:authorized",
                "revisionRef": "revision:current",
                "fragmentRef": "fragment:authorized",
                "projectedFields": ["body"],
                "runRef": "run-mcp-parity",
                "purpose": "context.answer",
                "authorizationAsOf": "2026-08-02T00:00:00Z",
                "decisionRef": "dec_" + "d" * 32,
                "policySnapshotRef": "policy-snapshot-current",
                "policyEpoch": 7,
                "sourceAclEvidence": {
                    "kind": "mirrored",
                    "projectionRef": "source-acl-current",
                    "aclAsOf": "2026-08-02T00:00:00Z",
                    "freshnessProfileRef": "file-source-current-v1",
                },
                "citationOpenRef": None,
            }
        )
    coverage: dict[str, object] = {
        "status": "sufficient" if text is not None else "empty"
    }
    if reason is not None:
        coverage["reason"] = reason
    package: dict[str, object] = {
        "packageId": "pkg_" + "b" * 32,
        "purpose": "context.answer",
        "audienceDigest": "c" * 64,
        "policyEpoch": 7,
        "policySnapshotRef": "policy-snapshot-current",
        "decisionRef": "dec_" + "d" * 32,
        "runRef": "run-mcp-parity",
        "releaseManifestRef": "release-current",
        "retentionPolicyRef": "package-digest-only-v1",
        "asOf": "2026-08-02T00:00:00Z",
        "expiresAt": "2026-08-02T00:05:00Z",
        "ttlSeconds": 300,
        "tokenizerRef": "utf8-byte-v1",
        "packageSchemaRef": "context-package-openapi-v0",
        "blocks": blocks,
        "evidence": evidence,
        "gaps": ([] if gap is None else [{"category": gap, "retryable": False}]),
        "coverage": coverage,
        "budgetUsage": {
            "tokens": 0 if text is None else len(text.encode("utf-8")),
            "providerCalls": 0,
            "costMicrounits": 0,
            "elapsedMs": 0,
        },
        "continuation": None,
    }
    package["packageDigest"] = context_package_digest(package)
    return package


def _resolved_package(**kwargs: str | None) -> dict[str, object]:
    return {"kind": "resolved", "package": _package(**kwargs), "egressGrant": None}


class RecordingResolveCaller:
    def __init__(self, outcome: dict[str, object]) -> None:
        self._outcome = outcome
        self.calls: list[tuple[dict[str, object], str]] = []

    def resolve_acquire_document(
        self,
        *,
        acquire: dict[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self.calls.append((acquire, request_id))
        return self._outcome


@pytest.mark.security_evidence(id="MCP-CONTRACT-215", layer="runtime")
def test_mcp_lists_one_exact_acquire_tool_without_trusted_inputs() -> None:
    caller = RecordingResolveCaller(
        {"kind": "request_not_available", "retryable": False}
    )

    async def exercise() -> None:
        async with Client(create_mcp_server(cast(ResolveCaller, caller))) as client:
            listed = await client.list_tools()

        assert len(listed.tools) == 1
        tool = listed.tools[0]
        assert tool.name == MCP_TOOL_NAME == "context_resolve"
        assert tool.input_schema == AcquireWire.model_json_schema()
        assert tool.output_schema == {
            "type": "object",
            **TypeAdapter(ResolutionOutcomeWire).json_schema(),
        }
        serialized_schema = str(tool.input_schema).casefold()
        for forbidden in (
            "organization",
            "principal",
            "membership",
            "audience",
            "accessticket",
            "authenticatedinvocation",
            "trusteddeliverycontext",
            "deliveryevidenceref",
            "egressgrant",
        ):
            assert forbidden not in serialized_schema

    asyncio.run(exercise())


def test_mcp_forwards_exact_acquire_and_returns_exact_http_outcome() -> None:
    outcome = {"kind": "request_not_available", "retryable": False}
    caller = RecordingResolveCaller(outcome)
    acquire: dict[str, object] = {
        "kind": "acquire",
        "need": {"query": "Which context is currently authorized?"},
        "packageBudget": {"maxTokens": 128, "maxElapsedMs": 2000},
        "requestNarrowing": {
            "sourceRefs": ["source:file:maintainer"],
            "resourceRefs": ["resource:file:authorized"],
        },
    }

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                cast(ResolveCaller, caller),
                request_id_factory=lambda: "mcp-session-request-1",
            )
        ) as client:
            result = await client.call_tool(MCP_TOOL_NAME, acquire)

        assert result.is_error is False
        assert result.content == []
        assert result.structured_content == outcome

    asyncio.run(exercise())
    assert caller.calls == [(acquire, "mcp-session-request-1")]


@pytest.mark.parametrize(
    "http_outcome",
    (
        _resolved_package(text="Authorized MCP context."),
        _resolved_package(text=None, reason="no_authorized_evidence"),
        _resolved_package(
            text=None,
            reason="stale_evidence",
            gap="stale_evidence",
        ),
        _resolved_package(
            text=None,
            reason="budget_exhausted",
            gap="budget_exhausted",
        ),
        {"kind": "request_not_available", "retryable": False},
    ),
    ids=("allowed", "revoked", "stale", "budget", "unavailable"),
)
def test_mcp_preserves_every_acquire_http_outcome_without_reinterpretation(
    http_outcome: dict[str, object],
) -> None:
    caller = RecordingResolveCaller(deepcopy(http_outcome))

    async def exercise() -> None:
        async with Client(create_mcp_server(cast(ResolveCaller, caller))) as client:
            result = await client.call_tool(
                MCP_TOOL_NAME,
                {"kind": "acquire", "need": {"query": "parity probe"}},
            )

        assert result.is_error is False
        assert result.content == []
        assert result.structured_content == http_outcome

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "failure",
    (
        RuntimeError("authentication failed"),
        ValueError("wrong Organization"),
        TypeError("malformed HTTP outcome"),
    ),
    ids=("authentication", "wrong-organization", "malformed-outcome"),
)
def test_mcp_collapses_non_outcome_failures_to_one_content_free_error(
    failure: Exception,
) -> None:
    class FailingCaller:
        def resolve_acquire_document(
            self,
            *,
            acquire: dict[str, object],
            request_id: str,
        ) -> dict[str, object]:
            del acquire, request_id
            raise failure

    async def exercise() -> None:
        async with Client(
            create_mcp_server(cast(ResolveCaller, FailingCaller()))
        ) as client:
            result = await client.call_tool(
                MCP_TOOL_NAME,
                {"kind": "acquire", "need": {"query": "failure probe"}},
            )

        assert result.is_error is True
        assert result.structured_content is None
        assert [item.text for item in result.content if hasattr(item, "text")] == [
            "Context resolve is unavailable."
        ]
        assert str(failure) not in str(result.content)

    asyncio.run(exercise())


@pytest.mark.security_evidence(id="MCP-TRUSTED-INPUT-215", layer="runtime")
def test_mcp_rejects_trusted_field_injection_before_http() -> None:
    caller = RecordingResolveCaller(
        {"kind": "request_not_available", "retryable": False}
    )
    injected = {
        "kind": "acquire",
        "need": {"query": "probe"},
        "organizationRef": "caller-authored-org",
    }

    async def exercise() -> None:
        async with Client(create_mcp_server(cast(ResolveCaller, caller))) as client:
            result = await client.call_tool(MCP_TOOL_NAME, injected)

        assert result.is_error is True
        assert result.structured_content is None
        assert all("caller-authored-org" not in str(item) for item in result.content)

    asyncio.run(exercise())
    assert caller.calls == []


@pytest.mark.security_evidence(id="MCP-STDIO-HTTP-215", layer="runtime")
def test_spawned_stdio_session_calls_only_loopback_http_and_preserves_outcome() -> None:
    observed: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            observed.append(
                {
                    "authorization": self.headers["Authorization"],
                    "body": json.loads(self.rfile.read(length)),
                    "path": self.path,
                    "request_id": self.headers["X-Context-Request-Id"],
                }
            )
            body = b'{"kind":"request_not_available","retryable":false}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()

    async def exercise() -> None:
        environment = {
            **os.environ,
            DOGFOOD_BASE_URL_ENV: f"http://127.0.0.1:{server.server_port}",
            DOGFOOD_SECRET_ENV: SECRET,
        }
        parameters = StdioServerParameters(
            command="context-engine-mcp",
            env=environment,
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            result = await session.call_tool(
                MCP_TOOL_NAME,
                arguments={
                    "kind": "acquire",
                    "need": {"query": "One spawned MCP request"},
                },
            )

        assert [tool.name for tool in listed.tools] == [MCP_TOOL_NAME]
        assert result.is_error is False
        assert result.content == []
        assert result.structured_content == {
            "kind": "request_not_available",
            "retryable": False,
        }

    try:
        asyncio.run(exercise())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert len(observed) == 1
    call = observed[0]
    assert call["authorization"] == f"Bearer {SECRET}"
    assert call["body"] == {
        "kind": "acquire",
        "need": {"query": "One spawned MCP request"},
    }
    assert call["path"] == "/v0/resolve"
    assert isinstance(call["request_id"], str)
    assert call["request_id"].startswith("mcp-")
    assert SECRET not in json.dumps(call["body"])


@pytest.mark.security_evidence(id="MCP-CONFIG-215", layer="runtime")
def test_spawned_stdio_server_fails_closed_without_private_environment() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {DOGFOOD_BASE_URL_ENV, DOGFOOD_SECRET_ENV}
    }

    completed = subprocess.run(
        ["context-engine-mcp"],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-mcp: configuration unavailable\n"


@pytest.mark.security_evidence(id="MCP-ARGUMENTS-215", layer="runtime")
def test_spawned_stdio_server_rejects_process_arguments_content_free() -> None:
    injected = "caller-authored-organization-secret"

    completed = subprocess.run(
        ["context-engine-mcp", injected],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-mcp: arguments are unavailable\n"
    assert injected not in completed.stdout + completed.stderr


@pytest.mark.security_evidence(id="MCP-IMPORT-BOUNDARY-215", layer="runtime")
def test_mcp_process_import_graph_excludes_privileged_modules() -> None:
    probe = """
import json
import sys

import applications.mcp

forbidden = (
    "engine",
    "sqlalchemy",
    "psycopg",
    "adapters.embeddings",
    "adapters.file_source",
    "adapters.fts",
    "adapters.hybrid",
    "adapters.pgvector",
    "applications.dogfood_evaluation",
)
loaded = sorted(
    module
    for module in sys.modules
    if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
)
print(json.dumps(loaded))
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


@pytest.mark.parametrize(
    (
        "case_name",
        "body",
        "secret",
        "organization_authority",
        "membership_authority",
        "acquire_capability",
    ),
    (
        (
            "allowed",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}},
            SECRET,
            DeterministicOrganizationAuthority(),
            DeterministicMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "missing-tenant",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}},
            SECRET,
            DeterministicOrganizationAuthority(),
            UnavailableTestMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "wrong-organization",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}},
            SECRET,
            RejectingTestOrganizationAuthority(),
            DeterministicMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "revoked-or-stale",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}},
            SECRET,
            DeterministicOrganizationAuthority(),
            RejectingTestMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "malformed",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}, "unknown": 1},
            SECRET,
            DeterministicOrganizationAuthority(),
            DeterministicMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "budget",
            {
                "kind": "acquire",
                "need": {"query": "HTTP/MCP parity"},
                "packageBudget": {"maxTokens": 1},
            },
            SECRET,
            DeterministicOrganizationAuthority(),
            DeterministicMembershipAuthority(),
            RuntimeCapability.MATERIALIZED_ACQUIRE,
        ),
        (
            "unavailable",
            {"kind": "acquire", "need": {"query": "HTTP/MCP parity"}},
            SECRET,
            DeterministicOrganizationAuthority(),
            DeterministicMembershipAuthority(),
            RuntimeCapability.FEDERATED_DISCOVERY,
        ),
    ),
)
@pytest.mark.security_evidence(id="MCP-HTTP-PARITY-215", layer="runtime")
def test_spawned_mcp_matches_the_public_http_seam_for_required_parity_cases(
    case_name: str,
    body: dict[str, object],
    secret: str,
    organization_authority: object,
    membership_authority: object,
    acquire_capability: RuntimeCapability,
) -> None:
    content_io = DownstreamContentIoSpy()
    app = create_app(
        authenticator=DogfoodAuthenticator(
            secret=SECRET,
            authentication=DeterministicAuthenticator().authenticate(VALID_TOKEN),
        ),
        organization_authority=cast(Any, organization_authority),
        membership_authority=cast(Any, membership_authority),
        scope_authority=DeterministicScopeAuthority(operands()),
        runtime=Runtime(
            required_kernel_dependencies(),
            content_io=RuntimeContentIo(
                index=content_io,
                provider=content_io,
                source_content=content_io,
            ),
            clock=lambda: RECEIVED_AT,
            query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
            acquire_capability=acquire_capability,
        ),
        clock=lambda: RECEIVED_AT,
    )
    client = TestClient(app)
    direct = client.post(
        "/v0/resolve",
        headers={
            "Authorization": f"Bearer {SECRET}",
            "X-Context-Request-Id": f"http-parity-{case_name}",
        },
        json=body,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            forwarded = client.post(
                self.path,
                headers={
                    "Authorization": self.headers["Authorization"],
                    "Content-Type": "application/json",
                    "X-Context-Request-Id": f"http-parity-{case_name}",
                },
                content=self.rfile.read(length),
            )
            response_body = forwarded.content
            self.send_response(forwarded.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()

    async def exercise() -> object:
        parameters = StdioServerParameters(
            command="context-engine-mcp",
            env={
                **os.environ,
                DOGFOOD_BASE_URL_ENV: f"http://127.0.0.1:{server.server_port}",
                DOGFOOD_SECRET_ENV: secret,
            },
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            return await session.call_tool(MCP_TOOL_NAME, arguments=body)

    try:
        mcp_result = cast(Any, asyncio.run(exercise()))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    if direct.status_code == 200:
        assert mcp_result.is_error is False
        assert mcp_result.content == []
        assert without_request_scoped_resolve_fields(
            mcp_result.structured_content
        ) == without_request_scoped_resolve_fields(direct.json())
    else:
        assert mcp_result.is_error is True
        assert mcp_result.structured_content is None
        assert [item.text for item in mcp_result.content if hasattr(item, "text")] == [
            "Context resolve is unavailable."
        ]
        assert direct.json() in (
            {"code": "authentication_failed"},
            {"code": "invalid_request"},
            {"code": "service_unavailable"},
        )
