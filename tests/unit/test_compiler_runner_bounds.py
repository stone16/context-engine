from __future__ import annotations

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown, rich_token_count
from engine.supply import CompilationFailure, MarkdownCompilerConfig, ParsedDocument

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


def test_oversize_blocks_split_under_hard_bound_with_exact_spans_and_ancestry() -> None:
    ceiling = 64
    source = (
        "# Root\n\n## Deep\n\n"
        + " ".join(f"token{index}" for index in range(17000))
        + "\n"
    ).encode()

    config = MarkdownCompilerConfig(
        version="markdown-config-v3",
        token_ceiling=ceiling,
    )
    outcome = compile_rich_markdown(source, config)

    assert type(outcome) is ParsedDocument
    assert outcome.provenance.token_ceiling == ceiling
    paragraph_fragments = outcome.fragments[2:]
    assert len(paragraph_fragments) > 1
    for fragment in paragraph_fragments:
        assert rich_token_count(fragment.contextual_text) <= ceiling
        assert tuple(heading.text for heading in fragment.parent_headings) == (
            "Root",
            "Deep",
        )
        span = fragment.position
        assert source[span.start.byte_offset : span.end.byte_offset] == (
            fragment.source_text.encode("utf-8")
        )
    assert " ".join(fragment.source_text for fragment in paragraph_fragments) == (
        outcome.canonical_text.split("\n\n", 2)[2].rstrip("\n")
    )


def test_oversize_indivisible_code_block_refuses_instead_of_token_splitting() -> None:
    ceiling = 32
    code = " ".join(f"operation_{index}()" for index in range(200))
    source = f"# Root\r\n\r\n```python\r\n{code}\r\n```\r\n".encode()

    config = MarkdownCompilerConfig(
        version="markdown-config-v3",
        token_ceiling=ceiling,
    )
    outcome = compile_rich_markdown(source, config)

    assert type(outcome) is CompilationFailure


def test_every_emitted_fragment_obeys_the_ceiling() -> None:
    ceiling = 12
    source = (
        "# Root\n\n" + " ".join(f"word{index}" for index in range(80)) + "\n"
    ).encode()

    config = MarkdownCompilerConfig(
        version="markdown-config-v3",
        token_ceiling=ceiling,
    )
    outcome = compile_rich_markdown(source, config)

    assert type(outcome) is ParsedDocument
    assert all(
        rich_token_count(fragment.contextual_text) <= ceiling
        for fragment in outcome.fragments
    )


def test_rich_token_ceiling_is_validated_by_configuration() -> None:
    with pytest.raises(ValueError, match="token ceiling"):
        MarkdownCompilerConfig(version="markdown-config-v3", token_ceiling=0)


def test_ceiling_is_serialized_as_representation_provenance() -> None:
    source = b"# Root\n\n" + b"word " * 80
    narrow = compile_rich_markdown(
        source,
        MarkdownCompilerConfig(version="markdown-config-v3", token_ceiling=16),
    )
    wide = compile_rich_markdown(
        source,
        MarkdownCompilerConfig(version="markdown-config-v3", token_ceiling=64),
    )

    assert type(narrow) is ParsedDocument
    assert type(wide) is ParsedDocument
    assert narrow.provenance.token_ceiling == 16
    assert wide.provenance.token_ceiling == 64
    assert narrow.provenance != wide.provenance
    assert narrow.compilation_digest != wide.compilation_digest
