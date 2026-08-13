from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

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
        database_probe=lambda _configuration, _plane: "ready",
        model_probe=lambda _configuration: "ready",
        release_probe=lambda _configuration: "ready",
        caller_probe=lambda _configuration: "ready",
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(result.rendered))


def test_preflight_contract_accepts_non_variable_malformed_selection() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = preflight.run_preflight(
        {},
        selected_planes=("migration", "migration"),
        schema_probe=lambda _configuration: "ready",
        database_probe=lambda _configuration, _plane: "ready",
        model_probe=lambda _configuration: "ready",
        release_probe=lambda _configuration: "ready",
        caller_probe=lambda _configuration: "ready",
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(result.rendered))


@pytest.mark.parametrize(
    "selected_check_indices",
    (
        (3,),
        (4,),
        (5,),
        (3, 4),
        (3, 5),
        (4, 5),
        (6,),
        (7,),
        (8,),
        (9,),
    ),
)
def test_preflight_contract_rejects_partially_selected_planes(
    selected_check_indices: tuple[int, ...],
) -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    migration_only = json.loads(
        preflight.run_preflight(
            {
                "CONTEXT_ENGINE_MIGRATOR_ROLE": "context_engine_migrator",
                "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
                    "postgresql+psycopg://context_engine_migrator:secret@db/"
                    "context_engine"
                ),
            },
            selected_planes=("migration",),
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "not_selected",
            model_probe=lambda _configuration: "not_selected",
            release_probe=lambda _configuration: "not_selected",
            caller_probe=lambda _configuration: "not_selected",
        ).rendered
    )
    Draft202012Validator(schema).validate(migration_only)
    for selected_check_index in selected_check_indices:
        migration_only["checks"][selected_check_index]["status"] = "ready"
        migration_only["checks"][selected_check_index]["category"] = "ready"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(migration_only)


def test_preflight_contract_requires_failures_for_missing_variables() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = json.loads(
        preflight.run_preflight(
            {},
            selected_planes=("migration",),
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "ready",
            model_probe=lambda _configuration: "ready",
            release_probe=lambda _configuration: "ready",
            caller_probe=lambda _configuration: "ready",
        ).rendered
    )
    del result["checks"][0]["failures"]

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(result)


def test_preflight_contract_failure_names_match_the_implementation_inventory() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert (
        set(
            schema["$defs"]["configurationFailures"]["items"]["properties"]["name"][
                "enum"
            ]
        )
        == preflight.REQUIRED_ENVIRONMENT_NAMES
    )


@pytest.mark.parametrize("invalid_name", (123, "CONTEXT_ENGINE_UNKNOWN"))
def test_preflight_contract_rejects_non_inventory_failure_names(
    invalid_name: object,
) -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    document = json.loads(
        preflight.run_preflight(
            {},
            selected_planes=("migration",),
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "ready",
            model_probe=lambda _configuration: "ready",
            release_probe=lambda _configuration: "ready",
            caller_probe=lambda _configuration: "ready",
        ).rendered
    )
    document["checks"][0]["failures"][0]["name"] = invalid_name

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


@pytest.mark.parametrize(
    ("row_category", "failure_categories"),
    (
        ("configuration_missing", ("configuration_malformed",)),
        (
            "configuration_malformed",
            ("configuration_malformed", "configuration_missing"),
        ),
    ),
)
def test_preflight_contract_rejects_configuration_summary_detail_mismatch(
    row_category: str,
    failure_categories: tuple[str, ...],
) -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    names = sorted(preflight.REQUIRED_ENVIRONMENT_NAMES)
    document = json.loads(
        preflight.run_preflight(
            {},
            selected_planes=("migration",),
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "ready",
            model_probe=lambda _configuration: "ready",
            release_probe=lambda _configuration: "ready",
            caller_probe=lambda _configuration: "ready",
        ).rendered
    )
    document["checks"][0] = {
        "check": "configuration",
        "status": "failed",
        "category": row_category,
        "failures": [
            {"name": name, "category": category}
            for name, category in zip(names, failure_categories, strict=False)
        ],
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


def test_preflight_environment_template_is_empty_and_matches_inventory() -> None:
    rows = (
        (ROOT / "deploy/local-preflight.env.example")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert rows == [f"{name}=" for name in sorted(preflight.ENVIRONMENT_TEMPLATE_NAMES)]


def test_preflight_contract_rejects_contradictory_status_and_check_outcomes() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/context-engine-preflight-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    missing = json.loads(
        preflight.run_preflight(
            {},
            selected_planes=None,
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "ready",
            model_probe=lambda _configuration: "ready",
            release_probe=lambda _configuration: "ready",
            caller_probe=lambda _configuration: "ready",
        ).rendered
    )
    missing["status"] = "ready"

    migration_ready = json.loads(
        preflight.run_preflight(
            {
                "CONTEXT_ENGINE_MIGRATOR_ROLE": "context_engine_migrator",
                "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
                    "postgresql+psycopg://context_engine_migrator:secret@db/"
                    "context_engine"
                ),
            },
            selected_planes=("migration",),
            schema_probe=lambda _configuration: "ready",
            database_probe=lambda _configuration, _plane: "not_selected",
            model_probe=lambda _configuration: "not_selected",
            release_probe=lambda _configuration: "not_selected",
            caller_probe=lambda _configuration: "not_selected",
        ).rendered
    )
    Draft202012Validator(schema).validate(migration_ready)
    selected_checks_ready_but_summary_not_ready = deepcopy(migration_ready)
    selected_checks_ready_but_summary_not_ready["status"] = "not_ready"
    migration_ready["checks"][1]["category"] = "schema_not_at_head"

    for contradictory in (
        missing,
        migration_ready,
        selected_checks_ready_but_summary_not_ready,
    ):
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(contradictory)


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


def test_preflight_subcommand_is_owned_only_by_capability_minimal_dispatch() -> None:
    control_tree = ast.parse(
        (ROOT / "applications/control.py").read_text(encoding="utf-8")
    )
    dispatch_tree = ast.parse(
        (ROOT / "applications/control_dispatch.py").read_text(encoding="utf-8")
    )

    def string_literals(tree: ast.AST) -> set[str]:
        return {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and type(node.value) is str
        }

    assert "preflight" not in string_literals(control_tree)
    assert "preflight" in string_literals(dispatch_tree)
