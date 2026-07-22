"""Pure adapter for the first deliberately narrow Markdown grammar."""

from __future__ import annotations

import re
from typing import Final

from engine.supply.markdown import (
    MARKDOWN_COMPILER_VERSION,
    CompilationFailure,
    CompilationFailureCode,
    CompilationOutcome,
    CompilationProvenance,
    MarkdownCompilerConfig,
    ParsedDocument,
    ParsedSection,
    SectionKind,
    SourcePoint,
    SourceSpan,
    StructuralPath,
    unsupported_markdown_construct,
)

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_HEADING_PATTERN: Final = re.compile(r"^# (\S(?:.*\S)?)$")


def _point(line: int, column: int, byte_offset: int) -> SourcePoint:
    return SourcePoint(line=line, column=column, byte_offset=byte_offset)


def _normalized_text(source: bytes) -> str | CompilationFailure:
    raw = source.removeprefix(_UTF8_BOM)
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        prefix = raw[: error.start].decode("utf-8", errors="strict")
        prefix = prefix.replace("\r\n", "\n").replace("\r", "\n")
        last_newline = prefix.rfind("\n")
        line = prefix.count("\n") + 1
        column = len(prefix[last_newline + 1 :]) + 1
        return CompilationFailure(
            code=CompilationFailureCode.INVALID_UTF8,
            position=_point(line, column, len(prefix.encode("utf-8"))),
        )
    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.rstrip("\n") + "\n"


def _failure_point(lines: list[str], line_index: int) -> SourcePoint:
    prior = "\n".join(lines[:line_index])
    byte_offset = len(prior.encode("utf-8")) + (1 if line_index else 0)
    return _point(line_index + 1, 1, byte_offset)


def _sections(
    normalized: str,
    heading_text: str,
) -> tuple[ParsedSection, ParsedSection]:
    heading_line, _, paragraph_line = normalized.removesuffix("\n").split("\n")
    heading_end = len(heading_line.encode("utf-8"))
    paragraph_start = heading_end + 2
    paragraph_end = paragraph_start + len(paragraph_line.encode("utf-8"))
    return (
        ParsedSection(
            kind=SectionKind.HEADING,
            text=heading_text,
            path=StructuralPath(("document", "heading[1]")),
            position=SourceSpan(
                start=_point(1, 1, 0),
                end=_point(1, len(heading_line) + 1, heading_end),
            ),
            level=1,
        ),
        ParsedSection(
            kind=SectionKind.PARAGRAPH,
            text=paragraph_line,
            path=StructuralPath(
                ("document", "heading[1]", "paragraph[1]")
            ),
            position=SourceSpan(
                start=_point(3, 1, paragraph_start),
                end=_point(3, len(paragraph_line) + 1, paragraph_end),
            ),
        ),
    )


def compile_markdown(
    source: bytes,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
    """Compile exact bytes into the one supported heading-plus-paragraph shape."""

    if type(source) is not bytes:
        raise TypeError("Markdown compiler source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig:
        raise TypeError("Markdown compiler config must be MarkdownCompilerConfig")
    normalized = _normalized_text(source)
    if isinstance(normalized, CompilationFailure):
        return normalized

    lines = normalized.removesuffix("\n").split("\n")
    for line_index, line in enumerate(lines):
        construct = unsupported_markdown_construct(
            line,
            supported_heading=line_index == 0,
        )
        if construct is not None:
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, line_index),
                construct=construct,
            )
    if (
        len(lines) != 3
        or lines[1] != ""
        or not lines[2]
        or lines[2] != lines[2].strip()
    ):
        return CompilationFailure(
            code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            position=_point(1, 1, 0),
        )
    heading_match = _HEADING_PATTERN.fullmatch(lines[0])
    if heading_match is None:
        return CompilationFailure(
            code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            position=_point(1, 1, 0),
        )
    provenance = CompilationProvenance(
        compiler_version=MARKDOWN_COMPILER_VERSION,
        config_version=config.version,
    )
    return ParsedDocument.issue_22(
        canonical_text=normalized,
        sections=_sections(normalized, heading_match.group(1)),
        provenance=provenance,
    )
