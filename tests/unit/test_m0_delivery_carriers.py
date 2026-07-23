from __future__ import annotations

import importlib.util

import engine.runtime as runtime


def test_m0_egress_carrier_is_unavailable_before_model_or_sender_bytes() -> None:
    """The grant tracer activates without a real provider or effect process."""

    assert importlib.util.find_spec("bot_delivery") is not None
    assert importlib.util.find_spec("action_plane") is None
    for public_name in (
        "ActionPlane",
        "ModelGateway",
        "Sender",
    ):
        assert not hasattr(runtime, public_name)
    assert hasattr(runtime, "EgressGrant")
