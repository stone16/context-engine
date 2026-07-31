from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import applications.compiler_runner as compiler_runner

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = ("engine", "adapters", "applications")
_IGNORED_MODULES = frozenset(
    {
        "adapters.parsers.ragflow_markdown",
        "applications.compiler_runner",
        "applications.leased_compiler_runner",
    }
)
_FORBIDDEN_MODULES = frozenset(
    {
        "adapters.parsers.ragflow_markdown",
        "applications.compiler_runner",
        "eval._compiler_acceptance",
    }
)
_LEASED_RUNNER_MODULE = "engine.supply.compiler_runner"


def _module_name(repository_root: Path, path: Path) -> str:
    relative = path.relative_to(repository_root)
    parts = relative.with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve_import_module(
    module: str,
    imported: str | None,
    level: int,
    *,
    is_package: bool,
) -> str:
    if level == 0:
        return imported or ""
    package = module if is_package else module.rsplit(".", maxsplit=1)[0]
    parts = package.split(".") if package else []
    retained = parts[: max(0, len(parts) - level + 1)]
    imported_parts = imported.split(".") if imported else []
    return ".".join((*retained, *imported_parts))


def _forbidden_imports(
    module: str,
    tree: ast.Module,
    *,
    is_package: bool,
) -> frozenset[str]:
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
                is_package=is_package,
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
                for imported in _forbidden_imports(
                    module,
                    tree,
                    is_package=path.name == "__init__.py",
                )
            )
    return tuple(sorted(violations))


def _leased_runner_imports(
    repository_root: Path,
) -> tuple[tuple[str, str], ...]:
    imports: set[tuple[str, str]] = set()
    for root_name in PRODUCTION_ROOTS:
        for path in (repository_root / root_name).rglob("*.py"):
            tree = ast.parse(path.read_bytes(), filename=str(path))
            relative = path.relative_to(repository_root).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(
                        (relative, alias.name)
                        for alias in node.names
                        if alias.name == _LEASED_RUNNER_MODULE
                        or alias.name.startswith(f"{_LEASED_RUNNER_MODULE}.")
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module == _LEASED_RUNNER_MODULE
                ):
                    imports.update(
                        (relative, f"{_LEASED_RUNNER_MODULE}.{alias.name}")
                        for alias in node.names
                    )
    return tuple(sorted(imports))


def test_unleased_subprocess_helper_is_explicitly_local_only() -> None:
    assert not hasattr(compiler_runner, "compile_in_compiler_runner")
    assert hasattr(compiler_runner, "compile_in_local_compiler_runner")


def test_no_production_module_imports_an_unleased_compiler_surface() -> None:
    assert _production_import_violations(
        REPOSITORY_ROOT,
        production_roots=PRODUCTION_ROOTS,
        ignored_modules=_IGNORED_MODULES,
    ) == ()


def test_only_the_file_import_worker_can_select_the_leased_compiler() -> None:
    assert _leased_runner_imports(REPOSITORY_ROOT) == (
        (
            "engine/persistence/file_imports.py",
            "engine.supply.compiler_runner.compile_in_leased_compiler_runner",
        ),
    )


def test_leased_entry_imports_only_the_registered_raw_compiler_surface() -> None:
    path = REPOSITORY_ROOT / "applications/leased_compiler_runner.py"
    tree = ast.parse(path.read_bytes(), filename=str(path))

    assert _forbidden_imports(
        "applications.leased_compiler_runner",
        tree,
        is_package=False,
    ) == frozenset({"adapters.parsers.ragflow_markdown.compile_rich_markdown"})


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


def test_production_import_gate_rejects_live_relative_call_from_package_init(
    tmp_path: Path,
) -> None:
    package = tmp_path / "adapters/parsers"
    package.mkdir(parents=True)
    (tmp_path / "adapters/__init__.py").write_text("", encoding="utf-8")
    (package / "ragflow_markdown.py").write_text(
        "def compile_rich_markdown(source: bytes):\n"
        "    return source + b' compiled'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from .ragflow_markdown import compile_rich_markdown\n"
        "\n"
        "def production_rich_compile(source: bytes):\n"
        "    return compile_rich_markdown(source)\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from adapters.parsers import production_rich_compile; "
            "assert production_rich_compile(b'source') == b'source compiled'",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode == 0
    assert _production_import_violations(
        tmp_path,
        production_roots=("adapters",),
        ignored_modules=frozenset(),
    ) == (
        (
            "adapters/parsers/__init__.py",
            "adapters.parsers.ragflow_markdown.compile_rich_markdown",
        ),
    )


def test_production_import_gate_resolves_every_relative_package_level(
    tmp_path: Path,
) -> None:
    parsers = tmp_path / "adapters/parsers"
    nested = parsers / "nested"
    deeper = nested / "deeper"
    deeper.mkdir(parents=True)
    (parsers / "__init__.py").write_text(
        "from . import ragflow_markdown\n",
        encoding="utf-8",
    )
    (nested / "__init__.py").write_text(
        "from ..ragflow_markdown import compile_rich_markdown\n",
        encoding="utf-8",
    )
    (deeper / "__init__.py").write_text(
        "from ... import ragflow_markdown\n",
        encoding="utf-8",
    )

    assert _production_import_violations(
        tmp_path,
        production_roots=("adapters",),
        ignored_modules=frozenset(),
    ) == (
        (
            "adapters/parsers/__init__.py",
            "adapters.parsers.ragflow_markdown",
        ),
        (
            "adapters/parsers/nested/__init__.py",
            "adapters.parsers.ragflow_markdown.compile_rich_markdown",
        ),
        (
            "adapters/parsers/nested/deeper/__init__.py",
            "adapters.parsers.ragflow_markdown",
        ),
    )
