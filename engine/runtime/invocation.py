"""Nominal trusted invocation constructed only by authenticated ingress."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from engine.runtime.actor import (
    MAX_MEMBERSHIP_VERSION,
    CurrentMembershipVerification,
    UserActor,
    _construct_user_actor,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    OrganizationVerificationProvenance,
)


class InvocationConstructionProvenance(StrEnum):
    """Closed provenance for trusted invocation construction."""

    AUTHENTICATED_HTTP_INGRESS = "authenticated_http_ingress"


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedInvocation:
    """Trusted identity facts unavailable to request-body deserialization."""

    request_id: str
    organization_ref: str
    user_ref: str
    principal_ref: str
    membership_ref: str
    membership_version: int
    agent_version_ref: str
    authenticated_application_ref: str
    authentication_binding_ref: str
    received_at: datetime
    organization_verification: ExistingOrganizationVerification
    user_actor: UserActor = field(repr=False)
    construction_provenance: InvocationConstructionProvenance

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "AuthenticatedInvocation can only be constructed by trusted ingress"
        )


def _construct_authenticated_http_invocation(
    *,
    request_id: str,
    authenticated_organization_ref: str,
    organization_verification: ExistingOrganizationVerification,
    user_ref: str,
    principal_ref: str,
    membership_ref: str,
    membership_version: int,
    current_membership_verification: CurrentMembershipVerification,
    agent_version_ref: str,
    authenticated_application_ref: str,
    authentication_binding_ref: str,
    received_at: datetime,
) -> AuthenticatedInvocation:
    """Build the nominal value at the authenticated HTTP adapter boundary."""

    required_refs = {
        "request_id": request_id,
        "authenticated_organization_ref": authenticated_organization_ref,
        "user_ref": user_ref,
        "principal_ref": principal_ref,
        "membership_ref": membership_ref,
        "agent_version_ref": agent_version_ref,
        "authenticated_application_ref": authenticated_application_ref,
        "authentication_binding_ref": authentication_binding_ref,
    }
    for field_name, value in required_refs.items():
        if type(value) is not str or not value or value.isspace():
            raise ValueError(f"trusted invocation {field_name} must be non-empty")
    for field_name, value in (
        ("authenticated_organization_ref", authenticated_organization_ref),
        ("user_ref", user_ref),
        ("membership_ref", membership_ref),
    ):
        try:
            canonical_ref = str(UUID(value))
        except ValueError:
            raise ValueError(
                f"trusted invocation {field_name} must be an internal UUID"
            ) from None
        if value != canonical_ref:
            raise ValueError(f"trusted invocation {field_name} must be canonical")
    if (
        type(membership_version) is not int
        or not 1 <= membership_version <= MAX_MEMBERSHIP_VERSION
    ):
        raise ValueError(
            "trusted invocation membership_version must fit a positive signed "
            "64-bit integer"
        )
    if (
        type(received_at) is not datetime
        or received_at.tzinfo is None
        or received_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("trusted invocation received_at must be timezone-aware UTC")
    if type(organization_verification) is not ExistingOrganizationVerification:
        raise TypeError(
            "trusted invocation requires ExistingOrganizationVerification"
        )
    if (
        organization_verification.construction_provenance
        is not OrganizationVerificationProvenance.AUTHENTICATED_HTTP_AUTHORITY
        or str(organization_verification.organization_id)
        != authenticated_organization_ref
        or organization_verification.request_id != request_id
        or organization_verification.authentication_binding_ref
        != authentication_binding_ref
        or organization_verification.verified_at != received_at
    ):
        raise ValueError(
            "trusted invocation Organization verification must match authentication"
        )
    user_actor = _construct_user_actor(current_membership_verification)
    if (
        str(user_actor.organization_id) != authenticated_organization_ref
        or str(user_actor.user_id) != user_ref
        or str(user_actor.membership_id) != membership_ref
        or user_actor.membership_version != membership_version
        or user_actor.principal_ref != principal_ref
        or user_actor.request_id != request_id
        or user_actor.authentication_binding_ref != authentication_binding_ref
        or user_actor.checked_at != received_at
    ):
        raise ValueError(
            "trusted invocation current Membership must match authentication"
        )

    invocation = object.__new__(AuthenticatedInvocation)
    object.__setattr__(invocation, "request_id", request_id)
    object.__setattr__(
        invocation,
        "organization_ref",
        authenticated_organization_ref,
    )
    object.__setattr__(invocation, "user_ref", user_ref)
    object.__setattr__(invocation, "principal_ref", principal_ref)
    object.__setattr__(invocation, "membership_ref", membership_ref)
    object.__setattr__(invocation, "membership_version", membership_version)
    object.__setattr__(invocation, "agent_version_ref", agent_version_ref)
    object.__setattr__(
        invocation,
        "authenticated_application_ref",
        authenticated_application_ref,
    )
    object.__setattr__(
        invocation,
        "authentication_binding_ref",
        authentication_binding_ref,
    )
    object.__setattr__(invocation, "received_at", received_at)
    object.__setattr__(
        invocation,
        "organization_verification",
        organization_verification,
    )
    object.__setattr__(invocation, "user_actor", user_actor)
    object.__setattr__(
        invocation,
        "construction_provenance",
        InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS,
    )
    return invocation
