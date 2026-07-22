from __future__ import annotations

from contextlib import ExitStack
from uuid import UUID

import pytest
from sqlalchemy import Engine

from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    MembershipIdentity,
    PostgreSQLAccessPolicyControl,
    PostgreSQLMembershipAuthority,
    ResourceAccessRevocation,
    create_database_engine,
)
from engine.runtime import TicketSigningKeyring
from engine.runtime.action_ticket import (
    ActionTicketIssuer,
    ActionTicketNoopHandler,
)
from engine.runtime.context_access_ticket import (
    ContextAccessTicketIssuer,
    ContextAccessTicketReadHandler,
)
from engine.runtime.delivery import _construct_direct_delivery_context
from engine.runtime.invocation import _construct_authenticated_http_invocation
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.ticket_identity import _construct_ticket_execution_identity
from engine.runtime.ticket_rejection import TicketNotAvailable
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)

pytestmark = pytest.mark.integration

KEYRING = TicketSigningKeyring(active_version=1, keys={1: b"t" * 32})
PROVIDER_REF = "provider:ticket-policy-epoch"
CHANNEL_REF = "channel:ticket-policy-epoch"


class _ReadEffectCounter:
    def __init__(self, *, organization_id: UUID) -> None:
        self._organization_id = organization_id
        self.effects = 0

    def read(self, *, organization_id: UUID, provider_ref: str) -> None:
        assert organization_id == self._organization_id
        assert provider_ref == PROVIDER_REF
        self.effects += 1


class _ActionEffectCounter:
    def __init__(self, *, organization_id: UUID) -> None:
        self._organization_id = organization_id
        self.effects = 0

    def perform_noop(self, *, organization_id: UUID, channel_ref: str) -> None:
        assert organization_id == self._organization_id
        assert channel_ref == CHANNEL_REF
        self.effects += 1


@pytest.mark.security_evidence(id="PG-ACTION-SEPARATION-014", layer="postgres")
def test_committed_epoch_bump_rejects_both_previously_valid_ticket_types(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    """Issue #18: one committed epoch bump invalidates both ticket planes."""

    fixture = _new_fixture()
    active = fixture.org_a
    principal_ref = f"principal:authorized-evidence:{active.label}"
    migration_engine = create_database_engine(migration_configuration)
    control_engine = create_database_engine(control_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        membership_identity = MembershipIdentity(
            organization_id=active.organization_id,
            user_id=active.user_id,
            membership_id=active.membership_id,
            membership_version=1,
            principal_ref=principal_ref,
            request_id="request:ticket-policy-epoch",
            authentication_binding_ref="binding:ticket-policy-epoch",
            checked_at=RECEIVED_AT,
        )

        with PostgreSQLMembershipAuthority(
            guarded_runtime_engine
        ).current_user_actor(membership_identity) as membership_verification:
            agent_version_ref = "agent:ticket-policy-epoch"
            purpose = "context.answer"
            scope_identity = ScopeAuthorityIdentity(
                organization_id=active.organization_id,
                user_id=active.user_id,
                membership_id=active.membership_id,
                membership_version=1,
                policy_epoch=membership_verification.policy_epoch,
                principal_ref=principal_ref,
                agent_version_ref="agent:ticket-policy-epoch",
                purpose=purpose,
                request_id=membership_identity.request_id,
                authentication_binding_ref=(
                    membership_identity.authentication_binding_ref
                ),
                checked_at=RECEIVED_AT,
            )
            organization_verification = (
                _construct_existing_http_organization_verification(
                    organization_id=active.organization_id,
                    request_id=membership_identity.request_id,
                    authentication_binding_ref=(
                        membership_identity.authentication_binding_ref
                    ),
                    verified_at=RECEIVED_AT,
                )
            )
            with ExitStack() as trusted_scope:
                scope_snapshot = trusted_scope.enter_context(
                    MissingTrustedScopeAuthority().current_scope(scope_identity)
                )
                invocation = _construct_authenticated_http_invocation(
                    request_id=membership_identity.request_id,
                    authenticated_organization_ref=str(active.organization_id),
                    organization_verification=organization_verification,
                    user_ref=str(active.user_id),
                    principal_ref=principal_ref,
                    membership_ref=str(active.membership_id),
                    membership_version=1,
                    current_membership_verification=membership_verification,
                    agent_version_ref=agent_version_ref,
                    authenticated_application_ref="application:ticket-policy-epoch",
                    authentication_binding_ref=(
                        membership_identity.authentication_binding_ref
                    ),
                    trusted_purpose=purpose,
                    received_at=RECEIVED_AT,
                    trusted_scope_snapshot=scope_snapshot,
                )
                delivery_context = _construct_direct_delivery_context(
                    purpose=purpose,
                    authenticated_application_ref="application:ticket-policy-epoch",
                    delivery_binding_ref=(
                        membership_identity.authentication_binding_ref
                    ),
                    established_at=RECEIVED_AT,
                )
                identity = _construct_ticket_execution_identity(
                    invocation=invocation,
                    delivery_context=delivery_context,
                )
                assert identity.policy_epoch == 1

                read_issuer = ContextAccessTicketIssuer(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    provider_ref=PROVIDER_REF,
                    clock=lambda: RECEIVED_AT,
                )
                action_issuer = ActionTicketIssuer(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    channel_ref=CHANNEL_REF,
                    clock=lambda: RECEIVED_AT,
                )

                fresh_read_effect = _ReadEffectCounter(
                    organization_id=active.organization_id,
                )
                fresh_action_effect = _ActionEffectCounter(
                    organization_id=active.organization_id
                )
                ContextAccessTicketReadHandler(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    provider_ref=PROVIDER_REF,
                    provider=fresh_read_effect,
                    clock=lambda: RECEIVED_AT,
                ).read(ticket=read_issuer.issue(identity), identity=identity)
                ActionTicketNoopHandler(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    channel_ref=CHANNEL_REF,
                    channel=fresh_action_effect,
                    clock=lambda: RECEIVED_AT,
                ).perform(ticket=action_issuer.issue(identity), identity=identity)
                assert fresh_read_effect.effects == 1
                assert fresh_action_effect.effects == 1

                stale_read_ticket = read_issuer.issue(identity)
                stale_action_ticket = action_issuer.issue(identity)

                next_epoch = PostgreSQLAccessPolicyControl(
                    control_engine
                ).change_access(
                    ResourceAccessRevocation(
                        organization_id=active.organization_id,
                        resource_ref=active.authorized.resource_ref,
                        principal_ref=principal_ref,
                        expected_access_version=1,
                    )
                )
                assert next_epoch.organization_id == active.organization_id
                assert next_epoch.value == 2

                stale_read_effect = _ReadEffectCounter(
                    organization_id=active.organization_id
                )
                stale_action_effect = _ActionEffectCounter(
                    organization_id=active.organization_id
                )
                read_handler = ContextAccessTicketReadHandler(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    provider_ref=PROVIDER_REF,
                    provider=stale_read_effect,
                    clock=lambda: RECEIVED_AT,
                )
                action_handler = ActionTicketNoopHandler(
                    keyring=KEYRING,
                    organization_id=active.organization_id,
                    channel_ref=CHANNEL_REF,
                    channel=stale_action_effect,
                    clock=lambda: RECEIVED_AT,
                )

                with pytest.raises(TicketNotAvailable):
                    read_handler.read(ticket=stale_read_ticket, identity=identity)
                with pytest.raises(TicketNotAvailable):
                    action_handler.perform(
                        ticket=stale_action_ticket,
                        identity=identity,
                    )

                assert stale_read_effect.effects == 0
                assert stale_action_effect.effects == 0
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            control_engine.dispose()
            migration_engine.dispose()
