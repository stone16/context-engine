from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import (
    MarkdownCompilerConfig,
    ParsedDocument,
    deserialize_parsed_document,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/markdown"
CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")
RICH_FIXTURES = (
    "rich-frontmatter.md",
    "rich-headings-lists.md",
    "rich-code-tables.md",
    "rich-expanded.md",
    "rich-inline-html.md",
    "rich-mixed-newlines.hex",
)


def _source(name: str) -> bytes:
    path = FIXTURES / name
    if path.suffix == ".hex":
        return bytes.fromhex(path.read_text(encoding="ascii"))
    return path.read_bytes()


@pytest.mark.parametrize("fixture", RICH_FIXTURES)
def test_rich_compilation_digest_is_stable_in_process_and_across_runner(
    fixture: str,
) -> None:
    source = _source(fixture)

    first = compile_rich_markdown(source, CONFIG)
    second = compile_rich_markdown(source, CONFIG)
    command = [
        sys.executable,
        "-m",
        "applications.compiler_runner",
        "--compile",
        "--config",
        CONFIG.version,
    ]
    first_process = subprocess.run(
        command,
        input=source,
        capture_output=True,
        check=True,
    )
    second_process = subprocess.run(
        command,
        input=source,
        capture_output=True,
        check=True,
    )
    assert first_process.stdout == second_process.stdout
    envelope = json.loads(first_process.stdout)
    subprocess_result = deserialize_parsed_document(
        base64.b64decode(envelope["document"], validate=True)
    )

    assert type(first) is ParsedDocument
    assert type(second) is ParsedDocument
    assert type(subprocess_result) is ParsedDocument
    assert first.compilation_digest == second.compilation_digest
    assert first.compilation_digest == subprocess_result.compilation_digest
    assert subprocess_result == first


def test_representation_digest_distinguishes_exact_trailing_newline_bytes() -> None:
    variants = (
        b"# Exact\n\nBody",
        b"# Exact\n\nBody\n",
        b"# Exact\r\n\r\nBody\r\n",
        b"# Exact\n\nBody\n\n",
    )

    outcomes = tuple(compile_rich_markdown(source, CONFIG) for source in variants)

    assert all(type(outcome) is ParsedDocument for outcome in outcomes)
    documents = tuple(
        outcome for outcome in outcomes if type(outcome) is ParsedDocument
    )
    assert tuple(document.canonical_text.encode("utf-8") for document in documents) == (
        variants
    )
    assert len({document.compilation_digest for document in documents}) == len(variants)
