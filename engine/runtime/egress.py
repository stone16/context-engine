"""Nominal one-hop egress grants and exact redemption bindings."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, NoReturn, Protocol, cast
from uuid import UUID

import rfc8785

MODEL_EGRESS_GRANT_PREFIX = "egrm"
CHANNEL_EGRESS_GRANT_PREFIX = "egrc"
EGRESS_GRANT_DIGEST_PROFILE = "egress-grant-locator-sha256-v1"
EGRESS_GRANT_PROFILE_LINEAGE = "egress-grant-v1"


class EgressGrantNotAvailable(Exception):
    """The opaque grant did not authorize the exact declared egress hop."""

    def __init__(self) -> None:
        super().__init__("egress grant not available")


class EgressGrantIssuanceUnavailable(RuntimeError):
    """The final egress gate could not persist its one-shot grant."""


class EgressGrantAuthorityUnavailable(RuntimeError):
    """The independent one-shot redemption authority could not decide."""


class EgressAuditCategory(StrEnum):
    """Restricted durable categories that never contain denied details."""

    ISSUED = "issued"
    CONSUMED = "consumed"


def _require_nonblank(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"egress {field_name} must be non-empty")
    return value


def _require_sha256(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"egress {field_name} must be lowercase SHA-256")
    return value


def _require_positive_epoch(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("egress policy_epoch must be a positive integer")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"egress {field_name} must be aware UTC")
    return value


def _require_opaque_grant(value: object, *, prefix: str) -> str:
    if (
        type(value) is not str
        or not value.startswith(f"{prefix}_")
        or len(value) != len(prefix) + 1 + 64
        or any(character not in "0123456789abcdef" for character in value[5:])
    ):
        raise ValueError("egress grant must use the closed opaque locator format")
    return value


def direct_egress_audience_digest(
    *,
    organization_id: UUID,
    membership_id: UUID,
    membership_version: int,
    authenticated_application_ref: str,
    delivery_binding_ref: str,
) -> str:
    """Bind a direct trusted consumer without inventing an AudienceSnapshot."""

    if type(organization_id) is not UUID or type(membership_id) is not UUID:
        raise TypeError("direct egress audience requires UUID ownership")
    if type(membership_version) is not int or membership_version < 1:
        raise ValueError("direct egress audience Membership version must be positive")
    _require_nonblank("authenticated_application_ref", authenticated_application_ref)
    _require_nonblank("delivery_binding_ref", delivery_binding_ref)
    document = {
        "applicationRef": authenticated_application_ref,
        "deliveryBindingRef": delivery_binding_ref,
        "membershipId": str(membership_id),
        "membershipVersion": membership_version,
        "organizationId": str(organization_id),
        "profile": "direct-egress-audience-rfc8785-sha256-v1",
    }
    return hashlib.sha256(
        b"context-engine.direct-egress-audience.v1\x00"
        + rfc8785.dumps(cast(Any, document))
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelEgressGrant:
    """Opaque capability for exactly one model hop."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_opaque_grant(self.value, prefix=MODEL_EGRESS_GRANT_PREFIX)

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.value.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class ChannelEgressGrant:
    """Opaque capability for exactly one channel preflight hop, never an effect."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_opaque_grant(self.value, prefix=CHANNEL_EGRESS_GRANT_PREFIX)

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.value.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class ModelEgressProfile:
    """Versioned, server-owned final policy for one exact model boundary."""

    profile_ref: str
    retention_policy_ref: str
    sensitivity_policy_ref: str
    issuer_ref: str
    consumer_ref: str
    provider_ref: str
    model_ref: str
    region_ref: str
    maximum_ttl: timedelta

    def __post_init__(self) -> None:
        for field_name in (
            "profile_ref",
            "retention_policy_ref",
            "sensitivity_policy_ref",
            "issuer_ref",
            "consumer_ref",
            "provider_ref",
            "model_ref",
            "region_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        if type(self.maximum_ttl) is not timedelta or self.maximum_ttl <= timedelta(0):
            raise ValueError("egress maximum_ttl must be positive")


@dataclass(frozen=True, slots=True)
class ChannelEgressProfile:
    """Versioned, server-owned final policy for one exact channel boundary."""

    profile_ref: str
    retention_policy_ref: str
    sensitivity_policy_ref: str
    issuer_ref: str
    consumer_ref: str
    channel_ref: str
    destination_ref: str
    region_ref: str
    maximum_ttl: timedelta

    def __post_init__(self) -> None:
        for field_name in (
            "profile_ref",
            "retention_policy_ref",
            "sensitivity_policy_ref",
            "issuer_ref",
            "consumer_ref",
            "channel_ref",
            "destination_ref",
            "region_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        if type(self.maximum_ttl) is not timedelta or self.maximum_ttl <= timedelta(0):
            raise ValueError("egress maximum_ttl must be positive")


@dataclass(frozen=True, slots=True)
class InternalOnlyEgressProfile:
    """Closed policy state that issues no authority for an external hop."""

    profile_ref: str = "internal-only-egress-v1"

    def __post_init__(self) -> None:
        if self.profile_ref != "internal-only-egress-v1":
            raise ValueError("internal-only egress profile is closed")


INTERNAL_ONLY_EGRESS_PROFILE = InternalOnlyEgressProfile()


@dataclass(frozen=True, slots=True)
class EgressGrantIssue:
    """Trusted final Package/hop facts persisted without the bearer value."""

    hop_kind: str
    organization_id: UUID = field(repr=False)
    package_digest: str
    payload_digest: str
    purpose: str
    audience_digest: str
    policy_epoch: int
    retention_policy_ref: str
    sensitivity_policy_ref: str
    issuer_ref: str
    consumer_ref: str
    provider_ref: str | None
    model_ref: str | None
    channel_ref: str | None
    destination_ref: str | None
    region_ref: str
    issued_at: datetime
    expires_at: datetime
    profile_ref: str
    grant_profile_ref: str = EGRESS_GRANT_PROFILE_LINEAGE
    category: EgressAuditCategory = EgressAuditCategory.ISSUED

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("egress issue Organization must be UUID")
        _require_sha256("package_digest", self.package_digest)
        _require_sha256("payload_digest", self.payload_digest)
        _require_sha256("audience_digest", self.audience_digest)
        _require_positive_epoch(self.policy_epoch)
        for field_name in (
            "purpose",
            "retention_policy_ref",
            "sensitivity_policy_ref",
            "issuer_ref",
            "consumer_ref",
            "region_ref",
            "profile_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        _require_utc("issued_at", self.issued_at)
        _require_utc("expires_at", self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ValueError("egress expiry must follow issuance")
        if self.grant_profile_ref != EGRESS_GRANT_PROFILE_LINEAGE:
            raise ValueError("egress grant profile lineage is not active")
        if self.category is not EgressAuditCategory.ISSUED:
            raise ValueError("new egress grant category must be issued")
        if self.hop_kind == "model":
            if (
                self.provider_ref is None
                or self.model_ref is None
                or self.channel_ref is not None
                or self.destination_ref is not None
            ):
                raise ValueError("model issue must contain only model hop fields")
            _require_nonblank("provider_ref", self.provider_ref)
            _require_nonblank("model_ref", self.model_ref)
        elif self.hop_kind == "channel":
            if (
                self.channel_ref is None
                or self.destination_ref is None
                or self.provider_ref is not None
                or self.model_ref is not None
            ):
                raise ValueError("channel issue must contain only channel hop fields")
            _require_nonblank("channel_ref", self.channel_ref)
            _require_nonblank("destination_ref", self.destination_ref)
        else:
            raise ValueError("egress issue hop_kind must be model or channel")

    @classmethod
    def for_model(
        cls,
        *,
        organization_id: UUID,
        package_digest: str,
        payload_digest: str,
        purpose: str,
        audience_digest: str,
        policy_epoch: int,
        issued_at: datetime,
        expires_at: datetime,
        profile: ModelEgressProfile,
    ) -> EgressGrantIssue:
        if type(profile) is not ModelEgressProfile:
            raise TypeError("model issue requires ModelEgressProfile")
        if expires_at - issued_at > profile.maximum_ttl:
            raise ValueError("model egress lifetime exceeds its profile")
        return cls(
            hop_kind="model",
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose=purpose,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            retention_policy_ref=profile.retention_policy_ref,
            sensitivity_policy_ref=profile.sensitivity_policy_ref,
            issuer_ref=profile.issuer_ref,
            consumer_ref=profile.consumer_ref,
            provider_ref=profile.provider_ref,
            model_ref=profile.model_ref,
            channel_ref=None,
            destination_ref=None,
            region_ref=profile.region_ref,
            issued_at=issued_at,
            expires_at=expires_at,
            profile_ref=profile.profile_ref,
        )

    @classmethod
    def for_channel(
        cls,
        *,
        organization_id: UUID,
        package_digest: str,
        payload_digest: str,
        purpose: str,
        audience_digest: str,
        policy_epoch: int,
        issued_at: datetime,
        expires_at: datetime,
        profile: ChannelEgressProfile,
    ) -> EgressGrantIssue:
        if type(profile) is not ChannelEgressProfile:
            raise TypeError("channel issue requires ChannelEgressProfile")
        if expires_at - issued_at > profile.maximum_ttl:
            raise ValueError("channel egress lifetime exceeds its profile")
        return cls(
            hop_kind="channel",
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose=purpose,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            retention_policy_ref=profile.retention_policy_ref,
            sensitivity_policy_ref=profile.sensitivity_policy_ref,
            issuer_ref=profile.issuer_ref,
            consumer_ref=profile.consumer_ref,
            provider_ref=None,
            model_ref=None,
            channel_ref=profile.channel_ref,
            destination_ref=profile.destination_ref,
            region_ref=profile.region_ref,
            issued_at=issued_at,
            expires_at=expires_at,
            profile_ref=profile.profile_ref,
        )


class EgressGrantIssuancePort(Protocol):
    """Digest-only write owned by the retained current-UserActor transaction."""

    def issue(self, request: EgressGrantIssue, grant_digest: bytes) -> bool: ...


class _EgressGrantIssuanceScope:
    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("egress issuance scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("egress issuance scopes are not serializable")


_EGRESS_GRANT_ISSUANCE_SCOPE_SEAL = object()


def _open_egress_grant_issuance_scope() -> _EgressGrantIssuanceScope:
    scope = object.__new__(_EgressGrantIssuanceScope)
    scope._active = True
    scope._seal = _EGRESS_GRANT_ISSUANCE_SCOPE_SEAL
    return scope


def _close_egress_grant_issuance_scope(scope: _EgressGrantIssuanceScope) -> None:
    if (
        type(scope) is not _EgressGrantIssuanceScope
        or getattr(scope, "_seal", None) is not _EGRESS_GRANT_ISSUANCE_SCOPE_SEAL
    ):
        raise TypeError("egress issuance scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class EgressGrantIssuanceSession:
    """Nominal issuance authority valid only in its owning transaction."""

    _authority_scope: _EgressGrantIssuanceScope = field(repr=False)
    _port: EgressGrantIssuancePort = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("EgressGrantIssuanceSession is authority-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("EgressGrantIssuanceSession is not serializable")


def _require_active_egress_grant_issuance_session(
    session: EgressGrantIssuanceSession,
) -> None:
    if type(session) is not EgressGrantIssuanceSession:
        raise TypeError("egress issuance session has the wrong nominal type")
    scope = session._authority_scope
    if (
        type(scope) is not _EgressGrantIssuanceScope
        or getattr(scope, "_seal", None) is not _EGRESS_GRANT_ISSUANCE_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("egress issuance requires an active authority scope")
    if not callable(getattr(session._port, "issue", None)):
        raise TypeError("egress issuance port is incomplete")


def _construct_egress_grant_issuance_session(
    *,
    authority_scope: _EgressGrantIssuanceScope,
    port: EgressGrantIssuancePort,
) -> EgressGrantIssuanceSession:
    session = object.__new__(EgressGrantIssuanceSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    _require_active_egress_grant_issuance_session(session)
    return session


def issue_egress_grant(
    session: EgressGrantIssuanceSession,
    request: EgressGrantIssue,
    *,
    reference_factory: Callable[[str], str] | None = None,
) -> EgressGrant:
    """Create one opaque variant and retain only its digest in durable audit."""

    _require_active_egress_grant_issuance_session(session)
    if type(request) is not EgressGrantIssue:
        raise TypeError("egress issuance requires EgressGrantIssue")
    prefix = (
        MODEL_EGRESS_GRANT_PREFIX
        if request.hop_kind == "model"
        else CHANNEL_EGRESS_GRANT_PREFIX
    )
    factory = reference_factory or (
        lambda selected: f"{selected}_{secrets.token_hex(32)}"
    )
    value = factory(prefix)
    if request.hop_kind == "model":
        grant: EgressGrant = ModelEgressGrant(value)
    else:
        grant = ChannelEgressGrant(value)
    try:
        persisted = session._port.issue(request, grant.digest)
    except EgressGrantIssuanceUnavailable:
        raise
    except Exception as error:
        raise EgressGrantIssuanceUnavailable from error
    if persisted is not True:
        raise EgressGrantIssuanceUnavailable(
            "egress issuance authority did not persist the one-shot grant"
        )
    return grant


@dataclass(frozen=True, slots=True)
class EgressGrantRedemption:
    """Exact expected binding presented to the one-shot persistence authority."""

    grant_digest: bytes = field(repr=False)
    hop_kind: str
    organization_id: UUID = field(repr=False)
    package_digest: str
    payload_digest: str
    purpose: str
    audience_digest: str
    policy_epoch: int
    retention_policy_ref: str
    sensitivity_policy_ref: str
    issuer_ref: str
    consumer_ref: str
    provider_ref: str | None
    model_ref: str | None
    channel_ref: str | None
    destination_ref: str | None
    region_ref: str
    profile_ref: str

    def __post_init__(self) -> None:
        if (
            type(self.grant_digest) is not bytes
            or len(self.grant_digest) != hashlib.sha256().digest_size
        ):
            raise ValueError("egress grant digest must be SHA-256 bytes")
        if type(self.organization_id) is not UUID:
            raise TypeError("egress Organization must be UUID")
        _require_sha256("package_digest", self.package_digest)
        _require_sha256("payload_digest", self.payload_digest)
        _require_sha256("audience_digest", self.audience_digest)
        _require_positive_epoch(self.policy_epoch)
        for field_name in (
            "purpose",
            "retention_policy_ref",
            "sensitivity_policy_ref",
            "issuer_ref",
            "consumer_ref",
            "region_ref",
            "profile_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        if self.hop_kind == "model":
            if (
                self.provider_ref is None
                or self.model_ref is None
                or self.channel_ref is not None
                or self.destination_ref is not None
            ):
                raise ValueError("model redemption must contain only model hop fields")
            _require_nonblank("provider_ref", self.provider_ref)
            _require_nonblank("model_ref", self.model_ref)
        elif self.hop_kind == "channel":
            if (
                self.channel_ref is None
                or self.destination_ref is None
                or self.provider_ref is not None
                or self.model_ref is not None
            ):
                raise ValueError(
                    "channel redemption must contain only channel hop fields"
                )
            _require_nonblank("channel_ref", self.channel_ref)
            _require_nonblank("destination_ref", self.destination_ref)
        else:
            raise ValueError("egress hop_kind must be model or channel")

    @classmethod
    def for_model(
        cls,
        *,
        grant: ModelEgressGrant,
        organization_id: UUID,
        package_digest: str,
        payload_digest: str,
        purpose: str,
        audience_digest: str,
        policy_epoch: int,
        profile: ModelEgressProfile,
    ) -> EgressGrantRedemption:
        if type(grant) is not ModelEgressGrant:
            raise TypeError("model redemption requires ModelEgressGrant")
        if type(profile) is not ModelEgressProfile:
            raise TypeError("model redemption requires ModelEgressProfile")
        return cls(
            grant_digest=grant.digest,
            hop_kind="model",
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose=purpose,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            retention_policy_ref=profile.retention_policy_ref,
            sensitivity_policy_ref=profile.sensitivity_policy_ref,
            issuer_ref=profile.issuer_ref,
            consumer_ref=profile.consumer_ref,
            provider_ref=profile.provider_ref,
            model_ref=profile.model_ref,
            channel_ref=None,
            destination_ref=None,
            region_ref=profile.region_ref,
            profile_ref=profile.profile_ref,
        )

    @classmethod
    def for_channel(
        cls,
        *,
        grant: ChannelEgressGrant,
        organization_id: UUID,
        package_digest: str,
        payload_digest: str,
        purpose: str,
        audience_digest: str,
        policy_epoch: int,
        profile: ChannelEgressProfile,
    ) -> EgressGrantRedemption:
        if type(grant) is not ChannelEgressGrant:
            raise TypeError("channel redemption requires ChannelEgressGrant")
        if type(profile) is not ChannelEgressProfile:
            raise TypeError("channel redemption requires ChannelEgressProfile")
        return cls(
            grant_digest=grant.digest,
            hop_kind="channel",
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose=purpose,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            retention_policy_ref=profile.retention_policy_ref,
            sensitivity_policy_ref=profile.sensitivity_policy_ref,
            issuer_ref=profile.issuer_ref,
            consumer_ref=profile.consumer_ref,
            provider_ref=None,
            model_ref=None,
            channel_ref=profile.channel_ref,
            destination_ref=profile.destination_ref,
            region_ref=profile.region_ref,
            profile_ref=profile.profile_ref,
        )


class EgressGrantRedemptionAuthority(Protocol):
    """One-shot authority used immediately before any outbound bytes."""

    def redeem(self, redemption: EgressGrantRedemption) -> bool: ...


type EgressGrant = ModelEgressGrant | ChannelEgressGrant
type EgressProfile = (
    InternalOnlyEgressProfile | ModelEgressProfile | ChannelEgressProfile
)


__all__ = [
    "CHANNEL_EGRESS_GRANT_PREFIX",
    "ChannelEgressGrant",
    "ChannelEgressProfile",
    "EGRESS_GRANT_DIGEST_PROFILE",
    "EGRESS_GRANT_PROFILE_LINEAGE",
    "EgressAuditCategory",
    "EgressGrant",
    "EgressGrantAuthorityUnavailable",
    "EgressGrantIssue",
    "EgressGrantIssuancePort",
    "EgressGrantIssuanceSession",
    "EgressGrantIssuanceUnavailable",
    "EgressGrantNotAvailable",
    "EgressGrantRedemption",
    "EgressGrantRedemptionAuthority",
    "EgressProfile",
    "INTERNAL_ONLY_EGRESS_PROFILE",
    "InternalOnlyEgressProfile",
    "MODEL_EGRESS_GRANT_PREFIX",
    "ModelEgressGrant",
    "ModelEgressProfile",
    "_close_egress_grant_issuance_scope",
    "_construct_egress_grant_issuance_session",
    "_open_egress_grant_issuance_scope",
    "_require_active_egress_grant_issuance_session",
    "direct_egress_audience_digest",
    "issue_egress_grant",
]
