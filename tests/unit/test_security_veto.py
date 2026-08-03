from __future__ import annotations

import importlib
import inspect
import pkgutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
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
        repository_root / source
        for source in _production_sources()
        if (
            "tests.support.eval_security"
            in (repository_root / source).read_text(encoding="utf-8")
            or "harness_security_result"
            in (repository_root / source).read_text(encoding="utf-8")
        )
    ]

    assert importers == []


_NON_PRODUCTION_TREES = frozenset({"tests", "third_party"})
# ``third_party`` is license/SBOM-governed vendored source, not first-party
# production composition; the test-private import veto scans every first-party tree.
_SECURITY_RESULT_TYPES = ("CaseSecurityObservation", "CaseSecurityViolation")


def _production_sources() -> tuple[str, ...]:
    repository_root = Path(__file__).resolve().parents[2]
    tracked_python_sources = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    sources = tuple(
        sorted(
            path.as_posix()
            for raw_path in tracked_python_sources
            if raw_path
            for path in (Path(raw_path.decode("utf-8")),)
            if not (_NON_PRODUCTION_TREES & set(path.parts))
            if (repository_root / path).is_file()
        )
    )
    assert len(sources) > 100
    return sources


def test_production_source_scan_excludes_untracked_python_files() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    with tempfile.NamedTemporaryFile(
        dir=repository_root,
        prefix="security-veto-untracked-",
        suffix=".py",
    ) as untracked_source:
        source = Path(untracked_source.name).relative_to(repository_root).as_posix()
        assert source not in _production_sources()


def test_production_source_scan_skips_tracked_files_missing_from_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    temporary_index = tmp_path / "index"
    missing_source = "engine/security_veto_deleted_probe.py"
    monkeypatch.setenv("GIT_INDEX_FILE", str(temporary_index))
    subprocess.run(
        ["git", "read-tree", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    tracked_blob = subprocess.run(
        ["git", "rev-parse", "HEAD:engine/__init__.py"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{tracked_blob},{missing_source}",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )

    assert _production_sources_containing("security-veto-deleted-path-probe") == []


def _production_sources_containing(*markers: str) -> list[str]:
    repository_root = Path(__file__).resolve().parents[2]
    return [
        source
        for source in _production_sources()
        if any(
            marker in (repository_root / source).read_text(encoding="utf-8")
            for marker in markers
        )
    ]


def _production_modules() -> tuple[ModuleType, ...]:
    modules: list[ModuleType] = []
    for package_name in ("applications", "engine.learning", "eval"):
        package = importlib.import_module(package_name)
        modules.append(package)
        modules.extend(
            importlib.import_module(f"{package_name}.{info.name}")
            for info in pkgutil.iter_modules(list(package.__path__))
            if not info.name.startswith("_")
        )
    return tuple(modules)


def _public_callables(
    module: ModuleType,
) -> tuple[tuple[str, Callable[..., object]], ...]:
    return tuple(
        (name, cast(Callable[..., object], value))
        for name, value in vars(module).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == module.__name__
    )


def test_no_importable_production_path_constructs_a_clean_security_result() -> None:
    producers = [
        f"{module.__name__}.{name}"
        for module in _production_modules()
        for name, value in _public_callables(module)
        if any(
            marker in str(getattr(value, "__annotations__", {}).get("return", ""))
            for marker in (*_SECURITY_RESULT_TYPES, "CaseSecurityResult")
        )
    ]

    assert producers == ["engine.learning.eval_report.refused_security_observation"]
    assert _production_sources_containing("_CaseSecurityViolationInput(") == [
        "applications/eval_executor.py"
    ]
    assert _production_sources_containing(
        "state=SecurityObservationState.OBSERVED_CLEAN"
    ) == ["applications/eval_executor.py"]
    assert _production_sources_containing(
        "_CaseSecurityObservationInput",
        "_CaseSecurityViolationInput",
    ) == ["applications/eval_executor.py", "engine/learning/eval_report.py"]


def test_the_run_executor_admits_no_caller_supplied_seam_counter_or_result() -> None:
    executor = importlib.import_module("applications.eval_executor")
    entry = inspect.signature(executor.execute_evaluation_report)

    assert [
        (name, str(parameter.annotation))
        for name, parameter in entry.parameters.items()
    ] == [
        ("golden_set", "GoldenSet"),
        ("judgments", "AnswerJudgments"),
        ("thresholds", "EvaluationThresholds"),
        ("generated_at", "datetime"),
    ]
    for name, value in _public_callables(executor):
        annotations = [
            str(parameter.annotation)
            for parameter in inspect.signature(value).parameters.values()
        ]
        assert not [
            annotation
            for annotation in annotations
            if any(
                marker in annotation
                for marker in (
                    "Callable",
                    "Caller",
                    "Client",
                    "Protocol",
                    *_SECURITY_RESULT_TYPES,
                )
            )
        ], name


def test_the_run_executor_acquires_no_release_publication_authority() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source = (repository_root / "applications/eval_executor.py").read_text(
        encoding="utf-8"
    )
    executor = importlib.import_module("applications.eval_executor")

    for forbidden in (
        "ContextLearning",
        "ReleaseCandidate",
        "ReleaseManifest",
        "promote",
        "promotion",
        "rollback",
    ):
        assert forbidden not in source
    assert not [
        name
        for name, value in vars(executor).items()
        if "promotion" in str(getattr(value, "__module__", ""))
        or "release" in str(getattr(value, "__module__", ""))
    ]
