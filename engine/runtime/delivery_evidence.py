"""Request-lived nominal contracts for opaque private delivery evidence."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, NoReturn, Protocol, cast
from uuid import UUID

import rfc8785

DELIVERY_EVIDENCE_DIGEST_PROFILE = "delivery-evidence-ref-sha256-v1"
PRIVATE_AUDIENCE_DIGEST_PROFILE = "private-delivery-audience-rfc8785-sha256-v1"
PRIVATE_DELIVERY_EVIDENCE_PROFILE = "private-delivery-evidence-v1"
DELIVERY_EVIDENCE_RETENTION_CLASS = "short_lived_digest_only"
PRIVATE_DELIVERY_KIND = "private"


class DeliveryEvidenceNotAvailable(Exception):
    """Opaque evidence did not establish the exact trusted delivery facts."""


class DeliveryEvidenceAuthorityUnavailable(RuntimeError):
    """The trusted delivery-evidence authority could not complete."""


@dataclass(frozen=True, slots=True)
class DeliveryEvidenceProfile:
    """Versioned deployment input for evidence lifetime and digest retention."""

    profile_ref: str
    maximum_ttl: timedelta
    retention_class: str = DELIVERY_EVIDENCE_RETENTION_CLASS

    def __post_init__(self) -> None:
        _require_nonblank("profile_ref", self.profile_ref)
        if type(self.maximum_ttl) is not timedelta or self.maximum_ttl <= timedelta(0):
            raise ValueError("delivery evidence maximum_ttl must be positive")
        if self.retention_class != DELIVERY_EVIDENCE_RETENTION_CLASS:
            raise ValueError("delivery evidence retention class is not active")


@dataclass(frozen=True, slots=True)
class PrivateDeliveryEvidenceIssue:
    """Trusted private-delivery facts accepted only by the identity Adapter."""

    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    authenticated_service_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    destination_ref: str = field(repr=False)
    consumer_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    profile_ref: str = field(
        default=PRIVATE_DELIVERY_EVIDENCE_PROFILE,
        repr=False,
    )

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"private delivery {field_name} must be UUID")
        for field_name in (
            "authenticated_service_ref",
            "authentication_binding_ref",
            "request_id",
            "destination_ref",
            "consumer_ref",
            "purpose",
            "profile_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        if type(self.membership_version) is not int or self.membership_version < 1:
            raise ValueError("private delivery membership_version must be positive")
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise ValueError("private delivery policy_epoch must be positive")
        _require_utc("issued_at", self.issued_at)
        _require_utc("expires_at", self.expires_at)
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0):
            raise ValueError("private delivery lifetime must be positive")
        if self.profile_ref != PRIVATE_DELIVERY_EVIDENCE_PROFILE:
            raise ValueError("private delivery profile is not active")


@dataclass(frozen=True, slots=True)
class IssuedDeliveryEvidence:
    """Opaque locator returned to a trusted delivery caller."""

    evidence_ref: str = field(repr=False)
    logical_resolution_ref: str = field(repr=False)
    expires_at: datetime = field(repr=False)
    profile_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonblank("reference", self.evidence_ref)
        _require_nonblank("logical_resolution_ref", self.logical_resolution_ref)
        _require_utc("expires_at", self.expires_at)
        if self.profile_ref != PRIVATE_DELIVERY_EVIDENCE_PROFILE:
            raise ValueError("issued delivery profile is not active")


class DeliveryEvidenceIssuerPort(Protocol):
    """Persistence port implemented by the dedicated identity database login."""

    def issue_private(
        self,
        *,
        request: PrivateDeliveryEvidenceIssue,
        evidence_digest: bytes,
        audience_digest: str,
        logical_resolution_ref: str,
    ) -> bool: ...


class DeliveryEvidenceRetentionPort(Protocol):
    """Organization-scoped cleanup using authority-owned current time."""

    def delete_expired_private(self, organization_id: UUID) -> int: ...


class PrivateDeliveryEvidenceRetention:
    """Delete only expired digest records through the trusted identity boundary."""

    def __init__(self, port: DeliveryEvidenceRetentionPort) -> None:
        if not callable(getattr(port, "delete_expired_private", None)):
            raise TypeError("delivery evidence retention port is incomplete")
        self._port = port

    def delete_expired(self, organization_id: UUID) -> int:
        if type(organization_id) is not UUID:
            raise TypeError("delivery evidence retention requires an Organization UUID")
        try:
            deleted = self._port.delete_expired_private(organization_id)
        except DeliveryEvidenceAuthorityUnavailable:
            raise
        except Exception as error:
            raise DeliveryEvidenceAuthorityUnavailable from error
        if type(deleted) is not int or deleted < 0:
            raise DeliveryEvidenceAuthorityUnavailable
        return deleted


class PrivateDeliveryEvidenceIssuer:
    """Generate opaque locators and persist only their digest and exact bindings."""

    def __init__(
        self,
        port: DeliveryEvidenceIssuerPort,
        *,
        profile: DeliveryEvidenceProfile,
        reference_factory: Callable[[], str] | None = None,
        resolution_ref_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(getattr(port, "issue_private", None)):
            raise TypeError("delivery evidence issuer port is incomplete")
        if type(profile) is not DeliveryEvidenceProfile:
            raise TypeError("delivery evidence issuer requires a versioned profile")
        self._port = port
        self._profile = profile
        self._reference_factory = reference_factory or (
            lambda: "der_" + secrets.token_hex(32)
        )
        self._resolution_ref_factory = resolution_ref_factory or (
            lambda: "dlr_" + secrets.token_hex(16)
        )

    def issue_private(
        self,
        request: PrivateDeliveryEvidenceIssue,
    ) -> IssuedDeliveryEvidence:
        if type(request) is not PrivateDeliveryEvidenceIssue:
            raise TypeError("private delivery issuance has the wrong request type")
        if (
            request.profile_ref != self._profile.profile_ref
            or request.expires_at - request.issued_at > self._profile.maximum_ttl
        ):
            raise DeliveryEvidenceNotAvailable
        evidence_ref = self._reference_factory()
        _require_nonblank("generated reference", evidence_ref)
        logical_resolution_ref = self._resolution_ref_factory()
        _require_nonblank("generated logical resolution", logical_resolution_ref)
        audience_digest = private_delivery_audience_digest(request)
        try:
            persisted = self._port.issue_private(
                request=request,
                evidence_digest=hashlib.sha256(evidence_ref.encode("utf-8")).digest(),
                audience_digest=audience_digest,
                logical_resolution_ref=logical_resolution_ref,
            )
        except DeliveryEvidenceAuthorityUnavailable:
            raise
        except Exception as error:
            raise DeliveryEvidenceAuthorityUnavailable from error
        if persisted is not True:
            raise DeliveryEvidenceNotAvailable
        return IssuedDeliveryEvidence(
            evidence_ref=evidence_ref,
            logical_resolution_ref=logical_resolution_ref,
            expires_at=request.expires_at,
            profile_ref=request.profile_ref,
        )


def private_delivery_audience_digest(request: PrivateDeliveryEvidenceIssue) -> str:
    """Compute the versioned private Membership/destination audience digest."""

    if type(request) is not PrivateDeliveryEvidenceIssue:
        raise TypeError("private audience digest requires trusted issuance facts")
    return private_delivery_audience_digest_for_binding(
        organization_id=request.organization_id,
        membership_id=request.membership_id,
        membership_version=request.membership_version,
        destination_ref=request.destination_ref,
        consumer_ref=request.consumer_ref,
    )


def private_delivery_audience_digest_for_binding(
    *,
    organization_id: UUID,
    membership_id: UUID,
    membership_version: int,
    destination_ref: str,
    consumer_ref: str,
) -> str:
    """Digest independently trusted private route and Membership bindings."""

    if type(organization_id) is not UUID or type(membership_id) is not UUID:
        raise TypeError("private audience binding requires UUID ownership")
    if type(membership_version) is not int or membership_version < 1:
        raise ValueError("private audience Membership version must be positive")
    _require_nonblank("destination_ref", destination_ref)
    _require_nonblank("consumer_ref", consumer_ref)
    document = {
        "consumerRef": consumer_ref,
        "destinationRef": destination_ref,
        "membershipId": str(membership_id),
        "membershipVersion": membership_version,
        "organizationId": str(organization_id),
        "profile": PRIVATE_AUDIENCE_DIGEST_PROFILE,
    }
    return hashlib.sha256(
        b"context-engine.private-delivery-audience.v1\x00"
        + rfc8785.dumps(cast(Any, document))
    ).hexdigest()


def _require_nonblank(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"delivery evidence {field_name} must be non-empty")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"delivery evidence {field_name} must be aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class PrivateDeliveryEvidenceRedemption:
    """Exact authenticated request bindings submitted to the trusted authority."""

    evidence_ref: str = field(repr=False)
    evidence_digest: bytes = field(repr=False)
    authenticated_service_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    destination_ref: str = field(repr=False)
    consumer_ref: str = field(repr=False)
    delivery_kind: str = field(repr=False)
    audience_digest: str = field(repr=False)
    purpose: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    redeemed_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonblank("reference", self.evidence_ref)
        if (
            type(self.evidence_digest) is not bytes
            or len(self.evidence_digest) != hashlib.sha256().digest_size
            or self.evidence_digest
            != hashlib.sha256(self.evidence_ref.encode("utf-8")).digest()
        ):
            raise ValueError("delivery evidence digest must match the opaque reference")
        for field_name in (
            "authenticated_service_ref",
            "authentication_binding_ref",
            "request_id",
            "destination_ref",
            "consumer_ref",
            "delivery_kind",
            "purpose",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"delivery evidence {field_name} must be UUID")
        if type(self.membership_version) is not int or self.membership_version < 1:
            raise ValueError("delivery evidence membership_version must be positive")
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise ValueError("delivery evidence policy_epoch must be positive")
        _require_sha256_hex("audience_digest", self.audience_digest)
        _require_utc("redeemed_at", self.redeemed_at)


@dataclass(frozen=True, slots=True)
class RedeemedPrivateDeliveryEvidence:
    """Trusted private-delivery facts returned by exact durable redemption."""

    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    authenticated_service_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    destination_ref: str = field(repr=False)
    consumer_ref: str = field(repr=False)
    delivery_kind: str = field(repr=False)
    purpose: str = field(repr=False)
    audience_digest: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    logical_resolution_ref: str = field(repr=False)
    profile_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"redeemed delivery {field_name} must be UUID")
        for field_name in (
            "authenticated_service_ref",
            "authentication_binding_ref",
            "request_id",
            "destination_ref",
            "consumer_ref",
            "delivery_kind",
            "purpose",
            "logical_resolution_ref",
            "profile_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        if self.delivery_kind != PRIVATE_DELIVERY_KIND:
            raise ValueError("redeemed delivery kind is not private")
        _require_sha256_hex("redeemed audience_digest", self.audience_digest)
        if type(self.membership_version) is not int or self.membership_version < 1:
            raise ValueError("redeemed delivery membership_version must be positive")
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise ValueError("redeemed delivery policy_epoch must be positive")
        _require_utc("issued_at", self.issued_at)
        _require_utc("expires_at", self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("redeemed delivery expiry must follow issuance")


class DeliveryEvidenceRedemptionPort(Protocol):
    """Narrow durable redemption operation held by one UserActor transaction."""

    def redeem_private(
        self,
        request: PrivateDeliveryEvidenceRedemption,
    ) -> RedeemedPrivateDeliveryEvidence | None: ...


class _DeliveryEvidenceRedemptionScope:
    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("delivery evidence redemption scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("delivery evidence redemption scopes are not serializable")


_DELIVERY_EVIDENCE_REDEMPTION_SCOPE_SEAL = object()


def _open_delivery_evidence_redemption_scope() -> _DeliveryEvidenceRedemptionScope:
    scope = object.__new__(_DeliveryEvidenceRedemptionScope)
    scope._active = True
    scope._seal = _DELIVERY_EVIDENCE_REDEMPTION_SCOPE_SEAL
    return scope


def _close_delivery_evidence_redemption_scope(
    scope: _DeliveryEvidenceRedemptionScope,
) -> None:
    if (
        type(scope) is not _DeliveryEvidenceRedemptionScope
        or getattr(scope, "_seal", None) is not _DELIVERY_EVIDENCE_REDEMPTION_SCOPE_SEAL
    ):
        raise TypeError("delivery evidence redemption scope has the wrong type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class DeliveryEvidenceRedemptionSession:
    """Nominal request-lived capability for one durable redemption call."""

    _authority_scope: _DeliveryEvidenceRedemptionScope = field(repr=False)
    _port: DeliveryEvidenceRedemptionPort = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("delivery evidence session requires a trusted transaction")

    def __reduce__(self) -> NoReturn:
        raise TypeError("delivery evidence session is not serializable")


def _require_active_delivery_evidence_redemption_session(
    session: DeliveryEvidenceRedemptionSession,
) -> None:
    if type(session) is not DeliveryEvidenceRedemptionSession:
        raise TypeError("delivery evidence session has the wrong nominal type")
    scope = session._authority_scope
    if (
        type(scope) is not _DeliveryEvidenceRedemptionScope
        or getattr(scope, "_seal", None) is not _DELIVERY_EVIDENCE_REDEMPTION_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("delivery evidence session requires active authority")
    if not callable(getattr(session._port, "redeem_private", None)):
        raise TypeError("delivery evidence redemption port is incomplete")


def _construct_delivery_evidence_redemption_session(
    *,
    authority_scope: _DeliveryEvidenceRedemptionScope,
    port: DeliveryEvidenceRedemptionPort,
) -> DeliveryEvidenceRedemptionSession:
    session = object.__new__(DeliveryEvidenceRedemptionSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    _require_active_delivery_evidence_redemption_session(session)
    return session


def redeem_private_delivery_evidence(
    session: DeliveryEvidenceRedemptionSession,
    request: PrivateDeliveryEvidenceRedemption,
) -> RedeemedPrivateDeliveryEvidence:
    """Redeem exact private evidence or return one generic unavailable result."""

    _require_active_delivery_evidence_redemption_session(session)
    if type(request) is not PrivateDeliveryEvidenceRedemption:
        raise TypeError("private delivery redemption has the wrong request type")
    try:
        result = session._port.redeem_private(request)
    except DeliveryEvidenceAuthorityUnavailable:
        raise
    except Exception as error:
        raise DeliveryEvidenceAuthorityUnavailable from error
    if type(result) is not RedeemedPrivateDeliveryEvidence:
        raise DeliveryEvidenceNotAvailable
    if (
        result.organization_id != request.organization_id
        or result.user_id != request.user_id
        or result.membership_id != request.membership_id
        or result.membership_version != request.membership_version
        or result.authenticated_service_ref != request.authenticated_service_ref
        or result.authentication_binding_ref != request.authentication_binding_ref
        or result.request_id != request.request_id
        or result.destination_ref != request.destination_ref
        or result.consumer_ref != request.consumer_ref
        or result.delivery_kind != request.delivery_kind
        or result.audience_digest != request.audience_digest
        or result.purpose != request.purpose
        or result.policy_epoch != request.policy_epoch
        or not result.issued_at <= request.redeemed_at < result.expires_at
    ):
        raise DeliveryEvidenceNotAvailable
    return result


def _require_sha256_hex(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"delivery evidence {field_name} must be SHA-256")
    return value
