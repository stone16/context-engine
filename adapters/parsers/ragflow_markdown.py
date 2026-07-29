"""Rich Markdown compiler built around the registered RAGFlow parser region."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from engine.supply.markdown import (
    MARKDOWN_CODE_LANGUAGE_MAX_LENGTH,
    MARKDOWN_COMPILER_V3_VERSION,
    MARKDOWN_RICH_CANONICALIZATION_PROFILE,
    MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
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
    unsupported_rich_markdown_inline,
)
from third_party.ragflow.deepdoc.parser.markdown_parser import MarkdownElementExtractor

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_ATX_HEADING: Final = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SETEXT: Final = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_LIST_ITEM: Final = re.compile(r"^([ \t]*)(?:[-+*]|[0-9]+[.)])[ \t]+(.+)$")
_TOKEN: Final = re.compile(r"\S+")
_CALLOUT: Final = re.compile(r"^ {0,3}>[ \t]*\[![A-Za-z0-9_-]+][+-]?(?: .*)?$")
_HTML_OPEN: Final = re.compile(
    r"^ {0,3}<(?P<tag>section|div|details|summary|table|thead|tbody|tr|"
    r"th|td|p|ul|ol|li)\b[^>]*>",
    re.IGNORECASE,
)
_ANGLE_LITERAL: Final = re.compile(r"<[^<>\r\n]+>")


@dataclass(frozen=True, slots=True)
class _Block:
    kind: SectionKind
    start: int
    end: int
    indivisible: bool = False
    level: int | None = None
    list_ordered: bool | None = None
    list_items: tuple[str, ...] = ()
    code_language: str | None = None
    code_body: str | None = None
    table_header: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()


class _ElementExtractor(Protocol):
    def _get_fence_marker(self, line: str) -> tuple[str, int] | None: ...

    def _is_closing_fence(
        self,
        line: str,
        fence_char: str,
        fence_len: int,
    ) -> bool: ...

    def _table_cells(self, line: str) -> list[str]: ...

    def _is_table_row(self, line: str) -> bool: ...

    def _is_table_separator_row(self, line: str) -> bool: ...


def rich_token_count(value: str) -> int:
    """Count deterministic representation tokens for the v3 hard bound."""

    if type(value) is not str:
        raise TypeError("rich Markdown token counting requires exact text")
    return sum(1 for _ in _TOKEN.finditer(value))


def _failure(
    code: CompilationFailureCode,
    text: str,
    offset: int,
    construct: UnsupportedConstruct | None = None,
) -> CompilationFailure:
    return CompilationFailure(
        code=code,
        position=_point(text, offset),
        construct=construct,
    )


def _point(text: str, offset: int) -> SourcePoint:
    prefix = text[:offset]
    logical_prefix = prefix.replace("\r\n", "\n").replace("\r", "\n")
    last_newline = logical_prefix.rfind("\n")
    return SourcePoint(
        line=logical_prefix.count("\n") + 1,
        column=len(logical_prefix[last_newline + 1 :]) + 1,
        byte_offset=len(prefix.encode("utf-8")),
    )


def _span(text: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(start=_point(text, start), end=_point(text, end))


def _normalize(source: bytes) -> str | CompilationFailure:
    try:
        decoded = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        safe_prefix = source[: error.start].decode("utf-8", errors="strict")
        return _failure(
            CompilationFailureCode.INVALID_UTF8,
            safe_prefix,
            len(safe_prefix),
        )
    if any(
        character == "\x00" or 0x7F <= ord(character) <= 0x9F
        for character in decoded
    ):
        offset = next(
            index
            for index, character in enumerate(decoded)
            if character == "\x00" or 0x7F <= ord(character) <= 0x9F
        )
        return _failure(
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
            decoded,
            offset,
            UnsupportedConstruct.CONTROL_CHARACTER,
        )
    return decoded


def _line_layout(text: str) -> tuple[list[str], list[int]]:
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        raw_lines = [""]
    lines: list[str] = []
    starts: list[int] = []
    offset = 0
    for index, raw_line in enumerate(raw_lines):
        content = raw_line.removesuffix("\n").removesuffix("\r")
        bom_width = 1 if index == 0 and content.startswith("\ufeff") else 0
        lines.append(content[bom_width:])
        starts.append(offset + bom_width)
        offset += len(raw_line)
    return lines, starts


def _line_end(lines: list[str], starts: list[int], index: int) -> int:
    return starts[index] + len(lines[index])


def _table_cells(extractor: _ElementExtractor, line: str) -> tuple[str, ...]:
    return tuple(extractor._table_cells(line))


def _is_table_source_line(line: str) -> bool:
    """Return whether a nonblank line remains part of an opened pipe table."""

    return "|" in line


def _frontmatter_end(lines: list[str]) -> int | None:
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            if index == 1 or not any(line.strip() for line in lines[1:index]):
                return -1
            return index
    return -1


def _blocks(text: str) -> tuple[_Block, ...] | CompilationFailure:
    lines, starts = _line_layout(text)
    extractor = cast(
        _ElementExtractor,
        cast(Any, MarkdownElementExtractor)(text.removesuffix("\n")),
    )
    blocks: list[_Block] = []
    index = 0
    frontmatter_end = _frontmatter_end(lines)
    if frontmatter_end == -1:
        return _failure(
            CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            text,
            0,
        )
    if frontmatter_end is not None:
        blocks.append(
            _Block(
                kind=SectionKind.PARAGRAPH,
                start=starts[0],
                end=_line_end(lines, starts, frontmatter_end),
            )
        )
        index = frontmatter_end + 1

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        atx = _ATX_HEADING.fullmatch(line)
        if atx is not None:
            construct = unsupported_rich_markdown_inline(atx.group(2).strip())
            if construct is not None:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    construct,
                )
            blocks.append(
                _Block(
                    kind=SectionKind.HEADING,
                    start=starts[index],
                    end=_line_end(lines, starts, index),
                    level=len(atx.group(1)),
                )
            )
            index += 1
            continue
        fence = extractor._get_fence_marker(line)
        if fence is not None:
            fence_character, fence_length = fence
            language = line.lstrip()[fence_length:].strip() or None
            closing = index + 1
            while closing < len(lines) and not extractor._is_closing_fence(
                lines[closing], fence_character, fence_length
            ):
                closing += 1
            if closing >= len(lines):
                if language is None and any(
                    candidate.strip()
                    for candidate in lines[index + 1 :]
                ):
                    blocks.append(
                        _Block(
                            kind=SectionKind.PARAGRAPH,
                            start=starts[index],
                            end=_line_end(lines, starts, len(lines) - 1),
                        )
                    )
                    index = len(lines)
                    continue
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    UnsupportedConstruct.CODE_BLOCK,
                )
            if language is not None and (
                len(language) > MARKDOWN_CODE_LANGUAGE_MAX_LENGTH
                or any(character.isspace() for character in language)
            ):
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    UnsupportedConstruct.CODE_BLOCK,
                )
            body = "\n".join(lines[index + 1 : closing])
            if not body or body.isspace():
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    UnsupportedConstruct.CODE_BLOCK,
                )
            blocks.append(
                _Block(
                    kind=SectionKind.FENCED_CODE,
                    start=starts[index],
                    end=_line_end(lines, starts, closing),
                    code_language=language,
                    code_body=body,
                )
            )
            index = closing + 1
            continue

        if index + 1 < len(lines) and _SETEXT.fullmatch(lines[index + 1]):
            construct = unsupported_rich_markdown_inline(line.strip())
            if construct is not None:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    construct,
                )
            blocks.append(
                _Block(
                    kind=SectionKind.HEADING,
                    start=starts[index],
                    end=_line_end(lines, starts, index + 1),
                    level=1 if lines[index + 1].lstrip().startswith("=") else 2,
                )
            )
            index += 2
            continue

        if (
            index + 1 < len(lines)
            and extractor._is_table_row(line)
            and extractor._is_table_separator_row(lines[index + 1])
        ):
            header = _table_cells(extractor, line)
            separator = _table_cells(extractor, lines[index + 1])
            width = len(header)
            rows: list[tuple[str, ...]] = []
            end = index + 2
            while end < len(lines) and _is_table_source_line(lines[end]):
                row = _table_cells(extractor, lines[end])
                rows.append(row)
                end += 1
            if (
                not rows
                or width < 2
                or len(separator) != width
                or any(
                    len(row) != width or any(not cell for cell in row)
                    for row in (header, *rows)
                )
            ):
                for table_index in range(index, end):
                    construct = unsupported_rich_markdown_inline(
                        lines[table_index]
                    )
                    if construct is not None:
                        return _failure(
                            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                            text,
                            starts[table_index],
                            construct,
                        )
                blocks.append(
                    _Block(
                        kind=SectionKind.PARAGRAPH,
                        start=starts[index],
                        end=_line_end(lines, starts, end - 1),
                        indivisible=True,
                    )
                )
                index = end
                continue
            blocks.append(
                _Block(
                    kind=SectionKind.TABLE,
                    start=starts[index],
                    end=_line_end(lines, starts, end - 1),
                    table_header=header,
                    table_rows=tuple(rows),
                )
            )
            index = end
            continue

        list_match = _LIST_ITEM.fullmatch(line)
        if list_match is not None:
            items: list[str] = []
            end = index
            ordered = re.match(r"[0-9]+[.)]", line.lstrip()) is not None
            while end < len(lines):
                candidate = lines[end]
                match = _LIST_ITEM.fullmatch(candidate)
                if match is not None:
                    item = match.group(2).rstrip(" \t")
                    construct = unsupported_rich_markdown_inline(item)
                    if construct not in {None, UnsupportedConstruct.LIST}:
                        return _failure(
                            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                            text,
                            starts[end],
                            construct,
                        )
                    items.append(item)
                    end += 1
                    continue
                if candidate.strip() and candidate.startswith((" ", "\t")):
                    construct = unsupported_rich_markdown_inline(
                        candidate.lstrip()
                    )
                    if construct is not None:
                        return _failure(
                            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                            text,
                            starts[end],
                            construct,
                        )
                    end += 1
                    continue
                break
            blocks.append(
                _Block(
                    kind=SectionKind.LIST,
                    start=starts[index],
                    end=_line_end(lines, starts, end - 1),
                    list_ordered=ordered,
                    list_items=tuple(items),
                )
            )
            index = end
            continue

        if line.lstrip().startswith("<"):
            if _ANGLE_LITERAL.fullmatch(line.strip()) is not None:
                blocks.append(
                    _Block(
                        kind=SectionKind.PARAGRAPH,
                        start=starts[index],
                        end=_line_end(lines, starts, index),
                    )
                )
                index += 1
                continue
            html_open = _HTML_OPEN.match(line)
            if html_open is None:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    UnsupportedConstruct.HTML,
                )
            tag = html_open.group("tag")
            end = index + 1
            while end < len(lines) and lines[end].strip():
                end += 1
            html_source = text[starts[index] : _line_end(lines, starts, end - 1)]
            closing_tag = re.search(
                rf"</{re.escape(tag)}[ \t]*>", html_source, re.IGNORECASE
            )
            if closing_tag is None:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[index],
                    UnsupportedConstruct.HTML,
                )
            blocks.append(
                _Block(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_line_end(lines, starts, end - 1),
                )
            )
            index = end
            continue

        if line.lstrip().startswith(">"):
            end = index + 1
            while end < len(lines) and lines[end].lstrip().startswith(">"):
                end += 1
            for quote_index in range(index, end):
                quoted = lines[quote_index].lstrip()[1:].lstrip()
                callout = _CALLOUT.fullmatch(lines[quote_index]) is not None
                construct = (
                    None
                    if callout
                    else unsupported_rich_markdown_inline(quoted)
                )
                if construct is not None:
                    return _failure(
                        CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                        text,
                        starts[quote_index],
                        construct,
                    )
            blocks.append(
                _Block(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_line_end(lines, starts, end - 1),
                )
            )
            index = end
            continue

        if re.match(
            r"^ {0,3}#{1,6}[ \t]*$",
            line,
        ):
            return _failure(
                CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                text,
                starts[index],
                UnsupportedConstruct.NESTED_HEADING,
            )

        if re.fullmatch(
            r" {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})",
            line,
        ):
            blocks.append(
                _Block(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_line_end(lines, starts, index),
                )
            )
            index += 1
            continue

        end = index + 1
        while end < len(lines) and lines[end].strip():
            if (
                _ATX_HEADING.fullmatch(lines[end])
                or extractor._get_fence_marker(lines[end]) is not None
                or _LIST_ITEM.fullmatch(lines[end])
                or lines[end].lstrip().startswith((">", "<"))
                or re.fullmatch(
                    r" {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|"
                    r"(?:-[ \t]*){3,})",
                    lines[end],
                )
                or (
                    end + 1 < len(lines)
                    and extractor._is_table_row(lines[end])
                    and extractor._is_table_separator_row(lines[end + 1])
                )
            ):
                break
            end += 1
        for paragraph_index in range(index, end):
            construct = unsupported_rich_markdown_inline(
                lines[paragraph_index]
            )
            if construct is not None:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
                    text,
                    starts[paragraph_index],
                    construct,
                )
        blocks.append(
            _Block(
                kind=SectionKind.PARAGRAPH,
                start=starts[index],
                end=_line_end(lines, starts, end - 1),
            )
        )
        index = end
    return tuple(blocks)


def _heading_text(source: str, level: int) -> str:
    lines = source.splitlines()
    if len(lines) == 2 and _SETEXT.fullmatch(lines[1]):
        return lines[0].strip()
    match = _ATX_HEADING.fullmatch(source)
    if match is None:
        raise ValueError("rich heading source must match its declared syntax")
    assert len(match.group(1)) == level
    return match.group(2).strip()


def _split_ranges(source: str, capacity: int) -> tuple[tuple[int, int], ...]:
    tokens = tuple(_TOKEN.finditer(source))
    if not tokens or capacity < 1:
        return ()
    return tuple(
        (tokens[index].start(), tokens[min(index + capacity, len(tokens)) - 1].end())
        for index in range(0, len(tokens), capacity)
    )


def _section(
    block: _Block,
    source: str,
    path: StructuralPath,
    position: SourceSpan,
) -> ParsedSection:
    return ParsedSection(
        kind=block.kind,
        text=(
            _heading_text(source, block.level)
            if block.kind is SectionKind.HEADING and block.level is not None
            else source
        ),
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


def compile_rich_markdown(
    source: bytes,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
    """Compile exact bytes through the explicit rich v3 representation."""

    if type(source) is not bytes:
        raise TypeError("rich Markdown compiler source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig:
        raise TypeError("rich Markdown compiler config must be exact")
    if config.version != "markdown-config-v3":
        raise ValueError("rich Markdown compiler requires markdown-config-v3")
    assert config.token_ceiling is not None
    token_ceiling = config.token_ceiling
    normalized = _normalize(source)
    if isinstance(normalized, CompilationFailure):
        return normalized
    try:
        blocks = _blocks(normalized)
    except Exception:
        return _failure(
            CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            normalized,
            0,
        )
    if isinstance(blocks, CompilationFailure):
        return blocks
    if not blocks:
        return _failure(
            CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            normalized,
            0,
        )

    headings: list[ParsedSection] = []
    counters: dict[tuple[tuple[str, ...], SectionKind], int] = {}
    ordinals: dict[SectionKind, int] = {}
    sections: list[ParsedSection] = []
    fragments: list[CompiledFragment] = []
    for block in blocks:
        full_source = normalized[block.start : block.end]
        if block.kind is SectionKind.HEADING:
            assert block.level is not None
            headings = [
                heading
                for heading in headings
                if heading.level is not None and heading.level < block.level
            ]
        parents = tuple(headings)
        parent_path = parents[-1].path.segments if parents else ("document",)
        ancestry = "\n\n".join(
            f"{'#' * heading.level} {heading.text}"
            for heading in parents
            if heading.level is not None
        )
        capacity = token_ceiling - rich_token_count(ancestry)
        if block.kind is SectionKind.HEADING:
            ranges: tuple[tuple[int, int], ...] = ((0, len(full_source)),)
            if rich_token_count(full_source) > capacity:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    normalized,
                    block.start,
                )
        elif block.indivisible or block.kind in {
            SectionKind.LIST,
            SectionKind.FENCED_CODE,
            SectionKind.TABLE,
        }:
            ranges = ((0, len(full_source)),)
            if rich_token_count(full_source) > capacity:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    normalized,
                    block.start,
                )
        else:
            ranges = _split_ranges(full_source, capacity)
            if not ranges:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    normalized,
                    block.start,
                )
        for relative_start, relative_end in ranges:
            source_text = full_source[relative_start:relative_end]
            key = (parent_path, block.kind)
            counters[key] = counters.get(key, 0) + 1
            try:
                path = StructuralPath(
                    parent_path + (f"{block.kind.value}[{counters[key]}]",)
                )
                position = _span(
                    normalized,
                    block.start + relative_start,
                    block.start + relative_end,
                )
                section = _section(block, source_text, path, position)
                ordinals[block.kind] = ordinals.get(block.kind, 0) + 1
                contextual = (
                    f"{ancestry}\n\n{source_text}" if ancestry else source_text
                )
                phrases = tuple(dict.fromkeys((source_text, section.text)))
                fragment = CompiledFragment(
                    fragment_ref=(
                        f"fragment:{block.kind.value}:{ordinals[block.kind]}"
                    ),
                    kind=block.kind,
                    path=path,
                    position=position,
                    source_text=source_text,
                    contextual_text=contextual,
                    parent_headings=parents,
                    search_phrases=phrases,
                )
            except Exception:
                return _failure(
                    CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
                    normalized,
                    block.start + relative_start,
                )
            fragments.append(fragment)
            sections.append(section)
            if block.kind is SectionKind.HEADING:
                headings.append(section)
    try:
        provenance = CompilationProvenance(
            compiler_version=MARKDOWN_COMPILER_V3_VERSION,
            config_version=config.version,
            canonicalization_profile=MARKDOWN_RICH_CANONICALIZATION_PROFILE,
            compilation_digest_profile=MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
            token_ceiling=token_ceiling,
        )
        return ParsedDocument.rich_v3(
            canonical_text=normalized,
            sections=tuple(sections),
            fragments=tuple(fragments),
            provenance=provenance,
        )
    except Exception:
        return _failure(
            CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
            normalized,
            0,
        )
