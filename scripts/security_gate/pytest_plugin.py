"""Small pytest plugin that emits deterministic raw M0 gate evidence."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _pytest.config.argparsing import Parser
from _pytest.reports import CollectReport, TestReport

from scripts.security_gate.artifacts import atomic_write_json
from scripts.security_gate.observations import (
    OBSERVATION_PROPERTY,
    SECURITY_EVIDENCE_MARKER,
    SECURITY_EVIDENCE_MARKER_VERSION,
    ObservationValidationError,
    SecurityEvidenceMarkerValidationError,
    normalize_fixture_observation,
    normalize_security_evidence_marker,
)

RAW_PATH_ENVIRONMENT = "CONTEXT_ENGINE_SECURITY_GATE_RAW_PATH"
RAW_EXECUTION_ID_ENVIRONMENT = "CONTEXT_ENGINE_SECURITY_GATE_EXECUTION_ID"
RAW_EVIDENCE_VERSION = "1.0.0"


class _ConfigLike(Protocol):
    _m0_security_gate_recorder: RawEvidenceRecorder


_ACTIVE_RECORDER: RawEvidenceRecorder | None = None


def _report_outcome(report: TestReport) -> str:
    if report.skipped:
        if hasattr(report, "wasxfail"):
            return "xfailed"
        return "skipped"
    if report.passed and hasattr(report, "wasxfail"):
        return "xpassed"
    if report.failed:
        return "failed" if report.when == "call" else "error"
    return "passed"


class RawEvidenceRecorder:
    """Accumulate collection, phase, and explicit observation facts."""

    def __init__(
        self,
        output_path: Path,
        *,
        runner_execution_id: str | None,
        selected_selectors: Sequence[str],
    ) -> None:
        self.output_path = output_path
        self.runner_execution_id = runner_execution_id
        self.selected_selectors = list(selected_selectors)
        self.collected_node_ids: list[str] = []
        self.collected_tests: list[dict[str, object]] = []
        self.collection_errors: list[dict[str, str]] = []
        self._reports: dict[str, dict[str, object]] = {}

    def collect(self, items: Sequence[pytest.Item]) -> None:
        self.collected_node_ids = [item.nodeid for item in items]
        collected_tests: list[dict[str, object]] = []
        for item in items:
            declarations: list[dict[str, str]] = []
            errors: list[str] = []
            seen: set[tuple[str, str]] = set()
            for marker in item.iter_markers(name=SECURITY_EVIDENCE_MARKER):
                try:
                    declaration = normalize_security_evidence_marker(
                        marker.args, marker.kwargs
                    )
                except SecurityEvidenceMarkerValidationError as error:
                    errors.append(str(error))
                    continue
                identity = (declaration["id"], declaration["layer"])
                if identity in seen:
                    errors.append(
                        "duplicate security_evidence marker: "
                        f"{declaration['id']} {declaration['layer']}"
                    )
                    continue
                seen.add(identity)
                declarations.append(declaration)
            collected_tests.append(
                {
                    "nodeId": item.nodeid,
                    "securityEvidence": sorted(
                        declarations, key=lambda value: (value["id"], value["layer"])
                    ),
                    "securityEvidenceErrors": errors,
                }
            )
        self.collected_tests = collected_tests

    def collect_report(self, report: CollectReport) -> None:
        if report.failed:
            self.collection_errors.append(
                {"nodeId": report.nodeid, "message": str(report.longrepr)}
            )

    def test_report(self, report: TestReport) -> None:
        record = self._reports.setdefault(
            report.nodeid,
            {
                "nodeId": report.nodeid,
                "outcome": "incomplete",
                "phases": [],
                "observations": [],
                "observationErrors": [],
            },
        )
        phases = cast(list[dict[str, object]], record["phases"])
        phases.append(
            {
                "phase": report.when,
                "outcome": _report_outcome(report),
                "durationSeconds": report.duration,
            }
        )
        outcome = _report_outcome(report)
        current = cast(str, record["outcome"])
        precedence = {
            "incomplete": 0,
            "passed": 1,
            "xpassed": 2,
            "xfailed": 3,
            "skipped": 4,
            "failed": 5,
            "error": 6,
        }
        if precedence[outcome] >= precedence[current]:
            record["outcome"] = outcome
        if report.when != "call":
            return
        observations = cast(list[dict[str, object]], record["observations"])
        errors = cast(list[str], record["observationErrors"])
        for key, value in report.user_properties:
            if key != OBSERVATION_PROPERTY:
                continue
            try:
                observations.append(normalize_fixture_observation(value))
            except ObservationValidationError as error:
                errors.append(str(error))

    def document(self, exit_code: int) -> dict[str, object]:
        return {
            "rawEvidenceVersion": RAW_EVIDENCE_VERSION,
            "rawProducer": "scripts.security_gate.pytest_plugin",
            "runnerExecutionId": self.runner_execution_id,
            "pytest": {
                "exitCode": exit_code,
                "selectedSelectors": self.selected_selectors,
                "securityEvidenceMarkerVersion": SECURITY_EVIDENCE_MARKER_VERSION,
                "collectedNodeIds": self.collected_node_ids,
                "collectedTests": self.collected_tests,
                "collectionErrors": sorted(
                    self.collection_errors,
                    key=lambda item: (item["nodeId"], item["message"]),
                ),
                "tests": [self._reports[node_id] for node_id in sorted(self._reports)],
            },
        }

    def write(self, exit_code: int) -> None:
        atomic_write_json(self.output_path, self.document(exit_code))


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("context-engine-security-gate")
    group.addoption(
        "--security-gate-raw",
        action="store",
        dest="security_gate_raw",
        default=None,
        help="write raw M0 security-gate evidence to this JSON path",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    global _ACTIVE_RECORDER
    configured = config.getoption("security_gate_raw")
    environment = os.environ.get(RAW_PATH_ENVIRONMENT)
    raw_path = configured or environment
    if raw_path is None:
        return
    config.addinivalue_line(
        "markers",
        "security_evidence(id, layer): versioned execution-owned M0 evidence identity",
    )
    recorder = RawEvidenceRecorder(
        Path(raw_path),
        runner_execution_id=os.environ.get(RAW_EXECUTION_ID_ENVIRONMENT),
        selected_selectors=config.args,
    )
    cast(_ConfigLike, config)._m0_security_gate_recorder = recorder
    _ACTIVE_RECORDER = recorder


def _recorder(config: pytest.Config) -> RawEvidenceRecorder | None:
    value: Any = getattr(config, "_m0_security_gate_recorder", None)
    return value if isinstance(value, RawEvidenceRecorder) else None


def pytest_collection_finish(session: pytest.Session) -> None:
    recorder = _recorder(session.config)
    if recorder is not None:
        recorder.collect(session.items)


def pytest_collectreport(report: CollectReport) -> None:
    if _ACTIVE_RECORDER is not None:
        _ACTIVE_RECORDER.collect_report(report)


def pytest_runtest_logreport(report: TestReport) -> None:
    if _ACTIVE_RECORDER is not None:
        _ACTIVE_RECORDER.test_report(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    recorder = _recorder(session.config)
    if recorder is not None:
        recorder.write(exitstatus)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _ACTIVE_RECORDER
    if _recorder(config) is _ACTIVE_RECORDER:
        _ACTIVE_RECORDER = None
