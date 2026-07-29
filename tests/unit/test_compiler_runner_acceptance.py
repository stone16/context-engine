from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply import (
    MARKDOWN_COMPILER_V3_VERSION,
    MARKDOWN_RICH_CANONICALIZATION_PROFILE,
    MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
    CompilationFailure,
    CompilationProvenance,
    CompiledFragment,
    MarkdownCompilerConfig,
    ParsedDocument,
    ParsedSection,
    SectionKind,
    SourcePoint,
    SourceSpan,
    StructuralPath,
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
def test_tracked_rich_construct_corpus_compiles_all_or_nothing(fixture: str) -> None:
    outcome = compile_rich_markdown(_source(fixture), CONFIG)

    assert not isinstance(outcome, CompilationFailure)
    assert type(outcome) is ParsedDocument
    assert outcome.fragments


@pytest.mark.parametrize("fixture", RICH_FIXTURES)
def test_every_fragment_span_round_trips_to_exact_original_utf8(
    fixture: str,
) -> None:
    source = _source(fixture)
    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    for fragment in outcome.fragments:
        span = fragment.position
        assert source[span.start.byte_offset : span.end.byte_offset] == (
            fragment.source_text.encode("utf-8")
        )


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


def test_lone_cr_setext_heading_compiles_with_exact_span_and_ancestry() -> None:
    source = "标题🙂\r====\r\r段落\r".encode()

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    heading, paragraph = outcome.fragments
    assert heading.kind is SectionKind.HEADING
    assert heading.source_text == "标题🙂\r===="
    assert paragraph.parent_headings == (outcome.sections[0],)
    for fragment in outcome.fragments:
        span = fragment.position
        assert source[span.start.byte_offset : span.end.byte_offset] == (
            fragment.source_text.encode("utf-8")
        )


@pytest.mark.parametrize(
    "source",
    (
        b"# T\n\n- body  \n",
        b"# T\n\n```\nbody\n```  \n",
        b"# T\n\n   - body  \n",
        b"# T\n\n   ```\nbody\n   ```  \n",
    ),
)
def test_valid_blocks_retain_trailing_whitespace_without_crashing(
    source: bytes,
) -> None:
    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    fragment = outcome.fragments[-1]
    span = fragment.position
    assert source[span.start.byte_offset : span.end.byte_offset] == (
        fragment.source_text.encode("utf-8")
    )


def test_fence_precedes_setext_recognition_when_first_body_line_is_rule_like() -> None:
    source = b"# T\n\n```text\n---\nbody\n```\n"

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    fence = outcome.fragments[-1]
    assert fence.kind is SectionKind.FENCED_CODE
    span = fence.position
    assert source[span.start.byte_offset : span.end.byte_offset] == (
        fence.source_text.encode("utf-8")
    )


@pytest.mark.parametrize(
    "source",
    (
        b"---\nkey: value\n---\n# T\n",
        b"T\n===\n\n## Child\n",
        b"# T\n\n- parent\n  1. child\n",
        b"# T\n\n~~~text\nbody\n~~~\n",
        b"# T\n\n| A | B |\n| --- | --- |\n| x | y |\n",
        b"# T\n\n[[target|label]] and ![[asset]]\n",
        b"# T\n\nText[^1].\n\n[^1]: Note.\n",
        b"# T\n\n<section>body</section>\n",
        b"# T\n\n> [!NOTE]\n> callout body\n",
        b"# T\n\n> quoted paragraph\n",
        b"# T\n\n$x^2$\n",
        b"# T\n\n**strong** and *emphasis*\n",
        b"# T\n\nUse `inline code`.\n",
        b"# T\n\n[link](https://example.test) and ![image](image.png)\n",
        b"# T\n\n[link][ref] and ![image][ref]\n\n[ref]: https://example.test\n",
        b"# T\n\n~~strikethrough~~\n",
        b"# T\n\n<literal value>\n",
        b"# T\n\nhard break  \nnext line\n",
        b"# T\n\nhard break\\\nnext line\n",
        b"# T\n\n---\n",
        b"# T\n\n* * *\n",
        b"# T\n\n_ _ _\n",
        b"# T\n\nordinary paragraph\n",
        b"# T\n\n| A | B |\n| --- | --- |\n| x |\n",
        b"# T\n\n| A | B |\n| --- | --- |\n| x |  |\n",
        b"# T\n\n```\nliteral unmatched fence\n",
    ),
)
def test_every_adr_listed_rich_construct_is_explicitly_accepted(
    source: bytes,
) -> None:
    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.fragments


@pytest.mark.parametrize("source", (b"---\n", b"---\n\ntext\n"))
def test_leading_unclosed_dash_rule_is_an_accepted_thematic_break(
    source: bytes,
) -> None:
    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.fragments[0].source_text == "---"


@pytest.mark.parametrize("newline", (b"\n", b"\r\n"))
@pytest.mark.parametrize("bom", (b"", b"\xef\xbb\xbf"))
@pytest.mark.parametrize(
    ("lines", "expected_fragments"),
    (
        ((b"---",), ("---",)),
        ((b"---", b"---"), ("---", "---")),
        ((b"---", b"", b"---"), ("---", "---")),
        ((b"---", b"key: value", b"---"), ("frontmatter",)),
        ((b"---", b"key: value"), ("---", "key: value")),
        ((b"---", b"---", b"text"), ("---", "---", "text")),
    ),
)
def test_leading_dash_delimiter_matrix_is_closed_and_exact(
    newline: bytes,
    bom: bytes,
    lines: tuple[bytes, ...],
    expected_fragments: tuple[str, ...],
) -> None:
    source = bom + newline.join(lines) + newline

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    expected = tuple(
        (newline.join(lines).decode("utf-8") if value == "frontmatter" else value)
        for value in expected_fragments
    )
    assert tuple(fragment.source_text for fragment in outcome.fragments) == expected
    for fragment in outcome.fragments:
        span = fragment.position
        assert source[span.start.byte_offset : span.end.byte_offset] == (
            fragment.source_text.encode("utf-8")
        )


def test_rich_constructor_rejects_forged_lineage_and_ancestry() -> None:
    compiled = compile_rich_markdown(b"# Root\n\nBody\n", CONFIG)
    assert type(compiled) is ParsedDocument
    heading, body = compiled.sections
    heading_fragment, body_fragment = compiled.fragments
    forged_heading = replace(
        heading,
        text="WRONG",
        path=type(heading.path)(("invented", "heading[7]")),
    )
    forged_body = replace(
        body,
        path=type(body.path)(("invented", "paragraph[9]")),
    )
    forged_fragments = (
        replace(
            heading_fragment,
            fragment_ref="fragment:heading:9",
            path=forged_heading.path,
            search_phrases=("poison-heading",),
        ),
        replace(
            body_fragment,
            fragment_ref="fragment:paragraph:99",
            path=forged_body.path,
            parent_headings=(),
            contextual_text=body_fragment.source_text,
            search_phrases=("poison-body",),
        ),
    )

    expected_error = "rich (?:heading metadata|Fragment derivation)"
    with pytest.raises(ValueError, match=expected_error):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(forged_heading, forged_body),
            fragments=forged_fragments,
            provenance=compiled.provenance,
        )


def _point_for(canonical_text: str, byte_offset: int) -> SourcePoint:
    prefix = canonical_text.encode("utf-8")[:byte_offset].decode("utf-8")
    logical = prefix.replace("\r\n", "\n").replace("\r", "\n")
    return SourcePoint(
        line=logical.count("\n") + 1,
        column=len(logical.rsplit("\n", maxsplit=1)[-1]) + 1,
        byte_offset=byte_offset,
    )


def _forged_sections(
    canonical_text: str,
    parts: tuple[tuple[SectionKind, str, tuple[str, ...]], ...],
) -> tuple[tuple[ParsedSection, ...], tuple[CompiledFragment, ...]]:
    sections: list[ParsedSection] = []
    fragments: list[CompiledFragment] = []
    search_start = 0
    kind_ordinals: dict[SectionKind, int] = {}
    for kind, source, list_items in parts:
        start = canonical_text.index(source, search_start)
        end = start + len(source.encode("utf-8"))
        search_start = end
        kind_ordinals[kind] = kind_ordinals.get(kind, 0) + 1
        ordinal = kind_ordinals[kind]
        path = StructuralPath(("document", f"{kind.value}[{ordinal}]"))
        position = SourceSpan(
            start=_point_for(canonical_text, start),
            end=_point_for(canonical_text, end),
        )
        section = ParsedSection(
            kind=kind,
            text=source,
            path=path,
            position=position,
            list_ordered=False if kind is SectionKind.LIST else None,
            list_items=list_items,
        )
        sections.append(section)
        fragments.append(
            CompiledFragment(
                fragment_ref=f"fragment:{kind.value}:{ordinal}",
                kind=kind,
                path=path,
                position=position,
                source_text=source,
                contextual_text=source,
                parent_headings=(),
                search_phrases=(source,),
            )
        )
    return tuple(sections), tuple(fragments)


def test_rich_constructor_rejects_split_of_undersize_paragraph() -> None:
    canonical_text = "one two three\n"
    sections, fragments = _forged_sections(
        canonical_text,
        (
            (SectionKind.PARAGRAPH, "one", ()),
            (SectionKind.PARAGRAPH, "two three", ()),
        ),
    )

    with pytest.raises(ValueError, match="rich block splitting must be exact"):
        ParsedDocument.rich_v3(
            canonical_text=canonical_text,
            sections=sections,
            fragments=fragments,
            provenance=CompilationProvenance(
                compiler_version=MARKDOWN_COMPILER_V3_VERSION,
                config_version="markdown-config-v3",
                canonicalization_profile=MARKDOWN_RICH_CANONICALIZATION_PROFILE,
                compilation_digest_profile=MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
                token_ceiling=2048,
            ),
        )


def test_rich_constructor_rejects_split_of_one_contiguous_list() -> None:
    canonical_text = "- one\n- two\n"
    sections, fragments = _forged_sections(
        canonical_text,
        (
            (SectionKind.LIST, "- one", ("one",)),
            (SectionKind.LIST, "- two", ("two",)),
        ),
    )

    with pytest.raises(ValueError, match="rich block splitting must be exact"):
        ParsedDocument.rich_v3(
            canonical_text=canonical_text,
            sections=sections,
            fragments=fragments,
            provenance=CompilationProvenance(
                compiler_version=MARKDOWN_COMPILER_V3_VERSION,
                config_version="markdown-config-v3",
                canonicalization_profile=MARKDOWN_RICH_CANONICALIZATION_PROFILE,
                compilation_digest_profile=MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
                token_ceiling=2048,
            ),
        )


def test_rich_constructor_rederives_section_kind_from_exact_source() -> None:
    compiled = compile_rich_markdown(b"# Root\n", CONFIG)
    assert type(compiled) is ParsedDocument
    heading = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_path = StructuralPath(("document", "paragraph[1]"))

    with pytest.raises(ValueError, match="rich section kind"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(
                replace(
                    heading,
                    kind=SectionKind.PARAGRAPH,
                    text=fragment.source_text,
                    path=forged_path,
                    level=None,
                ),
            ),
            fragments=(
                replace(
                    fragment,
                    fragment_ref="fragment:paragraph:1",
                    kind=SectionKind.PARAGRAPH,
                    path=forged_path,
                    search_phrases=(fragment.source_text,),
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rederives_table_kind_from_exact_source() -> None:
    compiled = compile_rich_markdown(
        b"| A | B |\n| --- | --- |\n| x | y |\n",
        CONFIG,
    )
    assert type(compiled) is ParsedDocument
    table = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_path = StructuralPath(("document", "paragraph[1]"))

    with pytest.raises(ValueError, match="rich section kind"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(
                replace(
                    table,
                    kind=SectionKind.PARAGRAPH,
                    path=forged_path,
                    table_header=(),
                    table_rows=(),
                ),
            ),
            fragments=(
                replace(
                    fragment,
                    fragment_ref="fragment:paragraph:1",
                    kind=SectionKind.PARAGRAPH,
                    path=forged_path,
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rejects_forged_table_kind_for_ragged_source() -> None:
    compiled = compile_rich_markdown(
        b"| A | B |\n| --- | --- |\n| x |\n",
        CONFIG,
    )
    assert type(compiled) is ParsedDocument
    paragraph = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_path = StructuralPath(("document", "table[1]"))

    with pytest.raises(ValueError, match="rich section kind"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(
                replace(
                    paragraph,
                    kind=SectionKind.TABLE,
                    path=forged_path,
                    table_header=("A", "B"),
                    table_rows=(("x",),),
                ),
            ),
            fragments=(
                replace(
                    fragment,
                    fragment_ref="fragment:table:1",
                    kind=SectionKind.TABLE,
                    path=forged_path,
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rejects_language_bearing_unmatched_fence() -> None:
    compiled = compile_rich_markdown(b"Plaintext\nbody\n", CONFIG)
    assert type(compiled) is ParsedDocument
    paragraph = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_source = "```python\nbody"
    assert len(forged_source.encode("utf-8")) == fragment.position.end.byte_offset

    with pytest.raises(ValueError, match="closed grammar"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(replace(paragraph, text=forged_source),),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


@pytest.mark.parametrize(
    "control_character",
    ("\x00", "\x07", "\x1b", "\x1f", "\x7f", "\x85"),
)
def test_rich_constructor_rejects_every_control_character_forged_inside_fence(
    control_character: str,
) -> None:
    compiled = compile_rich_markdown(b"```text\nbody\n```\n", CONFIG)
    assert type(compiled) is ParsedDocument
    section = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_body = f"bo{control_character}y"
    forged_source = fragment.source_text.replace("body", forged_body)

    with pytest.raises(ValueError, match="control character"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(
                replace(
                    section,
                    text=forged_source,
                    code_body=forged_body,
                ),
            ),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


@pytest.mark.parametrize(
    ("compiler_version", "config_version"),
    (
        ("context-engine-markdown-v1", "markdown-config-v1"),
        ("arbitrary-compiler", "arbitrary-config"),
    ),
)
def test_rich_constructor_rejects_forged_v3_provenance_identity(
    compiler_version: str,
    config_version: str,
) -> None:
    compiled = compile_rich_markdown(b"Plain\n", CONFIG)
    assert type(compiled) is ParsedDocument

    with pytest.raises(ValueError, match="rich provenance identity"):
        provenance = CompilationProvenance(
            compiler_version=compiler_version,
            config_version=config_version,
            canonicalization_profile=MARKDOWN_RICH_CANONICALIZATION_PROFILE,
            compilation_digest_profile=MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
            token_ceiling=2048,
        )
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=compiled.sections,
            fragments=compiled.fragments,
            provenance=provenance,
        )


def test_rich_constructor_rejects_unlisted_construct_in_forged_document() -> None:
    compiled = compile_rich_markdown(b"Plain\n", CONFIG)
    assert type(compiled) is ParsedDocument
    paragraph = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_source = "&amp;"

    with pytest.raises(ValueError, match="closed grammar"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(replace(paragraph, text=forged_source),),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rejects_unlisted_construct_in_ragged_table() -> None:
    compiled = compile_rich_markdown(
        b"| A | B |\n| --- | --- |\n| x | value |\n| ragged |\n",
        CONFIG,
    )
    assert type(compiled) is ParsedDocument
    paragraph = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_source = fragment.source_text.replace("value", "&amp;")

    with pytest.raises(ValueError, match="closed grammar"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(replace(paragraph, text=forged_source),),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_does_not_treat_arbitrary_pipe_lines_as_table() -> None:
    compiled = compile_rich_markdown(
        b"first | line\nsecond | line\nplain value\n",
        CONFIG,
    )
    assert type(compiled) is ParsedDocument
    paragraph = compiled.sections[0]
    fragment = compiled.fragments[0]
    forged_source = fragment.source_text.replace("value", "&amp;")

    with pytest.raises(ValueError, match="closed grammar"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(replace(paragraph, text=forged_source),),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rejects_split_fragments_of_one_atomic_ragged_table() -> None:
    canonical_text = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| one two three | four five six |\n"
        "| ragged seven eight |\n"
    )
    source_parts = (
        "| A | B |\n| --- |",
        "--- |\n| one two three | four",
        "five six |\n| ragged seven eight |",
    )

    def point(byte_offset: int) -> SourcePoint:
        prefix = canonical_text.encode("utf-8")[:byte_offset].decode("utf-8")
        return SourcePoint(
            line=prefix.count("\n") + 1,
            column=len(prefix.rsplit("\n", maxsplit=1)[-1]) + 1,
            byte_offset=byte_offset,
        )

    sections: list[ParsedSection] = []
    fragments: list[CompiledFragment] = []
    search_start = 0
    for ordinal, source_part in enumerate(source_parts, start=1):
        start = canonical_text.index(source_part, search_start)
        end = start + len(source_part)
        search_start = end
        path = StructuralPath(("document", f"paragraph[{ordinal}]"))
        position = SourceSpan(start=point(start), end=point(end))
        section = ParsedSection(
            kind=SectionKind.PARAGRAPH,
            text=source_part,
            path=path,
            position=position,
        )
        sections.append(section)
        fragments.append(
            CompiledFragment(
                fragment_ref=f"fragment:paragraph:{ordinal}",
                kind=SectionKind.PARAGRAPH,
                path=path,
                position=position,
                source_text=source_part,
                contextual_text=source_part,
                parent_headings=(),
                search_phrases=(source_part,),
            )
        )

    with pytest.raises(ValueError, match="rich table source must remain atomic"):
        ParsedDocument.rich_v3(
            canonical_text=canonical_text,
            sections=tuple(sections),
            fragments=tuple(fragments),
            provenance=CompilationProvenance(
                compiler_version=MARKDOWN_COMPILER_V3_VERSION,
                config_version="markdown-config-v3",
                canonicalization_profile=MARKDOWN_RICH_CANONICALIZATION_PROFILE,
                compilation_digest_profile=MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
                token_ceiling=8,
            ),
        )


def test_rich_constructor_rejects_ragged_table_forged_as_table_then_paragraph() -> None:
    table_source = "| A | B |\n| --- | --- |\n| x | y |"
    ragged_source = "| ragged |"
    canonical_text = f"{table_source}\n{ragged_source}\n"
    table_document = compile_rich_markdown(table_source.encode("utf-8"), CONFIG)
    ragged_document = compile_rich_markdown(ragged_source.encode("utf-8"), CONFIG)
    assert type(table_document) is ParsedDocument
    assert type(ragged_document) is ParsedDocument
    table_section = table_document.sections[0]
    table_fragment = table_document.fragments[0]
    ragged_start = len(f"{table_source}\n".encode())
    ragged_end = ragged_start + len(ragged_source.encode("utf-8"))
    ragged_path = StructuralPath(("document", "paragraph[1]"))
    ragged_position = SourceSpan(
        start=SourcePoint(line=4, column=1, byte_offset=ragged_start),
        end=SourcePoint(
            line=4,
            column=len(ragged_source) + 1,
            byte_offset=ragged_end,
        ),
    )
    ragged_section = replace(
        ragged_document.sections[0],
        path=ragged_path,
        position=ragged_position,
    )
    ragged_fragment = replace(
        ragged_document.fragments[0],
        path=ragged_path,
        position=ragged_position,
    )

    with pytest.raises(ValueError, match="rich table source must remain atomic"):
        ParsedDocument.rich_v3(
            canonical_text=canonical_text,
            sections=(table_section, ragged_section),
            fragments=(table_fragment, ragged_fragment),
            provenance=table_document.provenance,
        )


@pytest.mark.parametrize(
    ("original", "forged_source"),
    (
        (b"> plain\n", "> &amp;"),
        (b"- item\n  plain\n", "- item\n  &amp;"),
    ),
)
def test_rich_constructor_rejects_unlisted_construct_in_nested_context(
    original: bytes,
    forged_source: str,
) -> None:
    compiled = compile_rich_markdown(original, CONFIG)
    assert type(compiled) is ParsedDocument
    section = compiled.sections[0]
    fragment = compiled.fragments[0]
    assert len(forged_source.encode()) == fragment.position.end.byte_offset

    with pytest.raises(ValueError, match="closed grammar"):
        ParsedDocument.rich_v3(
            canonical_text=f"{forged_source}\n",
            sections=(replace(section, text=forged_source),),
            fragments=(
                replace(
                    fragment,
                    source_text=forged_source,
                    contextual_text=forged_source,
                    search_phrases=(forged_source,),
                ),
            ),
            provenance=compiled.provenance,
        )


def test_rich_constructor_rederives_list_metadata() -> None:
    compiled = compile_rich_markdown(b"- first\n  1. child\n", CONFIG)
    assert type(compiled) is ParsedDocument

    with pytest.raises(ValueError, match="rich section metadata"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(replace(compiled.sections[0], list_items=("forged",)),),
            fragments=compiled.fragments,
            provenance=compiled.provenance,
        )


def test_rich_constructor_rederives_code_metadata() -> None:
    compiled = compile_rich_markdown(b"```python\nbody\n```\n", CONFIG)
    assert type(compiled) is ParsedDocument

    with pytest.raises(ValueError, match="rich section metadata"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(
                replace(
                    compiled.sections[0],
                    code_language="forged",
                    code_body="forged",
                ),
            ),
            fragments=compiled.fragments,
            provenance=compiled.provenance,
        )


def test_rich_constructor_rederives_table_metadata() -> None:
    compiled = compile_rich_markdown(
        b"| A | B |\n| --- | --- |\n| x | y |\n",
        CONFIG,
    )
    assert type(compiled) is ParsedDocument

    with pytest.raises(ValueError, match="rich section metadata"):
        ParsedDocument.rich_v3(
            canonical_text=compiled.canonical_text,
            sections=(
                replace(
                    compiled.sections[0],
                    table_header=("forged",),
                    table_rows=(("forged",),),
                ),
            ),
            fragments=compiled.fragments,
            provenance=compiled.provenance,
        )


@pytest.mark.parametrize(
    "frontmatter",
    (
        (
            b"owner:\n"
            b"  name: compiler\n"
            b"  teams:\n"
            b"    - supply\n"
            b"description: |\n"
            b"  Compiler metadata keeps\n"
            b"  ---\n"
            b"  its exact source lines.\n"
        ),
        b"- alpha\n- beta\n",
    ),
)
def test_delimited_yaml_frontmatter_is_accepted_as_exact_source_fragment(
    frontmatter: bytes,
) -> None:
    source = b"---\n" + frontmatter + b"---\n# Handbook\n"

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    frontmatter_fragment = outcome.fragments[0]
    expected = source[: source.index(b"#")].rstrip(b"\n").decode()
    assert frontmatter_fragment.source_text == expected
    span = frontmatter_fragment.position
    assert source[span.start.byte_offset : span.end.byte_offset] == (
        frontmatter_fragment.source_text.encode("utf-8")
    )
