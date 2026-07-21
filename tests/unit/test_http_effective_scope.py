from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from adapters.http.app import create_app
from adapters.http.scope_authority import (
    ScopeAuthorityIdentity,
    ScopeAuthorityUnavailable,
)
from engine.runtime import Resolved, Runtime
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.scope import (
    MISSING_TRUSTED_SCOPE,
    MissingTrustedScope,
    ScopeSet,
    ScopeTarget,
    TrustedScopeOperands,
)
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.unit.test_http_trust_boundary import (
    INTERNAL_ORGANIZATION_REF,
    RECEIVED_AT,
    VALID_BODY,
    VALID_TOKEN,
    DeterministicAuthenticator,
    DeterministicMembershipAuthority,
    DeterministicOrganizationAuthority,
    DownstreamContentIoSpy,
)

ORGANIZATION_ID = UUID(INTERNAL_ORGANIZATION_REF)
TARGET_A = ScopeTarget(ORGANIZATION_ID, "source:http:a", "resource:http:a")
TARGET_B = ScopeTarget(ORGANIZATION_ID, "source:http:b", "resource:http:b")
ALL_TARGETS = ScopeSet(frozenset({TARGET_A, TARGET_B}))


def operands(
    **changes: ScopeSet | MissingTrustedScope,
) -> TrustedScopeOperands:
    values: dict[str, ScopeSet | MissingTrustedScope] = {
        "organization_boundary": ALL_TARGETS,
        "membership_rights": ALL_TARGETS,
        "principal_grants": ALL_TARGETS,
        "agent_ceiling": ALL_TARGETS,
        "source_native_acl": ALL_TARGETS,
        "resource_acl": ALL_TARGETS,
        "purpose_policy": ALL_TARGETS,
    }
    values.update(changes)
    return TrustedScopeOperands(**values)


class DeterministicScopeAuthority:
    def __init__(self, selected: TrustedScopeOperands) -> None:
        self._selected = selected
        self.identities: list[ScopeAuthorityIdentity] = []

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        self.identities.append(identity)
        authority_scope = _open_scope_authority_scope()
        try:
            yield _construct_trusted_scope_snapshot(
                authority_scope=authority_scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                principal_ref=identity.principal_ref,
                agent_version_ref=identity.agent_version_ref,
                purpose=identity.purpose,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                organization_boundary=self._selected.organization_boundary,
                membership_rights=self._selected.membership_rights,
                principal_grants=self._selected.principal_grants,
                agent_ceiling=self._selected.agent_ceiling,
                source_native_acl=self._selected.source_native_acl,
                resource_acl=self._selected.resource_acl,
                purpose_policy=self._selected.purpose_policy,
            )
        finally:
            _close_scope_authority_scope(authority_scope)


class UnavailableScopeAuthority:
    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        del identity
        raise ScopeAuthorityUnavailable
        yield cast(Any, None)


class InvalidEntryScopeAuthority:
    def __init__(self, error_type: type[TypeError] | type[ValueError]) -> None:
        self._error_type = error_type

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        del identity
        raise self._error_type("sensitive authority establishment detail")
        yield cast(Any, None)


class MutatedScopeAuthority(DeterministicScopeAuthority):
    def __init__(
        self,
        selected: TrustedScopeOperands,
        *,
        field_name: str = "principal_ref",
        field_value: str = "other-principal",
    ) -> None:
        super().__init__(selected)
        self._field_name = field_name
        self._field_value = field_value

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        with super().current_scope(identity) as snapshot:
            object.__setattr__(snapshot, self._field_name, self._field_value)
            yield snapshot


def request(
    selected: TrustedScopeOperands,
    *,
    body: dict[str, object] | None = None,
) -> tuple[object, Resolved, DownstreamContentIoSpy, DeterministicScopeAuthority]:
    authority = DeterministicScopeAuthority(selected)
    outcomes: list[Resolved] = []
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
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=authority,
            runtime=runtime,
            resolution_observer=outcomes.append,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: "scope-http-request",
        )
    )
    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=body or VALID_BODY,
    )
    assert len(outcomes) == 1
    return response, outcomes[0], content_io, authority


def assert_public_empty_package(response: Any, outcome: Resolved) -> None:
    assert response.status_code == 200
    assert response.json()["package"]["blocks"] == []
    assert response.json()["package"]["evidence"] == []
    assert response.json()["package"]["gaps"] == []
    assert response.json()["package"]["coverage"] == {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    protected = (
        TARGET_A.source_ref,
        cast(str, TARGET_A.resource_ref),
        TARGET_B.source_ref,
        cast(str, TARGET_B.resource_ref),
        outcome.scope_decision.digest,
    )
    assert all(value not in response.text for value in protected)


def test_http_scope_identity_is_server_bound_and_omitted_narrowing_is_identity(
) -> None:
    response, outcome, content_io, authority = request(operands())

    assert outcome.scope_decision.target_count == 2
    assert outcome.scope_decision.is_empty is False
    assert content_io.total_calls == 0
    assert len(authority.identities) == 1
    identity = authority.identities[0]
    assert identity.agent_version_ref == "agent-version-from-auth"
    assert identity.purpose == "context.answer"
    assert identity.request_id == "scope-http-request"
    assert identity.checked_at == RECEIVED_AT
    assert_public_empty_package(response, outcome)


def test_http_request_narrowing_is_exact_and_overbroad_refs_do_not_widen() -> None:
    trusted = operands()
    exact_body: dict[str, object] = {
        **VALID_BODY,
        "requestNarrowing": {
            "sourceRefs": [TARGET_A.source_ref],
            "resourceRefs": [TARGET_A.resource_ref],
        },
    }
    overbroad_body: dict[str, object] = {
        **VALID_BODY,
        "requestNarrowing": {
            "sourceRefs": [
                TARGET_A.source_ref,
                TARGET_B.source_ref,
                "source:unknown",
            ],
            "resourceRefs": [
                TARGET_A.resource_ref,
                TARGET_B.resource_ref,
                "resource:unknown",
            ],
        },
    }
    omitted_response, omitted, omitted_io, _ = request(trusted)
    exact_response, exact, exact_io, _ = request(trusted, body=exact_body)
    overbroad_response, overbroad, overbroad_io, _ = request(
        trusted,
        body=overbroad_body,
    )

    assert omitted.scope_decision.target_count == 2
    assert exact.scope_decision.target_count == 1
    assert overbroad.scope_decision.target_count == 2
    assert overbroad.scope_decision == omitted.scope_decision
    assert (
        omitted_io.total_calls
        == exact_io.total_calls
        == overbroad_io.total_calls
        == 0
    )
    assert_public_empty_package(omitted_response, omitted)
    assert_public_empty_package(exact_response, exact)
    assert_public_empty_package(overbroad_response, overbroad)


def test_http_agent_ceiling_cannot_exceed_principal_grants() -> None:
    broad_response, broad, broad_io, _ = request(
        operands(
            principal_grants=ScopeSet(frozenset({TARGET_A})),
            agent_ceiling=ALL_TARGETS,
        )
    )
    narrow_response, narrow, narrow_io, _ = request(
        operands(
            principal_grants=ALL_TARGETS,
            agent_ceiling=ScopeSet(frozenset({TARGET_A})),
        )
    )

    assert broad.scope_decision == narrow.scope_decision
    assert broad.scope_decision.target_count == 1
    assert broad_io.total_calls == narrow_io.total_calls == 0
    assert_public_empty_package(broad_response, broad)
    assert_public_empty_package(narrow_response, narrow)


@pytest.mark.parametrize(
    "operand_name",
    (
        "organization_boundary",
        "membership_rights",
        "principal_grants",
        "agent_ceiling",
        "source_native_acl",
        "resource_acl",
        "purpose_policy",
    ),
)
@pytest.mark.parametrize(
    "unavailable",
    (MISSING_TRUSTED_SCOPE, ScopeSet(frozenset())),
    ids=("missing", "empty"),
)
def test_http_each_unavailable_trusted_operand_returns_empty_without_io(
    operand_name: str,
    unavailable: ScopeSet | MissingTrustedScope,
) -> None:
    response, outcome, content_io, _ = request(
        operands(**{operand_name: unavailable})
    )

    assert outcome.scope_decision.target_count == 0
    assert outcome.scope_decision.is_empty is True
    assert content_io.total_calls == 0
    assert_public_empty_package(response, outcome)


def test_scope_authority_unavailability_is_generic_503_and_zero_io() -> None:
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
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=UnavailableScopeAuthority(),
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
            TARGET_A.source_ref,
            cast(str, TARGET_A.resource_ref),
        )
    )


@pytest.mark.parametrize("error_type", (TypeError, ValueError))
def test_scope_authority_entry_validation_failure_is_generic_503_and_zero_io(
    error_type: type[TypeError] | type[ValueError],
) -> None:
    content_io = DownstreamContentIoSpy()
    outcomes: list[Resolved] = []
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
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=InvalidEntryScopeAuthority(error_type),
            runtime=runtime,
            resolution_observer=outcomes.append,
            clock=lambda: RECEIVED_AT,
        ),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json=VALID_BODY,
    )

    assert response.status_code == 503
    assert response.content == b'{"code":"service_unavailable"}'
    assert outcomes == []
    assert content_io.total_calls == 0
    assert "sensitive authority establishment detail" not in response.text


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("principal_ref", "other-principal"),
        ("purpose", "context.other"),
    ),
)
def test_mutated_scope_snapshot_is_generic_503_and_never_reaches_runtime(
    field_name: str,
    field_value: str,
) -> None:
    content_io = DownstreamContentIoSpy()
    outcomes: list[Resolved] = []
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
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=MutatedScopeAuthority(
                operands(),
                field_name=field_name,
                field_value=field_value,
            ),
            runtime=runtime,
            resolution_observer=outcomes.append,
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
    assert outcomes == []
    assert content_io.total_calls == 0
    assert field_value not in response.text


@pytest.mark.parametrize(
    "attempted_operator",
    (
        {"sourceRefs": []},
        {"union": True},
        {"sourceWildcard": "*"},
        {"bypassAuthorization": True},
        {"agentCeiling": ["source:http:a"]},
    ),
)
def test_http_rejects_malformed_or_widening_request_narrowing_before_runtime(
    attempted_operator: dict[str, object],
) -> None:
    content_io = DownstreamContentIoSpy()
    outcomes: list[Resolved] = []
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
            membership_authority=DeterministicMembershipAuthority(),
            scope_authority=DeterministicScopeAuthority(operands()),
            runtime=runtime,
            resolution_observer=outcomes.append,
            clock=lambda: RECEIVED_AT,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        json={
            **VALID_BODY,
            "requestNarrowing": {
                "sourceRefs": [TARGET_A.source_ref],
                **attempted_operator,
            },
        },
    )

    assert response.status_code == 422
    assert response.content == b'{"code":"invalid_request"}'
    assert outcomes == []
    assert content_io.total_calls == 0
