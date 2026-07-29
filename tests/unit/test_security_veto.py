from __future__ import annotations

import importlib
from pathlib import Path
from typing import cast

import pytest

import engine.learning.eval_report as eval_report
from engine.learning.eval_report import (
    CaseSecurityObservation,
    EvaluationGateStatuses,
    SecurityEventKind,
    SecurityObservationState,
    _CaseSecurityObservationInput,
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
        CaseSecurityObservation(cast(_CaseSecurityObservationInput, object()))


def test_security_result_types_cannot_be_subclassed() -> None:
    with pytest.raises(TypeError, match="must not be subclassed"):

        class _ForgedObservation(CaseSecurityObservation):
            pass

    with pytest.raises(TypeError, match="must not be subclassed"):

        class _ForgedViolation(eval_report.CaseSecurityViolation):
            pass


def test_evaluation_authority_types_are_not_package_exports() -> None:
    learning_package = importlib.import_module("engine.learning")
    evaluation_package = importlib.import_module("eval")

    for package in (learning_package, evaluation_package):
        for name in (
            "CaseSecurityObservation",
            "CaseSecurityViolation",
            "EvaluationThresholds",
            "_SECURITY_HARNESS_SEAL",
            "_THRESHOLD_LOADER_SEAL",
            "_CaseSecurityObservationInput",
            "_CaseSecurityViolationInput",
            "_LoadedThresholdConfiguration",
        ):
            assert not hasattr(package, name)


def test_evaluation_docs_scope_seals_to_supported_paths_and_m1_threat_model() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    for path in (
        repository_root / "eval/README.md",
        repository_root
        / "docs/decisions/0080-refuse-authoritative-evaluation-without-an-executor.md",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "accident and misuse" in text
        assert "supported path" in text
        assert "in-process adversary" in text
        assert "single trusted local operator" in text


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
        CaseSecurityObservation(cast(_CaseSecurityObservationInput, object()))


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
    importers = [
        path
        for path in repository_root.rglob("*.py")
        if "tests" not in path.relative_to(repository_root).parts
        and (
            "tests.support.eval_security" in path.read_text(encoding="utf-8")
            or "harness_security_result" in path.read_text(encoding="utf-8")
        )
    ]

    assert importers == []
