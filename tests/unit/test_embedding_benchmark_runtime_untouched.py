from __future__ import annotations

import ast
from pathlib import Path

from engine.supply.embeddings import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ENTRY_POINTS = (
    REPOSITORY_ROOT / "applications" / "embedding_benchmark.py",
    REPOSITORY_ROOT / "eval" / "embedding_benchmark.py",
)
RUNTIME_ENTRY_POINT = REPOSITORY_ROOT / "engine" / "runtime" / "__init__.py"


def _repository_module_name(source_path: Path) -> str:
    relative_path = source_path.relative_to(REPOSITORY_ROOT)
    module_parts = list(relative_path.with_suffix("").parts)
    if module_parts[-1] == "__init__":
        module_parts.pop()
    return ".".join(module_parts)


def _import_from_base(
    module_name: str,
    *,
    is_package: bool,
    node: ast.ImportFrom,
) -> str:
    if node.level == 0:
        return node.module or ""
    source_module_parts = module_name.split(".")
    if not is_package:
        source_module_parts.pop()
    retained_parts = len(source_module_parts) - node.level + 1
    if retained_parts < 0:
        return ""
    base_parts = source_module_parts[:retained_parts]
    if node.module is not None:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _imported_module_names(
    source: str,
    *,
    module_name: str,
    is_package: bool,
) -> tuple[str, ...]:
    tree = ast.parse(source, filename=module_name)
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "importlib"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_module = _import_from_base(
                module_name,
                is_package=is_package,
                node=node,
            )
            if base_module:
                imports.append(base_module)
            imports.extend(
                f"{base_module}.{alias.name}" if base_module else alias.name
                for alias in node.names
                if alias.name != "*"
            )
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id in {"__import__", *import_module_aliases}
                or isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_aliases
                and node.func.attr == "import_module"
            )
        ):
            imports.append(node.args[0].value)
    return tuple(imports)


def _module_source(module_name: str) -> Path | None:
    module_path = REPOSITORY_ROOT.joinpath(*module_name.split("."))
    source_path = module_path.with_suffix(".py")
    if source_path.is_file():
        return source_path
    package_path = module_path / "__init__.py"
    return package_path if package_path.is_file() else None


def _repository_sources_for_import(module_name: str) -> tuple[Path, ...]:
    sources: list[Path] = []
    module_parts = module_name.split(".")
    for part_count in range(1, len(module_parts) + 1):
        source_path = _module_source(".".join(module_parts[:part_count]))
        if source_path is not None and source_path not in sources:
            sources.append(source_path)
    return tuple(sources)


def _reachable_repository_modules(entry_points: tuple[Path, ...]) -> set[str]:
    pending = list(entry_points)
    visited_paths: set[Path] = set()
    reachable_modules: set[str] = set()
    while pending:
        source_path = pending.pop()
        if source_path in visited_paths:
            continue
        visited_paths.add(source_path)
        imported_modules = _imported_module_names(
            source_path.read_text(encoding="utf-8"),
            module_name=_repository_module_name(source_path),
            is_package=source_path.name == "__init__.py",
        )
        for imported_module in imported_modules:
            reachable_modules.add(imported_module)
            pending.extend(_repository_sources_for_import(imported_module))
    return reachable_modules


def test_runtime_composition_oracle_covers_alternate_import_spellings() -> None:
    imported_modules = _imported_module_names(
        """
from engine import runtime
from . import runtime_probe
import importlib as loader
from importlib import import_module as load_module

loader.import_module("engine.runtime.construction")
load_module("engine.runtime.materialized")
""",
        module_name="eval",
        is_package=True,
    )

    assert "engine.runtime" in imported_modules
    assert "eval.runtime_probe" in imported_modules
    assert "engine.runtime.construction" in imported_modules
    assert "engine.runtime.materialized" in imported_modules


def test_offline_benchmark_does_not_compose_production_runtime() -> None:
    benchmark_dependencies = _reachable_repository_modules(BENCHMARK_ENTRY_POINTS)
    runtime_dependencies = _reachable_repository_modules((RUNTIME_ENTRY_POINT,))

    assert not any(
        module_name == "engine.runtime"
        or module_name.startswith("engine.runtime.")
        for module_name in benchmark_dependencies
    )
    assert CONTEXT_FRAGMENT_EMBEDDING_DIMENSION == 384
    assert "applications.embedding_benchmark" not in runtime_dependencies
    assert "eval.embedding_benchmark" not in runtime_dependencies


def test_benchmark_imports_the_one_production_dimension_constant() -> None:
    benchmark_source = (REPOSITORY_ROOT / "eval" / "embedding_benchmark.py").read_text(
        encoding="utf-8"
    )

    assert (
        "from engine.supply.embeddings import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION"
        in benchmark_source
    )
    assert "EMBEDDING_DIMENSION: Final = 384" not in benchmark_source
