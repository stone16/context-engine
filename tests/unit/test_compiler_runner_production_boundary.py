from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import applications.compiler_runner as compiler_runner

REPOSITORY_ROOT = Path(__file__).parents[2]
PRODUCTION_ROOTS = ("engine", "adapters", "applications")
_LOCAL_RUNNER = REPOSITORY_ROOT / "applications/compiler_runner.py"
_FORBIDDEN_PRODUCTION_CALLS = frozenset(
    {
        "adapters.parsers.ragflow_markdown.compile_rich_markdown",
        "applications.compiler_runner.compile_in_local_compiler_runner",
    }
)
_IGNORED_ROOT_MODULES = frozenset(
    {
        "adapters.parsers.ragflow_markdown",
        "applications.compiler_runner",
    }
)


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


def _module_name(repository_root: Path, path: Path) -> str:
    relative = path.relative_to(repository_root)
    parts = relative.with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _function_definitions(
    module: str, tree: ast.Module
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def collect(nodes: list[ast.stmt], prefix: str) -> None:
        for node in nodes:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = f"{prefix}.{node.name}"
                definitions[qualified] = node
                collect(node.body, qualified)
            elif isinstance(node, ast.ClassDef):
                collect(node.body, f"{prefix}.{node.name}")

    collect(tree.body, module)
    return definitions


def _resolve_import_module(module: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    package = module.rsplit(".", maxsplit=1)[0]
    parts = package.split(".") if package else []
    retained = parts[: max(0, len(parts) - level + 1)]
    return ".".join((*retained, *((imported or "").split("."))))


def _imports(nodes: Iterable[ast.AST], *, module: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", maxsplit=1)[0]
                imports[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_module(
                module,
                node.module,
                node.level,
            )
            for alias in node.names:
                imports[alias.asname or alias.name] = (
                    f"{imported_module}.{alias.name}"
                    if imported_module
                    else alias.name
                )
    return imports


def _module_imports(tree: ast.Module, *, module: str) -> dict[str, str]:
    return _imports(tree.body, module=module)


def _scoped_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.AST]:
    pending: list[ast.AST] = list(node.body)
    while pending:
        current = pending.pop()
        yield current
        if isinstance(
            current,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda,
        ):
            continue
        pending.extend(ast.iter_child_nodes(current))


def _resolve_expression(
    expression: ast.expr,
    *,
    names: dict[str, str],
    module: str,
    module_functions: frozenset[str],
) -> str | None:
    if isinstance(expression, ast.Name):
        imported = names.get(expression.id)
        local = f"{module}.{expression.id}"
        return imported if imported is not None else (
            local if local in module_functions else None
        )
    if isinstance(expression, ast.Attribute):
        base = _resolve_expression(
            expression.value,
            names=names,
            module=module,
            module_functions=module_functions,
        )
        return f"{base}.{expression.attr}" if base is not None else None
    return None


def _function_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    qualified: str,
    imports: dict[str, str],
    module: str,
    module_functions: frozenset[str],
) -> frozenset[str]:
    names = dict(imports)
    scoped = tuple(_scoped_nodes(node))
    names.update(_imports(scoped, module=module))
    names.update(
        {
            child.name: f"{qualified}.{child.name}"
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        }
    )
    changed = True
    while changed:
        changed = False
        for child in scoped:
            if not isinstance(child, ast.Assign):
                continue
            resolved = _resolve_expression(
                child.value,
                names=names,
                module=module,
                module_functions=module_functions,
            )
            if resolved is None:
                continue
            for target in child.targets:
                if isinstance(target, ast.Name) and names.get(target.id) != resolved:
                    names[target.id] = resolved
                    changed = True
    return frozenset(
        resolved
        for child in scoped
        if isinstance(child, ast.Call)
        for resolved in (
            _resolve_expression(
                child.func,
                names=names,
                module=module,
                module_functions=module_functions,
            ),
        )
        if resolved is not None
    )


def _production_paths_to_forbidden(
    repository_root: Path,
    *,
    production_roots: tuple[str, ...],
    ignored_root_modules: frozenset[str],
    forbidden: frozenset[str],
) -> tuple[tuple[str, ...], ...]:
    parsed: dict[str, ast.Module] = {}
    definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for root_name in production_roots:
        root = repository_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            module = _module_name(repository_root, path)
            tree = ast.parse(path.read_bytes(), filename=str(path))
            parsed[module] = tree
            definitions.update(_function_definitions(module, tree))
    function_names = frozenset(definitions)
    graph: dict[str, frozenset[str]] = {}
    for qualified, definition in definitions.items():
        module = qualified.rsplit(".", maxsplit=1)[0]
        while module not in parsed:
            module = module.rsplit(".", maxsplit=1)[0]
        graph[qualified] = _function_calls(
            definition,
            qualified=qualified,
            imports=_module_imports(parsed[module], module=module),
            module=module,
            module_functions=frozenset(
                name for name in function_names if name.startswith(f"{module}.")
            ),
        )

    paths: set[tuple[str, ...]] = set()

    def walk(current: str, path: tuple[str, ...]) -> None:
        for called in graph.get(current, frozenset()):
            next_path = (*path, called)
            if called in forbidden:
                paths.add(next_path)
            elif called in graph and called not in path:
                walk(called, next_path)

    for function in graph:
        if not any(
            function == module or function.startswith(f"{module}.")
            for module in ignored_root_modules
        ):
            walk(function, (function,))
    return tuple(sorted(paths))


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
    assert _production_paths_to_forbidden(
        REPOSITORY_ROOT,
        production_roots=PRODUCTION_ROOTS,
        ignored_root_modules=_IGNORED_ROOT_MODULES,
        forbidden=_FORBIDDEN_PRODUCTION_CALLS,
    ) == ()


def test_production_reachability_analysis_detects_transitive_indirect_calls(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "adapters/parsers").mkdir(parents=True)
    (tmp_path / "applications/bridge.py").write_text(
        """
from adapters.parsers.ragflow_markdown import compile_rich_markdown as rich_compile

def hidden(raw, config):
    compiler = rich_compile
    return compiler(raw, config)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "applications/entry.py").write_text(
        """
from applications.bridge import hidden

def production_entry(raw, config):
    return hidden(raw, config)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "adapters/parsers/ragflow_markdown.py").write_text(
        "def compile_rich_markdown(raw, config):\n    return raw, config\n",
        encoding="utf-8",
    )

    paths = _production_paths_to_forbidden(
        tmp_path,
        production_roots=("engine", "adapters", "applications"),
        ignored_root_modules=frozenset(),
        forbidden=frozenset(
            {"adapters.parsers.ragflow_markdown.compile_rich_markdown"}
        ),
    )

    assert (
        "applications.entry.production_entry",
        "applications.bridge.hidden",
        "adapters.parsers.ragflow_markdown.compile_rich_markdown",
    ) in paths


def test_production_reachability_detects_scoped_and_relative_import_aliases(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "adapters/parsers").mkdir(parents=True)
    (tmp_path / "applications/bridge.py").write_text(
        """
def hidden(raw, config):
    from adapters.parsers.ragflow_markdown import compile_rich_markdown as rich
    compiler = rich
    return compiler(raw, config)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "applications/entry.py").write_text(
        """
from .bridge import hidden as delegated

def production_entry(raw, config):
    return delegated(raw, config)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "adapters/parsers/ragflow_markdown.py").write_text(
        "def compile_rich_markdown(raw, config):\n    return raw, config\n",
        encoding="utf-8",
    )

    paths = _production_paths_to_forbidden(
        tmp_path,
        production_roots=("engine", "adapters", "applications"),
        ignored_root_modules=frozenset(),
        forbidden=frozenset(
            {"adapters.parsers.ragflow_markdown.compile_rich_markdown"}
        ),
    )

    assert (
        "applications.entry.production_entry",
        "applications.bridge.hidden",
        "adapters.parsers.ragflow_markdown.compile_rich_markdown",
    ) in paths


def test_production_reachability_detects_reachable_nested_closure(
    tmp_path: Path,
) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "adapters/parsers").mkdir(parents=True)
    (tmp_path / "applications/entry.py").write_text(
        """
def production_entry(raw, config):
    def hidden():
        from adapters.parsers.ragflow_markdown import compile_rich_markdown
        return compile_rich_markdown(raw, config)
    return hidden()
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "adapters/parsers/ragflow_markdown.py").write_text(
        "def compile_rich_markdown(raw, config):\n    return raw, config\n",
        encoding="utf-8",
    )

    paths = _production_paths_to_forbidden(
        tmp_path,
        production_roots=("engine", "adapters", "applications"),
        ignored_root_modules=frozenset(),
        forbidden=frozenset(
            {"adapters.parsers.ragflow_markdown.compile_rich_markdown"}
        ),
    )

    assert (
        "applications.entry.production_entry",
        "applications.entry.production_entry.hidden",
        "adapters.parsers.ragflow_markdown.compile_rich_markdown",
    ) in paths
