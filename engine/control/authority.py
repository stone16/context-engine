"""Lifetime- and operation-bound trusted ContextControl operator calls."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import NoReturn, Protocol
from uuid import UUID

from engine.control.contracts import _require_bounded_text, _require_utc


class ControlOperation(StrEnum):
    IMPORT_FILE = "import_file"
    OFFBOARD_FILE_SOURCE = "offboard_file_source"
    REGISTER_SOURCE = "register_source"
    READ_SOURCE = "read_source"
    READ_SOURCE_PROGRESS = "read_source_progress"
    TOMBSTONE_FILE_RESOURCE = "tombstone_file_resource"


class ControlOperatorAuthenticationRejected(Exception):
    """Opaque credentials did not establish the requested Control authority."""

    def __init__(self) -> None:
        super().__init__("control operator authentication rejected")


class ControlOperatorAuthorityUnavailable(RuntimeError):
    """The configured operator authenticator could not complete safely."""


@dataclass(frozen=True, slots=True)
class VerifiedControlOperatorIdentity:
    """Trusted current source-administration facts from one authenticator."""

    organization_id: UUID = field(repr=False)
    operator_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)
    allowed_operations: frozenset[ControlOperation] = field(repr=False)
    valid_from: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("control operator organization_id must be UUID")
        for name in (
            "operator_ref",
            "authentication_binding_ref",
            "authority_ref",
        ):
            _require_bounded_text(
                f"control operator {name}", getattr(self, name), 256
            )
        if (
            type(self.allowed_operations) is not frozenset
            or not self.allowed_operations
            or any(
                type(value) is not ControlOperation
                for value in self.allowed_operations
            )
        ):
            raise ValueError(
                "control operator operations must be a closed nonempty set"
            )
        valid_from = _require_utc("control operator valid_from", self.valid_from)
        expires_at = _require_utc("control operator expires_at", self.expires_at)
        if expires_at <= valid_from:
            raise ValueError("control operator lifetime must be positive")

    def __reduce__(self) -> NoReturn:
        raise TypeError("verified control operator identity is not serializable")


class ControlOperatorAuthenticator(Protocol):
    def authenticate(
        self, opaque_credential: str
    ) -> VerifiedControlOperatorIdentity: ...


class _ControlAuthorityScope:
    __slots__ = ("issuer_seal", "nonce", "seal")
    issuer_seal: object
    nonce: bytes
    seal: object

    def __init__(self) -> None:
        raise TypeError("control authority scopes are not constructible")


_CONTROL_SCOPE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class TrustedControlCall:
    """Construction-sealed one-operation call with no ambient authority."""

    organization_id: UUID = field(repr=False)
    operator_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)
    operation: ControlOperation
    request_id: str
    issued_at: datetime
    expires_at: datetime
    _digest: bytes = field(repr=False)
    _scope: _ControlAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("TrustedControlCall is authority-constructed")

    def __repr__(self) -> str:
        return "TrustedControlCall(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("TrustedControlCall is not serializable")


class ControlOperatorAuthority:
    """Authenticate one operator and retain one exact Control operation."""

    __slots__ = (
        "_active_calls",
        "_authenticator",
        "_call_key",
        "_call_ttl",
        "_clock",
        "_issuer_seal",
        "_state_lock",
    )

    def __init__(
        self,
        authenticator: ControlOperatorAuthenticator,
        *,
        call_ttl: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(getattr(authenticator, "authenticate", None)):
            raise TypeError("control operator authenticator is incomplete")
        if type(call_ttl) is not timedelta or call_ttl <= timedelta(0):
            raise ValueError("control call TTL must be positive")
        if not callable(clock):
            raise TypeError("control authority clock must be callable")
        self._authenticator = authenticator
        self._active_calls: dict[bytes, bool] = {}
        self._call_key = secrets.token_bytes(32)
        self._call_ttl = call_ttl
        self._clock = clock
        self._issuer_seal = object()
        self._state_lock = Lock()

    def authorize(
        self,
        *,
        opaque_credential: str,
        operation: ControlOperation,
        request_id: str,
    ) -> AbstractContextManager[TrustedControlCall]:
        """Authenticate and retain one trusted call only for its context."""

        if (
            type(opaque_credential) is not str
            or not opaque_credential
            or opaque_credential.isspace()
        ):
            raise ControlOperatorAuthenticationRejected
        if type(operation) is not ControlOperation:
            raise ControlOperatorAuthenticationRejected
        _require_bounded_text("control request_id", request_id, 256)
        return self._authorized_call(
            opaque_credential=opaque_credential,
            operation=operation,
            request_id=request_id,
        )

    @contextmanager
    def _authorized_call(
        self,
        *,
        opaque_credential: str,
        operation: ControlOperation,
        request_id: str,
    ) -> Iterator[TrustedControlCall]:
        try:
            identity = self._authenticator.authenticate(opaque_credential)
        except ControlOperatorAuthenticationRejected:
            raise ControlOperatorAuthenticationRejected from None
        except Exception:
            raise ControlOperatorAuthorityUnavailable(
                "control operator authority is unavailable"
            ) from None
        if type(identity) is not VerifiedControlOperatorIdentity:
            raise ControlOperatorAuthorityUnavailable(
                "control operator authority is unavailable"
            )
        now = _require_utc("control call issued_at", self._clock())
        if (
            operation not in identity.allowed_operations
            or now < identity.valid_from
            or now >= identity.expires_at
        ):
            raise ControlOperatorAuthenticationRejected
        expires_at = min(identity.expires_at, now + self._call_ttl)
        scope = object.__new__(_ControlAuthorityScope)
        scope.issuer_seal = self._issuer_seal
        scope.nonce = secrets.token_bytes(32)
        scope.seal = _CONTROL_SCOPE_SEAL
        with self._state_lock:
            self._active_calls[scope.nonce] = False
        call = object.__new__(TrustedControlCall)
        values: dict[str, object] = {
            "organization_id": identity.organization_id,
            "operator_ref": identity.operator_ref,
            "authentication_binding_ref": identity.authentication_binding_ref,
            "authority_ref": identity.authority_ref,
            "operation": operation,
            "request_id": request_id,
            "issued_at": now,
            "expires_at": expires_at,
            "_scope": scope,
        }
        values["_digest"] = _control_call_digest(
            key=self._call_key,
            organization_id=identity.organization_id,
            operator_ref=identity.operator_ref,
            authentication_binding_ref=identity.authentication_binding_ref,
            authority_ref=identity.authority_ref,
            operation=operation,
            request_id=request_id,
            issued_at=now,
            expires_at=expires_at,
            nonce=scope.nonce,
        )
        for name, value in values.items():
            object.__setattr__(call, name, value)
        try:
            yield call
        finally:
            with self._state_lock:
                self._active_calls.pop(scope.nonce, None)


def _validate_and_consume_control_call(
    call: TrustedControlCall,
    *,
    authority: ControlOperatorAuthority,
    expected_operation: ControlOperation,
    checked_at: datetime,
) -> None:
    if type(call) is not TrustedControlCall:
        raise ControlOperatorAuthenticationRejected
    try:
        scope = call._scope
        if type(scope) is not _ControlAuthorityScope:
            raise ControlOperatorAuthenticationRejected
        digest = call._digest
        expected_digest = _control_call_digest(
            key=authority._call_key,
            organization_id=call.organization_id,
            operator_ref=call.operator_ref,
            authentication_binding_ref=call.authentication_binding_ref,
            authority_ref=call.authority_ref,
            operation=call.operation,
            request_id=call.request_id,
            issued_at=call.issued_at,
            expires_at=call.expires_at,
            nonce=scope.nonce,
        )
    except (AttributeError, TypeError, ValueError):
        raise ControlOperatorAuthenticationRejected from None
    if (
        scope.seal is not _CONTROL_SCOPE_SEAL
        or scope.issuer_seal is not authority._issuer_seal
        or call.operation is not expected_operation
        or type(digest) is not bytes
        or not hmac.compare_digest(digest, expected_digest)
    ):
        raise ControlOperatorAuthenticationRejected
    now = _require_utc("control call checked_at", checked_at)
    if now < call.issued_at or now >= call.expires_at:
        raise ControlOperatorAuthenticationRejected
    with authority._state_lock:
        if authority._active_calls.get(scope.nonce) is not False:
            raise ControlOperatorAuthenticationRejected
        authority._active_calls[scope.nonce] = True


def _control_call_digest(
    *,
    key: bytes,
    organization_id: UUID,
    operator_ref: str,
    authentication_binding_ref: str,
    authority_ref: str,
    operation: ControlOperation,
    request_id: str,
    issued_at: datetime,
    expires_at: datetime,
    nonce: bytes,
) -> bytes:
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("control call signing key is invalid")
    if type(organization_id) is not UUID or type(operation) is not ControlOperation:
        raise TypeError("control call claims are invalid")
    for field_name, value in (
        ("operator_ref", operator_ref),
        ("authentication_binding_ref", authentication_binding_ref),
        ("authority_ref", authority_ref),
        ("request_id", request_id),
    ):
        _require_bounded_text(f"control call {field_name}", value, 256)
    _require_utc("control call issued_at", issued_at)
    _require_utc("control call expires_at", expires_at)
    if type(nonce) is not bytes or len(nonce) != 32:
        raise ValueError("control call nonce is invalid")
    document = {
        "authenticationBindingRef": authentication_binding_ref,
        "authorityRef": authority_ref,
        "expiresAt": expires_at.isoformat(),
        "issuedAt": issued_at.isoformat(),
        "nonce": nonce.hex(),
        "operation": operation.value,
        "operatorRef": operator_ref,
        "organizationId": str(organization_id),
        "requestId": request_id,
    }
    payload = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hmac.new(
        key,
        b"context-engine.control-call.v1\x00" + payload,
        hashlib.sha256,
    ).digest()
