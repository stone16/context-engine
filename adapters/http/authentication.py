"""Trusted HTTP authentication adapter contracts and fail-closed default."""

from dataclasses import dataclass
from typing import Protocol


class AuthenticationRejected(Exception):
    """Opaque credential did not establish verified authentication context."""


class InvalidAuthenticationContext(ValueError):
    """Verified claim material cannot form the nominal trusted context."""


@dataclass(frozen=True, slots=True)
class VerifiedAuthenticationContext:
    """Identity facts emitted by a verified transport/session authenticator."""

    organization_ref: str
    principal_ref: str
    membership_ref: str | None
    agent_version_ref: str
    authenticated_application_ref: str
    authentication_binding_ref: str

    def __post_init__(self) -> None:
        required_refs = (
            self.organization_ref,
            self.principal_ref,
            self.agent_version_ref,
            self.authenticated_application_ref,
            self.authentication_binding_ref,
        )
        if any(not value or value.isspace() for value in required_refs):
            raise InvalidAuthenticationContext(
                "verified authentication refs must be non-empty"
            )
        if self.membership_ref is not None and (
            not self.membership_ref or self.membership_ref.isspace()
        ):
            raise InvalidAuthenticationContext(
                "verified membership ref must be non-empty"
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
