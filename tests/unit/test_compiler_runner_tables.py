from __future__ import annotations

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import (
    CompilationFailure,
    CompilationFailureCode,
    MarkdownCompilerConfig,
    ParsedDocument,
    SectionKind,
)

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


def test_every_table_fragment_carries_a_round_tripping_source_span() -> None:
    tables = "\n\n".join(
        (
            f"| Key | Value |\n| --- | --- |\n"
            f"| item-{index} | value-{index} |"
        )
        for index in range(40)
    )
    source = f"# Tables\n\n{tables}\n".encode()

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    table_fragments = tuple(
        fragment
        for fragment in outcome.fragments
        if fragment.kind is SectionKind.TABLE
    )
    assert len(table_fragments) == 40
    canonical = outcome.canonical_text.encode("utf-8")
    for fragment in table_fragments:
        span = fragment.position
        assert canonical[span.start.byte_offset : span.end.byte_offset].decode(
            "utf-8"
        ) == fragment.source_text


def test_table_spans_round_trip_against_original_crlf_bytes() -> None:
    source = (
        b"# Tables\r\n\r\n"
        b"| Key | Value |\r\n"
        b"| --- | --- |\r\n"
        b"| alpha | ready |\r\n"
    )

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    table = next(
        fragment
        for fragment in outcome.fragments
        if fragment.kind is SectionKind.TABLE
    )
    span = table.position
    assert source[span.start.byte_offset : span.end.byte_offset] == (
        table.source_text.encode("utf-8")
    )
    assert b"\r\n" in table.source_text.encode("utf-8")


def test_table_with_trailing_whitespace_compiles_and_retains_exact_span() -> None:
    source = (
        b"# Tables\n\n"
        b"| Key | Value |\n"
        b"| --- | --- |\n"
        b"| alpha | ready |  \n"
    )

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    table = next(
        fragment
        for fragment in outcome.fragments
        if fragment.kind is SectionKind.TABLE
    )
    span = table.position
    assert source[span.start.byte_offset : span.end.byte_offset] == (
        table.source_text.encode("utf-8")
    )
    assert table.source_text.endswith("  ")


@pytest.mark.parametrize(
    "source",
    (
        b"| A | B |\n| --- | --- |\n| x | y |\n| ragged |\n",
        b"| A | B |\n| --- | --- |\n| x | y |\n|  |  |\n",
        b"| A | B |\n| --- | --- | --- |\n| x | y |\n",
    ),
)
def test_ragged_or_empty_row_keeps_table_as_one_exact_atomic_block(
    source: bytes,
) -> None:

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    assert len(outcome.fragments) == 1
    fragment = outcome.fragments[0]
    assert fragment.kind is SectionKind.PARAGRAPH
    span = fragment.position
    assert source[span.start.byte_offset : span.end.byte_offset] == source.rstrip(b"\n")
    assert fragment.source_text.encode("utf-8") == source.rstrip(b"\n")


def test_oversize_ragged_table_refuses_instead_of_splitting_atomic_source() -> None:
    source = (
        b"| A | B |\n"
        b"| --- | --- |\n"
        b"| one two three | four five six |\n"
        b"| ragged seven eight |\n"
    )
    bounded_config = MarkdownCompilerConfig(
        version="markdown-config-v3",
        token_ceiling=8,
    )

    outcome = compile_rich_markdown(source, bounded_config)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.construct is None
