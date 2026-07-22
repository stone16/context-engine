from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.contracts import ContinueWire, OpenCitationWire
from adapters.http.scope_authority import (
    ScopeAuthorityIdentity,
    ScopeAuthorityUnavailable,
)
from engine.runtime.capabilities import (
    RuntimeCapability,
    RuntimeRefusalCategory,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from tests.support.context_run import TEST_QUERY_DIGEST_KEYRING
from tests.unit.test_http_effective_scope import (
    DeterministicScopeAuthority,
    operands,
)
from tests.unit.test_http_trust_boundary import (
    RECEIVED_AT,
    VALID_BODY,
    VALID_TOKEN,
    DeterministicAuthenticator,
    DeterministicMembershipAuthority,
    DeterministicOrganizationAuthority,
    DownstreamContentIoSpy,
    InvocationSpy,
    reachable_schemas,
)


class ProhibitedScopeAuthority:
    def __init__(self) -> None:
        self.calls = 0

    @contextmanager
    def current_scope(self, identity: ScopeAuthorityIdentity) -> Iterator[Any]:
        del identity
        self.calls += 1
        raise ScopeAuthorityUnavailable
        yield cast(Any, None)


def client_for(
    *,
    acquire_capability: RuntimeCapability = RuntimeCapability.MATERIALIZED_ACQUIRE,
    detect_materialized_discovery: bool = False,
    scope_authority: Any | None = None,
) -> tuple[
    TestClient,
    DownstreamContentIoSpy,
    Runtime,
    InvocationSpy,
]:
    content_io = DownstreamContentIoSpy()
    runtime = Runtime(
        required_kernel_dependencies(),
        content_io=RuntimeContentIo(
            index=content_io,
            provider=content_io,
            source_content=content_io,
        ),
        candidate_index=(content_io if detect_materialized_discovery else None),
        acquire_capability=acquire_capability,
        clock=lambda: RECEIVED_AT,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )
    invocation_spy = InvocationSpy()
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=(
                scope_authority
                if scope_authority is not None
                else DeterministicScopeAuthority(operands())
            ),
            runtime=runtime,
            invocation_observer=invocation_spy.observe,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: "unavailable-http-request",
        )
    )
    return client, content_io, runtime, invocation_spy


def assert_unsupported_audit(runtime: Runtime, expected_count: int) -> None:
    assert runtime._unsupported_capability_audit_snapshot() == (
        RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY,
        expected_count,
        0,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "continue", "continuationToken": "opaque-token"},
        {"kind": "open_citation", "citationOpenRef": "opaque-citation"},
    ],
)
def test_unavailable_variants_skip_scope_authority_before_generic_outcome(
    body: dict[str, object],
) -> None:
    authority = ProhibitedScopeAuthority()
    client, content_io, runtime, invocations = client_for(
        scope_authority=authority,
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=body,
    )

    assert response.status_code == 200
    assert response.json()["kind"] in {
        "request_not_available",
        "citation_not_available",
    }
    assert authority.calls == 0
    assert content_io.total_calls == 0
    assert len(invocations.invocations) == 1
    assert_unsupported_audit(runtime, 1)


def test_active_acquire_still_requires_the_configured_scope_authority() -> None:
    authority = ProhibitedScopeAuthority()
    client, content_io, runtime, invocations = client_for(
        scope_authority=authority,
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert authority.calls == 1
    assert content_io.total_calls == 0
    assert invocations.invocations == []
    assert_unsupported_audit(runtime, 0)


@pytest.mark.parametrize(
    "token",
    ("opaque-epoch-8-token", "opaque-revoked-token", "opaque-missing-token"),
)
def test_accept_005_continue_is_generic_non_retryable_and_zero_io(token: str) -> None:
    client, content_io, runtime, invocations = client_for()

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={
            "kind": "continue",
            "continuationToken": token,
            "packageBudget": {"maxTokens": 1},
        },
    )

    assert response.status_code == 200
    assert response.content == (
        b'{"kind":"request_not_available","retryable":false}'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-context-request-id"] == "unavailable-http-request"
    assert token not in response.text
    assert content_io.total_calls == 0
    assert len(invocations.invocations) == 1
    assert_unsupported_audit(runtime, 1)


@pytest.mark.parametrize(
    "locator",
    ("citation-revoked", "citation-missing", "continuation-shaped-value"),
)
def test_accept_010_open_citation_is_generic_and_zero_io(locator: str) -> None:
    client, content_io, runtime, invocations = client_for()

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={"kind": "open_citation", "citationOpenRef": locator},
    )

    assert response.status_code == 200
    assert response.content == b'{"kind":"citation_not_available"}'
    assert locator not in response.text
    assert content_io.total_calls == 0
    assert len(invocations.invocations) == 1
    assert_unsupported_audit(runtime, 1)


@pytest.mark.parametrize(
    ("case_id", "acquire_capability"),
    [
        ("FEDERATED", RuntimeCapability.FEDERATED_DISCOVERY),
        ("PROV-010", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
        ("PROV-013", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
        ("PROV-014", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
        ("PROV-015", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
        ("PROV-018", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
        ("PROV-019", RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION),
    ],
)
def test_accept_009_server_owned_unavailable_source_paths_are_generic_and_zero_io(
    case_id: str,
    acquire_capability: RuntimeCapability,
) -> None:
    client, content_io, runtime, invocations = client_for(
        acquire_capability=acquire_capability,
        detect_materialized_discovery=True,
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert response.content == (
        b'{"kind":"request_not_available","retryable":false}'
    )
    assert case_id not in response.text
    assert content_io.index_calls == 0
    assert content_io.provider_calls == 0
    assert content_io.source_content_calls == 0
    assert len(invocations.invocations) == 1
    assert_unsupported_audit(runtime, 1)


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "unknown", "opaque": "value"},
        {"kind": "acquire", "need": {"query": "probe"}, "sourceMode": "federated"},
        {
            "kind": "acquire",
            "need": {"query": "probe"},
            "requiredCapabilities": ["source-native"],
        },
        {"kind": "continue", "continuationToken": "token", "sourceMode": "x"},
        {"kind": "continue", "continuationToken": ""},
        {"kind": "continue", "continuationToken": "contains whitespace"},
        {"kind": "continue", "continuationToken": "x" * 4097},
        {"kind": "continue", "citationOpenRef": "wrong-capability-class"},
        {"kind": "open_citation", "citationOpenRef": ""},
        {"kind": "open_citation", "citationOpenRef": "contains whitespace"},
        {"kind": "open_citation", "continuationToken": "wrong-capability-class"},
    ],
)
def test_unknown_variants_and_caller_authored_capabilities_are_422_before_runtime(
    body: dict[str, object],
) -> None:
    client, content_io, runtime, invocations = client_for()

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=body,
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert content_io.total_calls == 0
    assert invocations.invocations == []
    assert_unsupported_audit(runtime, 0)


@pytest.mark.parametrize(
    "query",
    (
        "sourceMode=federated",
        "requiredCapabilities=source_native",
        "kind=continue&continuationToken=opaque",
        "unrelated=value",
    ),
)
def test_query_parameters_are_rejected_as_closed_schema_violations(
    query: str,
) -> None:
    client, content_io, runtime, invocations = client_for()

    response = client.post(
        f"/v1/context:resolve?{query}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert content_io.total_calls == 0
    assert invocations.invocations == []
    assert_unsupported_audit(runtime, 0)


def test_known_unavailable_variant_still_requires_transport_authentication() -> None:
    client, content_io, runtime, invocations = client_for()

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer invalid"},
        json={"kind": "continue", "continuationToken": "opaque-token"},
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'
    assert content_io.total_calls == 0
    assert invocations.invocations == []
    assert_unsupported_audit(runtime, 0)


def test_refusal_audit_retains_only_the_safe_internal_typed_category() -> None:
    client, _, runtime, _ = client_for()
    opaque_value = "secret-opaque-capability-value"

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={"kind": "continue", "continuationToken": opaque_value},
    )

    assert response.status_code == 200
    snapshot = runtime._unsupported_capability_audit_snapshot()
    assert snapshot == (RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY, 1, 0)
    assert opaque_value not in repr(snapshot)
    assert "continue" not in repr(snapshot).casefold()


def test_opaque_wire_models_redact_capability_values_from_repr() -> None:
    opaque_token = "continuation-secret-marker"
    opaque_locator = "citation-secret-marker"

    continuation = ContinueWire(
        kind="continue",
        continuationToken=opaque_token,
    )
    citation = OpenCitationWire(
        kind="open_citation",
        citationOpenRef=opaque_locator,
    )

    assert opaque_token not in repr(continuation)
    assert opaque_locator not in repr(citation)


def test_openapi_freezes_the_closed_three_variant_request_and_outcome_unions(
) -> None:
    client, _, _, _ = client_for()
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/context:resolve"]["post"]
    components = schema["components"]["schemas"]

    request_schema = operation["requestBody"]["content"]["application/json"][
        "schema"
    ]
    request_union_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    assert request_union_name == "ResolveWire"
    request_union_schema = components[request_union_name]
    assert request_union_schema["discriminator"]["propertyName"] == "kind"
    request_models = reachable_schemas(request_schema, components)
    assert set(request_models) == {
        "ResolveWire",
        "AcquireWire",
        "ContinueWire",
        "OpenCitationWire",
        "ContextNeedWire",
        "PackageBudgetWire",
        "RequestNarrowingWire",
    }
    assert request_models["ContinueWire"]["properties"].keys() == {
        "kind",
        "continuationToken",
        "packageBudget",
    }
    assert request_models["OpenCitationWire"]["properties"].keys() == {
        "kind",
        "citationOpenRef",
    }
    assert all(
        model["additionalProperties"] is False
        for name, model in request_models.items()
        if name != "ResolveWire"
    )
    request_graph = repr(request_models).casefold()
    for forbidden in (
        "organization",
        "principal",
        "purpose",
        "sourcemode",
        "requiredcapabilities",
        "sourcenativeacl",
    ):
        assert forbidden not in request_graph

    response_schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    response_union_name = response_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    assert response_union_name == "ResolutionOutcomeWire"
    assert components[response_union_name]["discriminator"]["propertyName"] == "kind"
    response_models = reachable_schemas(response_schema, components)
    assert {
        "ResolvedWire",
        "RequestNotAvailableWire",
        "CitationNotAvailableWire",
    }.issubset(response_models)
    assert response_models["RequestNotAvailableWire"]["required"] == [
        "kind",
        "retryable",
    ]
    assert response_models["CitationNotAvailableWire"]["required"] == ["kind"]
    public_refusals = {
        name: response_models[name]
        for name in ("RequestNotAvailableWire", "CitationNotAvailableWire")
    }
    assert all(
        model["additionalProperties"] is False for model in public_refusals.values()
    )
    refusal_graph = repr(public_refusals).casefold()
    for forbidden in (
        "capability",
        "token",
        "citationopenref",
        "source",
        "resource",
        "reason",
        "category",
    ):
        assert forbidden not in refusal_graph


def test_existing_http_acquire_remains_a_resolved_package() -> None:
    client, content_io, runtime, _ = client_for()

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "resolved"
    assert response.json()["package"]["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    assert content_io.total_calls == 0
    assert_unsupported_audit(runtime, 0)
