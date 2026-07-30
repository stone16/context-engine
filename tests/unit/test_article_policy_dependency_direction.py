from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_control_and_persistence_do_not_depend_on_runtime_article_policy() -> None:
    shared_symbols = {
        "ArticleAccessPolicy",
        "ArticleAccessPolicySetting",
        "ArticlePolicyResolution",
        "apply_source_acl_floor",
        "resolve_article_access_policy",
    }
    for package in ("control", "persistence"):
        for path in (REPOSITORY_ROOT / "engine" / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            policy_imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module is not None
                and shared_symbols.intersection(alias.name for alias in node.names)
            }
            assert policy_imports <= {"engine.article_access_policy"}, path
