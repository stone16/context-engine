from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
REGISTRATION_ROOT = REPOSITORY_ROOT / "third_party/ragflow"
REGISTRATION_PATH = REGISTRATION_ROOT / "UPSTREAM.toml"
REQUIRED_EXCLUSIONS = {
    "deepdoc/parser/__init__.py",
    "rag/app/naive.py",
    "rag/nlp",
}
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "argparse",
    "collections",
    "dataclasses",
    "enum",
    "hashlib",
    "html",
    "json",
    "logging",
    "markdown",
    "pathlib",
    "re",
    "sys",
    "typing",
    "unicodedata",
}


def _registration() -> dict[str, object]:
    return tomllib.loads(REGISTRATION_PATH.read_text(encoding="utf-8"))


def test_vendored_bytes_match_complete_pinned_registration() -> None:
    registration = _registration()

    assert registration["repository"] == "https://github.com/infiniflow/ragflow.git"
    commit = registration["commit"]
    assert isinstance(commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert commit == "4391e03886b996201f3b8818f671b19eb24d0f7b"
    assert registration["reuse_mode"] == "copy-patch"
    assert registration["approval"] == (
        "https://github.com/stone16/context-engine/issues/124"
    )
    assert registration["source_paths"] == ["deepdoc/parser/markdown_parser.py"]
    assert registration["nested_dependencies"] == [
        {
            "name": "Python-Markdown",
            "version": "3.6",
            "license": "BSD-3-Clause",
            "license_path": "third_party/ragflow/LICENSE.python-markdown",
        }
    ]
    excluded_paths = registration["excluded_paths"]
    assert isinstance(excluded_paths, list)
    assert set(excluded_paths) >= REQUIRED_EXCLUSIONS

    files = registration["files"]
    assert isinstance(files, list)
    assert files
    registered_paths: set[Path] = set()
    for entry in files:
        assert isinstance(entry, dict)
        assert set(entry) == {"upstream_path", "vendored_path", "sha256"}
        upstream_path = entry["upstream_path"]
        vendored_path = entry["vendored_path"]
        expected_hash = entry["sha256"]
        assert isinstance(upstream_path, str) and upstream_path
        assert isinstance(vendored_path, str) and vendored_path
        assert isinstance(expected_hash, str)
        assert re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        path = REPOSITORY_ROOT / vendored_path
        path.relative_to(REGISTRATION_ROOT)
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        registered_paths.add(path)

    vendored_files = {
        path
        for path in REGISTRATION_ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(REGISTRATION_ROOT).parts
        and path.name
        not in {
            "LICENSE.python-markdown",
            "LICENSE.upstream",
            "MODIFICATIONS.md",
            "UPSTREAM.toml",
            "sbom.cyclonedx.json",
        }
        and "patches" not in path.relative_to(REGISTRATION_ROOT).parts
    }
    assert registered_paths == vendored_files
    assert (REGISTRATION_ROOT / "LICENSE.upstream").is_file()
    assert hashlib.sha256(
        (REGISTRATION_ROOT / "LICENSE.python-markdown").read_bytes()
    ).hexdigest() == "7ba4eb6d10b32b2d11dce13821340351cdbbb30ba8ccc67841db2ffd86e79aca"
    assert (REGISTRATION_ROOT / "MODIFICATIONS.md").is_file()
    assert (REGISTRATION_ROOT / "patches").is_dir()
    assert (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").is_file()
    sbom = json.loads(
        (REGISTRATION_ROOT / "sbom.cyclonedx.json").read_text(encoding="utf-8")
    )
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["bom-ref"] == (
        "context-engine:third-party:ragflow"
    )
    assert {
        (property_value["name"], property_value["value"])
        for property_value in sbom["metadata"]["properties"]
    } >= {
        ("context-engine:sbom:scope", "third_party/ragflow"),
        ("context-engine:sbom:artifact-wide", "false"),
    }
    assert {component["name"] for component in sbom["components"]} == {
        "Python-Markdown",
        "RAGFlow Markdown parser",
    }


def test_vendored_subtree_imports_only_approved_dependencies() -> None:
    registration = _registration()
    files = registration["files"]
    assert isinstance(files, list)

    imports: set[str] = set()
    for entry in files:
        assert isinstance(entry, dict)
        vendored_path = entry["vendored_path"]
        assert isinstance(vendored_path, str)
        path = REPOSITORY_ROOT / vendored_path
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None
                imports.add(node.module.partition(".")[0])

    assert imports <= ALLOWED_IMPORT_ROOTS
    modifications = (REGISTRATION_ROOT / "MODIFICATIONS.md").read_text(
        encoding="utf-8"
    )
    assert "Python-Markdown" in modifications
    assert "BSD 3-Clause" in modifications


def test_registered_parser_region_is_executed_by_the_ce_adapter() -> None:
    adapter = REPOSITORY_ROOT / "adapters/parsers/ragflow_markdown.py"
    tree = ast.parse(adapter.read_bytes(), filename=str(adapter))

    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "third_party.ragflow.deepdoc.parser.markdown_parser"
    ]
    assert len(imports) == 1
    assert [alias.name for alias in imports[0].names] == [
        "MarkdownElementExtractor"
    ]
    called_helpers = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {
        "_get_fence_marker",
        "_is_closing_fence",
        "_is_table_row",
        "_is_table_separator_row",
        "_table_cells",
    } <= called_helpers
