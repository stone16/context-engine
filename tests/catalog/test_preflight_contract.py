from __future__ import annotations

import ast
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from applications import preflight

ROOT = Path(__file__).parents[2]


def test_preflight_contract_schema_accepts_closed_missing_configuration() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = preflight.run_preflight(
        {},
        selected_planes=None,
        schema_probe=lambda _configuration: "ready",
        model_probe=lambda _configuration: "ready",
        release_probe=lambda _configuration: "ready",
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(result.rendered))


def test_preflight_environment_template_is_empty_and_matches_inventory() -> None:
    rows = (
        (ROOT / "deploy/local-preflight.env.example")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert rows == [f"{name}=" for name in sorted(preflight.REQUIRED_ENVIRONMENT_NAMES)]


def test_preflight_dispatch_and_module_have_no_mutation_imports() -> None:
    imported: set[str] = set()
    for relative_path in (
        "applications/control_dispatch.py",
        "applications/preflight.py",
    ):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    prohibited = (
        "applications.control",
        "applications.dogfood",
        "applications.release_promotion",
        "applications.worker",
        "engine.control",
        "engine.learning",
        "engine.persistence",
        "engine.runtime",
        "engine.supply",
        "alembic",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in prohibited
    )
