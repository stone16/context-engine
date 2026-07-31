from __future__ import annotations

import ast
from pathlib import Path

from adapters.http.app import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPOSITORY_ROOT / "ui"
CONSOLE_PROCESS_SOURCES = (
    REPOSITORY_ROOT / "applications" / "api.py",
    *sorted((REPOSITORY_ROOT / "adapters" / "http").rglob("*.py")),
    *sorted(UI_ROOT.rglob("*.py")),
)
_FORBIDDEN_CONSOLE_IMPORTS = (
    "adapters.parsers.ragflow_markdown",
    "applications.compiler_runner",
    "applications.leased_compiler_runner",
    "applications.worker",
    "engine.persistence.file_imports",
    "engine.supply.compiler_runner",
)
_FORBIDDEN_PERSISTENCE_EXPORTS = frozenset(
    {"FileImportLeaseRedemption", "PostgreSQLFileImportWorker"}
)


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


def _console_compiler_authority_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.update(
                alias.name
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_CONSOLE_IMPORTS)
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.startswith(_FORBIDDEN_CONSOLE_IMPORTS):
                violations.add(module)
            if module == "engine.persistence":
                violations.update(
                    alias.name
                    for alias in node.names
                    if alias.name in _FORBIDDEN_PERSISTENCE_EXPORTS
                )
    return violations


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


def test_console_never_compiles_rich_markdown_in_process() -> None:
    """The co-resident API/UI composition holds no rich compiler authority."""

    assert CONSOLE_PROCESS_SOURCES
    for path in CONSOLE_PROCESS_SOURCES:
        assert _console_compiler_authority_imports(path) == set(), path
        assert "markdown-config-v3" not in path.read_text(encoding="utf-8"), path
