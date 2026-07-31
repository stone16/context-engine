from __future__ import annotations

import ast
from pathlib import Path

import pytest

from engine.supply.execution import SourceAclObservation

ROOT = Path(__file__).parents[2]
_AUTHORITY_SYMBOLS = {
    "AuthorizationKernel",
    "AuthorizedProjection",
    "authorize",
    "grant",
    "resolve",
}


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                aliases[local_name] = alias.name.rsplit(".", 1)[-1]
    return aliases


def _symbol_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _authority_symbols(tree: ast.AST) -> set[str]:
    aliases = _import_aliases(tree)
    referenced = {
        symbol
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
        if (symbol := _symbol_name(node, aliases)) is not None
    }
    return referenced.intersection(_AUTHORITY_SYMBOLS)


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


@pytest.mark.parametrize(
    "source, expected_symbol",
    [
        ("kernel.authorize()", "authorize"),
        ("authority.grant()", "grant"),
        ("runtime.resolve()", "resolve"),
        (
            "import engine.runtime as runtime\nruntime.AuthorizationKernel()",
            "AuthorizationKernel",
        ),
        (
            "from engine.runtime.evidence import "
            "AuthorizedProjection as Projection\nProjection()",
            "AuthorizedProjection",
        ),
    ],
)
def test_adapter_authority_scanner_detects_attributes_and_import_aliases(
    source: str,
    expected_symbol: str,
) -> None:
    assert expected_symbol in _authority_symbols(ast.parse(source))


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
    evidence_producer_paths = {
        ROOT / "adapters" / "connectors" / connector
        for connector in ("feishu.py", "file.py")
    }

    for path in _production_python_files():
        if path in {definition_path, public_reexport_path} | evidence_producer_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _consumes_acl_observation(tree):
            forbidden_consumers.append(str(path.relative_to(ROOT)))

    assert forbidden_consumers == []


@pytest.mark.parametrize("connector", ["feishu.py", "file.py"])
def test_registered_supply_adapter_only_constructs_acl_evidence(
    connector: str,
) -> None:
    path = ROOT / "adapters" / "connectors" / connector
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _import_aliases(tree)
    called_symbols = {
        symbol
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        if (symbol := _symbol_name(node.func, aliases)) is not None
    }

    assert "SourceAclObservation" in called_symbols
    assert _authority_symbols(tree) == set()


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
