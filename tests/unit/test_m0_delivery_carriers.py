from __future__ import annotations

import importlib.util
from collections.abc import Callable

import pytest

import engine.runtime as runtime
from tests.support.security_gate import (
    probe_unavailable_module_carrier,
    record_security_oracles,
)


@pytest.mark.security_evidence(id="RUNTIME-EGRESS-011", layer="runtime")
def test_m0_egress_carrier_is_unavailable_before_model_or_sender_bytes() -> None:
    """M0 cannot construct the future delivery types or their trusted process."""

    assert importlib.util.find_spec("bot_delivery") is None
    for public_name in (
        "AuthorizedModelInput",
        "EgressGrant",
        "ModelGateway",
        "Sender",
    ):
        assert not hasattr(runtime, public_name)


@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-012", layer="runtime")
def test_accept_012_action_plane_is_unavailable_before_ticket_or_effect(
    record_property: Callable[[str, object], None],
) -> None:
    """The documented M0 form has no ActionPlane, ticket, Sender, or effect."""

    forbidden_runtime_authorities = tuple(
        public_name
        for public_name in ("ActionPlane", "AuthorizedModelInput", "EgressGrant")
        if hasattr(runtime, public_name)
    )
    probe = probe_unavailable_module_carrier(
        module_name="action_plane",
        request="ActionPlane.prepare(send_message, conversation-b, digest-b)",
        unavailable_code="action_not_available",
    )

    assert probe.request == (
        "ActionPlane.prepare(send_message, conversation-b, digest-b)"
    )
    assert (probe.external_status, probe.external_code) == (
        404,
        "action_not_available",
    )
    assert probe.carrier_available is False
    assert probe.ticket_created is False
    assert probe.sender_call_count == 0
    assert probe.effect_count == 0
    assert importlib.util.find_spec("bot_delivery") is None
    assert forbidden_runtime_authorities == ()

    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-012",
        unauthorized_evidence_count=len(forbidden_runtime_authorities),
        wrong_organization_effect_count=probe.effect_count,
        missing_context_fallback_count=int(
            probe.external_status != 404
            or probe.external_code != "action_not_available"
            or probe.ticket_created
            or probe.sender_call_count != 0
        ),
    )
