from __future__ import annotations

import pickle
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import Engine

import engine.persistence as persistence
import engine.persistence.context_runs as context_runs
from engine.persistence import (
    ContextRunOperatorAccessRequest,
    ContextRunOperatorAuthenticationRejected,
    ContextRunOperatorAuthority,
    ContextRunOperatorAuthorityUnavailable,
    ContextRunOperatorAuthorization,
    ContextRunReaderUnavailable,
    OperatorAuthorizationProvenance,
    PostgreSQLContextRunReader,
    VerifiedContextRunOperatorIdentity,
)

ORGANIZATION_ID = UUID("9a4790f3-5796-4a67-a98e-b74b741c36a3")
OTHER_ORGANIZATION_ID = UUID("b4c117cd-7e15-492a-b920-d28960073b94")
AUTHORIZED_AT = datetime(2026, 7, 22, 12, 30, tzinfo=UTC)
DECISION_REF = "dec_" + "a" * 32
OTHER_DECISION_REF = "dec_" + "b" * 32
OPAQUE_CREDENTIAL = "operator-secret-not-for-repr"


class _ExactAuthenticator:
    def __init__(
        self,
        *,
        organization_id: UUID = ORGANIZATION_ID,
        credential: str = OPAQUE_CREDENTIAL,
    ) -> None:
        self._organization_id = organization_id
        self._credential = credential
        self.calls: list[str] = []

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedContextRunOperatorIdentity:
        self.calls.append(opaque_credential)
        if opaque_credential != self._credential:
            raise ContextRunOperatorAuthenticationRejected
        return VerifiedContextRunOperatorIdentity(
            organization_id=self._organization_id,
            operator_ref="operator-secret-ref",
            authentication_binding_ref="operator-secret-binding",
            authorized_at=AUTHORIZED_AT,
        )


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> Mapping[str, Any] | None:
        return cast(Mapping[str, Any] | None, self._value)


class _FakeConnection:
    def __init__(
        self,
        *,
        issue_result: bool = True,
        read_result: Mapping[str, Any] | None = None,
        read_error: Exception | None = None,
        revoke_error: Exception | None = None,
    ) -> None:
        self.issue_result = issue_result
        self.read_result = read_result
        self.read_error = read_error
        self.revoke_error = revoke_error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _FakeResult:
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        if "issue_context_run_operator_read_ticket" in sql:
            return _FakeResult(self.issue_result)
        if "revoke_context_run_operator_read_ticket" in sql:
            if self.revoke_error is not None:
                raise self.revoke_error
            return _FakeResult(True)
        if "read_context_run_by_operator_ticket" in sql:
            if self.read_error is not None:
                raise self.read_error
            return _FakeResult(self.read_result)
        raise AssertionError(f"unexpected SQL: {sql}")


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self) -> Iterator[_FakeConnection]:
        yield self.connection


def _request(
    *,
    organization_id: UUID = ORGANIZATION_ID,
    decision_ref: str = DECISION_REF,
    credential: str = OPAQUE_CREDENTIAL,
) -> ContextRunOperatorAccessRequest:
    return ContextRunOperatorAccessRequest(
        organization_id=organization_id,
        decision_ref=decision_ref,
        request_id="operator-request-19",
        opaque_credential=credential,
    )


def _reader(
    authority: ContextRunOperatorAuthority,
    *,
    issue_result: bool = True,
    row: Mapping[str, Any] | None = None,
    read_error: Exception | None = None,
    revoke_error: Exception | None = None,
) -> tuple[PostgreSQLContextRunReader, _FakeConnection, _FakeConnection]:
    control = _FakeConnection(
        issue_result=issue_result,
        revoke_error=revoke_error,
    )
    operator = _FakeConnection(read_result=row, read_error=read_error)
    return (
        PostgreSQLContextRunReader(
            cast(Engine, _FakeEngine(control)),
            cast(Engine, _FakeEngine(operator)),
            operator_authority=authority,
        ),
        control,
        operator,
    )


def _safe_row() -> dict[str, object]:
    return {
        "organization_id": ORGANIZATION_ID,
        "run_ref": "run_" + "1" * 32,
        "decision_ref": DECISION_REF,
        "user_id": UUID("a42cb808-f7fe-47dc-b876-23391121bef1"),
        "membership_id": UUID("060d8c30-709b-4e73-920f-538597138ea4"),
        "membership_version": 1,
        "principal_ref": "principal-19",
        "agent_version_ref": "agent-19",
        "authenticated_application_ref": "application-19",
        "authentication_binding_ref": "binding-19",
        "request_id": "request-19",
        "purpose": "context.answer",
        "policy_snapshot_ref": "policy-19",
        "policy_epoch": 1,
        "effective_scope_digest": "1" * 64,
        "query_digest_profile": "hmac-sha256-v1",
        "query_digest_key_version": 1,
        "query_digest": "2" * 64,
        "outcome": "delivered_empty",
        "package_digest_profile": "rfc8785-sha256-v1",
        "package_digest": "3" * 64,
        "package_retention_mode": "digest_only",
        "authorized_evidence_refs": [],
        "effective_max_tokens": 1,
        "effective_max_provider_calls": 1,
        "effective_max_cost_microunits": 1,
        "effective_max_elapsed_ms": 1,
        "usage_tokens": 0,
        "usage_provider_calls": 0,
        "usage_cost_microunits": 0,
        "usage_elapsed_ms": 0,
        "accepted_at": AUTHORIZED_AT,
        "finalized_at": AUTHORIZED_AT,
        "package_as_of": AUTHORIZED_AT,
        "package_expires_at": AUTHORIZED_AT + timedelta(seconds=1),
        "audit_category": "no_authorized_evidence",
        "audit_recorded_at": AUTHORIZED_AT,
    }


@pytest.fixture(autouse=True)
def _accept_exact_database_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context_runs, "assert_control_role", lambda connection: None)
    monkeypatch.setattr(
        context_runs,
        "assert_security_operator_role",
        lambda connection: None,
    )


def test_authority_authenticates_and_binds_an_exact_lifetime_capability() -> None:
    authenticator = _ExactAuthenticator()
    authority = ContextRunOperatorAuthority(authenticator)
    reader, _, _ = _reader(authority)

    with authority.authorize(_request()) as authorization:
        assert type(authorization) is ContextRunOperatorAuthorization
        assert authorization.organization_id == ORGANIZATION_ID
        assert authorization.decision_ref == DECISION_REF
        assert authorization.authorized_at == AUTHORIZED_AT
        assert authorization.provenance is (
            OperatorAuthorizationProvenance.TRUSTED_OPERATOR_AUTHORITY
        )
        assert authenticator.calls == [OPAQUE_CREDENTIAL]
        serialized = repr(authorization)
        for secret in (
            OPAQUE_CREDENTIAL,
            str(ORGANIZATION_ID),
            DECISION_REF,
            "operator-secret-ref",
            "operator-secret-binding",
            "operator-request-19",
        ):
            assert secret not in serialized
        with pytest.raises(TypeError, match="not serializable"):
            pickle.dumps(authorization)

    with pytest.raises(ValueError, match="active trusted operator authorization"):
        reader.find_by_decision_ref(authorization, DECISION_REF)


def test_access_request_requires_a_closed_decision_ref() -> None:
    with pytest.raises(ValueError, match="closed format"):
        _request(decision_ref="decision-not-closed")


def test_operator_authorization_cannot_be_constructed_by_a_caller() -> None:
    with pytest.raises(TypeError, match="authority-constructed"):
        ContextRunOperatorAuthorization()


def test_authority_rejects_an_invalid_credential_without_a_capability() -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())

    with (
        pytest.raises(ContextRunOperatorAuthenticationRejected),
        authority.authorize(_request(credential="wrong-credential")),
    ):
        pytest.fail("invalid operator credential issued a capability")


def test_authority_rejects_a_verified_identity_for_another_organization() -> None:
    authority = ContextRunOperatorAuthority(
        _ExactAuthenticator(organization_id=OTHER_ORGANIZATION_ID)
    )

    with (
        pytest.raises(ContextRunOperatorAuthenticationRejected),
        authority.authorize(_request()),
    ):
        pytest.fail("wrong-Organization identity issued a capability")


def test_reader_rejects_a_capability_from_another_authority() -> None:
    issuing_authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    configured_authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, operator = _reader(configured_authority)

    with (
        issuing_authority.authorize(_request()) as authorization,
        pytest.raises(ValueError, match="configured operator authority"),
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)
    assert control.calls == operator.calls == []


def test_authority_normalizes_an_authenticator_failure_to_unavailable() -> None:
    class _UnavailableAuthenticator:
        def authenticate(
            self,
            opaque_credential: str,
        ) -> VerifiedContextRunOperatorIdentity:
            raise RuntimeError("private upstream failure")

    authority = ContextRunOperatorAuthority(_UnavailableAuthenticator())

    with (
        pytest.raises(ContextRunOperatorAuthorityUnavailable) as unavailable,
        authority.authorize(_request()),
    ):
        pytest.fail("unavailable operator authority issued a capability")
    assert str(unavailable.value) == "operator authority is unavailable"
    assert unavailable.value.__cause__ is None


def test_reader_uses_one_app_grant_and_commits_an_exact_ticket_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = "c" * 64
    monkeypatch.setattr(context_runs, "token_hex", lambda length: ticket)
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, operator = _reader(authority, row=_safe_row())

    with authority.authorize(_request()) as authorization:
        run = reader.find_by_decision_ref(authorization, DECISION_REF)
        with pytest.raises(ValueError, match="already been consumed"):
            reader.find_by_decision_ref(authorization, DECISION_REF)

    assert run is not None
    assert run.decision_ref == DECISION_REF
    assert ["issue_" in sql or "revoke_" in sql for sql, _ in control.calls] == [
        True,
        True,
    ]
    assert len(operator.calls) == 1
    issue_parameters = control.calls[0][1]
    read_parameters = operator.calls[0][1]
    revoke_parameters = control.calls[1][1]
    assert issue_parameters == {
        "ticket": ticket,
        "organization_id": ORGANIZATION_ID,
        "decision_ref": DECISION_REF,
        "operator_ref": "operator-secret-ref",
        "request_id": "operator-request-19",
        "authentication_binding_ref": "operator-secret-binding",
    }
    assert read_parameters == {
        "ticket": ticket,
        "organization_id": ORGANIZATION_ID,
        "decision_ref": DECISION_REF,
    }
    assert revoke_parameters == {"ticket": ticket}
    for public_repr in (repr(reader), repr(authorization), repr(run), repr(_request())):
        assert ticket not in public_repr


def test_exact_decision_capability_cannot_read_another_decision() -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, operator = _reader(authority)

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ValueError, match="exact decision_ref"),
    ):
        reader.find_by_decision_ref(authorization, OTHER_DECISION_REF)
    assert control.calls == operator.calls == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("organization_id", OTHER_ORGANIZATION_ID),
        ("decision_ref", OTHER_DECISION_REF),
    ],
)
def test_reader_rejects_a_database_projection_outside_the_exact_ticket_binding(
    field_name: str,
    value: object,
) -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    row = _safe_row()
    row[field_name] = value
    reader, control, _ = _reader(authority, row=row)

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ContextRunReaderUnavailable, match="reader is unavailable"),
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)
    assert len(control.calls) == 2


def test_nonexistent_exact_decision_returns_none_without_an_operator_read() -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, operator = _reader(authority, issue_result=False)

    with authority.authorize(_request()) as authorization:
        assert reader.find_by_decision_ref(authorization, DECISION_REF) is None

    assert len(control.calls) == 2
    assert operator.calls == []


def test_reader_normalizes_database_failure_and_revokes_the_ticket() -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, _ = _reader(
        authority,
        read_error=RuntimeError("private database failure with ticket material"),
    )

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ContextRunReaderUnavailable) as unavailable,
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)
    assert str(unavailable.value) == "ContextRun reader is unavailable"
    assert unavailable.value.__cause__ is None
    assert len(control.calls) == 2


def test_reader_propagates_revoke_failure_after_a_successful_read() -> None:
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, operator = _reader(
        authority,
        row=_safe_row(),
        revoke_error=RuntimeError("private revoke failure with ticket material"),
    )

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ContextRunReaderUnavailable) as unavailable,
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)

    assert str(unavailable.value) == "ContextRun reader is unavailable"
    assert unavailable.value.__cause__ is None
    assert len(control.calls) == 2
    assert len(operator.calls) == 1


def test_reader_preserves_primary_failure_when_ticket_revoke_also_fails() -> None:
    primary_error = ContextRunReaderUnavailable("primary read failure")
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, control, _ = _reader(
        authority,
        read_error=primary_error,
        revoke_error=RuntimeError("private revoke failure with ticket material"),
    )

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ContextRunReaderUnavailable) as unavailable,
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)

    assert unavailable.value is primary_error
    assert len(control.calls) == 2


@pytest.mark.parametrize("wrong_role", ["control", "operator"])
def test_reader_requires_both_exact_database_roles(
    monkeypatch: pytest.MonkeyPatch,
    wrong_role: str,
) -> None:
    def reject_role(connection: object) -> None:
        raise AssertionError("private wrong-role detail")

    monkeypatch.setattr(
        context_runs,
        (
            "assert_control_role"
            if wrong_role == "control"
            else "assert_security_operator_role"
        ),
        reject_role,
    )
    authority = ContextRunOperatorAuthority(_ExactAuthenticator())
    reader, _, _ = _reader(authority, row=_safe_row())

    with (
        authority.authorize(_request()) as authorization,
        pytest.raises(ContextRunReaderUnavailable) as unavailable,
    ):
        reader.find_by_decision_ref(authorization, DECISION_REF)
    assert str(unavailable.value) == "ContextRun reader is unavailable"
    assert unavailable.value.__cause__ is None


def test_internal_operator_minting_helpers_are_not_package_exports() -> None:
    for name in (
        "_open_operator_authority_scope",
        "_construct_context_run_operator_authorization",
        "_close_operator_authority_scope",
        "_consume_operator_authorization",
    ):
        assert name not in persistence.__all__
        assert not hasattr(persistence, name)
