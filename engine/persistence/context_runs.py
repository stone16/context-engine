"""Restricted same-Organization reader for durable ContextRun lineage."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from re import fullmatch
from secrets import token_hex
from typing import NoReturn, Protocol
from uuid import UUID

from sqlalchemy import Engine, RowMapping, text

from engine.persistence.role_guard import (
    assert_control_role,
    assert_security_operator_role,
)
from engine.runtime.context_run import (
    ContextRunOutcome,
    DecisionAuditCategory,
)


class ContextRunReaderUnavailable(RuntimeError):
    """The restricted operator read could not complete safely."""


class ContextRunOperatorAuthenticationRejected(Exception):
    """Opaque operator credentials did not establish exact-Organization access."""


class ContextRunOperatorAuthorityUnavailable(RuntimeError):
    """The trusted operator identity authority could not complete safely."""


class OperatorAuthorizationProvenance(StrEnum):
    """Closed trusted origin for the bounded operator test seam."""

    TRUSTED_OPERATOR_AUTHORITY = "trusted_operator_authority"


class _OperatorAuthorityScope:
    __slots__ = ("_active", "_consumed", "_issuer_seal", "_seal")
    _active: bool
    _consumed: bool
    _issuer_seal: object
    _seal: object

    def __init__(self) -> None:
        raise TypeError("operator authority scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("operator authority scopes are not serializable")


_OPERATOR_AUTHORITY_SCOPE_SEAL = object()


@dataclass(frozen=True, slots=True)
class ContextRunOperatorAccessRequest:
    """Untrusted request for one exact Organization-and-decision read lifetime."""

    organization_id: UUID = field(repr=False)
    decision_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    opaque_credential: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("operator access organization_id must be UUID")
        if (
            type(self.decision_ref) is not str
            or fullmatch(r"dec_[0-9a-f]{32}", self.decision_ref) is None
        ):
            raise ValueError("operator access decision_ref must use the closed format")
        for field_name in ("request_id", "opaque_credential"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"operator access {field_name} must be nonblank")

    def __reduce__(self) -> NoReturn:
        raise TypeError("operator access requests are not serializable")


@dataclass(frozen=True, slots=True)
class VerifiedContextRunOperatorIdentity:
    """Trusted identity facts emitted by the configured operator authenticator."""

    organization_id: UUID = field(repr=False)
    operator_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authorized_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("verified operator organization_id must be UUID")
        for field_name in ("operator_ref", "authentication_binding_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"verified operator {field_name} must be nonblank")
        if (
            type(self.authorized_at) is not datetime
            or self.authorized_at.tzinfo is None
            or self.authorized_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("verified operator time must be aware UTC")

    def __reduce__(self) -> NoReturn:
        raise TypeError("verified operator identities are not serializable")


class ContextRunOperatorAuthenticator(Protocol):
    """Verify opaque operator credentials against one trusted identity source."""

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedContextRunOperatorIdentity: ...


@dataclass(frozen=True, slots=True, init=False)
class ContextRunOperatorAuthorization:
    """Trusted exact Organization-and-decision fact, not a database-role grant."""

    organization_id: UUID = field(repr=False)
    decision_ref: str = field(repr=False)
    operator_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authorized_at: datetime = field(repr=False)
    provenance: OperatorAuthorizationProvenance
    _authority_scope: _OperatorAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("operator authorization is authority-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("operator authorization is not serializable")


class ContextRunOperatorAuthority:
    """Issue one lifetime-bound exact Organization-and-decision capability."""

    __slots__ = ("_authenticator", "_issuer_seal")

    def __init__(self, authenticator: ContextRunOperatorAuthenticator) -> None:
        if not callable(getattr(authenticator, "authenticate", None)):
            raise TypeError("operator authenticator is incomplete")
        self._authenticator = authenticator
        self._issuer_seal = object()

    def authorize(
        self,
        request: ContextRunOperatorAccessRequest,
    ) -> AbstractContextManager[ContextRunOperatorAuthorization]:
        """Authenticate one request and retain its capability only for the context."""

        if type(request) is not ContextRunOperatorAccessRequest:
            raise TypeError("operator access requires ContextRunOperatorAccessRequest")
        return self._authorized(request)

    @contextmanager
    def _authorized(
        self,
        request: ContextRunOperatorAccessRequest,
    ) -> Iterator[ContextRunOperatorAuthorization]:
        try:
            identity = self._authenticator.authenticate(request.opaque_credential)
        except ContextRunOperatorAuthenticationRejected:
            raise ContextRunOperatorAuthenticationRejected from None
        except Exception:
            raise ContextRunOperatorAuthorityUnavailable(
                "operator authority is unavailable"
            ) from None
        if type(identity) is not VerifiedContextRunOperatorIdentity:
            raise ContextRunOperatorAuthorityUnavailable(
                "operator authority is unavailable"
            )
        if identity.organization_id != request.organization_id:
            raise ContextRunOperatorAuthenticationRejected from None

        authority_scope = object.__new__(_OperatorAuthorityScope)
        authority_scope._active = True
        authority_scope._consumed = False
        authority_scope._issuer_seal = self._issuer_seal
        authority_scope._seal = _OPERATOR_AUTHORITY_SCOPE_SEAL
        authorization = object.__new__(ContextRunOperatorAuthorization)
        object.__setattr__(
            authorization,
            "organization_id",
            identity.organization_id,
        )
        object.__setattr__(authorization, "decision_ref", request.decision_ref)
        object.__setattr__(authorization, "operator_ref", identity.operator_ref)
        object.__setattr__(authorization, "request_id", request.request_id)
        object.__setattr__(
            authorization,
            "authentication_binding_ref",
            identity.authentication_binding_ref,
        )
        object.__setattr__(authorization, "authorized_at", identity.authorized_at)
        object.__setattr__(
            authorization,
            "provenance",
            OperatorAuthorizationProvenance.TRUSTED_OPERATOR_AUTHORITY,
        )
        object.__setattr__(authorization, "_authority_scope", authority_scope)
        try:
            yield authorization
        finally:
            authority_scope._active = False


def _require_active_operator_authorization(
    authorization: ContextRunOperatorAuthorization,
    *,
    authority: ContextRunOperatorAuthority,
) -> None:
    if type(authorization) is not ContextRunOperatorAuthorization:
        raise TypeError("reader requires ContextRunOperatorAuthorization")
    scope = authorization._authority_scope
    if (
        authorization.provenance
        is not OperatorAuthorizationProvenance.TRUSTED_OPERATOR_AUTHORITY
        or type(scope) is not _OperatorAuthorityScope
        or getattr(scope, "_seal", None) is not _OPERATOR_AUTHORITY_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("reader requires active trusted operator authorization")
    if getattr(scope, "_issuer_seal", None) is not authority._issuer_seal:
        raise ValueError("reader requires its configured operator authority")


def _consume_operator_authorization(
    authorization: ContextRunOperatorAuthorization,
) -> None:
    scope = authorization._authority_scope
    if getattr(scope, "_consumed", True):
        raise ValueError("operator authorization has already been consumed")
    scope._consumed = True


@dataclass(frozen=True, slots=True)
class ContextRunView:
    """Safe operator projection; no query text, Package body, or denied detail."""

    organization_id: UUID = field(repr=False)
    run_ref: str
    decision_ref: str
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    authenticated_application_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    purpose: str
    policy_snapshot_ref: str
    policy_epoch: int
    effective_scope_digest: str = field(repr=False)
    query_digest_profile: str = field(repr=False)
    query_digest_key_version: int = field(repr=False)
    query_digest: str = field(repr=False)
    outcome: ContextRunOutcome
    package_digest_profile: str
    package_digest: str
    package_retention_mode: str
    authorized_evidence_refs: tuple[str, ...]
    effective_max_tokens: int
    effective_max_provider_calls: int
    effective_max_cost_microunits: int
    effective_max_elapsed_ms: int
    usage_tokens: int
    usage_provider_calls: int
    usage_cost_microunits: int
    usage_elapsed_ms: int
    accepted_at: datetime
    finalized_at: datetime
    package_as_of: datetime
    package_expires_at: datetime
    decision_audit_category: DecisionAuditCategory | None
    decision_audit_recorded_at: datetime | None


class PostgreSQLContextRunReader:
    """Read one decision with a short-lived exact ticket and one-use app grant."""

    def __init__(
        self,
        control_engine: Engine,
        operator_engine: Engine,
        *,
        operator_authority: ContextRunOperatorAuthority,
    ) -> None:
        if type(operator_authority) is not ContextRunOperatorAuthority:
            raise TypeError("reader requires ContextRunOperatorAuthority")
        self._control_engine = control_engine
        self._operator_engine = operator_engine
        self._operator_authority = operator_authority

    def find_by_decision_ref(
        self,
        authorization: ContextRunOperatorAuthorization,
        decision_ref: str,
    ) -> ContextRunView | None:
        _require_active_operator_authorization(
            authorization,
            authority=self._operator_authority,
        )
        if (
            type(decision_ref) is not str
            or fullmatch(r"dec_[0-9a-f]{32}", decision_ref) is None
        ):
            raise ValueError("decision_ref must use the closed format")
        if decision_ref != authorization.decision_ref:
            raise ValueError("reader requires its authorization's exact decision_ref")
        _consume_operator_authorization(authorization)

        ticket = token_hex(32)
        primary_error: ContextRunReaderUnavailable | None = None
        try:
            issued = self._issue_ticket(
                ticket=ticket,
                authorization=authorization,
            )
            if not issued:
                return None
            with self._operator_engine.begin() as connection:
                try:
                    assert_security_operator_role(connection)
                except AssertionError:
                    raise ContextRunReaderUnavailable(
                        "ContextRun reader is unavailable"
                    ) from None
                row = (
                    connection.execute(
                        text(
                            """
                        SELECT *
                        FROM read_context_run_by_operator_ticket(
                            :ticket,
                            :organization_id,
                            :decision_ref
                        )
                        """
                        ),
                        {
                            "ticket": ticket,
                            "organization_id": authorization.organization_id,
                            "decision_ref": decision_ref,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                return None
            if (
                row["organization_id"] != authorization.organization_id
                or row["decision_ref"] != decision_ref
            ):
                raise ContextRunReaderUnavailable("ContextRun reader is unavailable")
            return self._view(row)
        except ContextRunReaderUnavailable as error:
            primary_error = error
            raise
        except Exception:
            primary_error = ContextRunReaderUnavailable(
                "ContextRun reader is unavailable"
            )
            raise primary_error from None
        finally:
            try:
                self._revoke_ticket(ticket)
            except ContextRunReaderUnavailable:
                if primary_error is None:
                    raise

    def _issue_ticket(
        self,
        *,
        ticket: str,
        authorization: ContextRunOperatorAuthorization,
    ) -> bool:
        try:
            with self._control_engine.begin() as connection:
                try:
                    assert_control_role(connection)
                except AssertionError:
                    raise ContextRunReaderUnavailable(
                        "ContextRun reader is unavailable"
                    ) from None
                issued = connection.execute(
                    text(
                        """
                        SELECT issue_context_run_operator_read_ticket(
                            :ticket,
                            :organization_id,
                            :decision_ref,
                            :operator_ref,
                            :request_id,
                            :authentication_binding_ref
                        )
                        """
                    ),
                    {
                        "ticket": ticket,
                        "organization_id": authorization.organization_id,
                        "decision_ref": authorization.decision_ref,
                        "operator_ref": authorization.operator_ref,
                        "request_id": authorization.request_id,
                        "authentication_binding_ref": (
                            authorization.authentication_binding_ref
                        ),
                    },
                ).scalar_one()
            if type(issued) is not bool:
                raise ContextRunReaderUnavailable("ContextRun reader is unavailable")
            return issued
        except ContextRunReaderUnavailable:
            raise
        except Exception:
            raise ContextRunReaderUnavailable(
                "ContextRun reader is unavailable"
            ) from None

    def _revoke_ticket(self, ticket: str) -> None:
        try:
            with self._control_engine.begin() as connection:
                try:
                    assert_control_role(connection)
                except AssertionError:
                    raise ContextRunReaderUnavailable(
                        "ContextRun reader is unavailable"
                    ) from None
                revoked = connection.execute(
                    text("SELECT revoke_context_run_operator_read_ticket(:ticket)"),
                    {"ticket": ticket},
                ).scalar_one()
            if type(revoked) is not bool:
                raise ContextRunReaderUnavailable("ContextRun reader is unavailable")
        except ContextRunReaderUnavailable:
            raise
        except Exception:
            raise ContextRunReaderUnavailable(
                "ContextRun reader is unavailable"
            ) from None

    @staticmethod
    def _view(row: RowMapping) -> ContextRunView:
        values = row
        return ContextRunView(
            organization_id=values["organization_id"],
            run_ref=values["run_ref"],
            decision_ref=values["decision_ref"],
            user_id=values["user_id"],
            membership_id=values["membership_id"],
            membership_version=values["membership_version"],
            principal_ref=values["principal_ref"],
            agent_version_ref=values["agent_version_ref"],
            authenticated_application_ref=values["authenticated_application_ref"],
            authentication_binding_ref=values["authentication_binding_ref"],
            request_id=values["request_id"],
            purpose=values["purpose"],
            policy_snapshot_ref=values["policy_snapshot_ref"],
            policy_epoch=values["policy_epoch"],
            effective_scope_digest=values["effective_scope_digest"],
            query_digest_profile=values["query_digest_profile"],
            query_digest_key_version=values["query_digest_key_version"],
            query_digest=values["query_digest"],
            outcome=ContextRunOutcome(values["outcome"]),
            package_digest_profile=values["package_digest_profile"],
            package_digest=values["package_digest"],
            package_retention_mode=values["package_retention_mode"],
            authorized_evidence_refs=tuple(values["authorized_evidence_refs"]),
            effective_max_tokens=values["effective_max_tokens"],
            effective_max_provider_calls=values["effective_max_provider_calls"],
            effective_max_cost_microunits=values["effective_max_cost_microunits"],
            effective_max_elapsed_ms=values["effective_max_elapsed_ms"],
            usage_tokens=values["usage_tokens"],
            usage_provider_calls=values["usage_provider_calls"],
            usage_cost_microunits=values["usage_cost_microunits"],
            usage_elapsed_ms=values["usage_elapsed_ms"],
            accepted_at=values["accepted_at"],
            finalized_at=values["finalized_at"],
            package_as_of=values["package_as_of"],
            package_expires_at=values["package_expires_at"],
            decision_audit_category=(
                DecisionAuditCategory(values["audit_category"])
                if values["audit_category"] is not None
                else None
            ),
            decision_audit_recorded_at=values["audit_recorded_at"],
        )
