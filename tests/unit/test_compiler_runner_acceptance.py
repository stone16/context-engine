from __future__ import annotations

from pathlib import Path

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import (
    CompilationFailure,
    MarkdownCompilerConfig,
    ParsedDocument,
    SectionKind,
)

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
def test_tracked_rich_construct_corpus_compiles_all_or_nothing(fixture: str) -> None:
    outcome = compile_rich_markdown(_source(fixture), CONFIG)

    assert not isinstance(outcome, CompilationFailure)
    assert type(outcome) is ParsedDocument
    assert outcome.fragments


@pytest.mark.parametrize("fixture", RICH_FIXTURES)
def test_every_fragment_span_round_trips_to_exact_canonical_utf8(
    fixture: str,
) -> None:
    outcome = compile_rich_markdown(_source(fixture), CONFIG)

    assert type(outcome) is ParsedDocument
    canonical = outcome.canonical_text.encode("utf-8")
    for fragment in outcome.fragments:
        span = fragment.position
        assert canonical[span.start.byte_offset : span.end.byte_offset].decode(
            "utf-8"
        ) == fragment.source_text


def test_nested_setext_and_duplicate_headings_have_stable_ancestry() -> None:
    outcome = compile_rich_markdown(_source("rich-headings-lists.md"), CONFIG)

    assert type(outcome) is ParsedDocument
    headings = [
        fragment
        for fragment in outcome.fragments
        if fragment.kind is SectionKind.HEADING
    ]
    assert [fragment.path.segments for fragment in headings] == [
        ("document", "heading[1]"),
        ("document", "heading[1]", "heading[1]"),
        ("document", "heading[1]", "heading[2]"),
    ]
    closing = outcome.fragments[-1]
    assert tuple(heading.text for heading in closing.parent_headings) == (
        "Handbook",
        "Repeated",
    )
    assert closing.path.segments == (
        "document",
        "heading[1]",
        "heading[2]",
        "paragraph[1]",
    )
