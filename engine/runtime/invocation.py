"""Nominal trusted invocation constructed only by authenticated ingress."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InvocationConstructionProvenance(StrEnum):
    """Closed provenance for trusted invocation construction."""

    AUTHENTICATED_HTTP_INGRESS = "authenticated_http_ingress"


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedInvocation:
    """Trusted identity facts unavailable to request-body deserialization."""

    request_id: str
    organization_ref: str
    principal_ref: str
    membership_ref: str | None
    agent_version_ref: str
    authenticated_application_ref: str
    authentication_binding_ref: str
    received_at: datetime
    construction_provenance: InvocationConstructionProvenance

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "AuthenticatedInvocation can only be constructed by trusted ingress"
        )


def _construct_authenticated_http_invocation(
    *,
    request_id: str,
    organization_ref: str,
    principal_ref: str,
    membership_ref: str | None,
    agent_version_ref: str,
    authenticated_application_ref: str,
    authentication_binding_ref: str,
    received_at: datetime,
) -> AuthenticatedInvocation:
    """Build the nominal value at the authenticated HTTP adapter boundary."""

    required_refs = {
        "request_id": request_id,
        "organization_ref": organization_ref,
        "principal_ref": principal_ref,
        "agent_version_ref": agent_version_ref,
        "authenticated_application_ref": authenticated_application_ref,
        "authentication_binding_ref": authentication_binding_ref,
    }
    for field_name, value in required_refs.items():
        if not value or value.isspace():
            raise ValueError(f"trusted invocation {field_name} must be non-empty")
    if membership_ref is not None and (not membership_ref or membership_ref.isspace()):
        raise ValueError("trusted invocation membership_ref must be non-empty")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("trusted invocation received_at must be timezone-aware")

    invocation = object.__new__(AuthenticatedInvocation)
    object.__setattr__(invocation, "request_id", request_id)
    object.__setattr__(invocation, "organization_ref", organization_ref)
    object.__setattr__(invocation, "principal_ref", principal_ref)
    object.__setattr__(invocation, "membership_ref", membership_ref)
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
        "construction_provenance",
        InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS,
    )
    return invocation
