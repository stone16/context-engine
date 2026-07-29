from __future__ import annotations

from pathlib import Path

import pytest

import engine.learning.eval_report as eval_report
from engine.learning.eval_report import (
    CaseSecurityObservation,
    EvaluationGateStatuses,
    SecurityEventKind,
    SecurityObservationState,
    final_report_status,
    refused_security_observation,
    security_report,
)
from tests.support.eval_security import harness_security_result


def _passing_gates() -> EvaluationGateStatuses:
    return EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pass",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="tracked",
    )


def test_caller_cannot_mint_clean_security_without_an_executed_run() -> None:
    assert not hasattr(eval_report, "SecurityHarness")
    assert not hasattr(eval_report, "execute_security_case")
    assert not hasattr(eval_report, "_observation")
    with pytest.raises(TypeError, match="harness-constructed"):
        CaseSecurityObservation(
            case_ref="synthetic-forged-clean",
            state=SecurityObservationState.OBSERVED_CLEAN,
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
    clean = harness_security_result("synthetic-safe")
    unsafe = harness_security_result(
        "synthetic-unsafe",
        (kind, f"synthetic-{kind}-event"),
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
    observed_clean = harness_security_result("synthetic-clean")

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
    result = (
        refused_security_observation(
            "synthetic-case", SecurityObservationState.NOT_OBSERVED
        )
        if observation == "not_observed"
        else refused_security_observation(
            "synthetic-case", SecurityObservationState.MALFORMED
        )
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
    pending = EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pending_preregistration",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="tracked",
    )

    assert final_report_status(
        pending,
        (harness_security_result("synthetic-clean"),),
    ) == "PENDING_PREREGISTRATION"


def test_nontracked_thresholds_can_never_render_an_authoritative_pass() -> None:
    non_authoritative = EvaluationGateStatuses(
        retrieval="measured",
        citation="pass",
        answer="pass",
        slice_statuses=("pass", "pass", "pass"),
        threshold_authority="non_authoritative",
    )

    assert final_report_status(
        non_authoritative,
        (harness_security_result("synthetic-clean"),),
    ) == "NON_AUTHORITATIVE"


def test_test_private_security_factory_is_never_imported_by_production() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    production_roots = (
        repository_root / "adapters",
        repository_root / "applications",
        repository_root / "engine",
    )

    importers = [
        path
        for root in production_roots
        for path in root.rglob("*.py")
        if "tests.support.eval_security" in path.read_text(encoding="utf-8")
    ]

    assert importers == []
