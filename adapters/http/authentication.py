"""Trusted HTTP authentication adapter contracts and fail-closed default."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from engine.runtime.actor import MAX_MEMBERSHIP_VERSION


class AuthenticationRejected(Exception):
    """Opaque credential did not establish verified authentication context."""


class InvalidAuthenticationContext(ValueError):
    """Verified claim material cannot form the nominal trusted context."""


@dataclass(frozen=True, slots=True)
class VerifiedPrivateDeliveryBinding:
    """Trusted route/session facts for one private delivery destination."""

    destination_ref: str = field(repr=False)
    consumer_ref: str = field(repr=False)
    delivery_kind: str = field(default="private", repr=False)

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value or value.isspace()
            for value in (self.destination_ref, self.consumer_ref)
        ):
            raise InvalidAuthenticationContext(
                "verified private delivery refs must be non-empty"
            )
        if self.delivery_kind != "private":
            raise InvalidAuthenticationContext(
                "verified private delivery kind is not active"
            )


@dataclass(frozen=True, slots=True)
class VerifiedAuthenticationContext:
    """Identity facts emitted by a verified transport/session authenticator."""

    organization_ref: str = field(repr=False)
    user_ref: str = field(repr=False)
    principal_ref: str = field(repr=False)
    membership_ref: str = field(repr=False)
    membership_version: int = field(repr=False)
    agent_version_ref: str = field(repr=False)
    authenticated_application_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    private_delivery_binding: VerifiedPrivateDeliveryBinding | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        required_refs = (
            self.organization_ref,
            self.user_ref,
            self.principal_ref,
            self.membership_ref,
            self.agent_version_ref,
            self.authenticated_application_ref,
            self.authentication_binding_ref,
        )
        if any(
            type(value) is not str or not value or value.isspace()
            for value in required_refs
        ):
            raise InvalidAuthenticationContext(
                "verified authentication refs must be non-empty"
            )
        for field_name in ("organization_ref", "user_ref", "membership_ref"):
            value = getattr(self, field_name)
            try:
                internal_id = UUID(value)
            except ValueError:
                label = field_name.removesuffix("_ref").replace("_", " ")
                raise InvalidAuthenticationContext(
                    f"verified {label} ref must be an internal UUID"
                ) from None
            object.__setattr__(self, field_name, str(internal_id))
        if (
            type(self.membership_version) is not int
            or not 1 <= self.membership_version <= MAX_MEMBERSHIP_VERSION
        ):
            raise InvalidAuthenticationContext(
                "verified Membership version must fit a positive signed 64-bit integer"
            )
        if (
            self.private_delivery_binding is not None
            and type(self.private_delivery_binding)
            is not VerifiedPrivateDeliveryBinding
        ):
            raise InvalidAuthenticationContext(
                "verified private delivery binding has the wrong nominal type"
            )


class Authenticator(Protocol):
    """Port from one opaque credential to already verified trusted facts."""

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedAuthenticationContext: ...


class RejectingAuthenticator:
    """Production-safe default until an owning identity-provider issue lands."""

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedAuthenticationContext:
        raise AuthenticationRejected
