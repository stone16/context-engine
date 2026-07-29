from __future__ import annotations

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import MarkdownCompilerConfig, ParsedDocument, SectionKind

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
