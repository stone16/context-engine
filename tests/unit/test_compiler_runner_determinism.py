from __future__ import annotations

from pathlib import Path

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from applications.compiler_runner import compile_in_local_compiler_runner
from engine.supply import MarkdownCompilerConfig, ParsedDocument

FIXTURES = Path(__file__).parents[1] / "fixtures/markdown"
CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")
RICH_FIXTURES = (
    "rich-frontmatter.md",
    "rich-headings-lists.md",
    "rich-code-tables.md",
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
    subprocess_result = compile_in_local_compiler_runner(source, CONFIG)

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
