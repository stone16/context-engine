from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import (
    OrganizationVerificationRejected,
)
from adapters.http.transport import HttpTransportProfile
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
    MembershipNotCurrent,
)
from engine.runtime import (
    AuthenticatedInvocation,
    InvocationConstructionProvenance,
    Resolved,
    Runtime,
)
from engine.runtime.actor import (
    CurrentMembershipVerification,
    MembershipRejectionAuditReceipt,
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.contracts import Acquire
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    OrganizationVerificationProvenance,
    _construct_existing_http_organization_verification,
)

VALID_BODY = {
    "kind": "acquire",
    "need": {"query": "Which decisions constrain Runtime delivery?"},
}
VALID_TOKEN = "opaque-test-credential"
RECEIVED_AT = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)
INTERNAL_ORGANIZATION_REF = "81e18bca-86a1-478a-937d-7675c6fe69b0"
INTERNAL_USER_REF = "d3d9893f-82d2-4890-8cb2-4c7e57a56f16"
INTERNAL_MEMBERSHIP_REF = "9c9e9f4c-a5ec-4417-9408-0346e1c6c998"


class DeterministicAuthenticator:
    """Test-only opaque credential map representing already verified auth."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        self.calls.append(opaque_credential)
        if opaque_credential != VALID_TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=INTERNAL_ORGANIZATION_REF,
            user_ref=INTERNAL_USER_REF,
            principal_ref="principal-from-auth",
            membership_ref=INTERNAL_MEMBERSHIP_REF,
            membership_version=7,
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class DeterministicMembershipAuthority:
    """Test twin retaining one nominal proof for the whole Runtime call."""

    def __init__(self) -> None:
        self.identities: list[MembershipIdentity] = []
        self.events: list[str] = []

    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        self.identities.append(identity)
        if (
            identity.organization_id != UUID(INTERNAL_ORGANIZATION_REF)
            or identity.user_id != UUID(INTERNAL_USER_REF)
            or identity.membership_id != UUID(INTERNAL_MEMBERSHIP_REF)
            or identity.membership_version != 7
        ):
            raise MembershipNotCurrent
        self.events.append("authority-open")
        scope = _open_membership_authority_scope()
        try:
            yield _construct_current_membership_verification(
                authority_scope=scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                principal_ref=identity.principal_ref,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
            )
        finally:
            _close_membership_authority_scope(scope)
            self.events.append("authority-close")


class RejectingTestMembershipAuthority:
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> AbstractContextManager[CurrentMembershipVerification]:
        del identity
        return _RaisingMembershipContext(MembershipNotCurrent())


class UnavailableTestMembershipAuthority:
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> AbstractContextManager[CurrentMembershipVerification]:
        del identity
        return _RaisingMembershipContext(MembershipAuthorityUnavailable())


class _RaisingMembershipContext(
    AbstractContextManager[CurrentMembershipVerification]
):
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> CurrentMembershipVerification:
        raise self._error

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None:
        del exc_type, exc_value, traceback
        return None


class ExitUnavailableMembershipAuthority(DeterministicMembershipAuthority):
    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        with super().current_user_actor(identity) as verification:
            yield verification
        raise MembershipAuthorityUnavailable


class CategorizedRejectingMembershipAuthority:
    """Represent any durable invalid category without changing its boundary result."""

    def __init__(self, category: str) -> None:
        self.category = category
        self.calls = 0

    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> AbstractContextManager[CurrentMembershipVerification]:
        del identity
        self.calls += 1
        return _RaisingMembershipContext(MembershipNotCurrent())


class DownstreamContentIoSpy:
    def __init__(self) -> None:
        self.index_calls = 0
        self.provider_calls = 0
        self.source_content_calls = 0

    def discover(self, request: Acquire) -> tuple[()]:
        del request
        self.index_calls += 1
        return ()

    def authorize_and_project(self) -> tuple[()]:
        self.provider_calls += 1
        return ()

    def read_content(self) -> tuple[()]:
        self.source_content_calls += 1
        return ()

    @property
    def total_calls(self) -> int:
        return self.index_calls + self.provider_calls + self.source_content_calls


class DeterministicOrganizationAuthority:
    """Test twin that recognizes the one registered conformance Organization."""

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        if authentication.organization_ref != INTERNAL_ORGANIZATION_REF:
            raise OrganizationVerificationRejected
        return _construct_existing_http_organization_verification(
            organization_id=UUID(authentication.organization_ref),
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )


class RejectingTestOrganizationAuthority:
    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        raise OrganizationVerificationRejected


class SwitchedOrganizationAuthority:
    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        return _construct_existing_http_organization_verification(
            organization_id=UUID(int=9),
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )


class WrongTypeOrganizationAuthority:
    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> Any:
        return {"organization_id": authentication.organization_ref}


class MismatchedOrganizationEvidenceAuthority:
    def __init__(self, field: str) -> None:
        self._field = field

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        return _construct_existing_http_organization_verification(
            organization_id=UUID(authentication.organization_ref),
            request_id=("other-request" if self._field == "request" else request_id),
            authentication_binding_ref=(
                "other-binding"
                if self._field == "binding"
                else authentication.authentication_binding_ref
            ),
            verified_at=(
                datetime(2026, 7, 21, 5, 1, tzinfo=UTC)
                if self._field == "time"
                else verified_at
            ),
        )


class InvalidProvenanceOrganizationAuthority(DeterministicOrganizationAuthority):
    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        evidence = super().verify_existing(
            authentication,
            request_id=request_id,
            verified_at=verified_at,
        )
        object.__setattr__(
            evidence,
            "construction_provenance",
            cast(OrganizationVerificationProvenance, "untrusted"),
        )
        return evidence


class InvalidResultAuthenticator:
    """Test double for a broken adapter that returns caller-shaped data."""

    def authenticate(self, opaque_credential: str) -> Any:
        return {"organization_ref": "caller-shaped"}


class InvalidClaimsAuthenticator:
    """Test double for verified claims that fail nominal validation."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        return VerifiedAuthenticationContext(
            organization_ref="organization-secret-malformed",
            user_ref=INTERNAL_USER_REF,
            principal_ref=" ",
            membership_ref=INTERNAL_MEMBERSHIP_REF,
            membership_version=7,
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class InvalidClaimTypeAuthenticator:
    """Test double for an untyped claim that cannot be a trusted ref."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        return VerifiedAuthenticationContext(
            organization_ref=cast(Any, 42),
            user_ref=INTERNAL_USER_REF,
            principal_ref="principal-from-auth",
            membership_ref=INTERNAL_MEMBERSHIP_REF,
            membership_version=7,
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class InvalidOrganizationRefAuthenticator:
    """Test double whose trusted Organization locator is not internal."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        return VerifiedAuthenticationContext(
            organization_ref="orgpkg_caller-replayed-output-reference",
            user_ref=INTERNAL_USER_REF,
            principal_ref="principal-from-auth",
            membership_ref=INTERNAL_MEMBERSHIP_REF,
            membership_version=7,
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class OversizedMembershipVersionAuthenticator:
    """Test double whose Membership version cannot fit durable BIGINT."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        del opaque_credential
        return VerifiedAuthenticationContext(
            organization_ref=INTERNAL_ORGANIZATION_REF,
            user_ref=INTERNAL_USER_REF,
            principal_ref="principal-from-auth",
            membership_ref=INTERNAL_MEMBERSHIP_REF,
            membership_version=1 << 63,
            agent_version_ref="agent-version-from-auth",
            authenticated_application_ref="application-from-auth",
            authentication_binding_ref="binding-from-auth",
        )


class AlternateUuidSpellingAuthenticator(DeterministicAuthenticator):
    """Return the same trusted UUID using a valid non-canonical spelling."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        context = super().authenticate(opaque_credential)
        return VerifiedAuthenticationContext(
            organization_ref=context.organization_ref.replace("-", "").upper(),
            user_ref=context.user_ref,
            principal_ref=context.principal_ref,
            membership_ref=context.membership_ref,
            membership_version=context.membership_version,
            agent_version_ref=context.agent_version_ref,
            authenticated_application_ref=context.authenticated_application_ref,
            authentication_binding_ref=context.authentication_binding_ref,
        )


class InvocationSpy:
    def __init__(self) -> None:
        self.invocations: list[AuthenticatedInvocation] = []

    def observe(self, invocation: AuthenticatedInvocation) -> None:
        self.invocations.append(invocation)


class ResolutionSpy:
    def __init__(self) -> None:
        self.outcomes: list[Resolved] = []

    def observe(self, outcome: Resolved) -> None:
        self.outcomes.append(outcome)


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

    assert response.status_code == 200
    assert response.json()["kind"] == "resolved"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-context-request-id"] == "request-from-header"
    assert authenticator.calls == [VALID_TOKEN]
    assert len(spy.invocations) == 1
    invocation = spy.invocations[0]
    assert invocation.request_id == "request-from-header"
    assert invocation.organization_ref == INTERNAL_ORGANIZATION_REF
    assert invocation.user_ref == INTERNAL_USER_REF
    assert invocation.principal_ref == "principal-from-auth"
    assert invocation.membership_ref == INTERNAL_MEMBERSHIP_REF
    assert invocation.membership_version == 7
    assert invocation.agent_version_ref == "agent-version-from-auth"
    assert invocation.authenticated_application_ref == "application-from-auth"
    assert invocation.authentication_binding_ref == "binding-from-auth"
    assert invocation.received_at == RECEIVED_AT
    assert (
        invocation.construction_provenance
        is InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS
    )
    assert VALID_TOKEN not in repr(invocation)


def test_valid_acquire_returns_canonical_tenant_safe_empty_package() -> None:
    client = trust_boundary_client(DeterministicAuthenticator(), InvocationSpy())

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={
            **VALID_BODY,
            "packageBudget": {"maxTokens": 100, "maxElapsedMs": 1_000},
            "requestNarrowing": {
                "sourceRefs": ["source_opaque"],
                "resourceRefs": ["resource_opaque"],
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "kind": "resolved",
        "package": {
            "organizationRef": response.json()["package"]["organizationRef"],
            "purpose": "context.answer",
            "ttlSeconds": 300,
            "asOf": "2026-07-21T05:00:00Z",
            "expiresAt": "2026-07-21T05:05:00Z",
            "decisionRef": response.json()["package"]["decisionRef"],
            "blocks": [],
            "evidence": [],
            "gaps": [],
            "budgetUsage": {
                "tokens": 0,
                "providerCalls": 0,
                "costMicrounits": 0,
                "elapsedMs": 0,
            },
            "coverage": {
                "status": "empty",
                "reason": "no_authorized_evidence",
            },
        },
    }
    assert response.json()["package"]["organizationRef"] != (
        INTERNAL_ORGANIZATION_REF
    )


def test_valid_http_acquire_reaches_the_single_runtime_entry_exactly_once() -> None:
    resolution_spy = ResolutionSpy()
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=DeterministicMembershipAuthority(),
            clock=lambda: RECEIVED_AT,
            resolution_observer=resolution_spy.observe,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert len(resolution_spy.outcomes) == 1
    assert resolution_spy.outcomes[0].package.evidence == ()


def test_valid_organization_uuid_is_canonicalized_once_at_auth_boundary() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(AlternateUuidSpellingAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert spy.invocations[0].organization_ref == INTERNAL_ORGANIZATION_REF


@pytest.mark.parametrize(
    "organization_authority",
    [
        RejectingTestOrganizationAuthority(),
        SwitchedOrganizationAuthority(),
        WrongTypeOrganizationAuthority(),
        MismatchedOrganizationEvidenceAuthority("request"),
        MismatchedOrganizationEvidenceAuthority("binding"),
        MismatchedOrganizationEvidenceAuthority("time"),
        InvalidProvenanceOrganizationAuthority(),
    ],
)
def test_unverified_or_switched_organization_fails_before_runtime(
    organization_authority: object,
) -> None:
    resolution_spy = ResolutionSpy()
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=cast(Any, organization_authority),
            resolution_observer=resolution_spy.observe,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'
    assert resolution_spy.outcomes == []


@pytest.mark.parametrize(
    "category",
    ("missing", "inactive", "expired", "revoked", "cross-organization"),
)
def test_invalid_membership_matrix_is_externally_equivalent_and_zero_io(
    category: str,
) -> None:
    authority = CategorizedRejectingMembershipAuthority(category)
    audit_receipts: list[MembershipRejectionAuditReceipt] = []
    content_io = DownstreamContentIoSpy()
    runtime = Runtime(
        required_kernel_dependencies(),
        content_io=RuntimeContentIo(
            index=content_io,
            provider=content_io,
            source_content=content_io,
        ),
        clock=lambda: RECEIVED_AT,
    )
    resolution_spy = ResolutionSpy()
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=authority,
            membership_rejection_observer=audit_receipts.append,
            runtime=runtime,
            resolution_observer=resolution_spy.observe,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 401
    assert response.content == b'{"code":"authentication_failed"}'
    assert response.headers["www-authenticate"] == "Bearer"
    assert authority.calls == 1
    assert resolution_spy.outcomes == []
    assert content_io.total_calls == 0
    assert audit_receipts == [MembershipRejectionAuditReceipt()]
    assert all(
        protected not in response.text
        for protected in (
            category,
            INTERNAL_ORGANIZATION_REF,
            INTERNAL_USER_REF,
            INTERNAL_MEMBERSHIP_REF,
        )
    )


def test_membership_authority_unavailability_is_generic_503_and_zero_io() -> None:
    content_io = DownstreamContentIoSpy()
    runtime = Runtime(
        required_kernel_dependencies(),
        content_io=RuntimeContentIo(
            index=content_io,
            provider=content_io,
            source_content=content_io,
        ),
        clock=lambda: RECEIVED_AT,
    )
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=UnavailableTestMembershipAuthority(),
            runtime=runtime,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert content_io.total_calls == 0
    assert all(
        protected not in response.text
        for protected in (
            INTERNAL_ORGANIZATION_REF,
            INTERNAL_USER_REF,
            INTERNAL_MEMBERSHIP_REF,
        )
    )


def test_membership_transaction_exit_failure_suppresses_prepared_200_as_503() -> None:
    resolution_spy = ResolutionSpy()
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=ExitUnavailableMembershipAuthority(),
            resolution_observer=resolution_spy.observe,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert len(resolution_spy.outcomes) == 1
    assert "resolved" not in response.text


def test_membership_authority_scope_encloses_invocation_runtime_and_response() -> None:
    authority = DeterministicMembershipAuthority()
    invocation_spy = InvocationSpy()
    resolution_spy = ResolutionSpy()

    def observe_invocation(invocation: AuthenticatedInvocation) -> None:
        authority.events.append("invocation")
        invocation_spy.observe(invocation)

    def observe_resolution(outcome: Resolved) -> None:
        authority.events.append("resolved")
        resolution_spy.observe(outcome)

    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=authority,
            invocation_observer=observe_invocation,
            resolution_observer=observe_resolution,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert authority.events == [
        "authority-open",
        "invocation",
        "resolved",
        "authority-close",
    ]
    assert len(invocation_spy.invocations) == len(resolution_spy.outcomes) == 1


@pytest.mark.parametrize(
    "body",
    [
        {**VALID_BODY, "packageBudget": {}},
        {**VALID_BODY, "packageBudget": {"maxTokens": 0}},
        {**VALID_BODY, "packageBudget": {"maxTokens": True}},
        {**VALID_BODY, "packageBudget": {"maxTokens": 1.5}},
        {**VALID_BODY, "packageBudget": {"maxTokens": "1"}},
        {**VALID_BODY, "packageBudget": {"unknown": 1}},
        {**VALID_BODY, "requestNarrowing": {}},
        {**VALID_BODY, "requestNarrowing": {"sourceRefs": []}},
        {
            **VALID_BODY,
            "requestNarrowing": {"sourceRefs": ["same", "same"]},
        },
        {**VALID_BODY, "requestNarrowing": {"resourceRefs": [" "]}},
        {
            **VALID_BODY,
            "requestNarrowing": {
                "sourceRefs": [f"source_{index}" for index in range(65)]
            },
        },
        {
            **VALID_BODY,
            "requestNarrowing": {"resourceRefs": ["r" * 257]},
        },
    ],
)
def test_budget_and_narrowing_wire_variants_are_strictly_closed(
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


def test_missing_correlation_header_uses_server_generated_request_id() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert response.headers["x-context-request-id"] == "server-generated-request"
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
    [
        InvalidResultAuthenticator(),
        InvalidClaimsAuthenticator(),
        InvalidClaimTypeAuthenticator(),
        InvalidOrganizationRefAuthenticator(),
        OversizedMembershipVersionAuthenticator(),
    ],
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
    ("field_name", "field_value"),
    [
        ("organization_ref", 42),
        ("principal_ref", True),
        ("membership_ref", []),
        ("agent_version_ref", {}),
        ("authenticated_application_ref", object()),
        ("authentication_binding_ref", 3.14),
    ],
)
def test_verified_authentication_context_rejects_non_string_refs(
    field_name: str,
    field_value: object,
) -> None:
    claims: dict[str, Any] = {
        "organization_ref": INTERNAL_ORGANIZATION_REF,
        "user_ref": INTERNAL_USER_REF,
        "principal_ref": "principal-from-auth",
        "membership_ref": INTERNAL_MEMBERSHIP_REF,
        "membership_version": 7,
        "agent_version_ref": "agent-version-from-auth",
        "authenticated_application_ref": "application-from-auth",
        "authentication_binding_ref": "binding-from-auth",
    }
    claims[field_name] = field_value

    with pytest.raises(ValueError, match="verified"):
        VerifiedAuthenticationContext(**claims)


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


def test_resolve_body_limit_is_enforced_before_authentication() -> None:
    raw_body = b'{"kind":"acquire","need":{"query":"probe"}}'
    exact_authenticator = DeterministicAuthenticator()
    exact_spy = InvocationSpy()
    exact_client = trust_boundary_client(
        exact_authenticator,
        exact_spy,
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=len(raw_body),
            max_json_nesting_depth=2,
        ),
    )
    rejected_authenticator = DeterministicAuthenticator()
    rejected_spy = InvocationSpy()
    rejected_client = trust_boundary_client(
        rejected_authenticator,
        rejected_spy,
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=len(raw_body) - 1,
            max_json_nesting_depth=2,
        ),
    )
    headers = {
        "Authorization": f"Bearer {VALID_TOKEN}",
        "Content-Type": "application/json",
    }

    exact = exact_client.post(
        "/v1/context:resolve",
        headers=headers,
        content=raw_body,
    )
    rejected = rejected_client.post(
        "/v1/context:resolve",
        headers=headers,
        content=raw_body,
    )

    assert exact.status_code == 200
    assert len(exact_spy.invocations) == 1
    assert rejected.status_code == 400
    assert rejected.content == b'{"code":"invalid_request"}'
    assert rejected_authenticator.calls == []
    assert rejected_spy.invocations == []


def test_chunked_body_cannot_bypass_the_receive_limit() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(
        authenticator,
        spy,
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=24,
            max_json_nesting_depth=2,
        ),
    )

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=iter(
            [
                b'{"kind":"acquire",',
                b'"need":{"query":"probe"}}',
            ]
        ),
    )

    assert response.request.headers["transfer-encoding"] == "chunked"
    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


def test_resolve_body_limit_does_not_change_health_requests() -> None:
    client = trust_boundary_client(
        DeterministicAuthenticator(),
        InvocationSpy(),
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=1,
            max_json_nesting_depth=16,
        ),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_resolve_body_limit_applies_when_application_is_mounted() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    inner = create_app(
        authenticator=authenticator,
        organization_authority=DeterministicOrganizationAuthority(),
        invocation_observer=spy.observe,
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=1,
            max_json_nesting_depth=16,
        ),
    )
    outer = FastAPI()
    outer.mount("/prefix", inner)
    client = TestClient(outer)

    response = client.post(
        "/prefix/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=b'{"kind":"acquire","need":{"query":"probe"}}',
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


@pytest.mark.parametrize(
    "declared_length",
    [b"not-a-number", b"-1"],
)
def test_invalid_declared_body_length_fails_before_authentication(
    declared_length: bytes,
) -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)

    response = client.post(
        "/v1/context:resolve",
        headers=[
            (b"Authorization", f"Bearer {VALID_TOKEN}".encode()),
            (b"Content-Type", b"application/json"),
            (b"Content-Length", declared_length),
        ],
        content=b'{"kind":"acquire","need":{"query":"probe"}}',
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


def test_duplicate_declared_body_length_fails_before_authentication() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)

    response = client.post(
        "/v1/context:resolve",
        headers=[
            (b"Authorization", f"Bearer {VALID_TOKEN}".encode()),
            (b"Content-Type", b"application/json"),
            (b"Content-Length", b"50"),
            (b"Content-Length", b"50"),
        ],
        content=b'{"kind":"acquire","need":{"query":"probe"}}',
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


def test_json_nesting_limit_is_enforced_before_authentication() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(
        authenticator,
        spy,
        transport_profile=HttpTransportProfile(
            max_resolve_body_bytes=1024,
            max_json_nesting_depth=2,
        ),
    )

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=(
            b'{"kind":"acquire","need":{"query":"probe"},'
            b'"unknown":[[]]}'
        ),
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


def test_parser_recursion_limit_is_still_a_generic_transport_rejection() -> None:
    authenticator = DeterministicAuthenticator()
    spy = InvocationSpy()
    client = trust_boundary_client(authenticator, spy)
    nested_value = b"[" * 2000 + b"0" + b"]" * 2000

    response = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Content-Type": "application/json",
        },
        content=(
            b'{"kind":"acquire","need":{"query":"probe"},"unknown":'
            + nested_value
            + b"}"
        ),
    )

    assert response.status_code == 400
    assert response.content == b'{"code":"invalid_request"}'
    assert authenticator.calls == []
    assert spy.invocations == []


@pytest.mark.parametrize(
    (
        "max_resolve_body_bytes",
        "max_json_nesting_depth",
        "max_correlation_id_characters",
    ),
    [(0, 2, 256), (1024, 0, 256), (1024, 2, 0)],
)
def test_transport_profile_rejects_non_positive_limits(
    max_resolve_body_bytes: int,
    max_json_nesting_depth: int,
    max_correlation_id_characters: int,
) -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpTransportProfile(
            max_resolve_body_bytes=max_resolve_body_bytes,
            max_json_nesting_depth=max_json_nesting_depth,
            max_correlation_id_characters=max_correlation_id_characters,
        )


def test_transport_profile_rejects_boolean_limits() -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpTransportProfile(
            max_resolve_body_bytes=True,
            max_json_nesting_depth=2,
        )
    with pytest.raises(ValueError, match="positive"):
        HttpTransportProfile(
            max_resolve_body_bytes=1024,
            max_json_nesting_depth=2,
            max_correlation_id_characters=True,
        )


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


def test_injected_authenticator_runs_only_the_real_sealed_runtime() -> None:
    client = TestClient(
        create_app(
            authenticator=DeterministicAuthenticator(),
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=DeterministicMembershipAuthority(),
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "resolved"


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
    correlation_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "X-Context-Request-Id"
    )
    correlation_schema = correlation_parameter["schema"]["anyOf"][0]
    assert correlation_schema["maxLength"] == 256

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    reachable = reachable_schemas(request_schema, schema["components"]["schemas"])
    assert set(reachable) == {
        "AcquireWire",
        "ContextNeedWire",
        "PackageBudgetWire",
        "RequestNarrowingWire",
    }
    assert reachable["AcquireWire"]["properties"].keys() == {
        "kind",
        "need",
        "packageBudget",
        "requestNarrowing",
    }
    assert reachable["ContextNeedWire"]["properties"].keys() == {"query"}
    assert reachable["PackageBudgetWire"]["properties"].keys() == {
        "maxTokens",
        "maxProviderCalls",
        "maxCostMicrounits",
        "maxElapsedMs",
    }
    assert reachable["RequestNarrowingWire"]["properties"].keys() == {
        "sourceRefs",
        "resourceRefs",
    }
    narrowing_properties = reachable["RequestNarrowingWire"]["properties"]
    for field_name in ("sourceRefs", "resourceRefs"):
        narrowing_schema = narrowing_properties[field_name]["anyOf"][0]
        assert narrowing_schema["maxItems"] == 64
        assert narrowing_schema["items"]["maxLength"] == 256
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
        "principalgrants",
        "agentceiling",
        "membershiprights",
        "sourcenativeacl",
        "resourceacl",
        "purposepolicy",
        "precomputedscope",
        "effectivescope",
    ):
        assert forbidden not in serialized_request_graph

    assert set(operation["responses"]) == {"200", "400", "401", "422", "503"}
    assert response_schema_name(operation, 400) == "InvalidRequestWire"
    assert response_schema_name(operation, 401) == "AuthenticationFailureWire"
    assert response_schema_name(operation, 422) == "InvalidRequestWire"
    assert response_schema_name(operation, 503) == "ServiceUnavailableWire"
    assert response_schema_name(operation, 200) == "ResolvedWire"
    response_schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    response_models = reachable_schemas(
        response_schema,
        schema["components"]["schemas"],
    )
    assert set(response_models) == {
        "ResolvedWire",
        "ContextPackageWire",
        "BudgetUsageWire",
        "CoverageWire",
    }
    package_schema = response_models["ContextPackageWire"]
    assert package_schema["additionalProperties"] is False
    assert package_schema["required"] == [
        "organizationRef",
        "purpose",
        "ttlSeconds",
        "asOf",
        "expiresAt",
        "decisionRef",
        "blocks",
        "evidence",
        "gaps",
        "budgetUsage",
        "coverage",
    ]
    assert package_schema["properties"]["blocks"]["maxItems"] == 0
    assert package_schema["properties"]["evidence"]["maxItems"] == 0
    assert package_schema["properties"]["gaps"]["maxItems"] == 0
    assert package_schema["properties"]["organizationRef"]["pattern"] == (
        "^orgpkg_[0-9a-f]{32}$"
    )
    assert package_schema["properties"]["decisionRef"]["pattern"] == (
        "^dec_[0-9a-f]{32}$"
    )
    assert all(
        response_model["additionalProperties"] is False
        for response_model in response_models.values()
    )
    serialized_response_graph = repr(response_models).casefold()
    for forbidden in (
        "effectivescope",
        "scopedecision",
        "scopetarget",
        "targetcount",
        "digest",
        "sourceref",
        "resourceref",
        "principalgrants",
        "agentceiling",
        "membershiprights",
        "sourcenativeacl",
        "resourceacl",
        "purposepolicy",
    ):
        assert forbidden not in serialized_response_graph
    assert "HTTPValidationError" not in schema["components"]["schemas"]

    for name, code in (
        ("InvalidRequestWire", "invalid_request"),
        ("AuthenticationFailureWire", "authentication_failed"),
        ("ServiceUnavailableWire", "service_unavailable"),
    ):
        error_schema = schema["components"]["schemas"][name]
        assert error_schema["additionalProperties"] is False
        assert error_schema["required"] == ["code"]
        assert error_schema["properties"]["code"]["const"] == code
        serialized_error_schema = repr(error_schema).casefold()
        assert "scope" not in serialized_error_schema
        assert "digest" not in serialized_error_schema


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


def test_correlation_header_is_bounded_by_the_active_transport_profile() -> None:
    spy = InvocationSpy()
    client = trust_boundary_client(DeterministicAuthenticator(), spy)

    exact = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "r" * 256,
        },
        json=VALID_BODY,
    )
    rejected = client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "X-Context-Request-Id": "r" * 257,
        },
        json=VALID_BODY,
    )

    assert exact.status_code == 200
    assert exact.headers["x-context-request-id"] == "r" * 256
    assert rejected.status_code == 422
    assert rejected.content == b'{"code":"invalid_request"}'
    assert [invocation.request_id for invocation in spy.invocations] == ["r" * 256]


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
    assert response.status_code == 200
    with pytest.raises(FrozenInstanceError):
        spy.invocations[0].organization_ref = "mutation"  # type: ignore[misc]


def trust_boundary_client(
    authenticator: DeterministicAuthenticator,
    spy: InvocationSpy,
    *,
    transport_profile: HttpTransportProfile | None = None,
) -> TestClient:
    if transport_profile is None:
        return TestClient(
            create_app(
                authenticator=authenticator,
                organization_authority=DeterministicOrganizationAuthority(),
                membership_authority=DeterministicMembershipAuthority(),
                invocation_observer=spy.observe,
                clock=lambda: RECEIVED_AT,
                request_id_factory=lambda: "server-generated-request",
            )
        )
    return TestClient(
        create_app(
            authenticator=authenticator,
            organization_authority=DeterministicOrganizationAuthority(),
            membership_authority=DeterministicMembershipAuthority(),
            invocation_observer=spy.observe,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: "server-generated-request",
            transport_profile=transport_profile,
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
