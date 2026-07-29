from __future__ import annotations

import pytest

from engine.learning.eval_report import (
    CaseSecurityObservation,
    EvaluationGateStatuses,
    HarnessSecurityEvent,
    SecurityEventKind,
    SecurityHarness,
    SecurityObservationState,
    final_report_status,
    security_report,
)


def _passing_gates() -> EvaluationGateStatuses:
    return EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pass",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="tracked",
    )


@pytest.mark.parametrize(
    "kind",
    (
        "unauthorized_evidence",
        "wrong_organization_effect",
        "missing_context_fallback",
    ),
)
def test_one_harness_observed_violation_forces_entire_report_to_fail(
    kind: SecurityEventKind,
) -> None:
    harness = SecurityHarness()
    clean = harness.observe("synthetic-safe", ())
    unsafe = harness.observe(
        "synthetic-unsafe",
        (HarnessSecurityEvent(kind, f"synthetic-{kind}-event"),),
    )

    assert final_report_status(_passing_gates(), (clean, unsafe)) == "FAIL"
    rendered = security_report((clean, unsafe))
    assert rendered["status"] == "fail"
    assert rendered[
        {
            "missing_context_fallback": "missingContextFallbackCount",
            "unauthorized_evidence": "unauthorizedEvidenceCount",
            "wrong_organization_effect": "wrongOrganizationEffectCount",
        }[kind]
    ] == 1


def test_only_harness_observed_zero_satisfies_the_security_precondition() -> None:
    harness = SecurityHarness()
    observed_clean = harness.observe("synthetic-clean", ())

    assert type(observed_clean) is CaseSecurityObservation
    assert observed_clean.state is SecurityObservationState.OBSERVED_CLEAN
    assert final_report_status(_passing_gates(), (observed_clean,)) == "PASS"


@pytest.mark.parametrize(
    ("observation", "expected_state"),
    (
        ("not_observed", SecurityObservationState.NOT_OBSERVED),
        ("malformed", SecurityObservationState.MALFORMED),
    ),
)
def test_unestablished_security_precondition_is_refused_as_a_typed_state(
    observation: str,
    expected_state: SecurityObservationState,
) -> None:
    harness = SecurityHarness()
    result = (
        harness.not_observed("synthetic-case")
        if observation == "not_observed"
        else harness.malformed("synthetic-case")
    )

    assert result.state is expected_state
    assert final_report_status(_passing_gates(), (result,)) == "REFUSED"


def test_security_observation_has_exactly_the_closed_adjudicated_states() -> None:
    assert set(SecurityObservationState) == {
        SecurityObservationState.OBSERVED_CLEAN,
        SecurityObservationState.NOT_OBSERVED,
        SecurityObservationState.MALFORMED,
    }


def test_callers_cannot_construct_clean_counts_or_observations_directly() -> None:
    with pytest.raises(TypeError, match="harness-constructed"):
        CaseSecurityObservation(
            case_ref="synthetic-forged-clean",
            state=SecurityObservationState.OBSERVED_CLEAN,
        )


def test_pending_gate_propagates_without_becoming_a_numeric_pass() -> None:
    harness = SecurityHarness()
    pending = EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pending_preregistration",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="tracked",
    )

    assert final_report_status(
        pending,
        (harness.observe("synthetic-clean", ()),),
    ) == "PENDING_PREREGISTRATION"


def test_nontracked_thresholds_can_never_render_an_authoritative_pass() -> None:
    harness = SecurityHarness()
    non_authoritative = EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pass",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="non_authoritative",
    )

    assert final_report_status(
        non_authoritative,
        (harness.observe("synthetic-clean", ()),),
    ) == "NON_AUTHORITATIVE"
