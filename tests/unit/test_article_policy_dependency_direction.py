from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_control_and_persistence_do_not_depend_on_runtime_article_policy() -> None:
    for package in ("control", "persistence"):
        for path in (REPOSITORY_ROOT / "engine" / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_modules = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            assert "engine.runtime.article_access_policy" not in imported_modules, path
