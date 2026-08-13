from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from applications.preflight import REQUIRED_ENVIRONMENT_NAMES

ROOT = Path(__file__).parents[2]


def _environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("CONTEXT_ENGINE_")
    }


def test_control_preflight_help_is_value_free_and_lazy() -> None:
    completed = subprocess.run(
        ["context-engine-control", "preflight", "--help"],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert "--plane" in completed.stdout
    for prohibited in (
        "--organization-id",
        "--database-url",
        "--model-dir",
        "--secret",
        "--path",
    ):
        assert prohibited not in completed.stdout

    imports = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from applications.control_dispatch import main; "
                "\ntry: main(['preflight', '--help'])\n"
                "except SystemExit as error:\n"
                " assert error.code == 0\n"
                "print('\\n'.join(sorted(sys.modules)))"
            ),
        ],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert imports.returncode == 0
    prohibited_prefixes = (
        "applications.control",
        "applications.dogfood",
        "applications.release_promotion",
        "applications.worker",
        "engine.control",
        "engine.learning",
        "engine.persistence.migrations",
        "engine.persistence.control_sources",
        "engine.persistence.releases",
        "engine.persistence.worker_jobs",
        "engine.runtime",
        "engine.supply",
        "alembic.command",
    )
    imported = imports.stdout.splitlines()
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in prohibited_prefixes
    )


def test_control_preflight_missing_configuration_is_closed_json() -> None:
    completed = subprocess.run(
        ["context-engine-control", "preflight"],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 10
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schemaVersion": "context-engine-preflight-v1",
        "service": "context-engine-control-preflight",
        "status": "not_ready",
        "checks": [
            {
                "check": "configuration",
                "status": "failed",
                "category": "configuration_missing",
                "failures": [
                    {"name": name, "category": "configuration_missing"}
                    for name in sorted(REQUIRED_ENVIRONMENT_NAMES)
                ],
            },
            {
                "check": "migration_schema",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "control_database",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "supply_scheduler_database",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "supply_worker_database",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "supply_model",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "release_learning_database",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "release_operator_database",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "runtime_release",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "runtime_model",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
            {
                "check": "caller_configuration",
                "status": "not_run",
                "category": "dependency_not_ready",
            },
        ],
    }
    assert (
        completed.stdout
        == json.dumps(json.loads(completed.stdout), separators=(",", ":")) + "\n"
    )


def test_control_preflight_invalid_selection_never_echoes_supplied_values() -> None:
    private = "postgresql+psycopg://secret@private.example/private-tenant"
    completed = subprocess.run(
        ["context-engine-control", "preflight", "--plane", private],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "context-engine-control: preflight refused\n"
    assert private not in completed.stdout + completed.stderr


def test_control_preflight_duplicate_selection_is_closed_contract_json() -> None:
    completed = subprocess.run(
        [
            "context-engine-control",
            "preflight",
            "--plane",
            "migration",
            "--plane",
            "migration",
        ],
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 10
    assert completed.stderr == ""
    document = json.loads(completed.stdout)
    assert document["checks"][0] == {
        "check": "configuration",
        "status": "failed",
        "category": "configuration_malformed",
    }

    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
