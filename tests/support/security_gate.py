"""Explicit hard-oracle observations emitted by registered security fixtures."""

from __future__ import annotations

from collections.abc import Callable


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
