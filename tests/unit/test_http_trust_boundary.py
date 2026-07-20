from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from engine.runtime import (
    AuthenticatedInvocation,
    InvocationConstructionProvenance,
)

VALID_BODY = {
    "kind": "acquire",
    "need": {"query": "Which decisions constrain Runtime delivery?"},
}
VALID_TOKEN = "opaque-test-credential"
RECEIVED_AT = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)


class DeterministicAuthenticator:
    """Test-only opaque credential map representing already verified auth."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        self.calls.append(opaque_credential)
        if opaque_credential != VALID_TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref="organization-from-auth",
            principal_ref="principal-from-auth",
            membership_ref="membership-from-auth",
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class InvalidResultAuthenticator:
    """Test double for a broken adapter that returns caller-shaped data."""

    def authenticate(self, opaque_credential: str) -> Any:
        return {"organization_ref": "caller-shaped"}


class InvalidClaimsAuthenticator:
    """Test double for verified claims that fail nominal validation."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        return VerifiedAuthenticationContext(
            organization_ref="organization-secret-malformed",
            principal_ref=" ",
            membership_ref="membership-from-auth",
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class InvocationSpy:
    def __init__(self) -> None:
        self.invocations: list[AuthenticatedInvocation] = []

    def observe(self, invocation: AuthenticatedInvocation) -> None:
        self.invocations.append(invocation)


def test_valid_auth_constructs_exact_trusted_invocation_once() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "request-from-header",
            "X-Organization-Id": "organization-from-untrusted-header",
        },
        json=VALID_BODY,
    )

    assert response.status_code == 204
    assert response.content == b""
    assert authenticator.calls == [VALID_TOKEN]
    assert len(spy.invocations) == 1
    invocation = spy.invocations[0]
    assert invocation.request_id == "request-from-header"
    assert invocation.organization_ref == "organization-from-auth"
    assert invocation.principal_ref == "principal-from-auth"
    assert invocation.membership_ref == "membership-from-auth"
    assert invocation.agent_version_ref == "agent-version-from-auth"
    assert invocation.authenticated_application_ref == "application-from-auth"
    assert invocation.authentication_binding_ref == "binding-from-auth"
    assert invocation.received_at == RECEIVED_AT
    assert (
        invocation.construction_provenance
        is InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS
    )
    assert VALID_TOKEN not in repr(invocation)


def test_missing_correlation_header_uses_server_generated_request_id() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 204
    assert [invocation.request_id for invocation in spy.invocations] == [
        "server-generated-request"
    ]


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer unknown-opaque-credential",
        "Bearer opaque-test-credential unexpected-suffix",
    ],
)
def test_authentication_failures_are_generic_and_call_no_domain_seam(
    authorization: str | None,
) -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization

    response = client.post(
        "/v1/context:resolve",
        headers=headers,
        json=VALID_BODY,
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'
    assert response.headers["www-authenticate"] == "Bearer"
    assert spy.invocations == []
    assert all(
        protected_name not in response.text
        for protected_name in ("organization", "principal", "membership", "resource")
    )


@pytest.mark.parametrize(
    "authenticator",
    [InvalidResultAuthenticator(), InvalidClaimsAuthenticator()],
)
def test_invalid_authenticator_output_is_a_generic_authentication_failure(
    authenticator: Any,
) -> None:
    spy = InvocationSpy()
    client = TestClient(
        create_app(
            authenticator=authenticator,
            invocation_observer=spy.observe,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'
    assert "organization-secret-malformed" not in response.text
    assert spy.invocations == []


@pytest.mark.parametrize(
    ("placement", "field_name", "field_value"),
    [
        ("top", "organizationId", "organization-conflict"),
        ("top", "tenant", "tenant-conflict"),
        ("top", "principalRef", "principal-conflict"),
        ("top", "userId", "user-conflict"),
        ("top", "membershipId", "membership-conflict"),
        ("top", "agentVersionRef", "agent-conflict"),
        ("top", "purpose", "admin"),
        ("top", "audience", ["everyone"]),
        ("top", "acl", "allow-all"),
        ("top", "rawSql", "select * from organization_record"),
        ("top", "placement", "untrusted-region"),
        ("top", "bypassAuthorization", True),
        ("top", "authenticatedInvocation", {"organizationRef": "other"}),
        ("top", "trustedDeliveryContext", {"purpose": "admin"}),
        ("need", "organizationRef", "nested-organization-conflict"),
        ("need", "principalRef", "nested-principal-conflict"),
        ("need", "sourceAcl", "allow-all"),
        ("need", "filterSql", "true"),
        ("need", "bypass", True),
    ],
)
def test_trusted_field_injection_is_closed_before_domain_execution(
    placement: str,
    field_name: str,
    field_value: object,
) -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)
    body: dict[str, Any] = {
        "kind": "acquire",
        "need": {"query": "trusted boundary probe"},
    }
    target = body if placement == "top" else body["need"]
    assert isinstance(target, dict)
    target[field_name] = field_value

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=body,
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert field_name not in response.text
    assert str(field_value) not in response.text
    assert spy.invocations == []


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "continue", "need": {"query": "not active"}},
        {"kind": "acquire", "need": {"query": "ok"}, "unknown": "field"},
        {"kind": "acquire", "need": {"query": "ok", "unknown": "field"}},
        {"kind": "acquire", "need": {}},
        {"kind": "acquire", "need": {"query": ""}},
        {"kind": "acquire", "need": {"query": " \t "}},
    ],
)
def test_closed_acquire_shape_rejects_every_schema_violation(
    body: dict[str, object],
) -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=body,
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert spy.invocations == []


@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"kind":"acquire","kind":"acquire","need":{"query":"probe"}}',
        b'{"kind":"acquire","need":{"query":"first","query":"second"}}',
        (
            b'{"kind":"acquire",'
            b'"need":{"query":"probe","organizationRef":"injected"},'
            b'"need":{"query":"probe"}}'
        ),
    ],
)
def test_duplicate_json_keys_cannot_shadow_injected_or_unknown_fields(
    raw_body: bytes,
) -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=raw_body,
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert "organizationRef" not in response.text
    assert spy.invocations == []


@pytest.mark.parametrize("non_finite", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_standard_json_numbers_fail_at_the_transport_boundary(
    non_finite: bytes,
) -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)
    raw_body = (
        b'{"kind":"acquire","need":{"query":"probe"},"unknown":'
        + non_finite
        + b"}"
    )

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=raw_body,
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


def test_invalid_json_and_media_type_use_generic_transport_error() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)
    headers = {"Authorization": f"Bearer {VALID_TOKEN}"}

    malformed = client.post(
        "/v1/context:resolve",
        headers={**headers, "Content-Type": "application/json"},
        content=b'{"kind":"acquire",',
    )
    wrong_media_type = client.post(
        "/v1/context:resolve",
        headers={**headers, "Content-Type": "text/plain"},
        content=b'{"kind":"acquire","need":{"query":"probe"}}',
    )

    assert malformed.status_code == 400
    assert malformed.content == b'{"code":"invalid_request"}'
    assert wrong_media_type.status_code == 400
    assert wrong_media_type.content == b'{"code":"invalid_request"}'
    assert spy.invocations == []


def test_invalid_utf8_json_uses_the_documented_generic_transport_error() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=b'{"kind":"acquire","need":{"query":"\xff"}}',
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert spy.invocations == []


@pytest.mark.parametrize(
    ("duplicate_name", "duplicate_values", "expected_status", "expected_body"),
    [
        (
            b"Authorization",
            (b"Bearer opaque-test-credential", b"Bearer invalid"),
            401,
            b'{"code":"authentication_failed"}',
        ),
        (
            b"Content-Type",
            (b"application/json", b"text/plain"),
            400,
            b'{"code":"invalid_request"}',
        ),
        (
            b"X-Context-Request-Id",
            (b"request-one", b"request-two"),
            400,
            b'{"code":"invalid_request"}',
        ),
    ],
)
def test_duplicate_security_transport_headers_fail_closed(
    duplicate_name: bytes,
    duplicate_values: tuple[bytes, bytes],
    expected_status: int,
    expected_body: bytes,
) -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)
    raw_headers = [
        (b"Authorization", f"Bearer {VALID_TOKEN}".encode()),
        (b"Content-Type", b"application/json"),
    ]
    raw_headers = [item for item in raw_headers if item[0] != duplicate_name]
    raw_headers.extend((duplicate_name, value) for value in duplicate_values)

    response = client.post(
        "/v1/context:resolve",
        headers=raw_headers,
        content=b'{"kind":"acquire","need":{"query":"probe"}}',
    )

    assert response.status_code == expected_status
    assert response.content == expected_body
    assert spy.invocations == []


def test_transport_syntax_precedes_authentication_and_schema_follows_it() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)
    invalid_auth = {"Authorization": "Bearer invalid-credential"}

    wrong_media_type = client.post(
        "/v1/context:resolve",
        headers={**invalid_auth, "Content-Type": "text/plain"},
        content=b"not-json",
    )
    malformed_json = client.post(
        "/v1/context:resolve",
        headers={**invalid_auth, "Content-Type": "application/json"},
        content=b"{",
    )
    schema_injection = client.post(
        "/v1/context:resolve",
        headers=invalid_auth,
        json={**VALID_BODY, "organizationId": "injected"},
    )

    assert wrong_media_type.status_code == 400
    assert wrong_media_type.content == b'{"code":"invalid_request"}'
    assert malformed_json.status_code == 400
    assert malformed_json.content == b'{"code":"invalid_request"}'
    assert schema_injection.status_code == 401
    assert schema_injection.content == b'{"code":"authentication_failed"}'
    assert authenticator.calls == ["invalid-credential"]
    assert spy.invocations == []


def test_default_application_rejects_all_credentials() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'


def test_injected_authenticator_requires_the_bounded_observer_seam() -> None:
    with pytest.raises(ValueError, match="invocation observer"):
        create_app(authenticator=DeterministicAuthenticator())


def test_openapi_body_is_closed_and_contains_no_trusted_fields() -> None:
    client = trust_boundary_client(DeterministicAuthenticator(), InvocationSpy())
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v1/context:resolve"]["post"]

    assert operation["security"] == [{"ContextEngineBearer": []}]
    assert schema["components"]["securitySchemes"]["ContextEngineBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    reachable = reachable_schemas(request_schema, schema["components"]["schemas"])
    assert set(reachable) == {"AcquireWire", "ContextNeedWire"}
    assert reachable["AcquireWire"]["properties"].keys() == {"kind", "need"}
    assert reachable["ContextNeedWire"]["properties"].keys() == {"query"}
    assert all(
        document["additionalProperties"] is False for document in reachable.values()
    )

    serialized_request_graph = repr(reachable).casefold()
    for forbidden in (
        "organization",
        "tenant",
        "principal",
        "user",
        "membership",
        "agentversion",
        "purpose",
        "audience",
        "acl",
        "sql",
        "placement",
        "bypass",
        "authenticatedinvocation",
        "trusteddeliverycontext",
    ):
        assert forbidden not in serialized_request_graph

    assert set(operation["responses"]) == {"204", "400", "401", "422"}
    assert response_schema_name(operation, 400) == "InvalidRequestWire"
    assert response_schema_name(operation, 401) == "AuthenticationFailureWire"
    assert response_schema_name(operation, 422) == "InvalidRequestWire"
    assert "content" not in operation["responses"]["204"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]

    for name, code in (
        ("InvalidRequestWire", "invalid_request"),
        ("AuthenticationFailureWire", "authentication_failed"),
    ):
        error_schema = schema["components"]["schemas"][name]
        assert error_schema["additionalProperties"] is False
        assert error_schema["required"] == ["code"]
        assert error_schema["properties"]["code"]["const"] == code


@pytest.mark.parametrize("header_value", ["", "   "])
def test_correlation_header_must_be_non_empty_if_present(header_value: str) -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": header_value,
        },
        json=VALID_BODY,
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert spy.invocations == []


def test_authenticated_invocation_is_not_a_body_model_or_public_constructor() -> None:
    assert not issubclass(AuthenticatedInvocation, BaseModel)
    assert not hasattr(AuthenticatedInvocation, "model_validate")

    with pytest.raises(TypeError, match="trusted ingress"):
        AuthenticatedInvocation(
            request_id="caller-authored",
            organization_ref="caller-authored",
            principal_ref="caller-authored",
            membership_ref="caller-authored",
            agent_version_ref="caller-authored",
            authenticated_application_ref="caller-authored",
            authentication_binding_ref="caller-authored",
            received_at=RECEIVED_AT,
            construction_provenance=(
                InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS
            ),
        )

    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)
    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )
    assert response.status_code == 204
    with pytest.raises(FrozenInstanceError):
        spy.invocations[0].organization_ref = "mutation"  # type: ignore[misc]


def trust_boundary_client(
    authenticator: DeterministicAuthenticator,
    spy: InvocationSpy,
) -> TestClient:
    return TestClient(
        create_app(
            authenticator=authenticator,
            invocation_observer=spy.observe,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: "server-generated-request",
        )
    )


def reachable_schemas(
    root: dict[str, Any],
    components: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def visit(node: object) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str):
                name = reference.rsplit("/", maxsplit=1)[-1]
                if name not in found:
                    found[name] = components[name]
                    visit(components[name])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(root)
    return found


def response_schema_name(operation: dict[str, Any], status_code: int) -> str:
    response = operation["responses"][str(status_code)]
    reference = response["content"]["application/json"]["schema"]["$ref"]
    assert isinstance(reference, str)
    return reference.rsplit("/", maxsplit=1)[-1]
