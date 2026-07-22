"""Explicit hard-oracle observations emitted by registered security fixtures."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnavailableCarrierProbe:
    """Observed result of attempting one explicitly absent M0 carrier."""

    request: str
    external_status: int
    external_code: str
    carrier_available: bool
    ticket_created: bool
    sender_call_count: int
    effect_count: int


def probe_unavailable_module_carrier(
    *, module_name: str, request: str, unavailable_code: str
) -> UnavailableCarrierProbe:
    """Attempt an M0 carrier and normalize only its exact module absence."""

    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return UnavailableCarrierProbe(
            request=request,
            external_status=404,
            external_code=unavailable_code,
            carrier_available=False,
            ticket_created=False,
            sender_call_count=0,
            effect_count=0,
        )
    return UnavailableCarrierProbe(
        request=request,
        external_status=500,
        external_code="unexpected_active_carrier",
        carrier_available=True,
        ticket_created=False,
        sender_call_count=0,
        effect_count=0,
    )


def record_security_oracles(
    record_property: Callable[[str, object], None],
    *,
    fixture_ref: str,
    unauthorized_evidence_count: int,
    wrong_organization_effect_count: int,
    missing_context_fallback_count: int,
) -> None:
    """Record three counts computed from one fixture's asserted outputs."""

    from scripts.security_gate.observations import record_fixture_observation

    record_fixture_observation(
        record_property,
        fixture_ref=fixture_ref,
        evidence_ref=f"FIXTURE-{fixture_ref}",
        unauthorized_evidence_count=unauthorized_evidence_count,
        wrong_organization_effect_count=wrong_organization_effect_count,
        missing_context_fallback_count=missing_context_fallback_count,
    )
