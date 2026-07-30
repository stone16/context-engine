from __future__ import annotations

import ast
from pathlib import Path

import pytest

from engine.supply.execution import SourceAclObservation

ROOT = Path(__file__).parents[2]


def _consumes_acl_observation(tree: ast.AST) -> bool:
    indirect_observation_access = any(
        isinstance(node, ast.Attribute) and node.attr == "acl_observation"
        for node in ast.walk(tree)
    )
    direct_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "SourceAclObservation"
    }
    execution_aliases = {
        alias.asname or alias.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "engine.supply.execution"
    } | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "engine.supply"
        for alias in node.names
        if alias.name == "execution"
    }
    supply_aliases = {
        alias.asname or alias.name.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "engine.supply"
    }
    module_chains = {
        (alias.asname or alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "engine.supply.execution"
    }
    attribute_consumption = any(
        isinstance(node, ast.Attribute)
        and node.attr == "SourceAclObservation"
        and (
            isinstance(node.value, ast.Name)
            and node.value.id in execution_aliases | supply_aliases
            or (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Attribute)
                and isinstance(node.value.value.value, ast.Name)
                and node.value.value.value.id in module_chains
                and node.value.value.attr == "supply"
                and node.value.attr == "execution"
            )
        )
        for node in ast.walk(tree)
    )
    name_consumption = any(
        isinstance(node, ast.Name) and node.id in direct_names
        for node in ast.walk(tree)
    )
    return name_consumption or attribute_consumption or indirect_observation_access


@pytest.mark.parametrize(
    "source",
    [
        "import engine.supply.execution as execution\n"
        "value: execution.SourceAclObservation\n",
        "import engine.supply.execution\n"
        "value: engine.supply.execution.SourceAclObservation\n",
        "from engine.supply import execution\n"
        "value: execution.SourceAclObservation\n",
        "from engine.supply import SourceAclObservation as Observation\n"
        "value: Observation\n",
        "import engine.supply as supply\n"
        "value: supply.SourceAclObservation\n",
        "from engine.supply import SupplyDocumentEnvelope\n"
        "def authorize(value: SupplyDocumentEnvelope):\n"
        "    return value.acl_observation\n",
        "from engine.supply import SupplyChangePage\n"
        "def grant(page: SupplyChangePage):\n"
        "    return page.documents[0].acl_observation\n",
    ],
)
def test_acl_usage_scanner_detects_direct_module_and_type_consumption(
    source: str,
) -> None:
    assert _consumes_acl_observation(ast.parse(source))


def _production_python_files() -> tuple[Path, ...]:
    return tuple(
        path
        for root in (
            ROOT / "engine",
            ROOT / "adapters",
            ROOT / "applications",
            ROOT / "action_plane",
            ROOT / "bot_delivery",
        )
        for path in root.rglob("*.py")
    )


def test_acl_observation_is_not_consumed_as_authorization_outside_kernel() -> None:
    forbidden_consumers: list[str] = []
    definition_path = ROOT / "engine" / "supply" / "execution.py"
    public_reexport_path = ROOT / "engine" / "supply" / "__init__.py"
    evidence_producer_paths = {ROOT / "adapters" / "connectors" / "file.py"}

    for path in _production_python_files():
        if path in {definition_path, public_reexport_path} | evidence_producer_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _consumes_acl_observation(tree):
            forbidden_consumers.append(str(path.relative_to(ROOT)))

    assert forbidden_consumers == []


def test_registered_supply_adapter_only_constructs_acl_evidence() -> None:
    path = ROOT / "adapters" / "connectors" / "file.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert "SourceAclObservation" in calls
    assert "AuthorizationKernel" not in names
    assert "AuthorizedProjection" not in names
    assert not {"authorize", "grant", "resolve"}.intersection(calls)


def test_acl_observation_module_cannot_construct_runtime_authority() -> None:
    execution_module = ast.parse(
        (ROOT / "engine" / "supply" / "execution.py").read_text(encoding="utf-8")
    )
    names = {
        node.id
        for node in ast.walk(execution_module)
        if isinstance(node, ast.Name)
    }

    assert "AuthorizationKernel" not in names
    assert "AuthorizedProjection" not in names
    assert not hasattr(SourceAclObservation, "authorize")
    assert not hasattr(SourceAclObservation, "grant")
