from __future__ import annotations

import ast
from pathlib import Path

from adapters.http.app import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPOSITORY_ROOT / "ui"


def _absolute_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module is not None:
                roots.add(node.module.partition(".")[0])
    return roots


def test_ui_uses_public_seam_only() -> None:
    """Presentation code may consume HTTP/wire values, never engine internals."""

    sources = tuple(sorted(UI_ROOT.rglob("*.py")))
    assert sources, "the server-rendered UI package must exist"
    for path in sources:
        assert "engine" not in _absolute_import_roots(path), path


def test_every_ui_backing_carrier_is_an_http_route() -> None:
    http_paths = {
        route.path for route in create_app().routes if hasattr(route, "path")
    }
    assert {
        "/v0/resolve",
        "/v0/ui/session",
        "/v0/ui/overview",
        "/v0/ui/profiles",
        "/v0/ui/import/preview",
        "/v0/ui/import/confirm",
        "/v0/ui/articles/view",
        "/v0/ui/articles/preview",
        "/v0/ui/articles/confirm",
        "/v0/ui/feedback",
    } <= http_paths
