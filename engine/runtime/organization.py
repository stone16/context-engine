"""Nominal proof that trusted ingress established an existing Organization."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class OrganizationVerificationProvenance(StrEnum):
    """Closed provenance for Organization-existence verification."""

    AUTHENTICATED_HTTP_AUTHORITY = "authenticated_http_authority"


@dataclass(frozen=True, slots=True, init=False)
class ExistingOrganizationVerification:
    """Trusted, request-bound proof unavailable to ordinary request bodies."""

    organization_id: UUID
    request_id: str
    authentication_binding_ref: str
    verified_at: datetime
    construction_provenance: OrganizationVerificationProvenance

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "ExistingOrganizationVerification can only be constructed by "
            "trusted Organization authority"
        )


def _construct_existing_http_organization_verification(
    *,
    organization_id: UUID,
    request_id: str,
    authentication_binding_ref: str,
    verified_at: datetime,
) -> ExistingOrganizationVerification:
    """Construct the nominal proof after an authority verifies existence."""

    if type(organization_id) is not UUID:
        raise TypeError("verified organization_id must be UUID")
    for field_name, value in (
        ("request_id", request_id),
        ("authentication_binding_ref", authentication_binding_ref),
    ):
        if type(value) is not str or not value or value.isspace():
            raise ValueError(f"verified Organization {field_name} must be non-empty")
    if (
        type(verified_at) is not datetime
        or verified_at.tzinfo is None
        or verified_at.utcoffset() is None
    ):
        raise ValueError("verified Organization time must be timezone-aware")

    verification = object.__new__(ExistingOrganizationVerification)
    object.__setattr__(verification, "organization_id", organization_id)
    object.__setattr__(verification, "request_id", request_id)
    object.__setattr__(
        verification,
        "authentication_binding_ref",
        authentication_binding_ref,
    )
    object.__setattr__(verification, "verified_at", verified_at)
    object.__setattr__(
        verification,
        "construction_provenance",
        OrganizationVerificationProvenance.AUTHENTICATED_HTTP_AUTHORITY,
    )
    return verification
