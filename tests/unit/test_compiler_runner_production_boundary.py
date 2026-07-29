from __future__ import annotations

import ast
from pathlib import Path

import applications.compiler_runner as compiler_runner

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = ("engine", "adapters", "applications")
_LOCAL_RUNNER = REPOSITORY_ROOT / "applications/compiler_runner.py"


def _qualified_production_calls() -> tuple[tuple[str, str], ...]:
    calls: list[tuple[str, str]] = []
    for root_name in PRODUCTION_ROOTS:
        for path in (REPOSITORY_ROOT / root_name).rglob("*.py"):
            if path == _LOCAL_RUNNER:
                continue
            tree = ast.parse(path.read_bytes(), filename=str(path))
            imported_modules: dict[str, str] = {}
            imported_symbols: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_modules[alias.asname or alias.name] = alias.name
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    for alias in node.names:
                        imported_symbols[alias.asname or alias.name] = (
                            f"{node.module}.{alias.name}"
                        )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    qualified = imported_symbols.get(node.func.id)
                elif (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                ):
                    module = imported_modules.get(node.func.value.id)
                    qualified = f"{module}.{node.func.attr}" if module else None
                else:
                    qualified = None
                if qualified is not None:
                    calls.append((str(path.relative_to(REPOSITORY_ROOT)), qualified))
    return tuple(calls)


def test_unleased_subprocess_helper_is_explicitly_local_only() -> None:
    assert not hasattr(compiler_runner, "compile_in_compiler_runner")
    assert hasattr(compiler_runner, "compile_in_local_compiler_runner")


def test_no_production_module_calls_the_local_compiler_runner() -> None:
    forbidden_module = "applications.compiler_runner"
    forbidden_symbol = "compile_in_local_compiler_runner"
    callers: list[str] = []
    for root_name in PRODUCTION_ROOTS:
        for path in (REPOSITORY_ROOT / root_name).rglob("*.py"):
            if path == REPOSITORY_ROOT / "applications/compiler_runner.py":
                continue
            tree = ast.parse(path.read_bytes(), filename=str(path))
            for node in ast.walk(tree):
                imported_from_runner = isinstance(node, ast.ImportFrom) and (
                    node.module == forbidden_module
                    or any(alias.name == forbidden_symbol for alias in node.names)
                    or (
                        node.module == "applications"
                        and any(
                            alias.name == "compiler_runner" for alias in node.names
                        )
                    )
                )
                imported_runner = isinstance(node, ast.Import) and any(
                    alias.name == forbidden_module for alias in node.names
                )
                if imported_from_runner or imported_runner:
                    callers.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert callers == []


def test_no_production_module_calls_the_unleased_raw_compiler_entry_point() -> None:
    forbidden_calls = {
        "adapters.parsers.ragflow_markdown.compile_rich_markdown",
        "applications.compiler_runner.compile_in_local_compiler_runner",
    }

    assert tuple(
        (path, qualified)
        for path, qualified in _qualified_production_calls()
        if qualified in forbidden_calls
    ) == ()
