"""Grant-gated deterministic model and channel egress boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn, Protocol
from uuid import UUID

from engine.runtime.contracts import ContextPackage
from engine.runtime.egress import (
    ChannelEgressGrant,
    ChannelEgressProfile,
    EgressGrantNotAvailable,
    EgressGrantRedemption,
    EgressGrantRedemptionAuthority,
    ModelEgressGrant,
    ModelEgressProfile,
)
from engine.runtime.egress_payload import (
    canonical_package_payload,
    channel_payload_bytes_digest,
    channel_payload_digest,
    model_input_digest,
    model_payload_bytes_digest,
)


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedModelInput:
    """Nominal model payload derived only from one exact ContextPackage."""

    package_digest: str
    purpose: str
    payload_digest: str
    _payload: bytes = field(repr=False)
    _grant_digest: bytes = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("AuthorizedModelInput can only be constructed by BotDelivery")

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthorizedModelInput is not serializable")


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedChannelPayload:
    """Nominal exact Package payload for channel preflight, not write authority."""

    package_digest: str
    purpose: str
    payload_digest: str
    _payload: bytes = field(repr=False)
    _grant_digest: bytes = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "AuthorizedChannelPayload can only be constructed by BotDelivery"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthorizedChannelPayload is not serializable")


@dataclass(frozen=True, slots=True, init=False)
class _ModelGatewayIdentity:
    """Nominal identity minted only by the trusted BotDelivery composition."""

    consumer_ref: str
    provider_ref: str
    model_ref: str
    region_ref: str


@dataclass(frozen=True, slots=True, init=False)
class _SenderPreflightIdentity:
    """Nominal identity minted only by the trusted BotDelivery composition."""

    consumer_ref: str
    channel_ref: str
    destination_ref: str
    region_ref: str


def _model_gateway_identity(profile: ModelEgressProfile) -> _ModelGatewayIdentity:
    identity = object.__new__(_ModelGatewayIdentity)
    for field_name in ("consumer_ref", "provider_ref", "model_ref", "region_ref"):
        object.__setattr__(identity, field_name, getattr(profile, field_name))
    return identity


def _sender_preflight_identity(
    profile: ChannelEgressProfile,
) -> _SenderPreflightIdentity:
    identity = object.__new__(_SenderPreflightIdentity)
    for field_name in (
        "consumer_ref",
        "channel_ref",
        "destination_ref",
        "region_ref",
    ):
        object.__setattr__(identity, field_name, getattr(profile, field_name))
    return identity


def prepare_authorized_model_input(
    package: ContextPackage,
    grant: ModelEgressGrant,
) -> AuthorizedModelInput:
    """Derive the deterministic tracer input from one Package and model grant."""

    if type(package) is not ContextPackage:
        raise TypeError("model input requires ContextPackage")
    if type(grant) is not ModelEgressGrant:
        raise TypeError("model input requires ModelEgressGrant")
    payload = canonical_package_payload(package)
    authorized = object.__new__(AuthorizedModelInput)
    object.__setattr__(authorized, "package_digest", package.package_digest)
    object.__setattr__(authorized, "purpose", package.purpose)
    object.__setattr__(
        authorized,
        "payload_digest",
        model_input_digest(package),
    )
    object.__setattr__(authorized, "_payload", payload)
    object.__setattr__(authorized, "_grant_digest", grant.digest)
    return authorized


def prepare_authorized_channel_payload(
    package: ContextPackage,
    grant: ChannelEgressGrant,
) -> AuthorizedChannelPayload:
    """Derive the exact tracer channel payload; this does not authorize an effect."""

    if type(package) is not ContextPackage:
        raise TypeError("channel payload requires ContextPackage")
    if type(grant) is not ChannelEgressGrant:
        raise TypeError("channel payload requires ChannelEgressGrant")
    payload = canonical_package_payload(package)
    authorized = object.__new__(AuthorizedChannelPayload)
    object.__setattr__(authorized, "package_digest", package.package_digest)
    object.__setattr__(authorized, "purpose", package.purpose)
    object.__setattr__(
        authorized,
        "payload_digest",
        channel_payload_digest(package),
    )
    object.__setattr__(authorized, "_payload", payload)
    object.__setattr__(authorized, "_grant_digest", grant.digest)
    return authorized


class _ModelGatewayPort(Protocol):
    def _egress_identity(self) -> _ModelGatewayIdentity: ...

    def _transmit(self, authorized_input: AuthorizedModelInput) -> None: ...


class _SenderPreflightPort(Protocol):
    def _egress_identity(self) -> _SenderPreflightIdentity: ...

    def _preflight(self, payload: AuthorizedChannelPayload) -> None: ...


class DeterministicModelGatewaySpy:
    """Network-free byte counter at the exact model boundary seam."""

    def __init__(self, profile: ModelEgressProfile) -> None:
        if type(profile) is not ModelEgressProfile:
            raise TypeError("model gateway requires ModelEgressProfile")
        self.__identity = _model_gateway_identity(profile)
        self.request_count = 0
        self.outbound_bytes = 0

    def _egress_identity(self) -> _ModelGatewayIdentity:
        return self.__identity

    def _transmit(self, authorized_input: AuthorizedModelInput) -> None:
        if type(authorized_input) is not AuthorizedModelInput:
            raise TypeError("gateway requires AuthorizedModelInput")
        self.request_count += 1
        self.outbound_bytes += len(authorized_input._payload)


class DeterministicSenderPreflightSpy:
    """Network-free channel preflight counter with no effect method."""

    def __init__(self, profile: ChannelEgressProfile) -> None:
        if type(profile) is not ChannelEgressProfile:
            raise TypeError("sender preflight requires ChannelEgressProfile")
        self.__identity = _sender_preflight_identity(profile)
        self.preflight_count = 0
        self.outbound_bytes = 0
        self.effect_count = 0

    def _egress_identity(self) -> _SenderPreflightIdentity:
        return self.__identity

    def _preflight(self, payload: AuthorizedChannelPayload) -> None:
        if type(payload) is not AuthorizedChannelPayload:
            raise TypeError("sender preflight requires AuthorizedChannelPayload")
        self.preflight_count += 1
        self.outbound_bytes += len(payload._payload)


def _require_audience_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("egress audience_digest must be lowercase SHA-256")
    return value


def _require_boundary(
    *,
    organization_id: UUID,
    audience_digest: str,
    policy_epoch: int,
    authority: EgressGrantRedemptionAuthority,
    port: object,
    method: str,
) -> None:
    if type(organization_id) is not UUID:
        raise TypeError("egress boundary Organization must be UUID")
    _require_audience_digest(audience_digest)
    if type(policy_epoch) is not int or policy_epoch < 1:
        raise ValueError("egress boundary Policy Epoch must be positive")
    if not callable(getattr(authority, "redeem", None)):
        raise TypeError("egress redemption authority is incomplete")
    if not callable(getattr(port, method, None)):
        raise TypeError("egress boundary port is incomplete")
    if not callable(getattr(port, "_egress_identity", None)):
        raise TypeError("egress boundary port has no trusted identity")


def _require_model_gateway_identity(
    gateway: _ModelGatewayPort,
    profile: ModelEgressProfile,
) -> None:
    identity = gateway._egress_identity()
    if type(identity) is not _ModelGatewayIdentity or identity != (
        _model_gateway_identity(profile)
    ):
        raise EgressGrantNotAvailable


def _require_sender_preflight_identity(
    sender: _SenderPreflightPort,
    profile: ChannelEgressProfile,
) -> None:
    identity = sender._egress_identity()
    if type(identity) is not _SenderPreflightIdentity or identity != (
        _sender_preflight_identity(profile)
    ):
        raise EgressGrantNotAvailable


class ModelEgressBoundary:
    """Redeem exact model bindings before the first outbound byte."""

    def __init__(
        self,
        *,
        organization_id: UUID,
        audience_digest: str,
        policy_epoch: int,
        profile: ModelEgressProfile,
        authority: EgressGrantRedemptionAuthority,
        gateway: _ModelGatewayPort,
    ) -> None:
        if type(profile) is not ModelEgressProfile:
            raise TypeError("model boundary requires ModelEgressProfile")
        _require_boundary(
            organization_id=organization_id,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            authority=authority,
            port=gateway,
            method="_transmit",
        )
        _require_model_gateway_identity(gateway, profile)
        self._organization_id = organization_id
        self._audience_digest = audience_digest
        self._policy_epoch = policy_epoch
        self._profile = profile
        self._authority = authority
        self._gateway = gateway

    def transmit(
        self,
        authorized_input: AuthorizedModelInput,
        grant: ModelEgressGrant,
    ) -> None:
        if type(authorized_input) is not AuthorizedModelInput:
            raise TypeError("model boundary requires AuthorizedModelInput")
        if type(grant) is not ModelEgressGrant:
            raise TypeError("model boundary requires ModelEgressGrant")
        _require_model_gateway_identity(self._gateway, self._profile)
        if authorized_input._grant_digest != grant.digest:
            raise EgressGrantNotAvailable
        if (
            model_payload_bytes_digest(authorized_input._payload)
            != authorized_input.payload_digest
        ):
            raise EgressGrantNotAvailable
        redemption = EgressGrantRedemption.for_model(
            grant=grant,
            organization_id=self._organization_id,
            package_digest=authorized_input.package_digest,
            payload_digest=authorized_input.payload_digest,
            purpose=authorized_input.purpose,
            audience_digest=self._audience_digest,
            policy_epoch=self._policy_epoch,
            profile=self._profile,
        )
        try:
            accepted = self._authority.redeem(redemption)
        except EgressGrantNotAvailable:
            raise
        except Exception:
            raise EgressGrantNotAvailable from None
        if accepted is not True:
            raise EgressGrantNotAvailable
        self._gateway._transmit(authorized_input)


class ChannelEgressBoundary:
    """Redeem exact channel bindings before preflight; no effect is exposed."""

    def __init__(
        self,
        *,
        organization_id: UUID,
        audience_digest: str,
        policy_epoch: int,
        profile: ChannelEgressProfile,
        authority: EgressGrantRedemptionAuthority,
        sender: _SenderPreflightPort,
    ) -> None:
        if type(profile) is not ChannelEgressProfile:
            raise TypeError("channel boundary requires ChannelEgressProfile")
        _require_boundary(
            organization_id=organization_id,
            audience_digest=audience_digest,
            policy_epoch=policy_epoch,
            authority=authority,
            port=sender,
            method="_preflight",
        )
        _require_sender_preflight_identity(sender, profile)
        self._organization_id = organization_id
        self._audience_digest = audience_digest
        self._policy_epoch = policy_epoch
        self._profile = profile
        self._authority = authority
        self._sender = sender

    def preflight(
        self,
        payload: AuthorizedChannelPayload,
        grant: ChannelEgressGrant,
    ) -> None:
        if type(payload) is not AuthorizedChannelPayload:
            raise TypeError("channel boundary requires AuthorizedChannelPayload")
        if type(grant) is not ChannelEgressGrant:
            raise TypeError("channel boundary requires ChannelEgressGrant")
        _require_sender_preflight_identity(self._sender, self._profile)
        if payload._grant_digest != grant.digest:
            raise EgressGrantNotAvailable
        if channel_payload_bytes_digest(payload._payload) != payload.payload_digest:
            raise EgressGrantNotAvailable
        redemption = EgressGrantRedemption.for_channel(
            grant=grant,
            organization_id=self._organization_id,
            package_digest=payload.package_digest,
            payload_digest=payload.payload_digest,
            purpose=payload.purpose,
            audience_digest=self._audience_digest,
            policy_epoch=self._policy_epoch,
            profile=self._profile,
        )
        try:
            accepted = self._authority.redeem(redemption)
        except EgressGrantNotAvailable:
            raise
        except Exception:
            raise EgressGrantNotAvailable from None
        if accepted is not True:
            raise EgressGrantNotAvailable
        self._sender._preflight(payload)


__all__ = [
    "AuthorizedChannelPayload",
    "AuthorizedModelInput",
    "ChannelEgressBoundary",
    "DeterministicModelGatewaySpy",
    "DeterministicSenderPreflightSpy",
    "ModelEgressBoundary",
    "prepare_authorized_channel_payload",
    "prepare_authorized_model_input",
]
