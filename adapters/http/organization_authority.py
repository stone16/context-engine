"""Trusted Organization-existence authority used before Runtime entry."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from adapters.http.authentication import VerifiedAuthenticationContext
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)


class OrganizationVerificationRejected(Exception):
    """The trusted authority could not establish an existing Organization."""


class OrganizationAuthority(Protocol):
    """Port to the authority that verifies the authenticated Organization."""

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification: ...


class RejectingOrganizationAuthority:
    """Production-safe default until the owning authority lands with #11."""

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        raise OrganizationVerificationRejected


class DogfoodOrganizationAuthority:
    """Bind Organization proof to the sole locally configured identity."""

    __slots__ = ("_organization_id",)

    def __init__(self, organization_id: UUID) -> None:
        if type(organization_id) is not UUID:
            raise TypeError("dogfood Organization must be UUID")
        self._organization_id = organization_id

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        if (
            type(authentication) is not VerifiedAuthenticationContext
            or authentication.organization_ref != str(self._organization_id)
        ):
            raise OrganizationVerificationRejected
        return _construct_existing_http_organization_verification(
            organization_id=self._organization_id,
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )
