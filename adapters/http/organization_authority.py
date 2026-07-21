"""Trusted Organization-existence authority used before Runtime entry."""

from datetime import datetime
from typing import Protocol

from adapters.http.authentication import VerifiedAuthenticationContext
from engine.runtime.organization import ExistingOrganizationVerification


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
