from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest

from bot_delivery.egress import (
    AuthorizedChannelPayload,
    AuthorizedModelInput,
    ChannelEgressBoundary,
    DeterministicModelGatewaySpy,
    DeterministicSenderPreflightSpy,
    ModelEgressBoundary,
    prepare_authorized_channel_payload,
    prepare_authorized_model_input,
)
from engine.runtime.contracts import (
    BudgetUsage,
    ContextPackage,
    Coverage,
    CoverageReason,
    CoverageStatus,
)
from engine.runtime.egress import (
    ChannelEgressGrant,
    ChannelEgressProfile,
    EgressGrantNotAvailable,
    EgressGrantRedemption,
    ModelEgressGrant,
    ModelEgressProfile,
)
from engine.runtime.evidence import AuthorizedProjection, CandidateRef

ORGANIZATION_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000002")
AUDIENCE_DIGEST = "a" * 64
NOW = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)


def _package(*, purpose: str = "answer") -> ContextPackage:
    return ContextPackage(
        organization_ref="orgpkg_" + "1" * 32,
        purpose=purpose,
        ttl_seconds=300,
        as_of=NOW,
        expires_at=NOW + timedelta(seconds=300),
        decision_ref="dec_" + "2" * 32,
        blocks=(),
        evidence=(),
        gaps=(),
        budget_usage=BudgetUsage(
            tokens=0,
            provider_calls=0,
            cost_microunits=0,
            elapsed_ms=0,
        ),
        coverage=Coverage(
            status=CoverageStatus.EMPTY,
            reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
        ),
    )


def _model_profile() -> ModelEgressProfile:
    return ModelEgressProfile(
        profile_ref="model-egress-test-v1",
        retention_policy_ref="no-provider-retention-v1",
        sensitivity_policy_ref="internal-authorized-package-v1",
        issuer_ref="context-runtime-test",
        consumer_ref="model-gateway-test",
        provider_ref="provider-test",
        model_ref="model-test",
        region_ref="region-test",
        maximum_ttl=timedelta(seconds=60),
    )


def _channel_profile() -> ChannelEgressProfile:
    return ChannelEgressProfile(
        profile_ref="channel-egress-test-v1",
        retention_policy_ref="no-channel-retention-v1",
        sensitivity_policy_ref="internal-authorized-package-v1",
        issuer_ref="context-runtime-test",
        consumer_ref="sender-preflight-test",
        channel_ref="channel-test",
        destination_ref="destination-test",
        region_ref="region-test",
        maximum_ttl=timedelta(seconds=60),
    )


class _ExactRedemptionAuthority:
    def __init__(self, expected: EgressGrantRedemption) -> None:
        self.expected = expected
        self.calls: list[EgressGrantRedemption] = []
        self.consumed = False

    def redeem(self, redemption: EgressGrantRedemption) -> bool:
        self.calls.append(redemption)
        if self.consumed or redemption != self.expected:
            return False
        self.consumed = True
        return True


def _model_boundary(
    package: ContextPackage,
    grant: ModelEgressGrant,
) -> tuple[ModelEgressBoundary, DeterministicModelGatewaySpy]:
    authorized_input = prepare_authorized_model_input(package, grant)
    expected = EgressGrantRedemption.for_model(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=authorized_input.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
    )
    gateway = DeterministicModelGatewaySpy(_model_profile())
    return (
        ModelEgressBoundary(
            organization_id=ORGANIZATION_ID,
            audience_digest=AUDIENCE_DIGEST,
            policy_epoch=7,
            profile=_model_profile(),
            authority=_ExactRedemptionAuthority(expected),
            gateway=gateway,
        ),
        gateway,
    )


def test_model_and_channel_grants_are_distinct_opaque_nominal_types() -> None:
    model = ModelEgressGrant("egrm_" + "1" * 64)
    channel = ChannelEgressGrant("egrc_" + "2" * 64)

    assert type(model) is ModelEgressGrant
    assert type(channel) is ChannelEgressGrant
    assert cast(object, model) != cast(object, channel)
    assert "1" * 64 not in repr(model)
    assert "2" * 64 not in repr(channel)
    assert not hasattr(model, "channel_ref")
    assert not hasattr(channel, "provider_ref")


def test_authorized_model_input_requires_exact_package_and_model_grant() -> None:
    package = _package()
    model_grant = ModelEgressGrant("egrm_" + "1" * 64)
    channel_grant = ChannelEgressGrant("egrc_" + "2" * 64)

    authorized = prepare_authorized_model_input(package, model_grant)

    assert type(authorized) is AuthorizedModelInput
    assert authorized.package_digest == package.package_digest
    assert authorized.payload_digest
    with pytest.raises(TypeError, match="BotDelivery"):
        AuthorizedModelInput()
    for rejected in (
        "arbitrary text",
        object.__new__(CandidateRef),
        object.__new__(AuthorizedProjection),
    ):
        with pytest.raises(TypeError, match="ContextPackage"):
            prepare_authorized_model_input(cast(Any, rejected), model_grant)
    with pytest.raises(TypeError, match="ModelEgressGrant"):
        prepare_authorized_model_input(package, cast(Any, channel_grant))


def test_only_exact_authorized_model_input_and_grant_reach_gateway_bytes() -> None:
    package = _package()
    grant = ModelEgressGrant("egrm_" + "1" * 64)
    boundary, gateway = _model_boundary(package, grant)
    authorized = prepare_authorized_model_input(package, grant)

    boundary.transmit(authorized, grant)

    assert gateway.request_count == 1
    assert gateway.outbound_bytes > 0
    with pytest.raises(EgressGrantNotAvailable):
        boundary.transmit(authorized, grant)
    assert gateway.request_count == 1


def test_wrong_grant_or_consumer_binding_emits_zero_model_bytes() -> None:
    package = _package()
    grant = ModelEgressGrant("egrm_" + "1" * 64)
    wrong_grant = ModelEgressGrant("egrm_" + "2" * 64)
    boundary, gateway = _model_boundary(package, grant)
    authorized = prepare_authorized_model_input(package, grant)

    with pytest.raises(EgressGrantNotAvailable):
        boundary.transmit(authorized, wrong_grant)
    assert gateway.request_count == 0
    assert gateway.outbound_bytes == 0


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: replace(value, organization_id=OTHER_ORGANIZATION_ID),
        lambda value: replace(value, package_digest="b" * 64),
        lambda value: replace(value, payload_digest="b" * 64),
        lambda value: replace(value, purpose="citation.open"),
        lambda value: replace(value, audience_digest="b" * 64),
        lambda value: replace(value, policy_epoch=8),
        lambda value: replace(value, retention_policy_ref="retain-wrong"),
        lambda value: replace(value, sensitivity_policy_ref="sensitivity-wrong"),
        lambda value: replace(value, issuer_ref="issuer-wrong"),
        lambda value: replace(value, consumer_ref="gateway-wrong"),
        lambda value: replace(value, provider_ref="provider-wrong"),
        lambda value: replace(value, model_ref="model-wrong"),
        lambda value: replace(value, region_ref="region-wrong"),
        lambda value: replace(value, profile_ref="profile-wrong"),
    ),
)
def test_each_model_redemption_binding_mismatch_emits_zero_bytes(
    mutation: Any,
) -> None:
    package = _package()
    grant = ModelEgressGrant("egrm_" + "1" * 64)
    authorized = prepare_authorized_model_input(package, grant)
    expected = EgressGrantRedemption.for_model(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=authorized.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
    )
    gateway = DeterministicModelGatewaySpy(_model_profile())
    boundary = ModelEgressBoundary(
        organization_id=ORGANIZATION_ID,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
        authority=_ExactRedemptionAuthority(mutation(expected)),
        gateway=gateway,
    )

    with pytest.raises(EgressGrantNotAvailable, match="not available"):
        boundary.transmit(authorized, grant)

    assert gateway.request_count == 0
    assert gateway.outbound_bytes == 0


def test_wrong_model_gateway_identity_is_non_enumerating_and_zero_bytes() -> None:
    package = _package()
    grant = ModelEgressGrant("egrm_" + "1" * 64)
    authorized = prepare_authorized_model_input(package, grant)
    expected = EgressGrantRedemption.for_model(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=authorized.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
    )
    gateway = DeterministicModelGatewaySpy(
        replace(_model_profile(), model_ref="wrong-model")
    )

    with pytest.raises(EgressGrantNotAvailable, match="not available"):
        ModelEgressBoundary(
            organization_id=ORGANIZATION_ID,
            audience_digest=AUDIENCE_DIGEST,
            policy_epoch=7,
            profile=_model_profile(),
            authority=_ExactRedemptionAuthority(expected),
            gateway=gateway,
        )

    assert gateway.request_count == 0
    assert gateway.outbound_bytes == 0


def test_mutated_model_payload_emits_zero_bytes_before_redemption() -> None:
    package = _package()
    grant = ModelEgressGrant("egrm_" + "1" * 64)
    boundary, gateway = _model_boundary(package, grant)
    authorized = prepare_authorized_model_input(package, grant)
    object.__setattr__(authorized, "_payload", authorized._payload + b"tampered")

    with pytest.raises(EgressGrantNotAvailable, match="not available"):
        boundary.transmit(authorized, grant)

    assert gateway.request_count == 0
    assert gateway.outbound_bytes == 0


def test_channel_grant_reaches_only_exact_payload_preflight_and_never_effect() -> None:
    package = _package()
    grant = ChannelEgressGrant("egrc_" + "3" * 64)
    payload = prepare_authorized_channel_payload(package, grant)
    expected = EgressGrantRedemption.for_channel(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=payload.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
    )
    sender = DeterministicSenderPreflightSpy(_channel_profile())
    boundary = ChannelEgressBoundary(
        organization_id=ORGANIZATION_ID,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
        authority=_ExactRedemptionAuthority(expected),
        sender=sender,
    )

    boundary.preflight(payload, grant)

    assert sender.preflight_count == 1
    assert sender.outbound_bytes > 0
    assert sender.effect_count == 0
    with pytest.raises(TypeError, match="BotDelivery"):
        AuthorizedChannelPayload()
    with pytest.raises(TypeError, match="AuthorizedChannelPayload"):
        boundary.preflight(cast(Any, grant), grant)
    assert sender.preflight_count == 1
    assert sender.effect_count == 0


def test_cross_org_channel_redemption_is_non_enumerating_and_zero_effect() -> None:
    package = _package()
    grant = ChannelEgressGrant("egrc_" + "3" * 64)
    payload = prepare_authorized_channel_payload(package, grant)
    expected = EgressGrantRedemption.for_channel(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=payload.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
    )
    sender = DeterministicSenderPreflightSpy(_channel_profile())
    boundary = ChannelEgressBoundary(
        organization_id=OTHER_ORGANIZATION_ID,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
        authority=_ExactRedemptionAuthority(expected),
        sender=sender,
    )

    with pytest.raises(EgressGrantNotAvailable, match="not available"):
        boundary.preflight(payload, grant)

    assert sender.preflight_count == 0
    assert sender.outbound_bytes == 0
    assert sender.effect_count == 0


def test_wrong_sender_identity_is_non_enumerating_and_zero_effect() -> None:
    package = _package()
    grant = ChannelEgressGrant("egrc_" + "3" * 64)
    payload = prepare_authorized_channel_payload(package, grant)
    expected = EgressGrantRedemption.for_channel(
        grant=grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=payload.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
    )
    sender = DeterministicSenderPreflightSpy(
        replace(_channel_profile(), destination_ref="wrong-destination")
    )

    with pytest.raises(EgressGrantNotAvailable, match="not available"):
        ChannelEgressBoundary(
            organization_id=ORGANIZATION_ID,
            audience_digest=AUDIENCE_DIGEST,
            policy_epoch=7,
            profile=_channel_profile(),
            authority=_ExactRedemptionAuthority(expected),
            sender=sender,
        )

    assert sender.preflight_count == 0
    assert sender.outbound_bytes == 0
    assert sender.effect_count == 0


@pytest.mark.security_evidence(id="PROP-EGRESS-011", layer="property")
def test_each_egress_binding_mutation_and_cross_kind_emits_zero_bytes_effects() -> None:
    package = _package()
    model_grant = ModelEgressGrant("egrm_" + "1" * 64)
    channel_grant = ChannelEgressGrant("egrc_" + "3" * 64)
    authorized = prepare_authorized_model_input(package, model_grant)
    payload = prepare_authorized_channel_payload(package, channel_grant)
    expected_model = EgressGrantRedemption.for_model(
        grant=model_grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=authorized.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
    )
    expected_channel = EgressGrantRedemption.for_channel(
        grant=channel_grant,
        organization_id=ORGANIZATION_ID,
        package_digest=package.package_digest,
        payload_digest=payload.payload_digest,
        purpose=package.purpose,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
    )
    common_mutations: tuple[
        Callable[[EgressGrantRedemption], EgressGrantRedemption], ...
    ] = (
        lambda value: replace(value, organization_id=OTHER_ORGANIZATION_ID),
        lambda value: replace(value, package_digest="b" * 64),
        lambda value: replace(value, payload_digest="b" * 64),
        lambda value: replace(value, purpose="citation.open"),
        lambda value: replace(value, audience_digest="b" * 64),
        lambda value: replace(value, policy_epoch=8),
        lambda value: replace(value, retention_policy_ref="retain-wrong"),
        lambda value: replace(value, sensitivity_policy_ref="sensitivity-wrong"),
        lambda value: replace(value, issuer_ref="issuer-wrong"),
        lambda value: replace(value, consumer_ref="consumer-wrong"),
        lambda value: replace(value, region_ref="region-wrong"),
        lambda value: replace(value, profile_ref="profile-wrong"),
    )
    model_mutations: tuple[
        Callable[[EgressGrantRedemption], EgressGrantRedemption], ...
    ] = common_mutations + (
        lambda value: replace(value, provider_ref="provider-wrong"),
        lambda value: replace(value, model_ref="model-wrong"),
    )
    for mutation in model_mutations:
        gateway = DeterministicModelGatewaySpy(_model_profile())
        model_boundary = ModelEgressBoundary(
            organization_id=ORGANIZATION_ID,
            audience_digest=AUDIENCE_DIGEST,
            policy_epoch=7,
            profile=_model_profile(),
            authority=_ExactRedemptionAuthority(mutation(expected_model)),
            gateway=gateway,
        )
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            model_boundary.transmit(authorized, model_grant)
        assert gateway.request_count == gateway.outbound_bytes == 0

    channel_mutations: tuple[
        Callable[[EgressGrantRedemption], EgressGrantRedemption], ...
    ] = common_mutations + (
        lambda value: replace(value, channel_ref="channel-wrong"),
        lambda value: replace(value, destination_ref="destination-wrong"),
    )
    for mutation in channel_mutations:
        sender = DeterministicSenderPreflightSpy(_channel_profile())
        channel_boundary = ChannelEgressBoundary(
            organization_id=ORGANIZATION_ID,
            audience_digest=AUDIENCE_DIGEST,
            policy_epoch=7,
            profile=_channel_profile(),
            authority=_ExactRedemptionAuthority(mutation(expected_channel)),
            sender=sender,
        )
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            channel_boundary.preflight(payload, channel_grant)
        assert (
            sender.preflight_count
            == sender.outbound_bytes
            == sender.effect_count
            == 0
        )

    for wrong_gateway_profile in (
        replace(_model_profile(), consumer_ref="consumer-wrong"),
        replace(_model_profile(), provider_ref="provider-wrong"),
        replace(_model_profile(), model_ref="model-wrong"),
        replace(_model_profile(), region_ref="region-wrong"),
    ):
        gateway = DeterministicModelGatewaySpy(wrong_gateway_profile)
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            ModelEgressBoundary(
                organization_id=ORGANIZATION_ID,
                audience_digest=AUDIENCE_DIGEST,
                policy_epoch=7,
                profile=_model_profile(),
                authority=_ExactRedemptionAuthority(expected_model),
                gateway=gateway,
            )
        assert gateway.request_count == gateway.outbound_bytes == 0

    for wrong_sender_profile in (
        replace(_channel_profile(), consumer_ref="consumer-wrong"),
        replace(_channel_profile(), channel_ref="channel-wrong"),
        replace(_channel_profile(), destination_ref="destination-wrong"),
        replace(_channel_profile(), region_ref="region-wrong"),
    ):
        sender = DeterministicSenderPreflightSpy(wrong_sender_profile)
        with pytest.raises(EgressGrantNotAvailable, match="not available"):
            ChannelEgressBoundary(
                organization_id=ORGANIZATION_ID,
                audience_digest=AUDIENCE_DIGEST,
                policy_epoch=7,
                profile=_channel_profile(),
                authority=_ExactRedemptionAuthority(expected_channel),
                sender=sender,
            )
        assert (
            sender.preflight_count
            == sender.outbound_bytes
            == sender.effect_count
            == 0
        )

    model_gateway = DeterministicModelGatewaySpy(_model_profile())
    model_boundary = ModelEgressBoundary(
        organization_id=ORGANIZATION_ID,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_model_profile(),
        authority=_ExactRedemptionAuthority(expected_model),
        gateway=model_gateway,
    )
    with pytest.raises(TypeError, match="ModelEgressGrant"):
        model_boundary.transmit(authorized, cast(Any, channel_grant))
    assert model_gateway.request_count == model_gateway.outbound_bytes == 0

    channel_sender = DeterministicSenderPreflightSpy(_channel_profile())
    channel_boundary = ChannelEgressBoundary(
        organization_id=ORGANIZATION_ID,
        audience_digest=AUDIENCE_DIGEST,
        policy_epoch=7,
        profile=_channel_profile(),
        authority=_ExactRedemptionAuthority(expected_channel),
        sender=channel_sender,
    )
    with pytest.raises(TypeError, match="ChannelEgressGrant"):
        channel_boundary.preflight(payload, cast(Any, model_grant))
    assert (
        channel_sender.preflight_count
        == channel_sender.outbound_bytes
        == channel_sender.effect_count
        == 0
    )
