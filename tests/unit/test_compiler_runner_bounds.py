from __future__ import annotations

from adapters.parsers.ragflow_markdown import compile_rich_markdown, rich_token_count
from engine.supply import MarkdownCompilerConfig, ParsedDocument

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


def test_oversize_blocks_split_under_hard_bound_with_exact_spans_and_ancestry() -> None:
    ceiling = 64
    source = (
        "# Root\n\n## Deep\n\n"
        + " ".join(f"token{index}" for index in range(17000))
        + "\n"
    ).encode()

    outcome = compile_rich_markdown(source, CONFIG, token_ceiling=ceiling)

    assert type(outcome) is ParsedDocument
    paragraph_fragments = outcome.fragments[2:]
    assert len(paragraph_fragments) > 1
    canonical = outcome.canonical_text.encode("utf-8")
    for fragment in paragraph_fragments:
        assert rich_token_count(fragment.contextual_text) <= ceiling
        assert tuple(heading.text for heading in fragment.parent_headings) == (
            "Root",
            "Deep",
        )
        span = fragment.position
        assert canonical[span.start.byte_offset : span.end.byte_offset].decode(
            "utf-8"
        ) == fragment.source_text
    assert " ".join(fragment.source_text for fragment in paragraph_fragments) == (
        outcome.canonical_text.split("\n\n", 2)[2].rstrip("\n")
    )
