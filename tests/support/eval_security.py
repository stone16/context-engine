"""Test-private factory for evaluation security veto/status oracles."""

from engine.learning.eval_report import (
    _SECURITY_HARNESS_SEAL,
    CaseSecurityObservation,
    CaseSecurityResult,
    CaseSecurityViolation,
    HarnessSecurityEvent,
    SecurityEventKind,
    SecurityObservationState,
    _case_ref,
)


def harness_security_result(
    case_ref: str,
    *events: tuple[SecurityEventKind, str],
) -> CaseSecurityResult:
    """Build synthetic harness output solely for test-tree assertions."""

    if not events:
        clean = object.__new__(CaseSecurityObservation)
        object.__setattr__(clean, "case_ref", _case_ref(case_ref))
        object.__setattr__(
            clean,
            "state",
            SecurityObservationState.OBSERVED_CLEAN,
        )
        object.__setattr__(clean, "_seal", _SECURITY_HARNESS_SEAL)
        return clean
    observations = tuple(HarnessSecurityEvent(*event) for event in events)
    refs = tuple(event.observation_ref for event in observations)
    if len(refs) != len(set(refs)):
        raise ValueError("synthetic security observation refs must be unique")
    violation = object.__new__(CaseSecurityViolation)
    object.__setattr__(violation, "case_ref", _case_ref(case_ref))
    object.__setattr__(violation, "events", observations)
    object.__setattr__(violation, "_seal", _SECURITY_HARNESS_SEAL)
    return violation
