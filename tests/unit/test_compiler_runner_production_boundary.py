from __future__ import annotations

import ast
from pathlib import Path

import applications.compiler_runner as compiler_runner

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = ("engine", "adapters", "applications")


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
