from __future__ import annotations

import importlib.util

import pytest

import engine.runtime as runtime


@pytest.mark.security_evidence(id="RUNTIME-EGRESS-011", layer="runtime")
def test_m0_egress_carrier_is_unavailable_before_model_or_sender_bytes() -> None:
    """M0 cannot construct the future delivery/action types or trusted process."""

    assert importlib.util.find_spec("bot_delivery") is None
    assert importlib.util.find_spec("action_plane") is None
    for public_name in (
        "ActionPlane",
        "AuthorizedModelInput",
        "EgressGrant",
        "ModelGateway",
        "Sender",
    ):
        assert not hasattr(runtime, public_name)
