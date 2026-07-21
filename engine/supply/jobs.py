"""Signed exact-job WorkerLease domain contracts for the Supply worker."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, NoReturn, cast
from uuid import UUID

WORKER_LEASE_ACTOR_KIND: Final = "service"
WORKER_LEASE_OPERATION: Final = "noop.complete"
_ALGORITHM: Final = "HS256"
_TOKEN_TYPE: Final = "CE-WorkerLease"
_TOKEN_VERSION: Final = 1
_DOMAIN: Final = "context-engine.worker-lease"
_MAX_KEY_VERSION: Final = (1 << 63) - 1
_MINIMUM_SECRET_BYTES: Final = 32
_NONCE_BYTES: Final = 32
_MAX_TOKEN_LENGTH: Final = 8192
_HEADER_FIELDS: Final = frozenset({"alg", "dom", "kid", "typ", "v"})
_CLAIM_FIELDS: Final = frozenset(
    {
        "actor_kind",
        "expires_at",
        "issued_at",
        "job_id",
        "nonce",
        "operation",
        "organization_id",
        "service_principal_id",
        "signing_key_version",
        "worker_audience",
        "workload",
    }
)


def _require_key_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_KEY_VERSION:
        raise ValueError("signing key version must be a positive signed 64-bit integer")
    return value


def _require_identifier(
    field_name: str, value: object, *, maximum_length: int
) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or len(value) > maximum_length
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank identifier")
    return value


def _require_uuid(field_name: str, value: object) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{field_name} must be UUID")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.microsecond != 0:
        raise ValueError(f"{field_name} must have whole-second precision")
    return value


@dataclass(frozen=True, slots=True)
class WorkerLeaseClaims:
    """Immutable exact-job authority claims; safe repr omits all claim values."""

    signing_key_version: int = field(repr=False)
    organization_id: UUID = field(repr=False)
    job_id: UUID = field(repr=False)
    service_principal_id: UUID = field(repr=False)
    workload: str = field(repr=False)
    worker_audience: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    nonce: bytes = field(repr=False)
    actor_kind: Literal["service"] = field(
        default=WORKER_LEASE_ACTOR_KIND, init=False, repr=False
    )
    operation: Literal["noop.complete"] = field(
        default=WORKER_LEASE_OPERATION, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_key_version(self.signing_key_version)
        _require_uuid("organization_id", self.organization_id)
        _require_uuid("job_id", self.job_id)
        _require_uuid("service_principal_id", self.service_principal_id)
        _require_identifier("workload", self.workload, maximum_length=128)
        _require_identifier(
            "worker_audience", self.worker_audience, maximum_length=255
        )
        _require_utc("issued_at", self.issued_at)
        _require_utc("expires_at", self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("WorkerLease expiry must follow issuance")
        if type(self.nonce) is not bytes or len(self.nonce) != _NONCE_BYTES:
            raise ValueError("WorkerLease nonce must contain exactly 256 bits")

    def __reduce__(self) -> NoReturn:
        raise TypeError("WorkerLeaseClaims are not serializable")


@dataclass(frozen=True, slots=True)
class WorkerLeaseToken:
    """Opaque signed token whose normal string forms never reveal its value."""

    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self._value) is not str
            or not self._value
            or len(self._value) > _MAX_TOKEN_LENGTH
            or any(character.isspace() for character in self._value)
        ):
            raise ValueError("WorkerLeaseToken must be a bounded opaque value")

    def __str__(self) -> str:
        return "<WorkerLeaseToken redacted>"

    def serialize(self) -> str:
        """Return the opaque value only at an explicit transport boundary."""

        return self._value

    def __reduce__(self) -> NoReturn:
        raise TypeError("WorkerLeaseToken is not serializable")


class WorkerLeaseKeyring:
    """Explicit versioned signing keys with no ambient or default key."""

    __slots__ = ("_active_version", "_keys")

    def __init__(self, *, active_version: int, keys: Mapping[int, bytes]) -> None:
        version = _require_key_version(active_version)
        if not isinstance(keys, Mapping) or not keys:
            raise ValueError("WorkerLeaseKeyring requires versioned keys")
        copied: dict[int, bytes] = {}
        for key_version, secret in keys.items():
            canonical_version = _require_key_version(key_version)
            if type(secret) is not bytes or len(secret) < _MINIMUM_SECRET_BYTES:
                raise ValueError("WorkerLease signing keys require at least 256 bits")
            copied[canonical_version] = bytes(secret)
        if version not in copied:
            raise ValueError("active WorkerLease key version must exist")
        self._active_version = version
        self._keys = MappingProxyType(copied)

    @property
    def active_version(self) -> int:
        return self._active_version

    def __repr__(self) -> str:
        return "WorkerLeaseKeyring(<redacted>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("WorkerLeaseKeyring is not serializable")

    def _key_for(self, version: int) -> bytes | None:
        return self._keys.get(version)


class WorkerLeaseRejectionCategory(StrEnum):
    """Restricted category shared by all untrusted lease failures."""

    WORK_NOT_AVAILABLE = "work_not_available"


@dataclass(frozen=True, slots=True)
class WorkerLeaseRejectionAuditReceipt:
    """Restricted metadata containing only category and opaque lease digest."""

    lease_digest: str
    category: WorkerLeaseRejectionCategory = (
        WorkerLeaseRejectionCategory.WORK_NOT_AVAILABLE
    )

    def __post_init__(self) -> None:
        if (
            type(self.lease_digest) is not str
            or len(self.lease_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.lease_digest
            )
        ):
            raise ValueError("lease digest must be lowercase SHA-256")
        if self.category is not WorkerLeaseRejectionCategory.WORK_NOT_AVAILABLE:
            raise ValueError("WorkerLease rejection category must remain closed")


class WorkNotAvailable(Exception):
    """Generic safe failure for every untrusted WorkerLease rejection."""

    def __init__(self, audit_receipt: WorkerLeaseRejectionAuditReceipt) -> None:
        self.audit_receipt = audit_receipt
        super().__init__("work not available")


def worker_lease_digest(token: WorkerLeaseToken) -> str:
    if type(token) is not WorkerLeaseToken:
        raise TypeError("token must be WorkerLeaseToken")
    return hashlib.sha256(token._value.encode("utf-8")).hexdigest()


def worker_lease_nonce_digest(nonce: bytes) -> str:
    if type(nonce) is not bytes or len(nonce) != _NONCE_BYTES:
        raise ValueError("WorkerLease nonce must contain exactly 256 bits")
    return hashlib.sha256(nonce).hexdigest()


def generate_worker_lease_nonce() -> bytes:
    """Generate the protocol's fixed high-entropy nonce."""

    return secrets.token_bytes(_NONCE_BYTES)


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claims_document(claims: WorkerLeaseClaims) -> dict[str, object]:
    return {
        "actor_kind": claims.actor_kind,
        "expires_at": _timestamp(claims.expires_at),
        "issued_at": _timestamp(claims.issued_at),
        "job_id": str(claims.job_id),
        "nonce": _base64url_encode(claims.nonce),
        "operation": claims.operation,
        "organization_id": str(claims.organization_id),
        "service_principal_id": str(claims.service_principal_id),
        "signing_key_version": claims.signing_key_version,
        "worker_audience": claims.worker_audience,
        "workload": claims.workload,
    }


class WorkerLeaseCodec:
    """Mint and verify the sole canonical WorkerLease token protocol."""

    __slots__ = ("_keyring",)

    def __init__(self, keyring: WorkerLeaseKeyring) -> None:
        if type(keyring) is not WorkerLeaseKeyring:
            raise TypeError("keyring must be WorkerLeaseKeyring")
        self._keyring = keyring

    @property
    def active_signing_key_version(self) -> int:
        """Return only the non-secret active version required by an issuer."""

        return self._keyring.active_version

    def mint(self, claims: WorkerLeaseClaims) -> WorkerLeaseToken:
        if type(claims) is not WorkerLeaseClaims:
            raise TypeError("claims must be WorkerLeaseClaims")
        if claims.signing_key_version != self._keyring.active_version:
            raise ValueError("claims must select the active WorkerLease key version")
        key = self._keyring._key_for(claims.signing_key_version)
        if key is None:  # pragma: no cover - keyring construction proves this
            raise ValueError("active WorkerLease signing key is unavailable")
        header = {
            "alg": _ALGORITHM,
            "dom": _DOMAIN,
            "kid": claims.signing_key_version,
            "typ": _TOKEN_TYPE,
            "v": _TOKEN_VERSION,
        }
        encoded_header = _base64url_encode(_canonical_json(header))
        encoded_claims = _base64url_encode(_canonical_json(_claims_document(claims)))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = hmac.digest(key, signing_input, "sha256")
        return WorkerLeaseToken(
            f"{encoded_header}.{encoded_claims}.{_base64url_encode(signature)}"
        )

    def verify(
        self,
        token: WorkerLeaseToken,
        *,
        expected_organization_id: UUID,
        expected_job_id: UUID,
        expected_service_principal_id: UUID,
        expected_workload: str,
        expected_operation: Literal["noop.complete"],
        expected_worker_audience: str,
        now: datetime,
    ) -> WorkerLeaseClaims:
        if type(token) is not WorkerLeaseToken:
            raise TypeError("token must be WorkerLeaseToken")
        _require_uuid("expected_organization_id", expected_organization_id)
        _require_uuid("expected_job_id", expected_job_id)
        _require_uuid("expected_service_principal_id", expected_service_principal_id)
        _require_identifier(
            "expected_workload", expected_workload, maximum_length=128
        )
        _require_identifier(
            "expected_worker_audience",
            expected_worker_audience,
            maximum_length=255,
        )
        checked_at = _require_utc("now", now)
        try:
            claims = self._verify_signed(token)
            if (
                claims.organization_id != expected_organization_id
                or claims.job_id != expected_job_id
                or claims.service_principal_id != expected_service_principal_id
                or claims.workload != expected_workload
                or claims.operation != expected_operation
                or claims.worker_audience != expected_worker_audience
                or checked_at < claims.issued_at
                or checked_at >= claims.expires_at
            ):
                raise ValueError
            return claims
        except (
            ValueError,
            TypeError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            raise WorkNotAvailable(
                WorkerLeaseRejectionAuditReceipt(worker_lease_digest(token))
            ) from None

    def _verify_signed(self, token: WorkerLeaseToken) -> WorkerLeaseClaims:
        encoded_header, encoded_claims, encoded_signature = token._value.split(".")
        header = _decode_document(encoded_header, _HEADER_FIELDS)
        if header != {
            "alg": _ALGORITHM,
            "dom": _DOMAIN,
            "kid": header.get("kid"),
            "typ": _TOKEN_TYPE,
            "v": _TOKEN_VERSION,
        }:
            raise ValueError
        key_version = _require_key_version(header["kid"])
        key = self._keyring._key_for(key_version)
        if key is None:
            raise ValueError
        supplied_signature = _base64url_decode(encoded_signature)
        if len(supplied_signature) != hashlib.sha256().digest_size:
            raise ValueError
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        expected_signature = hmac.digest(key, signing_input, "sha256")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError
        document = _decode_document(encoded_claims, _CLAIM_FIELDS)
        if document["signing_key_version"] != key_version:
            raise ValueError
        if document["actor_kind"] != WORKER_LEASE_ACTOR_KIND:
            raise ValueError
        if document["operation"] != WORKER_LEASE_OPERATION:
            raise ValueError
        return WorkerLeaseClaims(
            signing_key_version=key_version,
            organization_id=_parse_uuid(document["organization_id"]),
            job_id=_parse_uuid(document["job_id"]),
            service_principal_id=_parse_uuid(document["service_principal_id"]),
            workload=cast(str, document["workload"]),
            worker_audience=cast(str, document["worker_audience"]),
            issued_at=_parse_timestamp(document["issued_at"]),
            expires_at=_parse_timestamp(document["expires_at"]),
            nonce=_base64url_decode(cast(str, document["nonce"])),
        )


class _ServiceActorAuthorityScope:
    """Private lifetime token for one durable lease/job authority operation."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("ServiceActor authority scopes are not publicly constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("ServiceActor authority scopes are not serializable")


_SERVICE_ACTOR_AUTHORITY_SCOPE_SEAL = object()


def _open_service_actor_authority_scope() -> _ServiceActorAuthorityScope:
    scope = object.__new__(_ServiceActorAuthorityScope)
    scope._active = True
    scope._seal = _SERVICE_ACTOR_AUTHORITY_SCOPE_SEAL
    return scope


def _close_service_actor_authority_scope(
    scope: _ServiceActorAuthorityScope,
) -> None:
    if (
        type(scope) is not _ServiceActorAuthorityScope
        or getattr(scope, "_seal", None) is not _SERVICE_ACTOR_AUTHORITY_SCOPE_SEAL
    ):
        raise TypeError("ServiceActor authority scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class ServiceActor:
    """Least-privilege worker actor from registered service and exact lease facts."""

    organization_id: UUID = field(repr=False)
    service_principal_id: UUID = field(repr=False)
    workload: str = field(repr=False)
    worker_audience: str = field(repr=False)
    operation: Literal["noop.complete"] = field(repr=False)
    expires_at: datetime = field(repr=False)
    _authority_scope: _ServiceActorAuthorityScope = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "ServiceActor can only be constructed by trusted durable-job authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("ServiceActor is not serializable")


def _construct_service_actor(
    *,
    authority_scope: _ServiceActorAuthorityScope,
    claims: WorkerLeaseClaims,
    now: datetime,
) -> ServiceActor:
    if (
        type(authority_scope) is not _ServiceActorAuthorityScope
        or getattr(authority_scope, "_seal", None)
        is not _SERVICE_ACTOR_AUTHORITY_SCOPE_SEAL
        or not getattr(authority_scope, "_active", False)
    ):
        raise ValueError("ServiceActor requires an active trusted authority scope")
    if type(claims) is not WorkerLeaseClaims:
        raise TypeError("claims must be WorkerLeaseClaims")
    checked_at = _require_utc("now", now)
    if checked_at < claims.issued_at or checked_at >= claims.expires_at:
        raise ValueError("ServiceActor requires a current WorkerLease")
    actor = object.__new__(ServiceActor)
    object.__setattr__(actor, "organization_id", claims.organization_id)
    object.__setattr__(actor, "service_principal_id", claims.service_principal_id)
    object.__setattr__(actor, "workload", claims.workload)
    object.__setattr__(actor, "worker_audience", claims.worker_audience)
    object.__setattr__(actor, "operation", claims.operation)
    object.__setattr__(actor, "expires_at", claims.expires_at)
    object.__setattr__(actor, "_authority_scope", authority_scope)
    return actor


def _require_active_service_actor(actor: ServiceActor, *, now: datetime) -> None:
    if type(actor) is not ServiceActor:
        raise TypeError("ServiceActor has the wrong nominal type")
    checked_at = _require_utc("now", now)
    if (
        type(actor._authority_scope) is not _ServiceActorAuthorityScope
        or getattr(actor._authority_scope, "_seal", None)
        is not _SERVICE_ACTOR_AUTHORITY_SCOPE_SEAL
        or not getattr(actor._authority_scope, "_active", False)
        or actor.operation != WORKER_LEASE_OPERATION
        or checked_at >= actor.expires_at
    ):
        raise ValueError("ServiceActor requires active exact-job authority")


def _base64url_decode(value: str) -> bytes:
    if type(value) is not str or not value or "=" in value:
        raise ValueError
    alphabet = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    if any(character not in alphabet for character in value):
        raise ValueError
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if _base64url_encode(decoded) != value:
        raise ValueError
    return decoded


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError
        document[key] = value
    return document


def _decode_document(
    encoded: str, expected_fields: frozenset[str]
) -> dict[str, object]:
    raw = _base64url_decode(encoded)
    document = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    if type(document) is not dict or frozenset(document) != expected_fields:
        raise ValueError
    if _canonical_json(document) != raw:
        raise ValueError
    return document


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str:
        raise ValueError
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError
    return parsed


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if _timestamp(parsed) != value:
        raise ValueError
    return parsed
