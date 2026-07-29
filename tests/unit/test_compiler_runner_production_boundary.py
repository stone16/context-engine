from __future__ import annotations

import ast
from pathlib import Path

import applications.compiler_runner as compiler_runner

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = ("engine", "adapters", "applications")
_IGNORED_MODULES = frozenset(
    {
        "adapters.parsers.ragflow_markdown",
        "applications.compiler_runner",
    }
)
_FORBIDDEN_MODULES = frozenset(
    {
        "adapters.parsers.ragflow_markdown",
        "applications.compiler_runner",
        "eval._compiler_acceptance",
    }
)


def _module_name(repository_root: Path, path: Path) -> str:
    relative = path.relative_to(repository_root)
    parts = relative.with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve_import_module(module: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    package = module.rsplit(".", maxsplit=1)[0]
    parts = package.split(".") if package else []
    retained = parts[: max(0, len(parts) - level + 1)]
    imported_parts = imported.split(".") if imported else []
    return ".".join((*retained, *imported_parts))


def _forbidden_imports(module: str, tree: ast.Module) -> frozenset[str]:
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(
                    alias.name == forbidden
                    or alias.name.startswith(f"{forbidden}.")
                    for forbidden in _FORBIDDEN_MODULES
                ):
                    violations.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_module(
                module,
                node.module,
                node.level,
            )
            for alias in node.names:
                qualified = (
                    f"{imported_module}.{alias.name}"
                    if imported_module
                    else alias.name
                )
                if any(
                    imported_module == forbidden
                    or imported_module.startswith(f"{forbidden}.")
                    or qualified == forbidden
                    or qualified.startswith(f"{forbidden}.")
                    for forbidden in _FORBIDDEN_MODULES
                ):
                    violations.add(qualified)
    return frozenset(violations)


def _production_import_violations(
    repository_root: Path,
    *,
    production_roots: tuple[str, ...],
    ignored_modules: frozenset[str],
) -> tuple[tuple[str, str], ...]:
    violations: set[tuple[str, str]] = set()
    for root_name in production_roots:
        root = repository_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            module = _module_name(repository_root, path)
            if module in ignored_modules:
                continue
            tree = ast.parse(path.read_bytes(), filename=str(path))
            relative = path.relative_to(repository_root).as_posix()
            violations.update(
                (relative, imported)
                for imported in _forbidden_imports(module, tree)
            )
    return tuple(sorted(violations))


def test_unleased_subprocess_helper_is_explicitly_local_only() -> None:
    assert not hasattr(compiler_runner, "compile_in_compiler_runner")
    assert hasattr(compiler_runner, "compile_in_local_compiler_runner")


def test_no_production_module_imports_an_unleased_compiler_surface() -> None:
    assert _production_import_violations(
        REPOSITORY_ROOT,
        production_roots=PRODUCTION_ROOTS,
        ignored_modules=_IGNORED_MODULES,
    ) == ()


def test_direct_production_import_gate_rejects_the_unleased_entry_point(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "applications/entry.py").write_text(
        "from applications.compiler_runner import "
        "compile_in_local_compiler_runner\n",
        encoding="utf-8",
    )

    assert _production_import_violations(
        tmp_path,
        production_roots=("applications",),
        ignored_modules=frozenset(),
    ) == (
        (
            "applications/entry.py",
            "applications.compiler_runner.compile_in_local_compiler_runner",
        ),
    )


def test_production_import_gate_rejects_private_capability_imports(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "applications/entry.py").write_text(
        "from eval import _compiler_acceptance\n",
        encoding="utf-8",
    )

    assert _production_import_violations(
        tmp_path,
        production_roots=("applications",),
        ignored_modules=frozenset(),
    ) == (("applications/entry.py", "eval._compiler_acceptance"),)


def test_production_import_gate_rejects_module_and_submodule_spellings(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "applications/entry.py").write_text(
        "import applications.compiler_runner\n"
        "import adapters.parsers.ragflow_markdown.helpers\n",
        encoding="utf-8",
    )

    assert _production_import_violations(
        tmp_path,
        production_roots=("applications",),
        ignored_modules=frozenset(),
    ) == (
        ("applications/entry.py", "adapters.parsers.ragflow_markdown.helpers"),
        ("applications/entry.py", "applications.compiler_runner"),
    )
