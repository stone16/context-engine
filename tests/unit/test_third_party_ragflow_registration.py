from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from adapters.parsers.ragflow_documents import compile_document_bytes
from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import (
    DOCX_CONFIG_V1,
    PDF_TEXT_OUTLINE_V1,
    CompilationProfileRef,
    DocumentCompilationFailure,
    MarkdownCompilerConfig,
    ParsedDocument,
)
from third_party.ragflow.deepdoc.parser.markdown_parser import (
    MarkdownElementExtractor,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
REGISTRATION_ROOT = REPOSITORY_ROOT / "third_party/ragflow"
REGISTRATION_PATH = REGISTRATION_ROOT / "UPSTREAM.toml"
REQUIRED_EXCLUSIONS = {
    "deepdoc/parser/__init__.py",
    "deepdoc/parser/pdf_parser.py",
    "deepdoc/vision",
    "common/constants.py",
    "rag/app/naive.py",
    "rag/nlp",
    "rag/utils/lazy_image.py",
}
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "argparse",
    "collections",
    "dataclasses",
    "enum",
    "hashlib",
    "html",
    "io",
    "json",
    "logging",
    "markdown",
    "pathlib",
    "pypdf",
    "pypdfium2",
    "re",
    "sys",
    "typing",
    "unicodedata",
    "docx",
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
    assert registration["approvals"] == [
        {
            "reference": "https://github.com/stone16/context-engine/issues/124",
            "source_paths": ["deepdoc/parser/markdown_parser.py"],
        },
        {
            "reference": (
                "https://github.com/stone16/context-engine/issues/204; "
                "maintainer Decision D6 recorded in "
                "docs/research/2026-07-31-five-repository-implementation-blueprint.md "
                "section 5"
            ),
            "source_paths": [
                "deepdoc/parser/docx_parser.py",
                "deepdoc/parser/utils.py",
            ],
        },
    ]
    source_paths = registration["source_paths"]
    assert isinstance(source_paths, list)
    assert set(source_paths) == {
        "deepdoc/parser/markdown_parser.py",
        "deepdoc/parser/docx_parser.py",
        "deepdoc/parser/utils.py",
    }
    assert registration["nested_dependencies"] == [
        {
            "name": "Python-Markdown",
            "version": "3.6",
            "license": "BSD-3-Clause",
            "license_path": "third_party/ragflow/LICENSE.python-markdown",
        },
        {
            "name": "python-docx",
            "version": "1.2.0",
            "license": "MIT",
            "license_path": "third_party/ragflow/LICENSE.python-docx",
        },
        {
            "name": "pypdf",
            "version": "6.13.1",
            "license": "BSD-3-Clause",
            "license_path": "third_party/ragflow/LICENSE.pypdf",
        },
        {
            "name": "pypdfium2",
            "version": "5.12.1",
            "license": "BSD-3-Clause",
            "license_path": "third_party/ragflow/LICENSE.pypdfium2",
        },
        {
            "name": "PDFium binary bundle",
            "version": "152.0.7947.0",
            "license": "Apache-2.0",
            "license_path": "third_party/ragflow/LICENSE.pdfium-bundle",
        },
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
            "LICENSE.python-docx",
            "LICENSE.pypdf",
            "LICENSE.pypdfium2",
            "LICENSE.pdfium-bundle",
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
    assert hashlib.sha256(
        (REGISTRATION_ROOT / "LICENSE.python-docx").read_bytes()
    ).hexdigest() == "7652f271e46d0d533e9dc463f3b5fcbdcacf4d6a9c8d6b554d15efd0f37f6132"
    assert hashlib.sha256(
        (REGISTRATION_ROOT / "LICENSE.pypdf").read_bytes()
    ).hexdigest() == "a97ac230e5f33ef10a5367a850eb01f91f1a0b064e34742c7794d2294557f524"
    pdfium_bundle_license = (
        REGISTRATION_ROOT / "LICENSE.pdfium-bundle"
    ).read_text(encoding="utf-8")
    assert "pypdfium2 5.12.1 native bundle license evidence" in (
        pdfium_bundle_license
    )
    assert "BEGIN LICENSE FILE: Apache-2.0.txt" in pdfium_bundle_license
    assert "BEGIN LICENSE FILE: data/darwin_arm64/BUILD_LICENSES/pdfium.txt" in (
        pdfium_bundle_license
    )
    assert (REGISTRATION_ROOT / "MODIFICATIONS.md").is_file()
    assert (REGISTRATION_ROOT / "patches").is_dir()
    assert {
        path.name for path in (REGISTRATION_ROOT / "patches").glob("issue-204-*.patch")
    } == {
        "issue-204-docx-parser.patch",
        "issue-204-pdf-outline.patch",
    }
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
        "RAGFlow parser regions",
        "python-docx",
        "pypdf",
        "pypdfium2",
        "PDFium binary bundle",
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
    assert "python-docx 1.2.0" in modifications
    assert "pypdf 6.13.1" in modifications
    assert "pypdfium2 5.12.1" in modifications
    assert "PDFium" in modifications
    assert "891ffc11d2a3ac32e5c0d8b25b35aa62ab8cda1033c9e0a93782e9d45e759586" in (
        modifications
    )
    assert "7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849" in (
        modifications
    )


@pytest.mark.parametrize(
    ("filename", "patch_name", "upstream_sha256"),
    (
        (
            "docx_parser.py",
            "issue-204-docx-parser.patch",
            "891ffc11d2a3ac32e5c0d8b25b35aa62ab8cda1033c9e0a93782e9d45e759586",
        ),
        (
            "utils.py",
            "issue-204-pdf-outline.patch",
            "7d1674fb7c92b2db24964575cb2290139a823a923da89a321cbdaea795452849",
        ),
    ),
)
def test_issue_204_patch_reconstructs_pinned_and_vendored_bytes(
    tmp_path: Path,
    filename: str,
    patch_name: str,
    upstream_sha256: str,
) -> None:
    vendored = REGISTRATION_ROOT / "deepdoc/parser" / filename
    target = tmp_path / "deepdoc/parser" / filename
    target.parent.mkdir(parents=True)
    target.write_bytes(vendored.read_bytes())
    patch = REGISTRATION_ROOT / "patches" / patch_name

    subprocess.run(
        ["git", "apply", "--reverse", str(patch)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert hashlib.sha256(target.read_bytes()).hexdigest() == upstream_sha256
    subprocess.run(
        ["git", "apply", str(patch)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert target.read_bytes() == vendored.read_bytes()


def test_registered_parser_region_is_executed_by_the_ce_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    helper_names = (
        "_get_fence_marker",
        "_is_closing_fence",
        "_is_table_row",
        "_is_table_separator_row",
        "_table_cells",
    )
    calls: Counter[str] = Counter()
    for helper_name in helper_names:
        original = getattr(MarkdownElementExtractor, helper_name)

        def recording_helper(
            self: MarkdownElementExtractor,
            *args: object,
            _helper_name: str = helper_name,
            _original: object = original,
        ) -> object:
            calls[_helper_name] += 1
            assert callable(_original)
            return _original(self, *args)

        monkeypatch.setattr(MarkdownElementExtractor, helper_name, recording_helper)

    source = (
        b"# Executed\n\n"
        b"```python\nprint('registered')\n```\n\n"
        b"| Key | Value |\n| --- | --- |\n| parser | called |\n"
    )
    outcome = compile_rich_markdown(
        source,
        MarkdownCompilerConfig("markdown-config-v3"),
    )

    assert type(outcome) is ParsedDocument
    assert all(calls[name] > 0 for name in helper_names)


@pytest.mark.parametrize("profile_ref", (DOCX_CONFIG_V1, PDF_TEXT_OUTLINE_V1))
def test_new_registered_regions_are_selected_by_owned_profiles(
    profile_ref: str,
) -> None:
    compiler_ref = (
        "context-engine-docx-v1"
        if profile_ref == DOCX_CONFIG_V1
        else "context-engine-pdf-outline-v1"
    )
    outcome = compile_document_bytes(
        b"invalid fixture",
        CompilationProfileRef(compiler_ref, profile_ref),
    )

    assert type(outcome) is DocumentCompilationFailure
