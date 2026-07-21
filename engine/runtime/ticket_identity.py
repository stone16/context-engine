"""Nominal trusted identity used only to issue and redeem bounded M0 tickets."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import NoReturn
from uuid import UUID

from engine.runtime._ticket_signing import (
    _require_identifier,
    _require_positive_bigint,
    _require_uuid,
)
from engine.runtime.actor import UserActor, _require_active_user_actor
from engine.runtime.delivery import TrustedDeliveryContext
from engine.runtime.invocation import AuthenticatedInvocation
from engine.runtime.policy_epoch import PolicyEpochVerification
from engine.runtime.trusted_inputs import _validate_trusted_invocation_and_delivery


class TicketIdentityConstructionProvenance(StrEnum):
    """Closed provenance for the bounded ticket identity carrier."""

    TRUSTED_INVOCATION_AND_DELIVERY = "trusted_invocation_and_delivery"


_TICKET_IDENTITY_CONSTRUCTION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class TicketExecutionIdentity:
    """Trusted UserActor identity chain; never reconstructed from ticket claims."""

    organization_id: UUID = field(repr=False)
    subject_user_id: UUID = field(repr=False)
    subject_membership_id: UUID = field(repr=False)
    subject_membership_version: int = field(repr=False)
    actor_principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    policy_epoch_verification: PolicyEpochVerification = field(repr=False)
    user_actor: UserActor = field(repr=False)
    authenticated_invocation: AuthenticatedInvocation = field(repr=False)
    trusted_delivery_context: TrustedDeliveryContext = field(repr=False)
    construction_provenance: TicketIdentityConstructionProvenance
    _construction_seal: object = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "TicketExecutionIdentity can only be constructed from trusted "
            "invocation and delivery authorities"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("TicketExecutionIdentity is not serializable")


def _construct_ticket_execution_identity(
    *,
    invocation: AuthenticatedInvocation,
    delivery_context: TrustedDeliveryContext,
) -> TicketExecutionIdentity:
    _validate_trusted_invocation_and_delivery(invocation, delivery_context)
    user_actor = invocation.user_actor
    _require_active_user_actor(user_actor)
    _require_identifier(
        "ticket Agent version", invocation.agent_version_ref, maximum_length=256
    )
    _require_identifier(
        "ticket purpose", delivery_context.purpose, maximum_length=256
    )
    identity = object.__new__(TicketExecutionIdentity)
    object.__setattr__(identity, "organization_id", user_actor.organization_id)
    object.__setattr__(identity, "subject_user_id", user_actor.user_id)
    object.__setattr__(identity, "subject_membership_id", user_actor.membership_id)
    object.__setattr__(
        identity,
        "subject_membership_version",
        user_actor.membership_version,
    )
    object.__setattr__(identity, "actor_principal_ref", user_actor.principal_ref)
    object.__setattr__(identity, "agent_version_ref", invocation.agent_version_ref)
    object.__setattr__(identity, "purpose", delivery_context.purpose)
    object.__setattr__(identity, "policy_epoch", user_actor.policy_epoch)
    object.__setattr__(
        identity,
        "policy_epoch_verification",
        user_actor.policy_epoch_verification,
    )
    object.__setattr__(identity, "user_actor", user_actor)
    object.__setattr__(identity, "authenticated_invocation", invocation)
    object.__setattr__(identity, "trusted_delivery_context", delivery_context)
    object.__setattr__(
        identity,
        "construction_provenance",
        TicketIdentityConstructionProvenance.TRUSTED_INVOCATION_AND_DELIVERY,
    )
    object.__setattr__(
        identity,
        "_construction_seal",
        _TICKET_IDENTITY_CONSTRUCTION_SEAL,
    )
    _require_active_ticket_execution_identity(identity)
    return identity


def _require_active_ticket_execution_identity(
    identity: TicketExecutionIdentity,
) -> None:
    if type(identity) is not TicketExecutionIdentity:
        raise TypeError("ticket execution identity has the wrong nominal type")
    if (
        getattr(identity, "construction_provenance", None)
        is not TicketIdentityConstructionProvenance.TRUSTED_INVOCATION_AND_DELIVERY
        or getattr(identity, "_construction_seal", None)
        is not _TICKET_IDENTITY_CONSTRUCTION_SEAL
    ):
        raise ValueError("ticket execution identity has invalid provenance")
    invocation = identity.authenticated_invocation
    delivery_context = identity.trusted_delivery_context
    _validate_trusted_invocation_and_delivery(invocation, delivery_context)
    actor = identity.user_actor
    _require_active_user_actor(actor)
    _require_uuid("ticket Organization", identity.organization_id)
    _require_uuid("ticket subject User", identity.subject_user_id)
    _require_uuid("ticket subject Membership", identity.subject_membership_id)
    _require_positive_bigint(
        "ticket subject Membership version",
        identity.subject_membership_version,
    )
    _require_identifier(
        "ticket actor Principal",
        identity.actor_principal_ref,
        maximum_length=256,
    )
    _require_identifier(
        "ticket Agent version",
        identity.agent_version_ref,
        maximum_length=256,
    )
    _require_identifier("ticket purpose", identity.purpose, maximum_length=256)
    _require_positive_bigint("ticket Policy Epoch", identity.policy_epoch)
    if (
        identity.organization_id != actor.organization_id
        or identity.subject_user_id != actor.user_id
        or identity.subject_membership_id != actor.membership_id
        or identity.subject_membership_version != actor.membership_version
        or identity.actor_principal_ref != actor.principal_ref
        or actor is not invocation.user_actor
        or identity.agent_version_ref != invocation.agent_version_ref
        or identity.purpose != delivery_context.purpose
        or identity.policy_epoch != actor.policy_epoch
        or identity.policy_epoch_verification is not actor.policy_epoch_verification
    ):
        raise ValueError("ticket execution identity does not match its UserActor")
