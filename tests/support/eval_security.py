"""Test-private factory for evaluation security veto/status oracles."""

from engine.learning.eval_report import (
    CaseSecurityObservation,
    CaseSecurityResult,
    CaseSecurityViolation,
    HarnessSecurityEvent,
    SecurityEventKind,
    SecurityObservationState,
    _case_ref,
    _CaseSecurityObservationInput,
    _CaseSecurityViolationInput,
)


def harness_security_result(
    case_ref: str,
    *events: tuple[SecurityEventKind, str],
) -> CaseSecurityResult:
    """Build synthetic harness output solely for test-tree assertions."""

    if not events:
        return CaseSecurityObservation(
            _CaseSecurityObservationInput(
                case_ref=_case_ref(case_ref),
                state=SecurityObservationState.OBSERVED_CLEAN,
            )
        )
    observations = tuple(HarnessSecurityEvent(*event) for event in events)
    return CaseSecurityViolation(
        _CaseSecurityViolationInput(
            case_ref=_case_ref(case_ref),
            events=observations,
        )
    )
