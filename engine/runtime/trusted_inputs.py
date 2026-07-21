"""Shared validation for one authenticated invocation and delivery authority pair."""

from engine.runtime.actor import _require_active_user_actor
from engine.runtime.delivery import (
    DirectDeliveryConstructionProvenance,
    TrustedDeliveryContext,
)
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    InvocationConstructionProvenance,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    OrganizationVerificationProvenance,
)
from engine.runtime.scope_authority import _require_active_trusted_scope_snapshot


def _validate_trusted_invocation_and_delivery(
    invocation: AuthenticatedInvocation,
    delivery_context: TrustedDeliveryContext,
) -> None:
    if (
        type(invocation) is not AuthenticatedInvocation
        or invocation.construction_provenance
        is not InvocationConstructionProvenance.AUTHENTICATED_HTTP_INGRESS
    ):
        raise TypeError("Runtime requires a trusted AuthenticatedInvocation")
    verification = invocation.organization_verification
    if (
        type(verification) is not ExistingOrganizationVerification
        or verification.construction_provenance
        is not OrganizationVerificationProvenance.AUTHENTICATED_HTTP_AUTHORITY
        or str(verification.organization_id) != invocation.organization_ref
        or verification.request_id != invocation.request_id
        or verification.authentication_binding_ref
        != invocation.authentication_binding_ref
        or verification.verified_at != invocation.received_at
    ):
        raise ValueError(
            "Runtime requires a matching existing-Organization verification"
        )
    actor = invocation.user_actor
    _require_active_user_actor(actor)
    if (
        str(actor.organization_id) != invocation.organization_ref
        or str(actor.user_id) != invocation.user_ref
        or str(actor.membership_id) != invocation.membership_ref
        or actor.membership_version != invocation.membership_version
        or actor.principal_ref != invocation.principal_ref
        or actor.request_id != invocation.request_id
        or actor.authentication_binding_ref
        != invocation.authentication_binding_ref
        or actor.checked_at != invocation.received_at
        or actor.policy_epoch != invocation.policy_epoch
    ):
        raise ValueError("Runtime requires a matching current UserActor")
    if (
        type(delivery_context) is not TrustedDeliveryContext
        or delivery_context.construction_provenance
        is not DirectDeliveryConstructionProvenance.AUTHENTICATED_DIRECT_INGRESS
        or delivery_context.authenticated_application_ref
        != invocation.authenticated_application_ref
        or delivery_context.delivery_binding_ref
        != invocation.authentication_binding_ref
        or delivery_context.established_at != invocation.received_at
    ):
        raise ValueError("Runtime requires a matching trusted delivery context")
    scope_snapshot = invocation.trusted_scope_snapshot
    _require_active_trusted_scope_snapshot(scope_snapshot)
    if (
        scope_snapshot.organization_id != actor.organization_id
        or scope_snapshot.user_id != actor.user_id
        or scope_snapshot.membership_id != actor.membership_id
        or scope_snapshot.membership_version != actor.membership_version
        or scope_snapshot.principal_ref != invocation.principal_ref
        or scope_snapshot.agent_version_ref != invocation.agent_version_ref
        or scope_snapshot.purpose != delivery_context.purpose
        or scope_snapshot.request_id != invocation.request_id
        or scope_snapshot.authentication_binding_ref
        != invocation.authentication_binding_ref
        or scope_snapshot.checked_at != invocation.received_at
    ):
        raise ValueError("Runtime requires a matching trusted scope snapshot")
