from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from scripts import validate_active_carriers as active_carrier_validator
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
    return _categories_with_schema(registry, schema)


def _categories_with_schema(
    registry: dict[str, Any], schema: dict[str, Any]
) -> tuple[str, ...]:
    with pytest.raises(CarrierValidationError) as raised:
        validate_active_carrier_registry(
            registry,
            schema,
            repository_root=REPOSITORY_ROOT,
        )
    return raised.value.categories


@pytest.mark.parametrize(
    "path",
    (
        "/tmp/outside.md",
        "../outside.md",
        "docs/../outside.md",
        "docs/nested/../../outside.md",
    ),
)
def test_registry_schema_rejects_non_repository_relative_reference_paths(
    path: str,
) -> None:
    registry, schema = _documents()
    registry["carriers"][0]["entrypointRefs"][0]["path"] = path

    errors = tuple(Draft202012Validator(schema).iter_errors(registry))

    assert any(tuple(error.absolute_path)[-1] == "path" for error in errors)


@pytest.mark.parametrize(
    ("relationship", "category"),
    (
        ("statusOwner", "DANGLING_STATUS_OWNER"),
        ("entrypointRefs", "DANGLING_ENTRYPOINT"),
        ("migration", "DANGLING_MIGRATION"),
        ("highestPublicTestRefs", "DANGLING_PUBLIC_PROOF"),
        ("operatorDocRefs", "DANGLING_OPERATOR_DOCUMENT"),
    ),
)
def test_registry_rejects_each_reference_without_an_exact_marker(
    relationship: str,
    category: str,
) -> None:
    registry, _ = _documents()
    broken = deepcopy(registry)
    carrier = broken["carriers"][0]
    reference = (
        carrier[relationship]
        if relationship == "statusOwner"
        else carrier[relationship]["refs"][0]
        if relationship == "migration"
        else carrier[relationship][0]
    )
    del reference["marker"]

    assert _categories(broken) == (category,)


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


def test_public_validator_reports_invalid_schema_without_a_traceback(
    tmp_path: Path,
) -> None:
    _, schema = _documents()
    schema["$defs"]["reference"]["properties"]["path"]["pattern"] = "["
    invalid_schema = tmp_path / "active-carriers-v1.schema.json"
    invalid_schema.write_text(json.dumps(schema), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--registry",
            str(REGISTRY_PATH),
            "--schema",
            str(invalid_schema),
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
        "categories": ["REGISTRY_SCHEMA_INVALID"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


def test_public_validator_reports_unavailable_repository_without_input_content(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--registry",
            str(REGISTRY_PATH),
            "--schema",
            str(SCHEMA_PATH),
            "--repository-root",
            str(tmp_path / "missing-repository"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REPOSITORY_UNAVAILABLE"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize("unavailable_option", ("--registry", "--schema"))
def test_public_validator_keeps_unreadable_input_in_schema_category(
    tmp_path: Path,
    unavailable_option: str,
) -> None:
    command = [
        sys.executable,
        str(VALIDATOR_PATH),
        "--registry",
        str(REGISTRY_PATH),
        "--schema",
        str(SCHEMA_PATH),
        "--repository-root",
        str(REPOSITORY_ROOT),
    ]
    command[command.index(unavailable_option) + 1] = str(
        tmp_path / "missing-input.json"
    )

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REGISTRY_SCHEMA_INVALID"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize("malformed_option", ("--registry", "--schema"))
def test_public_validator_keeps_malformed_json_in_schema_category(
    tmp_path: Path,
    malformed_option: str,
) -> None:
    malformed_input = tmp_path / "malformed-input.json"
    malformed_input.write_text("{", encoding="utf-8")
    command = [
        sys.executable,
        str(VALIDATOR_PATH),
        "--registry",
        str(REGISTRY_PATH),
        "--schema",
        str(SCHEMA_PATH),
        "--repository-root",
        str(REPOSITORY_ROOT),
    ]
    command[command.index(malformed_option) + 1] = str(malformed_input)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REGISTRY_SCHEMA_INVALID"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize("invalid_utf8_option", ("--registry", "--schema"))
def test_public_validator_keeps_invalid_utf8_in_schema_category(
    tmp_path: Path,
    invalid_utf8_option: str,
) -> None:
    invalid_utf8_input = tmp_path / "invalid-utf8.json"
    invalid_utf8_input.write_bytes(b"\xff")
    command = [
        sys.executable,
        str(VALIDATOR_PATH),
        "--registry",
        str(REGISTRY_PATH),
        "--schema",
        str(SCHEMA_PATH),
        "--repository-root",
        str(REPOSITORY_ROOT),
    ]
    command[command.index(invalid_utf8_option) + 1] = str(invalid_utf8_input)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REGISTRY_SCHEMA_INVALID"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize("unreadable_path", ("Makefile", "STATUS.md", "README.md"))
def test_public_validator_reports_repository_read_failure_without_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unreadable_path: str,
) -> None:
    unreadable_reference = (REPOSITORY_ROOT / unreadable_path).resolve()
    original_read_text = Path.read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == unreadable_reference:
            raise OSError("private path and error detail")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    exit_code = active_carrier_validator.main(
        [
            "--registry",
            str(REGISTRY_PATH),
            "--schema",
            str(SCHEMA_PATH),
            "--repository-root",
            str(REPOSITORY_ROOT),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REPOSITORY_UNAVAILABLE"],
        "schemaVersion": "active-carrier-validation-result-v1",
        "securityVeto": "SEPARATE_NOT_EVALUATED",
        "status": "FAIL",
    }


@pytest.mark.parametrize("corrupt_input", ("proof.md", "Makefile", "STATUS.md"))
def test_public_validator_reports_each_invalid_utf8_repository_input_without_content(
    tmp_path: Path,
    corrupt_input: str,
) -> None:
    registry, _ = _documents()
    registry["carriers"][0]["statusOwner"] = {
        "path": "proof.md",
        "marker": "proof",
    }
    registry_path = tmp_path / "active-carriers-v1.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    (tmp_path / "proof.md").write_text("proof", encoding="utf-8")
    (tmp_path / "Makefile").write_text("check:\n", encoding="utf-8")
    (tmp_path / "STATUS.md").write_text("status\n", encoding="utf-8")
    (tmp_path / corrupt_input).write_bytes(b"\xffprivate repository content")

    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--registry",
            str(registry_path),
            "--schema",
            str(SCHEMA_PATH),
            "--repository-root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "capabilityCompleteness": "INCOMPLETE",
        "categories": ["REPOSITORY_UNAVAILABLE"],
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
    broken_migration["carriers"][0]["migration"]["refs"][0]["path"] = "absent.py"
    broken_gate = deepcopy(registry)
    broken_gate["carriers"][0]["gateTargets"] = ["catalog", "absent"]

    assert _categories(broken_migration) == ("DANGLING_MIGRATION",)
    assert _categories(broken_gate) == ("DANGLING_GATE",)


def test_registry_schema_accepts_the_process_smoke_gate() -> None:
    registry, schema = _documents()
    materializer = next(
        carrier
        for carrier in registry["carriers"]
        if carrier["id"] == "registered-model-materializer"
    )
    materializer["gateTargets"] = ["smoke", "check"]

    errors = tuple(Draft202012Validator(schema).iter_errors(registry))

    assert errors == ()


@pytest.mark.parametrize(
    ("proof_path", "proof_marker", "wrong_gate"),
    (
        (
            "tests/unit/test_local_context.py",
            "test_skill_prescribes_stdin_only_invocation_without_bearer_material",
            "catalog",
        ),
        (
            "tests/catalog/test_validate_active_carrier_registry.py",
            "test_validator_rejects_public_proof_registered_under_wrong_lane",
            "test",
        ),
        (
            "tests/process/test_model_materializer_process.py",
            "test_model_materializer_fetches_tiny_registered_twin_and_publishes_once",
            "test",
        ),
        (
            "tests/integration/test_z_egress_grant_file.py",
            "test_packed_typescript_sdk_resolves_authorized_file_package_over_live_http",
            "smoke",
        ),
    ),
)
def test_validator_rejects_public_proof_registered_under_wrong_lane(
    proof_path: str,
    proof_marker: str,
    wrong_gate: str,
) -> None:
    registry, _ = _documents()
    materializer = next(
        carrier
        for carrier in registry["carriers"]
        if carrier["id"] == "registered-model-materializer"
    )
    materializer["highestPublicTestRefs"] = [
        {"path": proof_path, "marker": proof_marker}
    ]
    materializer["gateTargets"] = [wrong_gate, "check"]

    assert _categories(registry) == ("DANGLING_GATE",)


@pytest.mark.parametrize(
    "gate_targets",
    (["smoke"], ["smoke", "test", "check"]),
)
def test_validator_requires_the_exact_public_proof_lane_and_check_target(
    gate_targets: list[str],
) -> None:
    registry, _ = _documents()
    materializer = next(
        carrier
        for carrier in registry["carriers"]
        if carrier["id"] == "registered-model-materializer"
    )
    materializer["gateTargets"] = gate_targets

    assert _categories(registry) == ("DANGLING_GATE",)


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


def test_validator_rejects_non_adr_authority() -> None:
    registry, _ = _documents()
    non_adr = deepcopy(registry)
    non_adr["carriers"][0]["authorityRefs"][0] = {
        "path": "README.md",
        "marker": "Status: accepted",
    }
    assert _categories(non_adr) == ("DANGLING_AUTHORITY",)


def test_validator_handles_continued_make_rules_before_rejecting_repository_escape(
    tmp_path: Path,
) -> None:
    registry, schema = _documents()
    relations = registry["closedNotActiveRelations"]
    scanned_root = tmp_path / "scanned"
    (scanned_root / "docs/decisions").mkdir(parents=True)
    (scanned_root / "tests/unit").mkdir(parents=True)
    (tmp_path / "outside.md").write_text("proof", encoding="utf-8")
    (scanned_root / "Makefile").write_text(
        "catalog \\\ntest:\n\t@true\ncheck: \\\n\tcatalog \\\n\ttest\n\t@true\n",
        encoding="utf-8",
    )
    (scanned_root / "STATUS.md").write_text(
        "status proof\n"
        "<!-- active-carrier-registry-v1:start -->\n"
        "- `deferred-carrier`: `NOT_ACTIVE`\n"
        "<!-- active-carrier-registry-v1:end -->\n",
        encoding="utf-8",
    )
    (scanned_root / "README.md").write_text("operator proof", encoding="utf-8")
    (scanned_root / "tests/unit/proof.py").write_text("public proof", encoding="utf-8")
    (scanned_root / "docs/decisions/0001-proof.md").write_text(
        "Status: accepted",
        encoding="utf-8",
    )
    registry["carriers"] = [
        {
            "id": "deferred-carrier",
            "status": "NOT_ACTIVE",
            "statusOwner": {"path": "STATUS.md", "marker": "status proof"},
            "authorityRefs": [
                {
                    "path": "docs/decisions/0001-proof.md",
                    "marker": "Status: accepted",
                }
            ],
            "entrypointRefs": [{"path": "../outside.md", "marker": "proof"}],
            "migration": {
                "notApplicableRationale": "DEFERRED_CARRIER_HAS_NO_DURABLE_STATE"
            },
            "highestPublicTestRefs": [
                {"path": "tests/unit/proof.py", "marker": "public proof"}
            ],
            "gateTargets": ["test", "check"],
            "operatorDocRefs": [{"path": "README.md", "marker": "operator proof"}],
            "notActiveRelations": relations,
        }
    ]
    schema["$defs"]["reference"]["properties"]["path"]["pattern"] = (
        "^[A-Za-z0-9_.\\/-]+$"
    )

    with pytest.raises(CarrierValidationError) as raised:
        validate_active_carrier_registry(
            registry,
            schema,
            repository_root=scanned_root,
        )

    assert raised.value.categories == ("DANGLING_ENTRYPOINT",)


def test_status_mirror_refuses_a_contradictory_duplicate_carrier_line(
    tmp_path: Path,
) -> None:
    registry, schema = _documents()
    relations = registry["closedNotActiveRelations"]
    scanned_root = tmp_path / "scanned"
    (scanned_root / "docs/decisions").mkdir(parents=True)
    (scanned_root / "tests/unit").mkdir(parents=True)
    (scanned_root / "Makefile").write_text(
        "catalog \\\ntest:\n\t@true\ncheck: \\\n\tcatalog \\\n\ttest\n\t@true\n",
        encoding="utf-8",
    )
    (scanned_root / "STATUS.md").write_text(
        "status proof\n"
        "<!-- active-carrier-registry-v1:start -->\n"
        "- `deferred-carrier`: `ACTIVE_BOUNDED`\n"
        "- `deferred-carrier`: `NOT_ACTIVE`\n"
        "<!-- active-carrier-registry-v1:end -->\n",
        encoding="utf-8",
    )
    (scanned_root / "README.md").write_text("operator proof", encoding="utf-8")
    (scanned_root / "tests/unit/proof.py").write_text("public proof", encoding="utf-8")
    (scanned_root / "docs/decisions/0001-proof.md").write_text(
        "Status: accepted",
        encoding="utf-8",
    )
    registry["carriers"] = [
        {
            "id": "deferred-carrier",
            "status": "NOT_ACTIVE",
            "statusOwner": {"path": "STATUS.md", "marker": "status proof"},
            "authorityRefs": [
                {
                    "path": "docs/decisions/0001-proof.md",
                    "marker": "Status: accepted",
                }
            ],
            "entrypointRefs": [
                {"path": "tests/unit/proof.py", "marker": "public proof"}
            ],
            "migration": {
                "notApplicableRationale": "DEFERRED_CARRIER_HAS_NO_DURABLE_STATE"
            },
            "highestPublicTestRefs": [
                {"path": "tests/unit/proof.py", "marker": "public proof"}
            ],
            "gateTargets": ["test", "check"],
            "operatorDocRefs": [{"path": "README.md", "marker": "operator proof"}],
            "notActiveRelations": relations,
        }
    ]

    with pytest.raises(CarrierValidationError) as raised:
        validate_active_carrier_registry(
            registry,
            schema,
            repository_root=scanned_root,
        )

    assert raised.value.categories == ("DANGLING_STATUS_OWNER",)


def test_validator_rejects_missing_marker_under_a_permissive_substitute_schema() -> (
    None
):
    registry, schema = _documents()
    del registry["carriers"][0]["entrypointRefs"][0]["marker"]
    schema["$defs"]["reference"]["required"] = ["path"]

    assert _categories_with_schema(registry, schema) == ("DANGLING_ENTRYPOINT",)


def test_tracked_registry_is_complete_but_does_not_evaluate_security() -> None:
    registry, schema = _documents()

    report = validate_active_carrier_registry(
        registry,
        schema,
        repository_root=REPOSITORY_ROOT,
    )

    assert report.carrier_count == 23
    assert report.active_count == 13
    assert report.inactive_count == 10
    assert any(
        carrier["id"] == "registered-model-materializer"
        and carrier["status"] == "ACTIVE_BOUNDED"
        for carrier in registry["carriers"]
    )
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
