from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.run_m0_security_gate import main as security_gate_main
from scripts.security_gate.observations import (
    OBSERVATION_PROPERTY,
    ObservationValidationError,
    normalize_fixture_observation,
    record_fixture_observation,
)
from scripts.security_gate.report import (
    build_release_gate_report,
    canonical_digest,
    reconcile_execution,
)
from scripts.security_gate.runner import (
    DatabaseEnvironmentError,
    GatePaths,
    GateRunError,
    _git_state,
    _provenance,
    build_pytest_command,
    deduplicate_selectors,
    load_database_environment,
    run_gate,
)
from scripts.validate_security_catalog import CatalogValidationError

ORACLE_KEYS = (
    "unauthorizedEvidenceCount",
    "wrongOrganizationEffectCount",
    "missingContextFallbackCount",
)


def complete_provenance(*, commit: str = "a" * 40) -> dict[str, object]:
    digest = "b" * 64
    return {
        "runnerVersion": "1.0.0",
        "commit": commit,
        "trackedDirty": False,
        "trackedDiffDigest": digest,
        "stagedDiffDigest": digest,
        "unstagedDiffDigest": digest,
        "untrackedContentDigest": digest,
        "untrackedFileCount": 0,
        "contentStateDirty": False,
        "contentStateDigest": digest,
        "executionCommand": ["python", "-m", "pytest", "registered-selector"],
        "catalogDigest": digest,
        "catalogSchemaDigest": digest,
        "executionRegistryDigest": digest,
        "executionRegistrySchemaDigest": digest,
        "fixtureDigest": digest,
        "schemaManifestDigest": digest,
        "migrationStateDigest": digest,
        "configurationDigest": digest,
        "alembicHead": "head-one",
        "liveDatabaseRevision": "head-one",
    }


def registry() -> dict[str, Any]:
    evidence = [
        {
            "id": "PROP-001",
            "layer": "property",
            "selector": "tests/test_gate_sample.py::test_property",
        },
        {
            "id": "PG-001",
            "layer": "postgres",
            "selector": "tests/test_gate_sample.py::test_postgres",
        },
        {
            "id": "RUN-001",
            "layer": "runtime",
            "selector": "tests/test_gate_sample.py::test_runtime",
        },
        {
            "id": "FIXTURE-ACCEPT-001",
            "layer": "runtime",
            "selector": "tests/test_gate_sample.py::test_fixture",
        },
    ]
    adapters = []
    hard_oracle_evidence_refs: dict[str, list[str]] = {}
    oracle_names = (
        "Unauthorized Evidence",
        "wrong-Organization effect",
        "missing-context fallback",
    )
    for oracle_ref, result_key in zip(oracle_names, ORACLE_KEYS, strict=True):
        hard_oracle_evidence_refs[result_key] = ["FIXTURE-ACCEPT-001"]
        adapters.append(
            {
                "oracleRef": oracle_ref,
                "resultKey": result_key,
                "assertion": "equals",
                "requiredValue": 0,
                "observation": {
                    "source": "pytest-user-property",
                    "evidenceRefs": ["FIXTURE-ACCEPT-001"],
                    "reducer": "sum",
                },
            }
        )
    return {
        "registryVersion": "1.0.0",
        "catalog": {
            "path": "eval/catalogs/security-invariants.yaml",
            "schemaPath": "eval/catalogs/security-catalog.schema.json",
            "catalogVersion": "1.1.0",
        },
        "execution": {
            "framework": "pytest",
            "forbidOutcomes": [
                "failed",
                "error",
                "skipped",
                "xfailed",
                "xpassed",
                "incomplete",
            ],
            "evidenceLayers": ["property", "postgres", "runtime"],
            "observationProperty": OBSERVATION_PROPERTY,
        },
        "hardOracleAdapters": adapters,
        "evidence": evidence,
        "invariantMappings": [
            {
                "invariantRef": "TENANT-OWNERSHIP-001",
                "evidenceRefs": {
                    "property": ["PROP-001"],
                    "postgres": ["PG-001"],
                    "runtime": ["RUN-001"],
                },
            }
        ],
        "fixtureMappings": [
            {
                "fixtureRef": "ACCEPT-001",
                "carrierStatusAtM0": "available",
                "m0Expectation": "active_fail_closed",
                "evidenceRefs": ["FIXTURE-ACCEPT-001"],
                "hardOracleEvidenceRefs": hard_oracle_evidence_refs,
            }
        ],
    }


def raw_execution(*, fixture_value: int = 0) -> dict[str, Any]:
    evidence = registry()["evidence"]
    selectors = [entry["selector"] for entry in evidence]
    evidence_by_selector: dict[str, list[dict[str, str]]] = {}
    for entry in evidence:
        evidence_by_selector.setdefault(entry["selector"], []).append(
            {"id": entry["id"], "layer": entry["layer"]}
        )
    tests = []
    for selector in selectors:
        observations: list[dict[str, object]] = []
        if selector.endswith("test_fixture"):
            observations.append(
                {
                    "fixtureRef": "ACCEPT-001",
                    "evidenceRef": "FIXTURE-ACCEPT-001",
                    "values": {
                        "unauthorizedEvidenceCount": fixture_value,
                        "wrongOrganizationEffectCount": 0,
                        "missingContextFallbackCount": 0,
                    },
                }
            )
        tests.append(
            {
                "nodeId": selector,
                "outcome": "passed",
                "observations": observations,
                "observationErrors": [],
            }
        )
    return {
        "rawEvidenceVersion": "1.0.0",
        "pytest": {
            "exitCode": 0,
            "securityEvidenceMarkerVersion": "1.0.0",
            "selectedSelectors": list(selectors),
            "collectedNodeIds": list(selectors),
            "collectedTests": [
                {
                    "nodeId": selector,
                    "securityEvidence": evidence_by_selector[selector],
                    "securityEvidenceErrors": [],
                }
                for selector in selectors
            ],
            "collectionErrors": [],
            "tests": tests,
        },
    }


def test_observation_helper_emits_one_closed_versioned_property() -> None:
    properties: list[tuple[str, object]] = []

    record_fixture_observation(
        lambda key, value: properties.append((key, value)),
        fixture_ref="ACCEPT-001",
        evidence_ref="FIXTURE-ACCEPT-001",
        unauthorized_evidence_count=0,
        wrong_organization_effect_count=0,
        missing_context_fallback_count=0,
    )

    assert properties == [
        (
            OBSERVATION_PROPERTY,
            {
                "fixtureRef": "ACCEPT-001",
                "evidenceRef": "FIXTURE-ACCEPT-001",
                "values": {
                    "unauthorizedEvidenceCount": 0,
                    "wrongOrganizationEffectCount": 0,
                    "missingContextFallbackCount": 0,
                },
            },
        )
    ]
    assert normalize_fixture_observation(properties[0][1]) == properties[0][1]


@pytest.mark.parametrize("invalid", [-1, True, 1.0, "0"])
def test_observation_helper_rejects_noncanonical_counts(invalid: object) -> None:
    with pytest.raises(ObservationValidationError):
        record_fixture_observation(
            lambda _key, _value: None,
            fixture_ref="ACCEPT-001",
            evidence_ref="FIXTURE-ACCEPT-001",
            unauthorized_evidence_count=invalid,  # type: ignore[arg-type]
            wrong_organization_effect_count=0,
            missing_context_fallback_count=0,
        )


def test_selectors_are_deduplicated_but_pytest_is_invoked_once_exactly() -> None:
    document = registry()
    document["evidence"].append(
        {
            "id": "SHARED-001",
            "layer": "runtime",
            "selector": "tests/test_gate_sample.py::test_runtime",
        }
    )

    selectors = deduplicate_selectors(document)
    command = build_pytest_command(
        selectors,
        raw_path=Path("artifacts/raw-evidence.json"),
        python_executable="python-under-test",
    )

    assert selectors.count("tests/test_gate_sample.py::test_runtime") == 1
    assert command[:4] == (
        "python-under-test",
        "-m",
        "pytest",
        "-p",
    )
    assert command.count("tests/test_gate_sample.py::test_runtime") == 1
    assert "--reruns" not in command
    assert command.count("pytest") == 1


def test_reconciliation_passes_only_with_tests_and_explicit_zero_observations() -> None:
    result = reconcile_execution(registry(), raw_execution())
    invariants = cast(list[dict[str, Any]], result["invariants"])
    fixtures = cast(list[dict[str, Any]], result["fixtures"])
    hard_oracles = cast(list[dict[str, Any]], result["hardOracles"])

    assert result["passed"] is True
    assert result["failures"] == []
    assert invariants[0]["status"] == "PASS"
    assert fixtures[0]["status"] == "PASS"
    assert [entry["observedValue"] for entry in hard_oracles] == [
        0,
        0,
        0,
    ]


@pytest.mark.parametrize(
    ("mutator", "failure_fragment"),
    [
        (
            lambda raw: raw["pytest"]["tests"][0].update(outcome="skipped"),
            "forbidden outcome",
        ),
        (
            lambda raw: raw["pytest"]["tests"][-1].update(observations=[]),
            "missing observation",
        ),
        (
            lambda raw: raw["pytest"]["tests"][-1]["observations"].append(
                copy.deepcopy(raw["pytest"]["tests"][-1]["observations"][0])
            ),
            "duplicate observation",
        ),
        (
            lambda raw: raw["pytest"]["tests"][-1]["observations"][0].update(
                fixtureRef="ACCEPT-999"
            ),
            "orphan observation",
        ),
    ],
)
def test_reconciliation_rejects_nonexecuted_or_unreconciled_evidence(
    mutator: Callable[[dict[str, Any]], None], failure_fragment: str
) -> None:
    raw = raw_execution()
    mutator(raw)

    result = reconcile_execution(registry(), raw)

    assert result["passed"] is False
    failures = cast(list[str], result["failures"])
    assert any(failure_fragment in failure for failure in failures)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: raw["pytest"]["collectedNodeIds"].pop(),
        lambda raw: raw["pytest"]["collectedNodeIds"].append(
            "tests/test_gate_sample.py::test_unreported"
        ),
        lambda raw: raw["pytest"]["collectedNodeIds"].append(
            raw["pytest"]["collectedNodeIds"][0]
        ),
    ],
)
def test_reconciliation_requires_exact_collected_and_reported_node_ids(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    raw = raw_execution()
    mutator(raw)

    result = reconcile_execution(registry(), raw)

    assert result["passed"] is False
    failures = cast(list[str], result["failures"])
    assert any(
        "collected node IDs differ from test results" in item for item in failures
    )


@pytest.mark.parametrize(
    ("mutator", "failure_fragment"),
    [
        (
            lambda raw: raw["pytest"]["collectedTests"][0].update(
                securityEvidence=[]
            ),
            "missing security_evidence marker",
        ),
        (
            lambda raw: raw["pytest"]["collectedTests"][0][
                "securityEvidence"
            ].append(
                copy.deepcopy(
                    raw["pytest"]["collectedTests"][0]["securityEvidence"][0]
                )
            ),
            "duplicate security_evidence marker",
        ),
        (
            lambda raw: raw["pytest"]["collectedTests"][0][
                "securityEvidence"
            ][0].update(layer="runtime"),
            "wrong security_evidence layer",
        ),
        (
            lambda raw: raw["pytest"]["collectedTests"][0][
                "securityEvidenceErrors"
            ].append("marker must declare exactly id and layer"),
            "malformed security_evidence marker",
        ),
    ],
)
def test_reconciliation_requires_execution_owned_evidence_identity(
    mutator: Callable[[dict[str, Any]], None], failure_fragment: str
) -> None:
    raw = raw_execution()
    mutator(raw)

    result = reconcile_execution(registry(), raw)

    assert result["passed"] is False
    failures = cast(list[str], result["failures"])
    assert any(failure_fragment in item for item in failures)


def test_each_hard_oracle_is_an_independent_security_veto() -> None:
    for result_key in ORACLE_KEYS:
        raw = raw_execution()
        raw["pytest"]["tests"][-1]["observations"][0]["values"][result_key] = 1

        result = reconcile_execution(registry(), raw)

        assert result["passed"] is False
        hard_oracles = cast(list[dict[str, Any]], result["hardOracles"])
        oracle = next(item for item in hard_oracles if item["resultKey"] == result_key)
        assert oracle["status"] == "FAIL"


def test_nonzero_fixture_oracle_fails_fixture_and_catalog_mapped_invariants() -> None:
    raw = raw_execution()
    raw["pytest"]["tests"][-1]["observations"][0]["values"][
        "wrongOrganizationEffectCount"
    ] = 1

    result = reconcile_execution(
        registry(),
        raw,
        catalog={
            "fixtures": [
                {
                    "id": "ACCEPT-001",
                    "invariantRefs": ["TENANT-OWNERSHIP-001"],
                }
            ]
        },
    )
    fixtures = cast(list[dict[str, Any]], result["fixtures"])
    invariants = cast(list[dict[str, Any]], result["invariants"])

    assert fixtures[0]["status"] == "FAIL"
    assert fixtures[0]["failedOracleResultKeys"] == [
        "wrongOrganizationEffectCount"
    ]
    assert invariants[0]["status"] == "FAIL"
    assert invariants[0]["failedFixtureRefs"] == ["ACCEPT-001"]


def test_parameterized_leaves_may_repeat_observations_across_leaves() -> None:
    raw = raw_execution()
    fixture = raw["pytest"]["tests"].pop()
    selector = fixture["nodeId"]
    leaves = []
    for parameter in ("a", "b", "c"):
        leaf = copy.deepcopy(fixture)
        leaf["nodeId"] = f"{selector}[{parameter}]"
        leaves.append(leaf)
    raw["pytest"]["tests"].extend(leaves)
    raw["pytest"]["collectedNodeIds"].remove(selector)
    raw["pytest"]["collectedNodeIds"].extend(leaf["nodeId"] for leaf in leaves)
    collected_fixture = raw["pytest"]["collectedTests"].pop()
    raw["pytest"]["collectedTests"].extend(
        {
            **copy.deepcopy(collected_fixture),
            "nodeId": leaf["nodeId"],
        }
        for leaf in leaves
    )
    raw["pytest"]["selectedSelectors"] = [
        entry for entry in raw["pytest"]["selectedSelectors"] if entry != selector
    ] + [selector]

    result = reconcile_execution(registry(), raw)
    fixtures = cast(list[dict[str, Any]], result["fixtures"])

    assert result["passed"] is True
    assert fixtures[0]["observationCount"] == 3


def test_release_report_keeps_four_gates_independent_and_has_no_score() -> None:
    reconciliation = reconcile_execution(registry(), raw_execution())
    rls = {"passed": True, "coverage": {"numerator": 20, "denominator": 20}}
    report = build_release_gate_report(
        reconciliation=reconciliation,
        rls_audit=rls,
        provenance=complete_provenance(),
        raw_result_digest="b" * 64,
    )

    assert report["m0SecurityDecision"] == "pass"
    assert report["releaseDecision"] == "not-evaluated"
    assert report["promotionReadiness"] == "not-evaluated"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["Security"]["status"] == "pass"
    for gate in ("Reliability", "Quality", "Budget"):
        assert gates[gate]["status"] == "not-evaluated"
    assert "score" not in json.dumps(report).casefold()
    assert canonical_digest(report) == canonical_digest(copy.deepcopy(report))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda provenance: provenance.update(commit="unavailable"),
        lambda provenance: provenance.update(contentStateDigest="unavailable"),
        lambda provenance: provenance.update(liveDatabaseRevision="old-head"),
    ],
)
def test_release_report_fails_closed_when_required_provenance_is_unavailable(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    provenance = complete_provenance()
    mutation(provenance)

    report = build_release_gate_report(
        reconciliation=reconcile_execution(registry(), raw_execution()),
        rls_audit={"passed": True},
        provenance=provenance,
        raw_result_digest="b" * 64,
    )

    assert report["m0SecurityDecision"] == "fail"
    assert report["releaseDecision"] == "fail"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["Security"]["status"] == "fail"
    assert any(
        "provenance" in failure
        for failure in gates["Security"]["failures"]
    )


def test_cli_reports_m0_security_without_claiming_release_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "scripts.run_m0_security_gate.run_gate",
        lambda _paths: {
            "m0SecurityDecision": "pass",
            "releaseDecision": "not-evaluated",
        },
    )

    assert security_gate_main(["--output-dir", "artifacts"]) == 0
    output = capsys.readouterr().out
    assert "M0 SECURITY PASS" in output
    assert "release pass" not in output.casefold()


def test_provenance_has_exact_nonsecret_config_migration_and_fixture_digests(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "migrations/versions").mkdir(parents=True)
    (repository / "eval/catalogs").mkdir(parents=True)
    (repository / "engine/persistence").mkdir(parents=True)
    output = repository / ".context-engine/security-gate"
    for relative, contents in {
        "compose.yaml": "services: {}\n",
        "alembic.ini": "[alembic]\nscript_location = migrations\n",
        "migrations/env.py": "# test\n",
        "migrations/script.py.mako": "# test\n",
        "migrations/versions/a_head.py": (
            "revision = 'head_one'\n"
            "down_revision = None\n"
            "branch_labels = None\n"
            "depends_on = None\n"
        ),
        "eval/catalogs/security-invariants.yaml": "{}\n",
        "eval/catalogs/security-catalog.schema.json": "{}\n",
        "eval/catalogs/m0-security-evidence.yaml": "{}\n",
        "eval/catalogs/m0-security-evidence.schema.json": "{}\n",
        "engine/persistence/schema_security_manifest.yaml": "{}\n",
    }.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    paths = GatePaths(
        repository_root=repository,
        catalog=repository / "eval/catalogs/security-invariants.yaml",
        catalog_schema=repository / "eval/catalogs/security-catalog.schema.json",
        registry=repository / "eval/catalogs/m0-security-evidence.yaml",
        registry_schema=(
            repository / "eval/catalogs/m0-security-evidence.schema.json"
        ),
        manifest=repository / "engine/persistence/schema_security_manifest.yaml",
        database_environment=repository / ".context-engine/database.env",
        output_directory=output,
    )

    provenance = _provenance(
        paths,
        registry(),
        {"fixtures": [{"id": "ACCEPT-001", "secret": "not-a-secret"}]},
        selectors=("tests/test_gate_sample.py::test_fixture",),
        live_database_revision="head_one",
    )

    assert provenance["alembicHead"] == "head_one"
    assert provenance["liveDatabaseRevision"] == "head_one"
    assert provenance["trackedDirty"] == "unavailable"
    assert len(cast(str, provenance["fixtureDigest"])) == 64
    assert len(cast(str, provenance["configurationDigest"])) == 64
    assert "database.env" not in json.dumps(provenance)


def test_git_state_hashes_staged_unstaged_and_untracked_nonignored_content(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "gate@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Gate Test"],
        check=True,
    )
    (repository / ".gitignore").write_text(
        ".context-engine/\n.harness/\n", encoding="utf-8"
    )
    (repository / "tracked.py").write_text("tracked = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    tracked_ignored = repository / ".context-engine/tracked-artifact"
    tracked_ignored.parent.mkdir()
    tracked_ignored.write_text("artifact = 1\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "add",
            "--force",
            ".context-engine/tracked-artifact",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "initial"], check=True
    )
    (repository / "tracked.py").write_text("tracked = 2\n", encoding="utf-8")
    (repository / "staged.py").write_text("staged = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "staged.py"], check=True
    )
    executed = repository / "tests/test_executed.py"
    executed.parent.mkdir()
    executed.write_text("def test_executed(): pass\n", encoding="utf-8")
    ignored = repository / ".context-engine/database.env"
    ignored.parent.mkdir(exist_ok=True)
    ignored.write_text("PASSWORD=first\n", encoding="utf-8")

    before = _git_state(repository)
    executed.write_text("def test_executed(): assert True\n", encoding="utf-8")
    after_untracked_change = _git_state(repository)
    ignored.write_text("PASSWORD=second\n", encoding="utf-8")
    after_ignored_change = _git_state(repository)
    tracked_ignored.write_text("artifact = 2\n", encoding="utf-8")
    after_tracked_ignored_change = _git_state(repository)

    assert before["contentStateDirty"] is True
    assert before["contentStateDigest"] != after_untracked_change["contentStateDigest"]
    assert (
        after_untracked_change["contentStateDigest"]
        == after_ignored_change["contentStateDigest"]
    )
    assert (
        after_ignored_change["contentStateDigest"]
        == after_tracked_ignored_change["contentStateDigest"]
    )
    serialized = json.dumps(after_ignored_change)
    assert "PASSWORD" not in serialized
    assert ".context-engine" not in serialized


def test_database_environment_parser_rejects_duplicates_and_shell_syntax(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "database.env"
    duplicate.write_text("KEY=value\nKEY=other\n", encoding="utf-8")
    duplicate.chmod(0o600)

    with pytest.raises(DatabaseEnvironmentError, match="unexpected variable"):
        load_database_environment(duplicate)

    unsafe = tmp_path / "database-unsafe.env"
    unsafe.write_text("export CONTEXT_ENGINE_RUNTIME_ROLE=value\n", encoding="utf-8")
    unsafe.chmod(0o600)
    with pytest.raises(DatabaseEnvironmentError, match="KEY=value"):
        load_database_environment(unsafe)


def test_runner_failure_overwrites_a_stale_pass_report(tmp_path: Path) -> None:
    output = tmp_path / "artifacts"
    output.mkdir()
    report_path = output / "release-gate-report.json"
    report_path.write_text('{"releaseDecision":"pass"}\n', encoding="utf-8")
    missing = tmp_path / "missing"
    paths = GatePaths(
        repository_root=tmp_path,
        catalog=missing / "catalog.json",
        catalog_schema=missing / "catalog-schema.json",
        registry=missing / "registry.json",
        registry_schema=missing / "registry-schema.json",
        manifest=missing / "manifest.json",
        database_environment=missing / "database.env",
        output_directory=output,
    )

    with pytest.raises(CatalogValidationError):
        run_gate(paths)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["releaseDecision"] == "fail"
    assert report["gates"]["Security"]["status"] == "fail"
    raw = json.loads((output / "raw-evidence.json").read_text(encoding="utf-8"))
    assert raw["runnerFailure"] == "CatalogValidationError"


def test_runner_failure_keeps_safe_available_provenance(tmp_path: Path) -> None:
    output = tmp_path / "artifacts"
    missing = tmp_path / "missing"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    paths = GatePaths(
        repository_root=tmp_path,
        catalog=missing / "catalog.json",
        catalog_schema=missing / "catalog-schema.json",
        registry=missing / "registry.json",
        registry_schema=missing / "registry-schema.json",
        manifest=missing / "manifest.json",
        database_environment=missing / "database.env",
        output_directory=output,
    )

    with pytest.raises(CatalogValidationError):
        run_gate(paths)

    report = json.loads(
        (output / "release-gate-report.json").read_text(encoding="utf-8")
    )
    provenance = report["provenance"]
    assert provenance["runnerVersion"] == "1.0.0"
    assert "commit" in provenance
    assert "contentStateDigest" in provenance
    assert "rawResultDigest" in provenance
    assert "database.env" not in json.dumps(report)


def test_gate_fails_before_pytest_when_live_revision_differs_from_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def executor(
        command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> int:
        del command, cwd, env
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(
        "scripts.security_gate.runner._load_gate_inputs",
        lambda _paths, _validator: (
            {"fixtures": []},
            {},
            registry(),
            {},
            {},
        ),
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner.load_database_environment", lambda _path: {}
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._alembic_head", lambda _paths: "head"
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._live_database_revision",
        lambda _environment: "old",
    )
    paths = GatePaths(
        repository_root=tmp_path,
        catalog=tmp_path / "catalog.json",
        catalog_schema=tmp_path / "catalog-schema.json",
        registry=tmp_path / "registry.json",
        registry_schema=tmp_path / "registry-schema.json",
        manifest=tmp_path / "manifest.json",
        database_environment=tmp_path / "database.env",
        output_directory=tmp_path / "artifacts",
    )

    with pytest.raises(GateRunError, match="live Alembic revision"):
        run_gate(paths, pytest_executor=executor)

    assert called is False


def test_gate_rejects_stale_or_spoofed_raw_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.security_gate.runner._load_gate_inputs",
        lambda _paths, _validator: (
            {"fixtures": []},
            {},
            registry(),
            {},
            {},
        ),
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner.load_database_environment", lambda _path: {}
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._alembic_head", lambda _paths: "head"
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._live_database_revision",
        lambda _environment: "head",
    )

    def executor(
        command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> int:
        raw_path = Path(command[command.index("--security-gate-raw") + 1])
        spoofed = raw_execution()
        spoofed["rawProducer"] = "scripts.security_gate.pytest_plugin"
        spoofed["runnerExecutionId"] = "stale-run"
        raw_path.write_text(json.dumps(spoofed), encoding="utf-8")
        return 0

    paths = GatePaths(
        repository_root=tmp_path,
        catalog=tmp_path / "catalog.json",
        catalog_schema=tmp_path / "catalog-schema.json",
        registry=tmp_path / "registry.json",
        registry_schema=tmp_path / "registry-schema.json",
        manifest=tmp_path / "manifest.json",
        database_environment=tmp_path / "database.env",
        output_directory=tmp_path / "artifacts",
    )

    with pytest.raises(GateRunError, match="stale or belongs to another run"):
        run_gate(paths, pytest_executor=executor)

    retained = json.loads(
        (paths.output_directory / "raw-evidence.json").read_text(encoding="utf-8")
    )
    assert retained["runnerFailure"] == "GateRunError"
    assert retained.get("runnerExecutionId") != "stale-run"


def test_post_pytest_failure_preserves_the_valid_raw_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.security_gate.runner._load_gate_inputs",
        lambda _paths, _validator: (
            {"fixtures": []},
            {},
            registry(),
            {},
            {},
        ),
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner.load_database_environment", lambda _path: {}
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._alembic_head", lambda _paths: "head"
    )
    monkeypatch.setattr(
        "scripts.security_gate.runner._live_database_revision",
        lambda _environment: "head",
    )

    def executor(
        command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> int:
        del cwd
        raw_path = Path(command[command.index("--security-gate-raw") + 1])
        valid = raw_execution()
        valid["rawProducer"] = "scripts.security_gate.pytest_plugin"
        valid["runnerExecutionId"] = env[
            "CONTEXT_ENGINE_SECURITY_GATE_EXECUTION_ID"
        ]
        raw_path.write_text(json.dumps(valid), encoding="utf-8")
        return 0

    def fail_after_pytest(**_kwargs: object) -> Mapping[str, object]:
        raise RuntimeError("post-pytest RLS audit failure")

    paths = GatePaths(
        repository_root=tmp_path,
        catalog=tmp_path / "catalog.json",
        catalog_schema=tmp_path / "catalog-schema.json",
        registry=tmp_path / "registry.json",
        registry_schema=tmp_path / "registry-schema.json",
        manifest=tmp_path / "manifest.json",
        database_environment=tmp_path / "database.env",
        output_directory=tmp_path / "artifacts",
    )

    with pytest.raises(RuntimeError, match="post-pytest RLS audit failure"):
        run_gate(paths, pytest_executor=executor, rls_auditor=fail_after_pytest)

    retained = json.loads(
        (paths.output_directory / "raw-evidence.json").read_text(encoding="utf-8")
    )
    assert retained["runnerFailure"] == "RuntimeError"
    assert retained["pytest"]["exitCode"] == 0
    assert len(retained["pytest"]["tests"]) == 4
    assert retained["rawProducer"] == "scripts.security_gate.pytest_plugin"


def test_pytest_plugin_emits_raw_json_with_xpass_and_observation(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_plugin_sample.py"
    test_file.write_text(
        """
import pytest
from scripts.security_gate.observations import record_fixture_observation

@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-001", layer="runtime")
def test_observed(record_property):
    record_fixture_observation(
        record_property,
        fixture_ref="ACCEPT-001",
        evidence_ref="FIXTURE-ACCEPT-001",
        unauthorized_evidence_count=0,
        wrong_organization_effect_count=0,
        missing_context_fallback_count=0,
    )

@pytest.mark.security_evidence(id="RUNTIME-XPASS-001", layer="runtime")
@pytest.mark.xfail(strict=False)
def test_unexpected_pass():
    pass
""".lstrip(),
        encoding="utf-8",
    )
    raw_path = tmp_path / "raw.json"
    selectors = (
        f"{test_file}::test_observed",
        f"{test_file}::test_unexpected_pass",
    )
    command = build_pytest_command(
        selectors,
        raw_path=raw_path,
        python_executable=sys.executable,
    )

    import subprocess

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode == 0
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    outcomes = {test["nodeId"]: test["outcome"] for test in raw["pytest"]["tests"]}
    observed_node = next(node for node in outcomes if node.endswith("::test_observed"))
    xpass_node = next(
        node for node in outcomes if node.endswith("::test_unexpected_pass")
    )
    assert outcomes[observed_node] == "passed"
    assert outcomes[xpass_node] == "xpassed"
    assert raw["pytest"]["tests"][0]["observations"][0]["fixtureRef"] == (
        "ACCEPT-001"
    )
    assert raw["pytest"]["securityEvidenceMarkerVersion"] == "1.0.0"
    metadata = {
        item["nodeId"]: item["securityEvidence"]
        for item in raw["pytest"]["collectedTests"]
    }
    assert metadata[observed_node] == [
        {"id": "FIXTURE-ACCEPT-001", "layer": "runtime"}
    ]
