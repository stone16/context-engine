import base64
import hmac
import json
import pickle
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from inspect import signature
from typing import cast, get_type_hints
from uuid import UUID

import pytest

import engine.runtime as runtime
from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthorityIdentity,
)
from engine.runtime.action_ticket import (
    ActionTicket,
    ActionTicketIssuer,
    ActionTicketNoopHandler,
    SyntheticNoopChannel,
)
from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.context_access_ticket import (
    ContextAccessTicket,
    ContextAccessTicketIssuer,
    ContextAccessTicketReadHandler,
    SyntheticReadProvider,
)
from engine.runtime.delivery import (
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    _construct_authenticated_http_invocation,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    PolicyEpochPortFailure,
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.ticket_identity import (
    TicketExecutionIdentity,
    _construct_ticket_execution_identity,
)
from engine.runtime.ticket_rejection import (
    TicketNotAvailable,
    TicketRejectionAuditReceipt,
    TicketRejectionCategory,
)
from tests.support.security_gate import record_security_oracles

NOW = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")
KEYRING = runtime.TicketSigningKeyring(active_version=7, keys={7: b"k" * 32})
SIGNING_KEY = b"k" * 32
NONCE = bytes(range(32))


def _decode_ticket(value: str) -> tuple[dict[str, object], dict[str, object]]:
    encoded_header, encoded_claims, _signature = value.split(".")
    header = json.loads(base64.urlsafe_b64decode(encoded_header + "=="))
    claims = json.loads(base64.urlsafe_b64decode(encoded_claims + "=="))
    assert isinstance(header, dict)
    assert isinstance(claims, dict)
    return header, claims


def _flip_one_ascii_bit(value: str) -> str:
    for index, character in enumerate(value):
        replacement = chr(ord(character) ^ 1)
        if replacement.isascii() and (
            replacement.isalnum() or replacement in "-_"
        ):
            return f"{value[:index]}{replacement}{value[index + 1:]}"
    raise AssertionError("fixture has no base64url character with a safe bit peer")


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _resign_ticket(
    ticket: ContextAccessTicket | ActionTicket,
    *,
    claim_name: str,
    claim_value: object,
) -> str:
    header, claims = _decode_ticket(ticket.serialize())
    claims[claim_name] = claim_value
    encoded_header = _b64url(_canonical_json(header))
    encoded_claims = _b64url(_canonical_json(claims))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature_value = hmac.digest(SIGNING_KEY, signing_input, "sha256")
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature_value)}"


def _sign_raw_documents(header: bytes, claims: bytes) -> str:
    encoded_header = _b64url(header)
    encoded_claims = _b64url(claims)
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature_value = hmac.digest(SIGNING_KEY, signing_input, "sha256")
    return f"{encoded_header}.{encoded_claims}.{_b64url(signature_value)}"


def _rewrite_to_unknown_key_version(
    ticket: ContextAccessTicket | ActionTicket,
) -> str:
    header, claims = _decode_ticket(ticket.serialize())
    header["kid"] = 8
    claims["signing_key_version"] = 8
    return _sign_raw_documents(_canonical_json(header), _canonical_json(claims))


class _EpochPort:
    def __init__(
        self,
        organization_id: UUID = ORGANIZATION_ID,
        current: object = 11,
    ) -> None:
        self.organization_id = organization_id
        self.current = current

    def read_current_epoch(self, organization_id: UUID) -> object:
        assert organization_id == self.organization_id
        return self.current


class _FailingEpochPort(_EpochPort):
    def read_current_epoch(self, organization_id: UUID) -> object:
        assert organization_id == self.organization_id
        raise PolicyEpochPortFailure("secret epoch backend diagnostic")


class _Provider(SyntheticReadProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def read(self, *, organization_id: UUID, provider_ref: str) -> None:
        self.calls.append((organization_id, provider_ref))

    @property
    def effects(self) -> int:
        return len(self.calls)


class _Channel(SyntheticNoopChannel):
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []

    def perform_noop(self, *, organization_id: UUID, channel_ref: str) -> None:
        self.calls.append((organization_id, channel_ref))

    @property
    def effects(self) -> int:
        return len(self.calls)


@contextmanager
def _trusted_inputs(
    *,
    organization_id: UUID = ORGANIZATION_ID,
    user_id: UUID = USER_ID,
    membership_id: UUID = MEMBERSHIP_ID,
    membership_version: int = 9,
    principal_ref: str = "principal-a",
    agent_version_ref: str = "agent-v1",
    purpose: str = "context.answer",
    epoch_port: _EpochPort | None = None,
) -> Iterator[tuple[AuthenticatedInvocation, TrustedDeliveryContext]]:
    epoch_scope = _open_policy_epoch_authority_scope()
    port = epoch_port or _EpochPort(organization_id)
    epoch = _observe_current_policy_epoch(
        _construct_policy_epoch_session(
            authority_scope=epoch_scope,
            organization_id=organization_id,
            port=port,
        )
    )
    membership_scope = _open_membership_authority_scope()
    membership = _construct_current_membership_verification(
        authority_scope=membership_scope,
        organization_id=organization_id,
        user_id=user_id,
        membership_id=membership_id,
        membership_version=membership_version,
        principal_ref=principal_ref,
        request_id="request-a",
        authentication_binding_ref="binding-a",
        checked_at=NOW,
        policy_epoch_verification=epoch,
    )
    try:
        scope_identity = ScopeAuthorityIdentity(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=membership_version,
            policy_epoch=membership.policy_epoch,
            principal_ref=principal_ref,
            agent_version_ref=agent_version_ref,
            purpose=purpose,
            request_id="request-a",
            authentication_binding_ref="binding-a",
            checked_at=NOW,
        )
        organization = _construct_existing_http_organization_verification(
            organization_id=organization_id,
            request_id="request-a",
            authentication_binding_ref="binding-a",
            verified_at=NOW,
        )
        with MissingTrustedScopeAuthority().current_scope(
            scope_identity
        ) as scope_snapshot:
            invocation = _construct_authenticated_http_invocation(
                request_id="request-a",
                authenticated_organization_ref=str(organization_id),
                organization_verification=organization,
                user_ref=str(user_id),
                principal_ref=principal_ref,
                membership_ref=str(membership_id),
                membership_version=membership_version,
                current_membership_verification=membership,
                agent_version_ref=agent_version_ref,
                authenticated_application_ref="application-a",
                authentication_binding_ref="binding-a",
                trusted_purpose=purpose,
                received_at=NOW,
                trusted_scope_snapshot=scope_snapshot,
            )
            delivery_context = _construct_direct_delivery_context(
                purpose=purpose,
                authenticated_application_ref="application-a",
                delivery_binding_ref="binding-a",
                established_at=NOW,
            )
            yield invocation, delivery_context
    finally:
        _close_membership_authority_scope(membership_scope)
        _close_policy_epoch_authority_scope(epoch_scope)


@contextmanager
def _identity(
    *,
    organization_id: UUID = ORGANIZATION_ID,
    user_id: UUID = USER_ID,
    membership_id: UUID = MEMBERSHIP_ID,
    membership_version: int = 9,
    principal_ref: str = "principal-a",
    agent_version_ref: str = "agent-v1",
    purpose: str = "context.answer",
    epoch_port: _EpochPort | None = None,
) -> Iterator[TicketExecutionIdentity]:
    with _trusted_inputs(
        organization_id=organization_id,
        user_id=user_id,
        membership_id=membership_id,
        membership_version=membership_version,
        principal_ref=principal_ref,
        agent_version_ref=agent_version_ref,
        purpose=purpose,
        epoch_port=epoch_port,
    ) as (invocation, delivery_context):
        yield _construct_ticket_execution_identity(
            invocation=invocation,
            delivery_context=delivery_context,
        )


def test_valid_tickets_invoke_only_their_bound_synthetic_effects() -> None:
    provider = _Provider()
    channel = _Channel()
    with _identity() as identity:
        read_ticket = ContextAccessTicket.deserialize(
            ContextAccessTicketIssuer(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                clock=lambda: NOW,
            )
            .issue(identity)
            .serialize(),
            keyring=KEYRING,
        )
        action_ticket = ActionTicket.deserialize(
            ActionTicketIssuer(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                clock=lambda: NOW,
            )
            .issue(identity)
            .serialize(),
            keyring=KEYRING,
        )

        ContextAccessTicketReadHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            provider=provider,
            clock=lambda: NOW,
        ).read(ticket=read_ticket, identity=identity)
        ActionTicketNoopHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            channel=channel,
            clock=lambda: NOW,
        ).perform(ticket=action_ticket, identity=identity)

    assert provider.effects == 1
    assert channel.effects == 1


@pytest.mark.security_evidence(id="PROP-ACTION-SEPARATION-014", layer="property")
def test_public_ticket_types_and_server_side_issuers_are_structurally_distinct() -> (
    None
):
    assert runtime.ContextAccessTicket.__module__ == (
        "engine.runtime.context_access_ticket"
    )
    assert runtime.ActionTicket.__module__ == "engine.runtime.action_ticket"
    assert get_type_hints(runtime.ContextAccessTicketIssuer.issue)["return"] is (
        runtime.ContextAccessTicket
    )
    assert get_type_hints(runtime.ActionTicketIssuer.issue)["return"] is (
        runtime.ActionTicket
    )
    assert list(signature(runtime.ContextAccessTicketIssuer.issue).parameters) == [
        "self",
        "identity",
    ]
    assert list(signature(runtime.ActionTicketIssuer.issue).parameters) == [
        "self",
        "identity",
    ]
    assert list(signature(_construct_ticket_execution_identity).parameters) == [
        "invocation",
        "delivery_context",
    ]
    with pytest.raises(TypeError, match="issuer or validated deserializer"):
        ContextAccessTicket("caller-authored")
    with pytest.raises(TypeError, match="issuer or validated deserializer"):
        ActionTicket("caller-authored")


def test_ticket_identity_derives_agent_and_purpose_from_trusted_inputs() -> None:
    with _trusted_inputs(
        agent_version_ref="agent-authority",
        purpose="citation.open",
    ) as (invocation, delivery_context):
        identity = _construct_ticket_execution_identity(
            invocation=invocation,
            delivery_context=delivery_context,
        )

        assert identity.agent_version_ref == invocation.agent_version_ref
        assert identity.purpose == delivery_context.purpose
        assert identity.user_actor is invocation.user_actor


def test_mismatched_trusted_invocation_and_delivery_cannot_build_identity() -> None:
    with (
        _trusted_inputs(purpose="context.answer") as (invocation, _delivery),
        _trusted_inputs(purpose="citation.open") as (_other_invocation, delivery),
        pytest.raises(ValueError, match="trusted scope snapshot"),
    ):
        _construct_ticket_execution_identity(
            invocation=invocation,
            delivery_context=delivery,
        )


def test_signed_ticket_schemas_bind_distinct_domains_audiences_and_identity(
) -> None:
    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
            nonce_factory=lambda: NONCE,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
            nonce_factory=lambda: NONCE,
        ).issue(identity)

    read_header, read_claims = _decode_ticket(read_ticket.serialize())
    action_header, action_claims = _decode_ticket(action_ticket.serialize())

    assert read_header == {
        "alg": "HS256",
        "dom": "context-engine.context-access-ticket",
        "kid": 7,
        "typ": "CE-ContextAccessTicket",
        "v": 1,
    }
    assert action_header == {
        "alg": "HS256",
        "dom": "context-engine.action-ticket",
        "kid": 7,
        "typ": "CE-ActionTicket",
        "v": 1,
    }
    shared_identity_claims = {
        "actor_principal_ref": "principal-a",
        "agent_version_ref": "agent-v1",
        "expires_at": "2026-07-22T08:01:00Z",
        "issued_at": "2026-07-22T08:00:00Z",
        "nonce": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "organization_id": str(ORGANIZATION_ID),
        "policy_epoch": 11,
        "purpose": "context.answer",
        "signing_key_version": 7,
        "subject_membership_id": str(MEMBERSHIP_ID),
        "subject_membership_version": 9,
        "subject_user_id": str(USER_ID),
    }
    assert read_claims == {
        **shared_identity_claims,
        "audience": "context-read:provider-a",
        "operation": "synthetic.provider.read",
        "provider_ref": "provider-a",
    }
    assert action_claims == {
        **shared_identity_claims,
        "audience": "im-send:channel-a",
        "channel_ref": "channel-a",
        "operation": "synthetic.channel.noop",
    }


def test_ticket_targets_are_bound_to_a_trusted_organization_configuration(
) -> None:
    other_organization = UUID("115f61f7-9006-44ba-bd82-d395c3bc57df")
    provider = _Provider()
    channel = _Channel()

    with _identity(organization_id=other_organization) as other_identity:
        other_read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=other_organization,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(other_identity)
        other_action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=other_organization,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(other_identity)

        with pytest.raises(ValueError, match="configured Organization"):
            ContextAccessTicketIssuer(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                clock=lambda: NOW,
            ).issue(other_identity)
        with pytest.raises(ValueError, match="configured Organization"):
            ActionTicketIssuer(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                clock=lambda: NOW,
            ).issue(other_identity)
        with pytest.raises(TicketNotAvailable):
            ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider,
                clock=lambda: NOW,
            ).read(ticket=other_read_ticket, identity=other_identity)
        with pytest.raises(TicketNotAvailable):
            ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                channel=channel,
                clock=lambda: NOW,
            ).perform(ticket=other_action_ticket, identity=other_identity)

    assert provider.effects == 0
    assert channel.effects == 0


def test_forged_exact_identity_is_normalized_before_any_effect() -> None:
    provider = _Provider()

    with _identity() as identity:
        ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        forged = object.__new__(TicketExecutionIdentity)
        object.__setattr__(forged, "user_actor", identity.user_actor)

        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider,
                clock=lambda: NOW,
            ).read(ticket=ticket, identity=forged)

    assert provider.effects == 0


@pytest.mark.security_evidence(id="RUNTIME-ACTION-SEPARATION-014", layer="runtime")
def test_each_ticket_is_rejected_by_the_other_plane_and_deserializer(
) -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        read_handler = ContextAccessTicketReadHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            provider=provider,
            clock=lambda: NOW,
        )
        action_handler = ActionTicketNoopHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            channel=channel,
            clock=lambda: NOW,
        )

        rejected_uses = (
            lambda: read_handler.read(
                ticket=cast(ContextAccessTicket, action_ticket),
                identity=identity,
            ),
            lambda: action_handler.perform(
                ticket=cast(ActionTicket, read_ticket),
                identity=identity,
            ),
            lambda: ContextAccessTicket.deserialize(
                action_ticket.serialize(), keyring=KEYRING
            ),
            lambda: ActionTicket.deserialize(
                read_ticket.serialize(), keyring=KEYRING
            ),
        )
        rejections: list[TicketNotAvailable] = []
        for rejected_use in rejected_uses:
            with pytest.raises(
                TicketNotAvailable,
                match="^capability not available$",
            ) as error:
                rejected_use()
            rejections.append(error.value)

    assert len(rejections) == len(rejected_uses)
    assert provider.effects == channel.effects == 0


@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-012", layer="runtime")
def test_accept_012_context_read_ticket_cannot_create_an_action_effect(
    record_property: Callable[[str, object], None],
) -> None:
    """Invoke the highest current action seam with read-only authority."""

    channel = _Channel()
    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        handler = ActionTicketNoopHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="conversation-b",
            channel=channel,
            clock=lambda: NOW,
        )

        with pytest.raises(
            TicketNotAvailable,
            match="^capability not available$",
        ) as error:
            handler.perform(
                ticket=cast(ActionTicket, read_ticket),
                identity=identity,
            )

    rejection = error.value
    non_generic_rejection_count = int(
        rejection.audit_receipt.category
        is not TicketRejectionCategory.CAPABILITY_NOT_AVAILABLE
        or rejection.audit_receipt.denied_detail_count != 0
    )
    assert channel.effects == 0
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-012",
        unauthorized_evidence_count=0,
        wrong_organization_effect_count=channel.effects,
        missing_context_fallback_count=non_generic_rejection_count,
    )


@pytest.mark.parametrize("segment_index", [0, 1, 2])
def test_one_bit_tamper_in_each_signed_segment_is_rejected_before_effect(
    segment_index: int,
) -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        read_segments = read_ticket.serialize().split(".")
        action_segments = action_ticket.serialize().split(".")
        read_segments[segment_index] = _flip_one_ascii_bit(
            read_segments[segment_index]
        )
        action_segments[segment_index] = _flip_one_ascii_bit(
            action_segments[segment_index]
        )

        with pytest.raises(TicketNotAvailable):
            ContextAccessTicket.deserialize(
                ".".join(read_segments),
                keyring=KEYRING,
            )
        with pytest.raises(TicketNotAvailable):
            ActionTicket.deserialize(
                ".".join(action_segments),
                keyring=KEYRING,
            )

    assert provider.effects == 0
    assert channel.effects == 0


def test_unknown_signing_key_version_is_generic_and_has_zero_effects() -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)

        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ContextAccessTicket.deserialize(
                _rewrite_to_unknown_key_version(read_ticket),
                keyring=KEYRING,
            )
        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ActionTicket.deserialize(
                _rewrite_to_unknown_key_version(action_ticket),
                keyring=KEYRING,
            )

    assert provider.effects == 0
    assert channel.effects == 0


@pytest.mark.parametrize(
    ("claim_name", "mutated_value"),
    [
        ("organization_id", "115f61f7-9006-44ba-bd82-d395c3bc57df"),
        ("subject_user_id", "3bfa89a1-73ae-4964-b404-d31b52e0b237"),
        ("subject_membership_id", "d6423281-2575-4312-844b-603e38389e72"),
        ("subject_membership_version", 10),
        ("actor_principal_ref", "principal-b"),
        ("agent_version_ref", "agent-v2"),
        ("purpose", "context.other"),
        ("policy_epoch", 12),
        ("issued_at", "2026-07-22T08:00:01Z"),
        ("expires_at", "2026-07-22T08:00:00Z"),
    ],
)
def test_validly_resigned_identity_or_freshness_mutation_is_rejected(
    claim_name: str,
    mutated_value: object,
) -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)

        with pytest.raises(TicketNotAvailable):
            ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider,
                clock=lambda: NOW,
            ).read(
                ticket=ContextAccessTicket.deserialize(
                    _resign_ticket(
                        read_ticket,
                        claim_name=claim_name,
                        claim_value=mutated_value,
                    ),
                    keyring=KEYRING,
                ),
                identity=identity,
            )
        with pytest.raises(TicketNotAvailable):
            ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                channel=channel,
                clock=lambda: NOW,
            ).perform(
                ticket=ActionTicket.deserialize(
                    _resign_ticket(
                        action_ticket,
                        claim_name=claim_name,
                        claim_value=mutated_value,
                    ),
                    keyring=KEYRING,
                ),
                identity=identity,
            )

    assert provider.effects == 0
    assert channel.effects == 0


def test_validly_signed_overlong_lifetimes_are_rejected_before_effect() -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        overlong_expiry = "2026-07-22T08:05:01Z"

        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ContextAccessTicket.deserialize(
                _resign_ticket(
                    read_ticket,
                    claim_name="expires_at",
                    claim_value=overlong_expiry,
                ),
                keyring=KEYRING,
            )
        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ActionTicket.deserialize(
                _resign_ticket(
                    action_ticket,
                    claim_name="expires_at",
                    claim_value=overlong_expiry,
                ),
                keyring=KEYRING,
            )

    assert provider.effects == 0
    assert channel.effects == 0


@pytest.mark.parametrize(
    ("plane", "claim_name", "mutated_value"),
    [
        ("read", "provider_ref", "provider-b"),
        ("read", "audience", "context-read:provider-b"),
        ("read", "operation", "synthetic.channel.noop"),
        ("action", "channel_ref", "channel-b"),
        ("action", "audience", "im-send:channel-b"),
        ("action", "operation", "synthetic.provider.read"),
    ],
)
def test_valid_signature_never_overrides_wrong_target_audience_or_operation(
    plane: str,
    claim_name: str,
    mutated_value: object,
) -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        if plane == "read":
            with pytest.raises(TicketNotAvailable):
                ContextAccessTicketReadHandler(
                    keyring=KEYRING,
                    organization_id=ORGANIZATION_ID,
                    provider_ref="provider-a",
                    provider=provider,
                    clock=lambda: NOW,
                ).read(
                    ticket=ContextAccessTicket.deserialize(
                        _resign_ticket(
                            read_ticket,
                            claim_name=claim_name,
                            claim_value=mutated_value,
                        ),
                        keyring=KEYRING,
                    ),
                    identity=identity,
                )
        else:
            with pytest.raises(TicketNotAvailable):
                ActionTicketNoopHandler(
                    keyring=KEYRING,
                    organization_id=ORGANIZATION_ID,
                    channel_ref="channel-a",
                    channel=channel,
                    clock=lambda: NOW,
                ).perform(
                    ticket=ActionTicket.deserialize(
                        _resign_ticket(
                            action_ticket,
                            claim_name=claim_name,
                            claim_value=mutated_value,
                        ),
                        keyring=KEYRING,
                    ),
                    identity=identity,
                )

    assert provider.effects == 0
    assert channel.effects == 0


def test_deep_validly_signed_json_is_normalized_before_effect() -> None:
    provider = _Provider()

    with _identity() as identity:
        ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        header, claims = _decode_ticket(ticket.serialize())
        claims["purpose"] = "__DEEP_VALUE__"
        raw_claims = _canonical_json(claims).replace(
            b'"__DEEP_VALUE__"',
            b"[" * 1100 + b"0" + b"]" * 1100,
        )
        with pytest.raises(TicketNotAvailable, match="^capability not available$"):
            ContextAccessTicket.deserialize(
                _sign_raw_documents(_canonical_json(header), raw_claims),
                keyring=KEYRING,
            )

    assert provider.effects == 0


def test_tickets_for_target_a_or_at_expiry_are_rejected_at_target_b(
) -> None:
    provider_b = _Provider()
    channel_b = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)

        rejected_uses = (
            lambda: ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-b",
                provider=provider_b,
                clock=lambda: NOW,
            ).read(ticket=read_ticket, identity=identity),
            lambda: ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-b",
                channel=channel_b,
                clock=lambda: NOW,
            ).perform(ticket=action_ticket, identity=identity),
            lambda: ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider_b,
                clock=lambda: NOW + timedelta(seconds=60),
            ).read(ticket=read_ticket, identity=identity),
            lambda: ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                channel=channel_b,
                clock=lambda: NOW + timedelta(seconds=60),
            ).perform(ticket=action_ticket, identity=identity),
        )
        for rejected_use in rejected_uses:
            with pytest.raises(
                TicketNotAvailable,
                match="^capability not available$",
            ):
                rejected_use()

    assert provider_b.effects == 0
    assert channel_b.effects == 0


def test_ticket_rejection_is_one_closed_non_enumerating_result() -> None:
    receipt = TicketNotAvailable().audit_receipt

    assert str(TicketNotAvailable()) == "capability not available"
    assert receipt.category is TicketRejectionCategory.CAPABILITY_NOT_AVAILABLE
    assert receipt.denied_detail_count == 0
    assert {item.name for item in fields(receipt)} == {
        "category",
        "denied_detail_count",
    }
    with pytest.raises(ValueError, match="category must remain closed"):
        TicketRejectionAuditReceipt(category="target_missing")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="detail count must remain zero"):
        TicketRejectionAuditReceipt(denied_detail_count=1)  # type: ignore[arg-type]


def test_public_ticket_authority_objects_are_redacted_and_not_serializable(
) -> None:
    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        secret_markers = (
            str(ORGANIZATION_ID),
            str(USER_ID),
            str(MEMBERSHIP_ID),
            "principal-a",
            "agent-v1",
            "context.answer",
            "provider-a",
            "channel-a",
            read_ticket.serialize(),
            action_ticket.serialize(),
            str(SIGNING_KEY),
        )

        for value in (read_ticket, action_ticket, identity, KEYRING):
            for display in (repr(value), str(value)):
                assert all(marker not in display for marker in secret_markers)
            with pytest.raises(TypeError):
                pickle.dumps(value)


@pytest.mark.parametrize("malformed_epoch", [None, True, 0, -1, 1 << 63, "11"])
def test_unavailable_current_epoch_is_generic_and_has_zero_effects(
    malformed_epoch: object,
) -> None:
    port = _EpochPort(current=11)
    provider = _Provider()
    channel = _Channel()

    with _identity(epoch_port=port) as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        port.current = malformed_epoch

        with pytest.raises(TicketNotAvailable):
            ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider,
                clock=lambda: NOW,
            ).read(ticket=read_ticket, identity=identity)
        with pytest.raises(TicketNotAvailable):
            ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                channel=channel,
                clock=lambda: NOW,
            ).perform(ticket=action_ticket, identity=identity)

    assert provider.effects == 0
    assert channel.effects == 0


def test_reported_epoch_port_failure_is_generic_and_has_zero_effects() -> None:
    port = _EpochPort(current=11)
    provider = _Provider()
    channel = _Channel()

    with _identity(epoch_port=port) as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)
        object.__setattr__(
            identity.policy_epoch_verification.validation_session,
            "_port",
            _FailingEpochPort(),
        )

        for rejected_use in (
            lambda: ContextAccessTicketReadHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                provider_ref="provider-a",
                provider=provider,
                clock=lambda: NOW,
            ).read(ticket=read_ticket, identity=identity),
            lambda: ActionTicketNoopHandler(
                keyring=KEYRING,
                organization_id=ORGANIZATION_ID,
                channel_ref="channel-a",
                channel=channel,
                clock=lambda: NOW,
            ).perform(ticket=action_ticket, identity=identity),
        ):
            with pytest.raises(TicketNotAvailable) as rejection:
                rejected_use()
            assert str(rejection.value) == "capability not available"
            assert "secret epoch backend diagnostic" not in repr(rejection.value)

    assert provider.effects == 0
    assert channel.effects == 0


def test_closed_trusted_identity_scope_is_generic_and_has_zero_effects() -> None:
    provider = _Provider()
    channel = _Channel()

    with _identity() as identity:
        read_ticket = ContextAccessTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            clock=lambda: NOW,
        ).issue(identity)
        action_ticket = ActionTicketIssuer(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            clock=lambda: NOW,
        ).issue(identity)

    with pytest.raises(TicketNotAvailable):
        ContextAccessTicketReadHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            provider_ref="provider-a",
            provider=provider,
            clock=lambda: NOW,
        ).read(ticket=read_ticket, identity=identity)
    with pytest.raises(TicketNotAvailable):
        ActionTicketNoopHandler(
            keyring=KEYRING,
            organization_id=ORGANIZATION_ID,
            channel_ref="channel-a",
            channel=channel,
            clock=lambda: NOW,
        ).perform(ticket=action_ticket, identity=identity)

    assert provider.effects == 0
    assert channel.effects == 0
