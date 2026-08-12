from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_active_carriers import (
    CarrierValidationError,
    validate_active_carrier_registry,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPOSITORY_ROOT / "eval/catalogs/active-carriers-v1.json"
SCHEMA_PATH = REPOSITORY_ROOT / "eval/catalogs/active-carriers-v1.schema.json"
VALIDATOR_PATH = REPOSITORY_ROOT / "scripts/validate_active_carriers.py"


def _documents() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8")),
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    )


def _categories(registry: dict[str, Any]) -> tuple[str, ...]:
    _, schema = _documents()
    with pytest.raises(CarrierValidationError) as raised:
        validate_active_carrier_registry(
            registry,
            schema,
            repository_root=REPOSITORY_ROOT,
        )
    return raised.value.categories


def test_public_validator_reports_missing_authority_without_registry_content(
    tmp_path: Path,
) -> None:
    registry, _ = _documents()
    registry["carriers"][0]["authorityRefs"] = []
    broken_registry = tmp_path / "active-carriers-v1.json"
    broken_registry.write_text(json.dumps(registry), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--registry",
            str(broken_registry),
            "--schema",
            str(SCHEMA_PATH),
            "--repository-root",
            str(REPOSITORY_ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["DANGLING_AUTHORITY"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize(
    ("relationship", "category"),
    [
        ("authorityRefs", "DANGLING_AUTHORITY"),
        ("entrypointRefs", "DANGLING_ENTRYPOINT"),
        ("highestPublicTestRefs", "DANGLING_PUBLIC_PROOF"),
        ("operatorDocRefs", "DANGLING_OPERATOR_DOCUMENT"),
    ],
)
def test_validator_rejects_each_dangling_file_relationship(
    relationship: str,
    category: str,
) -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    broken["carriers"][0][relationship][0]["path"] = "absent.md"

    assert _categories(broken) == (category,)


@pytest.mark.parametrize(
    ("relationship", "category"),
    [
        ("authorityRefs", "DANGLING_AUTHORITY"),
        ("entrypointRefs", "DANGLING_ENTRYPOINT"),
        ("highestPublicTestRefs", "DANGLING_PUBLIC_PROOF"),
        ("operatorDocRefs", "DANGLING_OPERATOR_DOCUMENT"),
    ],
)
def test_validator_rejects_each_missing_file_relationship(
    relationship: str,
    category: str,
) -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    broken["carriers"][0][relationship] = []

    assert _categories(broken) == (category,)


def test_validator_rejects_dangling_migration_and_make_gate() -> None:
    registry, _ = _documents()
    broken_migration = deepcopy(registry)
    broken_migration["carriers"][0]["migration"]["refs"][0]["path"] = (
        "absent.py"
    )
    broken_gate = deepcopy(registry)
    broken_gate["carriers"][0]["gateTargets"] = ["catalog", "absent"]

    assert _categories(broken_migration) == ("DANGLING_MIGRATION",)
    assert _categories(broken_gate) == ("DANGLING_GATE",)


def test_validator_rejects_dangling_status_owner() -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    broken["carriers"][0]["statusOwner"]["marker"] = "missing marker"

    assert _categories(broken) == ("DANGLING_STATUS_OWNER",)


@pytest.mark.parametrize(
    ("relationship", "category"),
    [
        ("status", "INACTIVE_STATUS_CONFLICT"),
        ("statusOwner", "DANGLING_STATUS_OWNER"),
        ("migration", "DANGLING_MIGRATION"),
        ("gateTargets", "DANGLING_GATE"),
    ],
)
def test_validator_rejects_each_missing_required_relationship(
    relationship: str,
    category: str,
) -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    del broken["carriers"][0][relationship]

    assert _categories(broken) == (category,)


def test_validator_rejects_duplicate_owner_and_both_status_drift_directions() -> None:
    registry, _ = _documents()
    duplicate = deepcopy(registry)
    duplicate["carriers"][1]["id"] = duplicate["carriers"][0]["id"]
    promoted = deepcopy(registry)
    promoted["carriers"][-1]["status"] = "ACTIVE_BOUNDED"
    demoted = deepcopy(registry)
    demoted["carriers"][0]["status"] = "NOT_ACTIVE"

    assert _categories(duplicate) == (
        "DANGLING_STATUS_OWNER",
        "DUPLICATE_CARRIER_OWNER",
    )
    assert _categories(promoted) == (
        "DANGLING_STATUS_OWNER",
        "INACTIVE_STATUS_CONFLICT",
    )
    assert _categories(demoted) == ("DANGLING_STATUS_OWNER",)


def test_validator_requires_complete_closed_inactive_relations() -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    broken["carriers"][-1]["notActiveRelations"] = ["CONTINUE"]

    assert _categories(broken) == ("INACTIVE_STATUS_CONFLICT",)


def test_validator_rejects_corrupt_inactive_relation_and_na_rationale() -> None:
    registry, _ = _documents()
    corrupt_relation = deepcopy(registry)
    corrupt_relation["carriers"][-1]["notActiveRelations"] = ["CONTINUE"]
    corrupt_rationale = deepcopy(registry)
    consumer = next(
        carrier
        for carrier in corrupt_rationale["carriers"]
        if carrier["id"] == "maintainer-context-cli-acquire-v0"
    )
    consumer["migration"]["notApplicableRationale"] = "GENERIC_NA"

    assert _categories(corrupt_relation) == ("INACTIVE_STATUS_CONFLICT",)
    assert _categories(corrupt_rationale) == ("DANGLING_MIGRATION",)


def test_validator_rejects_non_adr_authority_and_repository_escape(
    tmp_path: Path,
) -> None:
    registry, _ = _documents()
    non_adr = deepcopy(registry)
    non_adr["carriers"][0]["authorityRefs"][0] = {
        "path": "README.md",
        "marker": "Status: accepted",
    }
    escaped = deepcopy(registry)
    outside = tmp_path / "outside.md"
    outside.write_text("proof", encoding="utf-8")
    escaped["carriers"][0]["entrypointRefs"][0] = {
        "path": "../outside.md",
        "marker": "proof",
    }

    assert _categories(non_adr) == ("DANGLING_AUTHORITY",)
    assert _categories(escaped) == ("DANGLING_ENTRYPOINT",)


def test_tracked_registry_is_complete_but_does_not_evaluate_security() -> None:
    registry, schema = _documents()

    report = validate_active_carrier_registry(
        registry,
        schema,
        repository_root=REPOSITORY_ROOT,
    )

    assert report.carrier_count == 22
    assert report.active_count == 12
    assert report.inactive_count == 10
    assert registry["securityVetoRelationship"] == "SEPARATE_INDEPENDENT_GATE"
    inactive_relations = {
        relation
        for carrier in registry["carriers"]
        if carrier["status"] == "NOT_ACTIVE"
        for relation in carrier["notActiveRelations"]
    }
    assert inactive_relations == set(registry["closedNotActiveRelations"])


def test_public_validator_accepts_the_tracked_registry() -> None:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "COMPLETE",
        "categories": [],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "PASS",
    }
