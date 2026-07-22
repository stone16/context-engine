"""Pure adapter for the versioned deterministic Markdown grammar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from engine.supply.markdown import (
    MARKDOWN_CANONICALIZATION_PROFILE,
    MARKDOWN_CODE_LANGUAGE_MAX_LENGTH,
    MARKDOWN_COMPILATION_DIGEST_PROFILE,
    MARKDOWN_COMPILER_V1_VERSION,
    MARKDOWN_COMPILER_VERSION,
    CompilationFailure,
    CompilationFailureCode,
    CompilationOutcome,
    CompilationProvenance,
    CompiledFragment,
    MarkdownCompilerConfig,
    ParsedDocument,
    ParsedSection,
    SectionKind,
    SourcePoint,
    SourceSpan,
    StructuralPath,
    UnsupportedConstruct,
    unsupported_markdown_construct,
)

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_HEADING_PATTERN: Final = re.compile(r"^# (\S(?:.*\S)?)$")
_STRUCTURAL_HEADING_PATTERN: Final = re.compile(r"^(#{1,6}) (\S(?:.*\S)?)$")
_UNORDERED_LIST_PATTERN: Final = re.compile(r"^- (\S(?:.*\S)?)$")
_ORDERED_LIST_PATTERN: Final = re.compile(r"^[1-9][0-9]*\. (\S(?:.*\S)?)$")
_FENCE_PATTERN: Final = re.compile(
    rf"^```([A-Za-z0-9_.+-]{{1,{MARKDOWN_CODE_LANGUAGE_MAX_LENGTH}}})?$"
)
_TABLE_DELIMITER_CELL: Final = re.compile(r"^-{3,}$")


@dataclass(frozen=True, slots=True)
class _StructuralBlock:
    kind: SectionKind
    start_index: int
    end_index: int
    level: int | None = None
    list_ordered: bool | None = None
    list_items: tuple[str, ...] = ()
    code_language: str | None = None
    code_body: str | None = None
    table_header: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()


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


def _compile_v1(
    normalized: str,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
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
        compiler_version=MARKDOWN_COMPILER_V1_VERSION,
        config_version=config.version,
    )
    return ParsedDocument.issue_22(
        canonical_text=normalized,
        sections=_sections(normalized, heading_match.group(1)),
        provenance=provenance,
    )


def _table_cells(line: str) -> tuple[str, ...] | None:
    if not line.startswith("|") or not line.endswith("|"):
        return None
    cells = tuple(cell.strip() for cell in line[1:-1].split("|"))
    if not cells or any(not cell for cell in cells):
        return None
    return cells


def _plain_construct(value: str) -> UnsupportedConstruct | None:
    return unsupported_markdown_construct(value, supported_heading=False)


def _invalid_control(value: str) -> bool:
    return any(
        ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        for character in value
        if character != "\n"
    )


def _starts_structural_or_unsupported_block(
    lines: list[str],
    index: int,
) -> bool:
    value = lines[index]
    if value != value.strip() or _invalid_control(value):
        return True
    if (
        _STRUCTURAL_HEADING_PATTERN.fullmatch(value) is not None
        or re.match(r"^ {0,3}#{1,6}(?:[ \t]+|$)", value) is not None
        or value.startswith(("```", "~~~", "    ", "\t"))
        or _UNORDERED_LIST_PATTERN.fullmatch(value) is not None
        or _ORDERED_LIST_PATTERN.fullmatch(value) is not None
        or re.match(r"^ {0,3}(?:[-+*]|[0-9]+[.)])\s+", value) is not None
        or _plain_construct(value) is not None
    ):
        return True
    cells = _table_cells(value)
    return (
        cells is not None
        and index + 1 < len(lines)
        and lines[index + 1].startswith("|")
    )


def _structural_blocks(
    lines: list[str],
) -> tuple[_StructuralBlock, ...] | CompilationFailure:
    blocks: list[_StructuralBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "":
            index += 1
            continue
        if line != line.strip() or _invalid_control(line):
            construct = unsupported_markdown_construct(
                line,
                supported_heading=False,
            )
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, index),
                construct=construct or UnsupportedConstruct.CONTROL_CHARACTER,
            )

        heading = _STRUCTURAL_HEADING_PATTERN.fullmatch(line)
        if heading is not None:
            heading_text = heading.group(2)
            construct = unsupported_markdown_construct(
                f"# {heading_text}",
                supported_heading=True,
            )
            if construct is not None:
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    position=_failure_point(lines, index),
                    construct=construct,
                )
            blocks.append(
                _StructuralBlock(
                    kind=SectionKind.HEADING,
                    start_index=index,
                    end_index=index,
                    level=len(heading.group(1)),
                )
            )
            index += 1
            continue
        if re.match(r"^ {0,3}#{1,6}(?:[ \t]+|$)", line):
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, index),
                construct=UnsupportedConstruct.NESTED_HEADING,
            )

        fence = _FENCE_PATTERN.fullmatch(line)
        if fence is not None:
            closing = index + 1
            while closing < len(lines) and lines[closing] != "```":
                if _invalid_control(lines[closing]):
                    return CompilationFailure(
                        code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                        position=_failure_point(lines, closing),
                        construct=UnsupportedConstruct.CONTROL_CHARACTER,
                    )
                closing += 1
            if closing >= len(lines) or closing == index + 1:
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    position=_failure_point(lines, index),
                    construct=UnsupportedConstruct.CODE_BLOCK,
                )
            code_body = "\n".join(lines[index + 1 : closing])
            if not code_body or code_body.isspace():
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    position=_failure_point(lines, index),
                    construct=UnsupportedConstruct.CODE_BLOCK,
                )
            blocks.append(
                _StructuralBlock(
                    kind=SectionKind.FENCED_CODE,
                    start_index=index,
                    end_index=closing,
                    code_language=fence.group(1),
                    code_body=code_body,
                )
            )
            index = closing + 1
            continue
        if line.startswith(("```", "~~~", "    ", "\t")):
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, index),
                construct=UnsupportedConstruct.CODE_BLOCK,
            )

        unordered = _UNORDERED_LIST_PATTERN.fullmatch(line)
        ordered = _ORDERED_LIST_PATTERN.fullmatch(line)
        if unordered is not None or ordered is not None:
            is_ordered = ordered is not None
            items: list[str] = []
            end = index
            while end < len(lines):
                match = (
                    _ORDERED_LIST_PATTERN.fullmatch(lines[end])
                    if is_ordered
                    else _UNORDERED_LIST_PATTERN.fullmatch(lines[end])
                )
                if match is None:
                    break
                item = match.group(1)
                construct = _plain_construct(item)
                if construct is not None:
                    return CompilationFailure(
                        code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                        position=_failure_point(lines, end),
                        construct=construct,
                    )
                items.append(item)
                end += 1
            blocks.append(
                _StructuralBlock(
                    kind=SectionKind.LIST,
                    start_index=index,
                    end_index=end - 1,
                    list_ordered=is_ordered,
                    list_items=tuple(items),
                )
            )
            index = end
            continue
        if re.match(r"^ {0,3}(?:[-+*]|[0-9]+[.)])\s+", line):
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, index),
                construct=UnsupportedConstruct.LIST,
            )

        if index + 1 < len(lines) and _table_cells(line) is not None:
            header = _table_cells(line)
            delimiter = _table_cells(lines[index + 1])
            if delimiter is not None and all(
                _TABLE_DELIMITER_CELL.fullmatch(cell) for cell in delimiter
            ):
                assert header is not None
                if len(delimiter) != len(header):
                    return CompilationFailure(
                        code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                        position=_failure_point(lines, index + 1),
                        construct=UnsupportedConstruct.TABLE,
                    )
                rows: list[tuple[str, ...]] = []
                end = index + 2
                while end < len(lines) and lines[end] != "":
                    row = _table_cells(lines[end])
                    if row is None:
                        if lines[end].startswith("|"):
                            return CompilationFailure(
                                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                                position=_failure_point(lines, end),
                                construct=UnsupportedConstruct.TABLE,
                            )
                        break
                    if len(row) != len(header):
                        return CompilationFailure(
                            code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                            position=_failure_point(lines, end),
                            construct=UnsupportedConstruct.TABLE,
                        )
                    rows.append(row)
                    end += 1
                if not rows or any(
                    _plain_construct(cell) is not None
                    for row in (header, *rows)
                    for cell in row
                ):
                    return CompilationFailure(
                        code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                        position=_failure_point(lines, index),
                        construct=UnsupportedConstruct.TABLE,
                    )
                blocks.append(
                    _StructuralBlock(
                        kind=SectionKind.TABLE,
                        start_index=index,
                        end_index=end - 1,
                        table_header=header,
                        table_rows=tuple(rows),
                    )
                )
                index = end
                continue
            if index + 1 < len(lines) and lines[index + 1].startswith("|"):
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    position=_failure_point(lines, index + 1),
                    construct=UnsupportedConstruct.TABLE,
                )

        construct = _plain_construct(line)
        if construct is not None:
            return CompilationFailure(
                code=CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                position=_failure_point(lines, index),
                construct=construct,
            )
        end = index + 1
        while (
            end < len(lines)
            and lines[end] != ""
            and not _starts_structural_or_unsupported_block(lines, end)
        ):
            end += 1
        blocks.append(
            _StructuralBlock(
                kind=SectionKind.PARAGRAPH,
                start_index=index,
                end_index=end - 1,
            )
        )
        index = end
    return tuple(blocks)


def _span(lines: list[str], start_index: int, end_index: int) -> SourceSpan:
    start = _failure_point(lines, start_index)
    prior = "\n".join(lines[:end_index])
    end_start = len(prior.encode("utf-8")) + (1 if end_index else 0)
    end_line = lines[end_index]
    return SourceSpan(
        start=start,
        end=_point(
            end_index + 1,
            len(end_line) + 1,
            end_start + len(end_line.encode("utf-8")),
        ),
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _compile_v2(
    normalized: str,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
    lines = normalized.removesuffix("\n").split("\n")
    blocks = _structural_blocks(lines)
    if isinstance(blocks, CompilationFailure):
        return blocks
    if not blocks or blocks[0].kind is not SectionKind.HEADING or blocks[0].level != 1:
        return CompilationFailure(
            code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            position=_point(1, 1, 0),
        )

    headings: list[ParsedSection] = []
    counters: dict[tuple[tuple[str, ...], SectionKind], int] = {}
    kind_ordinals: dict[SectionKind, int] = {}
    sections: list[ParsedSection] = []
    fragments: list[CompiledFragment] = []
    for block in blocks:
        source_text = "\n".join(lines[block.start_index : block.end_index + 1])
        position = _span(lines, block.start_index, block.end_index)
        if block.kind is SectionKind.HEADING:
            assert block.level is not None
            if block.level > len(headings) + 1:
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    position=position.start,
                )
            headings = headings[: block.level - 1]
            parent_path = (
                headings[-1].path.segments if headings else ("document",)
            )
        else:
            if not headings:
                return CompilationFailure(
                    code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    position=position.start,
                )
            parent_path = headings[-1].path.segments
        counter_key = (parent_path, block.kind)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        path = StructuralPath(
            parent_path + (f"{block.kind.value}[{counters[counter_key]}]",)
        )
        text_value = (
            source_text[block.level + 1 :]
            if block.kind is SectionKind.HEADING and block.level is not None
            else source_text
        )
        section = ParsedSection(
            kind=block.kind,
            text=text_value,
            path=path,
            position=position,
            level=block.level,
            list_ordered=block.list_ordered,
            list_items=block.list_items,
            code_language=block.code_language,
            code_body=block.code_body,
            table_header=block.table_header,
            table_rows=block.table_rows,
        )
        parent_headings = tuple(headings)
        if block.kind is SectionKind.HEADING:
            headings.append(section)
        kind_ordinals[block.kind] = kind_ordinals.get(block.kind, 0) + 1
        if block.kind is SectionKind.LIST:
            phrases = _unique((source_text, *block.list_items))
        elif block.kind is SectionKind.FENCED_CODE:
            assert block.code_body is not None
            phrases = _unique((source_text, block.code_body))
        elif block.kind is SectionKind.TABLE:
            phrases = _unique(
                (
                    source_text,
                    *block.table_header,
                    *(cell for row in block.table_rows for cell in row),
                )
            )
        elif block.kind is SectionKind.HEADING:
            phrases = _unique((source_text, section.text))
        else:
            phrases = (source_text,)
        ancestry_text = "\n\n".join(
            f"{'#' * heading.level} {heading.text}"
            for heading in parent_headings
            if heading.level is not None
        )
        contextual_text = (
            f"{ancestry_text}\n\n{source_text}" if ancestry_text else source_text
        )
        sections.append(section)
        fragments.append(
            CompiledFragment(
                fragment_ref=(
                    f"fragment:{block.kind.value}:{kind_ordinals[block.kind]}"
                ),
                kind=block.kind,
                path=path,
                position=position,
                source_text=source_text,
                contextual_text=contextual_text,
                parent_headings=parent_headings,
                search_phrases=phrases,
            )
        )
    provenance = CompilationProvenance(
        compiler_version=MARKDOWN_COMPILER_VERSION,
        config_version=config.version,
        canonicalization_profile=MARKDOWN_CANONICALIZATION_PROFILE,
        compilation_digest_profile=MARKDOWN_COMPILATION_DIGEST_PROFILE,
    )
    return ParsedDocument.structural_v2(
        canonical_text=normalized,
        sections=tuple(sections),
        fragments=tuple(fragments),
        provenance=provenance,
    )


def compile_markdown(
    source: bytes,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
    """Compile exact bytes under one explicit versioned Markdown contract."""

    if type(source) is not bytes:
        raise TypeError("Markdown compiler source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig:
        raise TypeError("Markdown compiler config must be MarkdownCompilerConfig")
    normalized = _normalized_text(source)
    if isinstance(normalized, CompilationFailure):
        return normalized

    if config.version == "markdown-config-v1":
        return _compile_v1(normalized, config)
    if config.version == "markdown-config-v2":
        return _compile_v2(normalized, config)
    raise ValueError("Markdown compiler config version is not implemented")
