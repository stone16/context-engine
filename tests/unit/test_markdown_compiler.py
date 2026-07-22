from __future__ import annotations

import ast
import builtins
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from adapters.parsers.markdown import compile_markdown
from engine.supply import (
    MARKDOWN_COMPILER_VERSION,
    CompilationFailure,
    CompilationFailureCode,
    CompilationProvenance,
    MarkdownCompilerConfig,
    ParsedDocument,
    ParsedSection,
    SectionKind,
    SourcePoint,
    SourceSpan,
    StructuralPath,
    UnsupportedConstruct,
    canonicalize_parsed_document,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/markdown"
CONFIG = MarkdownCompilerConfig(version="markdown-config-v1")


def _hex_fixture(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text(encoding="ascii").strip())


def test_frozen_heading_and_paragraph_compile_to_exact_typed_document() -> None:
    source = (FIXTURES / "heading-paragraph.md").read_bytes()

    outcome = compile_markdown(source, CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.canonical_text == (
        "# Handbook\n\nContextEngine delivers context.\n"
    )
    assert outcome.provenance.compiler_version == MARKDOWN_COMPILER_VERSION
    assert outcome.provenance.config_version == "markdown-config-v1"
    assert outcome.warnings == ()
    assert [section.kind for section in outcome.sections] == [
        SectionKind.HEADING,
        SectionKind.PARAGRAPH,
    ]

    heading, paragraph = outcome.sections
    assert (heading.text, heading.level, heading.path.segments) == (
        "Handbook",
        1,
        ("document", "heading[1]"),
    )
    assert (
        heading.position.start.line,
        heading.position.start.column,
        heading.position.start.byte_offset,
        heading.position.end.line,
        heading.position.end.column,
        heading.position.end.byte_offset,
    ) == (1, 1, 0, 1, 11, 10)
    assert (paragraph.text, paragraph.level, paragraph.path.segments) == (
        "ContextEngine delivers context.",
        None,
        ("document", "heading[1]", "paragraph[1]"),
    )
    assert (
        paragraph.position.start.line,
        paragraph.position.start.column,
        paragraph.position.start.byte_offset,
        paragraph.position.end.line,
        paragraph.position.end.column,
        paragraph.position.end.byte_offset,
    ) == (3, 1, 12, 3, 32, 43)

    expected = (FIXTURES / "heading-paragraph.expected.json").read_bytes().strip()
    assert canonicalize_parsed_document(outcome) == expected


def test_bom_crlf_and_missing_final_newline_have_one_canonical_identity() -> None:
    canonical = (FIXTURES / "heading-paragraph.md").read_bytes()
    bom_crlf = _hex_fixture("heading-paragraph-bom-crlf.hex")

    outcomes = (
        compile_markdown(canonical, CONFIG),
        compile_markdown(bom_crlf, CONFIG),
        compile_markdown(canonical.removesuffix(b"\n"), CONFIG),
    )

    assert all(type(outcome) is ParsedDocument for outcome in outcomes)
    documents = tuple(
        outcome for outcome in outcomes if type(outcome) is ParsedDocument
    )
    assert len({canonicalize_parsed_document(value) for value in documents}) == 1
    assert len({value.content_hash for value in documents}) == 1
    assert len({value.compilation_digest for value in documents}) == 1


def test_config_version_changes_identity_not_canonical_content_hash() -> None:
    source = (FIXTURES / "heading-paragraph.md").read_bytes()
    first = compile_markdown(source, CONFIG)
    second = compile_markdown(
        source,
        MarkdownCompilerConfig(version="markdown-config-v2"),
    )

    assert type(first) is ParsedDocument
    assert type(second) is ParsedDocument
    assert first.canonical_text == second.canonical_text
    assert first.content_hash == second.content_hash
    assert first.compilation_digest != second.compilation_digest
    assert canonicalize_parsed_document(first) != canonicalize_parsed_document(second)


def test_compiler_version_changes_compilation_identity_not_content_hash() -> None:
    current = compile_markdown(
        (FIXTURES / "heading-paragraph.md").read_bytes(),
        CONFIG,
    )
    assert type(current) is ParsedDocument

    next_version = ParsedDocument.issue_22(
        canonical_text=current.canonical_text,
        sections=(current.sections[0], current.sections[1]),
        provenance=CompilationProvenance(
            compiler_version="context-engine-markdown-v2",
            config_version=current.provenance.config_version,
        ),
    )

    assert current.content_hash == next_version.content_hash
    assert current.compilation_digest != next_version.compilation_digest
    assert canonicalize_parsed_document(current) != canonicalize_parsed_document(
        next_version
    )


def test_invalid_utf8_is_typed_failure_without_partial_document() -> None:
    outcome = compile_markdown(_hex_fixture("invalid-utf8.hex"), CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.INVALID_UTF8
    assert outcome.position is not None
    assert outcome.position.byte_offset == 19
    assert not hasattr(outcome, "document")
    assert not hasattr(outcome, "canonical_text")


def test_unsupported_markdown_is_typed_failure_not_reinterpreted_text() -> None:
    outcome = compile_markdown(
        (FIXTURES / "unsupported-list.md").read_bytes(),
        CONFIG,
    )

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is UnsupportedConstruct.LIST
    assert outcome.position is not None
    assert (
        outcome.position.line,
        outcome.position.column,
        outcome.position.byte_offset,
    ) == (3, 1, 12)
    assert not hasattr(outcome, "document")


@pytest.mark.parametrize(
    ("markdown", "construct"),
    [
        (
            b"# Handbook #\n\nParagraph.\n",
            UnsupportedConstruct.ATX_CLOSING_SEQUENCE,
        ),
        (
            b"# Handbook ###\n\nParagraph.\n",
            UnsupportedConstruct.ATX_CLOSING_SEQUENCE,
        ),
        (
            b"# #\n\nParagraph.\n",
            UnsupportedConstruct.ATX_CLOSING_SEQUENCE,
        ),
        (
            b"# ####\n\nParagraph.\n",
            UnsupportedConstruct.ATX_CLOSING_SEQUENCE,
        ),
        (b"## Nested\n\nParagraph.\n", UnsupportedConstruct.NESTED_HEADING),
        (b"# Handbook\n\n## Nested\n", UnsupportedConstruct.NESTED_HEADING),
        (b"# Handbook\n\n# Second\n", UnsupportedConstruct.NESTED_HEADING),
        (b"# Handbook\n\n   ## Nested\n", UnsupportedConstruct.NESTED_HEADING),
        (b"# Handbook\n\n```text\n", UnsupportedConstruct.CODE_BLOCK),
        (b"# Handbook\n\n    indented code\n", UnsupportedConstruct.CODE_BLOCK),
        (b"# Handbook\n\n  - indented item\n", UnsupportedConstruct.LIST),
        (b"# Handbook\n\n> quoted\n", UnsupportedConstruct.BLOCKQUOTE),
        (b"# Handbook\n\n   > quoted\n", UnsupportedConstruct.BLOCKQUOTE),
        (
            b"# Handbook\n\n[linked](https://invalid.example)\n",
            UnsupportedConstruct.LINK_OR_IMAGE,
        ),
        (
            b"# Handbook\n\n[linked][target]\n",
            UnsupportedConstruct.LINK_OR_IMAGE,
        ),
        (
            b"# Handbook\n\n![image][target]\n",
            UnsupportedConstruct.LINK_OR_IMAGE,
        ),
        (b"# Handbook\n\n**emphasized**\n", UnsupportedConstruct.EMPHASIS),
        (b"# Handbook\n\n_emphasized_\n", UnsupportedConstruct.EMPHASIS),
        (b"# Handbook\n\n___\n", UnsupportedConstruct.FRONTMATTER_OR_RULE),
        (b"# Handbook\n\n***\n", UnsupportedConstruct.FRONTMATTER_OR_RULE),
        (b"# Handbook\n\n* * *\n", UnsupportedConstruct.FRONTMATTER_OR_RULE),
        (b"# Handbook\n\n<span>html</span>\n", UnsupportedConstruct.HTML),
        (b"# Handbook\n\n&lt;escaped&gt;\n", UnsupportedConstruct.ENTITY),
        (b"# Handbook\n\nescaped\\*text\n", UnsupportedConstruct.ESCAPE),
        (b"# Handbook\n\nParagraph.\\\n", UnsupportedConstruct.HARD_BREAK),
        (b"# *Handbook*\n\nParagraph.\n", UnsupportedConstruct.EMPHASIS),
    ],
)
def test_other_out_of_scope_constructs_fail_closed(
    markdown: bytes,
    construct: UnsupportedConstruct,
) -> None:
    outcome = compile_markdown(markdown, CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is construct


@pytest.mark.parametrize(
    "markdown",
    [
        b"#  \n\nParagraph.\n",
        b"# Handbook\n\n   \n",
        b"# Handbook  \n\nParagraph.\n",
        b"# Handbook\n\n Paragraph.\n",
        b"# Handbook\n\nParagraph. \n",
    ],
)
def test_whitespace_is_not_silently_canonicalized_as_content(
    markdown: bytes,
) -> None:
    outcome = compile_markdown(markdown, CONFIG)

    assert type(outcome) is CompilationFailure


def test_pipe_in_plain_single_line_paragraph_is_not_a_table() -> None:
    outcome = compile_markdown(b"# Handbook\n\nA | B\n", CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.sections[1].text == "A | B"


def test_backslash_before_non_punctuation_remains_plain_text() -> None:
    outcome = compile_markdown(b"# Handbook\n\nC:\\Users\n", CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.sections[1].text == r"C:\Users"


@pytest.mark.parametrize(
    "paragraph",
    ("[literal]", "foo_bar_baz", "2 * 3 * 4", "Use * literally * here"),
)
def test_plain_brackets_and_intraword_underscores_remain_text(
    paragraph: str,
) -> None:
    outcome = compile_markdown(f"# Handbook\n\n{paragraph}\n".encode(), CONFIG)

    assert type(outcome) is ParsedDocument
    assert outcome.sections[1].text == paragraph


def test_parsed_document_factory_cannot_bypass_closed_grammar() -> None:
    canonical_text = "# *Heading*\n\nParagraph.\n"
    heading_line = "# *Heading*"
    paragraph_start = len(heading_line.encode()) + 2

    with pytest.raises(ValueError, match="unsupported Markdown construct"):
        ParsedDocument.issue_22(
            canonical_text=canonical_text,
            sections=(
                ParsedSection(
                    kind=SectionKind.HEADING,
                    text="*Heading*",
                    path=StructuralPath(("document", "heading[1]")),
                    position=SourceSpan(
                        start=SourcePoint(1, 1, 0),
                        end=SourcePoint(1, len(heading_line) + 1, len(heading_line)),
                    ),
                    level=1,
                ),
                ParsedSection(
                    kind=SectionKind.PARAGRAPH,
                    text="Paragraph.",
                    path=StructuralPath(
                        ("document", "heading[1]", "paragraph[1]")
                    ),
                    position=SourceSpan(
                        start=SourcePoint(3, 1, paragraph_start),
                        end=SourcePoint(3, 11, paragraph_start + 10),
                    ),
                ),
            ),
            provenance=CompilationProvenance(
                compiler_version=MARKDOWN_COMPILER_VERSION,
                config_version=CONFIG.version,
            ),
        )


def test_structural_path_rejects_blank_or_padded_segments() -> None:
    with pytest.raises(ValueError, match="nonblank"):
        StructuralPath(("document", "   "))
    with pytest.raises(ValueError, match="nonblank"):
        StructuralPath(("document", " paragraph[1]"))


@pytest.mark.parametrize("control", ("\x7f", "\x80", "\x9f"))
def test_unicode_control_characters_fail_closed(control: str) -> None:
    outcome = compile_markdown(
        f"# Handbook\n\nParagraph{control}.\n".encode(),
        CONFIG,
    )

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is UnsupportedConstruct.CONTROL_CHARACTER


def test_source_span_rejects_inconsistent_coordinate_order() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        SourceSpan(
            start=SourcePoint(line=2, column=1, byte_offset=0),
            end=SourcePoint(line=1, column=2, byte_offset=1),
        )
    with pytest.raises(ValueError, match="must not precede"):
        SourceSpan(
            start=SourcePoint(line=1, column=2, byte_offset=1),
            end=SourcePoint(line=1, column=1, byte_offset=2),
        )
    with pytest.raises(ValueError, match="must advance together"):
        SourceSpan(
            start=SourcePoint(line=1, column=1, byte_offset=0),
            end=SourcePoint(line=2, column=1, byte_offset=0),
        )
    with pytest.raises(ValueError, match="must advance together"):
        SourceSpan(
            start=SourcePoint(line=1, column=1, byte_offset=0),
            end=SourcePoint(line=1, column=1, byte_offset=1),
        )


def test_unicode_columns_and_canonical_utf8_byte_offsets_are_distinct() -> None:
    outcome = compile_markdown("# 手册\r\r正文。".encode(), CONFIG)

    assert type(outcome) is ParsedDocument
    heading, paragraph = outcome.sections
    assert outcome.canonical_text == "# 手册\n\n正文。\n"
    assert (heading.position.end.column, heading.position.end.byte_offset) == (5, 8)
    assert (paragraph.position.start.column, paragraph.position.start.byte_offset) == (
        1,
        10,
    )
    assert (paragraph.position.end.column, paragraph.position.end.byte_offset) == (
        4,
        19,
    )


def test_canonical_output_is_identical_in_two_fresh_processes() -> None:
    program = """
import sys
from adapters.parsers.markdown import compile_markdown
from engine.supply import (
    MarkdownCompilerConfig,
    ParsedDocument,
    canonicalize_parsed_document,
)
source = sys.stdin.buffer.read()
result = compile_markdown(source, MarkdownCompilerConfig(version='markdown-config-v1'))
if type(result) is not ParsedDocument:
    raise SystemExit(2)
sys.stdout.buffer.write(canonicalize_parsed_document(result))
"""
    source = (FIXTURES / "heading-paragraph.md").read_bytes()
    outputs: list[bytes] = []
    for seed in ("17", "941"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", program],
            input=source,
            capture_output=True,
            check=True,
            env=environment,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0]) == json.loads(
        (FIXTURES / "heading-paragraph.expected.json").read_bytes()
    )


def test_compiler_module_has_no_io_or_outer_layer_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = Path(__file__).parents[2] / "adapters/parsers/markdown.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module.split(".", maxsplit=1)[0])

    assert imports <= {
        "__future__",
        "dataclasses",
        "enum",
        "engine",
        "hashlib",
        "re",
        "typing",
        "rfc8785",
    }
    forbidden_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } & {"__import__", "compile", "eval", "exec", "open"}
    assert forbidden_calls == set()

    calls: list[str] = []

    def reject(name: str) -> None:
        calls.append(name)
        raise AssertionError(f"compiler performed forbidden I/O: {name}")

    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: reject("open"))
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: reject("Path.open"))
    monkeypatch.setattr(Path, "read_bytes", lambda *args, **kwargs: reject("read"))
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: reject("socket"))
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: reject("urlopen"),
    )

    outcome = compile_markdown(
        b"# Handbook\n\nContextEngine delivers context.\n",
        CONFIG,
    )

    assert type(outcome) is ParsedDocument
    assert calls == []
