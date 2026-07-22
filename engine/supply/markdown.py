"""Typed contracts for the first deterministic Markdown compiler seam."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Final, cast

import rfc8785

MARKDOWN_COMPILER_V1_VERSION: Final = "context-engine-markdown-v1"
MARKDOWN_COMPILER_VERSION: Final = "context-engine-markdown-v2"
MARKDOWN_CANONICALIZATION_V1_PROFILE: Final = "markdown-heading-paragraph-v1"
MARKDOWN_CANONICALIZATION_PROFILE: Final = "markdown-structural-units-v2"
MARKDOWN_CONTENT_HASH_PROFILE: Final = "sha256-canonical-utf8-v1"
MARKDOWN_COMPILATION_DIGEST_V1_PROFILE: Final = "rfc8785-sha256-v1"
MARKDOWN_COMPILATION_DIGEST_PROFILE: Final = "rfc8785-sha256-v2"
MARKDOWN_CODE_LANGUAGE_MAX_LENGTH: Final = 64
_COMPILATION_DIGEST_V1_DOMAIN: Final = b"context-engine.markdown-compilation.v1\x00"
_COMPILATION_DIGEST_DOMAIN: Final = b"context-engine.markdown-compilation.v2\x00"
_MAX_VERSION_LENGTH: Final = 128


def _require_version(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_VERSION_LENGTH
        or not (value[0].isascii() and value[0].isalnum())
        or any(
            not (character.isascii() and (character.isalnum() or character in "._-"))
            for character in value
        )
    ):
        raise ValueError("Markdown config version must be a bounded opaque token")
    return value


def _require_sha256(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class MarkdownCompilerConfig:
    """Explicit representation-affecting compiler configuration identity."""

    version: str

    def __post_init__(self) -> None:
        _require_version(self.version)


@dataclass(frozen=True, slots=True)
class SourcePoint:
    """One source point; line/column are one-based and byte is zero-based."""

    line: int
    column: int
    byte_offset: int

    def __post_init__(self) -> None:
        if type(self.line) is not int or self.line < 1:
            raise ValueError("source line must be a positive integer")
        if type(self.column) is not int or self.column < 1:
            raise ValueError("source column must be a positive integer")
        if type(self.byte_offset) is not int or self.byte_offset < 0:
            raise ValueError("source byte offset must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """End-exclusive source span over canonical normalized UTF-8 text."""

    start: SourcePoint
    end: SourcePoint

    def __post_init__(self) -> None:
        if type(self.start) is not SourcePoint or type(self.end) is not SourcePoint:
            raise TypeError("source span requires exact SourcePoint values")
        if (
            self.end.byte_offset < self.start.byte_offset
            or (self.end.line, self.end.column) < (self.start.line, self.start.column)
        ):
            raise ValueError("source span end must not precede its start")
        byte_advanced = self.end.byte_offset > self.start.byte_offset
        coordinate_advanced = (self.end.line, self.end.column) > (
            self.start.line,
            self.start.column,
        )
        if byte_advanced is not coordinate_advanced:
            raise ValueError("source span coordinates and bytes must advance together")


@dataclass(frozen=True, slots=True)
class StructuralPath:
    """Stable structural address within the narrow parsed document."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.segments) is not tuple
            or not self.segments
            or any(
                type(segment) is not str
                or not segment
                or segment != segment.strip()
                for segment in self.segments
            )
        ):
            raise ValueError("structural path requires nonblank string segments")


class SectionKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    FENCED_CODE = "fenced_code"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """One typed source-ordered section in the supported Markdown shape."""

    kind: SectionKind
    text: str
    path: StructuralPath
    position: SourceSpan
    level: int | None = None
    list_ordered: bool | None = None
    list_items: tuple[str, ...] = ()
    code_language: str | None = None
    code_body: str | None = None
    table_header: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not SectionKind:
            raise TypeError("parsed section kind must be SectionKind")
        if (
            type(self.text) is not str
            or not self.text
            or self.text.isspace()
            or self.text != self.text.strip()
        ):
            raise ValueError("parsed section text must be exact nonblank text")
        if type(self.path) is not StructuralPath:
            raise TypeError("parsed section path must be StructuralPath")
        if type(self.position) is not SourceSpan:
            raise TypeError("parsed section position must be SourceSpan")
        if self.kind is SectionKind.HEADING:
            if type(self.level) is not int or not 1 <= self.level <= 6:
                raise ValueError("heading level must be between one and six")
        elif self.level is not None:
            raise ValueError("non-heading sections have no heading level")
        if self.kind is SectionKind.LIST:
            if type(self.list_ordered) is not bool or not self.list_items:
                raise ValueError("list sections require ordered identity and items")
            if any(
                type(item) is not str or not item or item.isspace()
                for item in self.list_items
            ):
                raise ValueError("list items must be exact nonblank text")
        elif self.list_ordered is not None or self.list_items:
            raise ValueError("only list sections carry list values")
        if self.kind is SectionKind.FENCED_CODE:
            if self.code_body is None or self.code_body.isspace():
                raise ValueError("fenced code sections require a nonblank body")
            if self.code_language is not None and (
                not self.code_language
                or len(self.code_language) > MARKDOWN_CODE_LANGUAGE_MAX_LENGTH
                or self.code_language != self.code_language.strip()
                or any(character.isspace() for character in self.code_language)
            ):
                raise ValueError("code language must be a bounded opaque token")
        elif self.code_language is not None or self.code_body is not None:
            raise ValueError("only fenced code sections carry code values")
        if self.kind is SectionKind.TABLE:
            if not self.table_header or not self.table_rows:
                raise ValueError("table sections require a header and rows")
            width = len(self.table_header)
            if width < 1 or any(len(row) != width for row in self.table_rows):
                raise ValueError("table rows must match the header width")
            if any(
                type(cell) is not str or not cell or cell.isspace()
                for row in (self.table_header, *self.table_rows)
                for cell in row
            ):
                raise ValueError("table cells must be exact nonblank text")
        elif self.table_header or self.table_rows:
            raise ValueError("only table sections carry table values")


@dataclass(frozen=True, slots=True)
class CompiledFragment:
    """One deterministic structural delivery unit derived from a Revision."""

    fragment_ref: str
    kind: SectionKind
    path: StructuralPath
    position: SourceSpan
    source_text: str
    contextual_text: str
    parent_headings: tuple[ParsedSection, ...]
    search_phrases: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.fragment_ref) is not str
            or not re.fullmatch(r"fragment:[a-z_]+:[1-9][0-9]*", self.fragment_ref)
        ):
            raise ValueError("compiled Fragment ref must use the stable closed format")
        if type(self.kind) is not SectionKind:
            raise TypeError("compiled Fragment kind must be SectionKind")
        if type(self.path) is not StructuralPath:
            raise TypeError("compiled Fragment path must be StructuralPath")
        if type(self.position) is not SourceSpan:
            raise TypeError("compiled Fragment position must be SourceSpan")
        for field_name, value in (
            ("source text", self.source_text),
            ("contextual text", self.contextual_text),
        ):
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"compiled Fragment {field_name} must be nonblank")
        if type(self.parent_headings) is not tuple or any(
            type(heading) is not ParsedSection
            or heading.kind is not SectionKind.HEADING
            for heading in self.parent_headings
        ):
            raise TypeError("compiled Fragment parents must be typed headings")
        if (
            type(self.search_phrases) is not tuple
            or not self.search_phrases
            or len(self.search_phrases) != len(set(self.search_phrases))
            or any(
                type(phrase) is not str or not phrase or phrase.isspace()
                for phrase in self.search_phrases
            )
        ):
            raise ValueError(
                "compiled Fragment search phrases must be unique and nonblank"
            )


class CompilationWarningCode(StrEnum):
    """Closed empty vocabulary; Issue #22 emits no warnings."""


@dataclass(frozen=True, slots=True)
class CompilationWarning:
    """Typed warning carrier reserved for later non-lossy compiler notices."""

    code: CompilationWarningCode
    position: SourcePoint

    def __post_init__(self) -> None:
        if type(self.code) is not CompilationWarningCode:
            raise TypeError("compilation warning code must be CompilationWarningCode")
        if type(self.position) is not SourcePoint:
            raise TypeError("compilation warning position must be SourcePoint")


@dataclass(frozen=True, slots=True)
class CompilationProvenance:
    """Exact compiler, configuration, canonicalization, and digest profiles."""

    compiler_version: str
    config_version: str
    canonicalization_profile: str = MARKDOWN_CANONICALIZATION_V1_PROFILE
    content_hash_profile: str = MARKDOWN_CONTENT_HASH_PROFILE
    compilation_digest_profile: str = MARKDOWN_COMPILATION_DIGEST_V1_PROFILE

    def __post_init__(self) -> None:
        _require_version(self.compiler_version)
        _require_version(self.config_version)
        profiles = (
            self.canonicalization_profile,
            self.compilation_digest_profile,
        )
        if profiles not in {
            (
                MARKDOWN_CANONICALIZATION_V1_PROFILE,
                MARKDOWN_COMPILATION_DIGEST_V1_PROFILE,
            ),
            (MARKDOWN_CANONICALIZATION_PROFILE, MARKDOWN_COMPILATION_DIGEST_PROFILE),
        }:
            raise ValueError("Markdown canonicalization and digest profiles must match")
        if self.content_hash_profile != MARKDOWN_CONTENT_HASH_PROFILE:
            raise ValueError("content hash profile must use the active version")

    @property
    def is_structural_v2(self) -> bool:
        return self.canonicalization_profile == MARKDOWN_CANONICALIZATION_PROFILE


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Immutable deterministic result for one supported Markdown document."""

    canonical_text: str
    sections: tuple[ParsedSection, ...]
    content_hash: str
    compilation_digest: str
    provenance: CompilationProvenance
    fragments: tuple[CompiledFragment, ...]
    warnings: tuple[CompilationWarning, ...] = ()

    def __post_init__(self) -> None:
        if type(self.canonical_text) is not str or not self.canonical_text.endswith(
            "\n"
        ):
            raise ValueError("parsed document requires final-newline canonical text")
        if type(self.sections) is not tuple or any(
            type(section) is not ParsedSection for section in self.sections
        ):
            raise TypeError("parsed document sections must be typed immutable values")
        if type(self.fragments) is not tuple or any(
            type(fragment) is not CompiledFragment for fragment in self.fragments
        ):
            raise TypeError("parsed document Fragments must be typed immutable values")
        _require_sha256("content hash", self.content_hash)
        _require_sha256("compilation digest", self.compilation_digest)
        if type(self.provenance) is not CompilationProvenance:
            raise TypeError("parsed document provenance must be CompilationProvenance")
        if type(self.warnings) is not tuple or any(
            type(warning) is not CompilationWarning for warning in self.warnings
        ):
            raise TypeError("parsed document warnings must be typed immutable values")
        if self.warnings:
            raise ValueError("the active Markdown compiler emits no warnings")
        if self.provenance.is_structural_v2:
            _validate_structural_content(
                self.canonical_text,
                self.sections,
                self.fragments,
            )
        else:
            _validate_issue_22_content(self.canonical_text, self.sections)
            if self.fragments != (_issue_22_fragment(self.sections),):
                raise ValueError("Issue #22 Fragment must preserve compatibility")
        expected_content_hash = sha256(self.canonical_text.encode("utf-8")).hexdigest()
        if self.content_hash != expected_content_hash:
            raise ValueError("content hash must match canonical text")
        expected_compilation_digest = _compilation_digest(
            canonical_text=self.canonical_text,
            sections=self.sections,
            fragments=self.fragments,
            content_hash=self.content_hash,
            provenance=self.provenance,
            warnings=self.warnings,
        )
        if self.compilation_digest != expected_compilation_digest:
            raise ValueError("compilation digest must match the parsed document")

    @classmethod
    def issue_22(
        cls,
        *,
        canonical_text: str,
        sections: tuple[ParsedSection, ParsedSection],
        provenance: CompilationProvenance,
    ) -> ParsedDocument:
        """Build the exact self-validating Issue #22 document from parsed values."""

        if cls is not ParsedDocument:
            raise TypeError("Issue #22 ParsedDocument construction is exact")
        content_hash = sha256(canonical_text.encode("utf-8")).hexdigest()
        fragments = (_issue_22_fragment(sections),)
        compilation_digest = _compilation_digest(
            canonical_text=canonical_text,
            sections=sections,
            fragments=fragments,
            content_hash=content_hash,
            provenance=provenance,
            warnings=(),
        )
        return ParsedDocument(
            canonical_text=canonical_text,
            sections=sections,
            content_hash=content_hash,
            compilation_digest=compilation_digest,
            provenance=provenance,
            fragments=fragments,
        )

    @classmethod
    def structural_v2(
        cls,
        *,
        canonical_text: str,
        sections: tuple[ParsedSection, ...],
        fragments: tuple[CompiledFragment, ...],
        provenance: CompilationProvenance,
    ) -> ParsedDocument:
        """Build one self-validating structural compilation result."""

        if cls is not ParsedDocument or not provenance.is_structural_v2:
            raise TypeError(
                "structural ParsedDocument construction requires v2 provenance"
            )
        content_hash = sha256(canonical_text.encode("utf-8")).hexdigest()
        compilation_digest = _compilation_digest(
            canonical_text=canonical_text,
            sections=sections,
            fragments=fragments,
            content_hash=content_hash,
            provenance=provenance,
            warnings=(),
        )
        return ParsedDocument(
            canonical_text=canonical_text,
            sections=sections,
            content_hash=content_hash,
            compilation_digest=compilation_digest,
            provenance=provenance,
            fragments=fragments,
        )


class CompilationFailureCode(StrEnum):
    INVALID_UTF8 = "invalid_utf8"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    UNSUPPORTED_DOCUMENT_SHAPE = "unsupported_document_shape"


class UnsupportedConstruct(StrEnum):
    ATX_CLOSING_SEQUENCE = "atx_closing_sequence"
    BLOCKQUOTE = "blockquote"
    CODE_BLOCK = "code_block"
    CONTROL_CHARACTER = "control_character"
    EMPHASIS = "emphasis"
    ENTITY = "entity"
    ESCAPE = "escape"
    FRONTMATTER_OR_RULE = "frontmatter_or_rule"
    HARD_BREAK = "hard_break"
    HTML = "html"
    INLINE_CODE = "inline_code"
    LINK_OR_IMAGE = "link_or_image"
    LIST = "list"
    NESTED_HEADING = "nested_heading"
    STRIKETHROUGH = "strikethrough"
    TABLE = "table"


_LIST_PATTERN: Final = re.compile(r"^ {0,3}(?:[-+*]|[0-9]+[.)])\s+")
_HEADING_BLOCK_PATTERN: Final = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
_THEMATIC_BREAK_PATTERN: Final = re.compile(
    r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$"
)
_REFERENCE_LINK_PATTERN: Final = re.compile(
    r"!?\[[^]]*](?:\[[^]]*]|\s*:)"
)
_ENTITY_PATTERN: Final = re.compile(
    r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"
)
_ESCAPE_PATTERN: Final = re.compile(
    r'''\\[!"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~]'''
)
_ATX_CLOSING_SEQUENCE_PATTERN: Final = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_EMPHASIS_PATTERN: Final = re.compile(
    r"(?:\*\*(?=\S)(?:(?!\*\*).)*\S\*\*|"
    r"(?<![\w_])__(?=\S)(?:(?!__).)*\S__(?![\w_])|"
    r"(?<!\*)\*(?=\S)(?:[^*\n]*\S)?\*(?!\*)|"
    r"(?<![\w_])_(?=\S)(?:[^_\n]*\S)?_(?![\w_]))"
)


def unsupported_markdown_construct(
    line: str,
    *,
    supported_heading: bool,
) -> UnsupportedConstruct | None:
    """Classify syntax outside the deliberately closed Issue #22 grammar."""

    inspected = line[2:] if supported_heading and line.startswith("# ") else line
    if line.startswith(("    ", "\t")):
        return UnsupportedConstruct.CODE_BLOCK
    if any(
        ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        for character in line
    ):
        return UnsupportedConstruct.CONTROL_CHARACTER
    if re.match(r"^ {0,3}(?:`{3,}|~{3,})", line):
        return UnsupportedConstruct.CODE_BLOCK
    if re.match(r"^ {0,3}>", line):
        return UnsupportedConstruct.BLOCKQUOTE
    if _HEADING_BLOCK_PATTERN.match(line) and not (
        supported_heading and line.startswith("# ")
    ):
        return UnsupportedConstruct.NESTED_HEADING
    if supported_heading and _ATX_CLOSING_SEQUENCE_PATTERN.search(inspected):
        return UnsupportedConstruct.ATX_CLOSING_SEQUENCE
    if _THEMATIC_BREAK_PATTERN.fullmatch(line):
        return UnsupportedConstruct.FRONTMATTER_OR_RULE
    if _LIST_PATTERN.match(line):
        return UnsupportedConstruct.LIST
    if re.search(r"!?\[[^]]*]\([^)]*\)", inspected):
        return UnsupportedConstruct.LINK_OR_IMAGE
    if _REFERENCE_LINK_PATTERN.search(inspected):
        return UnsupportedConstruct.LINK_OR_IMAGE
    if "`" in inspected:
        return UnsupportedConstruct.INLINE_CODE
    if _EMPHASIS_PATTERN.search(inspected):
        return UnsupportedConstruct.EMPHASIS
    if "~~" in inspected:
        return UnsupportedConstruct.STRIKETHROUGH
    if re.search(r"<[/!?A-Za-z][^>]*>", inspected):
        return UnsupportedConstruct.HTML
    if _ESCAPE_PATTERN.search(inspected):
        return UnsupportedConstruct.ESCAPE
    if _ENTITY_PATTERN.search(inspected):
        return UnsupportedConstruct.ENTITY
    if inspected.endswith(("  ", "\\")):
        return UnsupportedConstruct.HARD_BREAK
    return None


@dataclass(frozen=True, slots=True)
class CompilationFailure:
    """Typed all-or-nothing failure; it never carries partial ParsedDocument data."""

    code: CompilationFailureCode
    position: SourcePoint | None
    construct: UnsupportedConstruct | None = None

    def __post_init__(self) -> None:
        if type(self.code) is not CompilationFailureCode:
            raise TypeError("compilation failure code must be CompilationFailureCode")
        if self.position is not None and type(self.position) is not SourcePoint:
            raise TypeError("compilation failure position must be SourcePoint or None")
        if self.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT:
            if type(self.construct) is not UnsupportedConstruct:
                raise ValueError(
                    "unsupported construct failure must name its construct"
                )
        elif self.construct is not None:
            raise ValueError("only unsupported construct failures name a construct")


type CompilationOutcome = ParsedDocument | CompilationFailure


def _section_document(section: ParsedSection) -> dict[str, object]:
    document: dict[str, object] = {
        "kind": section.kind.value,
        "path": list(section.path.segments),
        "position": {
            "end": {
                "byteOffset": section.position.end.byte_offset,
                "column": section.position.end.column,
                "line": section.position.end.line,
            },
            "start": {
                "byteOffset": section.position.start.byte_offset,
                "column": section.position.start.column,
                "line": section.position.start.line,
            },
        },
        "text": section.text,
    }
    if section.level is not None:
        document["level"] = section.level
    if section.kind is SectionKind.LIST:
        document["ordered"] = section.list_ordered
        document["items"] = list(section.list_items)
    elif section.kind is SectionKind.FENCED_CODE:
        document["language"] = section.code_language
        document["code"] = section.code_body
    elif section.kind is SectionKind.TABLE:
        document["header"] = list(section.table_header)
        document["rows"] = [list(row) for row in section.table_rows]
    return document


def _point_document(point: SourcePoint) -> dict[str, int]:
    return {
        "byteOffset": point.byte_offset,
        "column": point.column,
        "line": point.line,
    }


def _fragment_document(fragment: CompiledFragment) -> dict[str, object]:
    return {
        "contextualText": fragment.contextual_text,
        "fragmentRef": fragment.fragment_ref,
        "kind": fragment.kind.value,
        "parentHeadings": [
            {
                "level": heading.level,
                "path": list(heading.path.segments),
                "position": {
                    "end": _point_document(heading.position.end),
                    "start": _point_document(heading.position.start),
                },
                "text": heading.text,
            }
            for heading in fragment.parent_headings
        ],
        "path": list(fragment.path.segments),
        "position": {
            "end": _point_document(fragment.position.end),
            "start": _point_document(fragment.position.start),
        },
        "searchPhrases": list(fragment.search_phrases),
        "sourceText": fragment.source_text,
    }


def _compilation_document(
    *,
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    fragments: tuple[CompiledFragment, ...],
    content_hash: str,
    provenance: CompilationProvenance,
    warnings: tuple[CompilationWarning, ...],
) -> dict[str, object]:
    document: dict[str, object] = {
        "canonicalText": canonical_text,
        "contentHash": content_hash,
        "provenance": {
            "canonicalizationProfile": provenance.canonicalization_profile,
            "compilationDigestProfile": provenance.compilation_digest_profile,
            "compilerVersion": provenance.compiler_version,
            "configVersion": provenance.config_version,
            "contentHashProfile": provenance.content_hash_profile,
        },
        "sections": [_section_document(section) for section in sections],
        "warnings": [
            {
                "code": warning.code.value,
                "position": {
                    "byteOffset": warning.position.byte_offset,
                    "column": warning.position.column,
                    "line": warning.position.line,
                },
            }
            for warning in warnings
        ],
    }
    if provenance.is_structural_v2:
        document["fragments"] = [
            _fragment_document(fragment) for fragment in fragments
        ]
    return document


def _document_without_digest(document: ParsedDocument) -> dict[str, object]:
    return _compilation_document(
        canonical_text=document.canonical_text,
        sections=document.sections,
        fragments=document.fragments,
        content_hash=document.content_hash,
        provenance=document.provenance,
        warnings=document.warnings,
    )


def _compilation_digest(
    *,
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    fragments: tuple[CompiledFragment, ...],
    content_hash: str,
    provenance: CompilationProvenance,
    warnings: tuple[CompilationWarning, ...],
) -> str:
    document = _compilation_document(
        canonical_text=canonical_text,
        sections=sections,
        fragments=fragments,
        content_hash=content_hash,
        provenance=provenance,
        warnings=warnings,
    )
    domain = (
        _COMPILATION_DIGEST_DOMAIN
        if provenance.is_structural_v2
        else _COMPILATION_DIGEST_V1_DOMAIN
    )
    return sha256(domain + rfc8785.dumps(cast(Any, document))).hexdigest()


def _issue_22_fragment(
    sections: tuple[ParsedSection, ...],
) -> CompiledFragment:
    heading, paragraph = sections
    return CompiledFragment(
        fragment_ref="fragment:paragraph:1",
        kind=SectionKind.PARAGRAPH,
        path=paragraph.path,
        position=paragraph.position,
        source_text=paragraph.text,
        contextual_text=paragraph.text,
        parent_headings=(heading,),
        search_phrases=(paragraph.text,),
    )


def _heading_source(heading: ParsedSection) -> str:
    assert heading.level is not None
    return f"{'#' * heading.level} {heading.text}"


def _expected_contextual_text(fragment: CompiledFragment) -> str:
    ancestry = "\n\n".join(
        _heading_source(heading) for heading in fragment.parent_headings
    )
    return f"{ancestry}\n\n{fragment.source_text}" if ancestry else fragment.source_text


def _expected_search_phrases(
    section: ParsedSection,
    source_text: str,
) -> tuple[str, ...]:
    if section.kind is SectionKind.LIST:
        values = (source_text, *section.list_items)
    elif section.kind is SectionKind.FENCED_CODE:
        assert section.code_body is not None
        values = (source_text, section.code_body)
    elif section.kind is SectionKind.TABLE:
        values = (
            source_text,
            *section.table_header,
            *(cell for row in section.table_rows for cell in row),
        )
    elif section.kind is SectionKind.HEADING:
        values = (source_text, section.text)
    else:
        values = (source_text,)
    return tuple(dict.fromkeys(values))


def _validate_section_source(section: ParsedSection, source_text: str) -> None:
    lines = source_text.split("\n")
    if section.kind is SectionKind.HEADING:
        assert section.level is not None
        expected_prefix = f"{'#' * section.level} "
        valid = (
            len(lines) == 1
            and source_text.startswith(expected_prefix)
            and section.text == source_text[len(expected_prefix) :]
            and unsupported_markdown_construct(
                f"# {section.text}", supported_heading=True
            )
            is None
        )
    elif section.kind is SectionKind.PARAGRAPH:
        valid = section.text == source_text and all(
            line and unsupported_markdown_construct(line, supported_heading=False)
            is None
            for line in lines
        )
    elif section.kind is SectionKind.LIST:
        pattern = (
            re.compile(r"^[1-9][0-9]*\. (\S(?:.*\S)?)$")
            if section.list_ordered
            else re.compile(r"^- (\S(?:.*\S)?)$")
        )
        matches = tuple(pattern.fullmatch(line) for line in lines)
        valid = (
            all(match is not None for match in matches)
            and section.text == source_text
            and section.list_items
            == tuple(match.group(1) for match in matches if match is not None)
            and all(
                unsupported_markdown_construct(item, supported_heading=False) is None
                for item in section.list_items
            )
        )
    elif section.kind is SectionKind.FENCED_CODE:
        assert section.code_body is not None
        fence = re.fullmatch(
            rf"```([A-Za-z0-9_.+-]{{1,{MARKDOWN_CODE_LANGUAGE_MAX_LENGTH}}})?",
            lines[0],
        )
        valid = (
            len(lines) >= 3
            and fence is not None
            and lines[-1] == "```"
            and section.text == source_text
            and section.code_language == fence.group(1)
            and section.code_body == "\n".join(lines[1:-1])
            and all(
                not any(
                    ord(character) < 0x20
                    or 0x7F <= ord(character) <= 0x9F
                    for character in code_line
                )
                for code_line in lines[1:-1]
            )
        )
    else:
        cells = tuple(
            tuple(cell.strip() for cell in line[1:-1].split("|"))
            if line.startswith("|") and line.endswith("|")
            else ()
            for line in lines
        )
        valid = (
            len(cells) >= 3
            and all(cells)
            and section.text == source_text
            and section.table_header == cells[0]
            and all(re.fullmatch(r"-{3,}", cell) for cell in cells[1])
            and section.table_rows == cells[2:]
            and all(len(row) == len(cells[0]) for row in cells)
            and all(
                unsupported_markdown_construct(cell, supported_heading=False) is None
                for row in (section.table_header, *section.table_rows)
                for cell in row
            )
        )
    if not valid:
        raise ValueError("structural section metadata must match its source text")


def _expected_point(canonical_text: str, byte_offset: int) -> SourcePoint:
    prefix = canonical_text.encode("utf-8")[:byte_offset].decode("utf-8")
    last_newline = prefix.rfind("\n")
    return SourcePoint(
        line=prefix.count("\n") + 1,
        column=len(prefix[last_newline + 1 :]) + 1,
        byte_offset=byte_offset,
    )


def _validate_structural_content(
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    fragments: tuple[CompiledFragment, ...],
) -> None:
    if (
        "\r" in canonical_text
        or canonical_text.startswith("\ufeff")
        or canonical_text.endswith("\n\n")
    ):
        raise ValueError("structural Markdown must use canonical transport text")
    if not sections or not fragments or len(sections) != len(fragments):
        raise ValueError("structural Markdown requires one Fragment per section")
    if sections[0].kind is not SectionKind.HEADING or sections[0].level != 1:
        raise ValueError("structural Markdown must begin with a level-one heading")
    canonical_bytes = canonical_text.encode("utf-8")
    prior_end = -1
    fragment_refs: set[str] = set()
    headings: list[ParsedSection] = []
    counters: dict[tuple[tuple[str, ...], SectionKind], int] = {}
    kind_ordinals: dict[SectionKind, int] = {}
    for section, fragment in zip(sections, fragments, strict=True):
        if (
            fragment.kind is not section.kind
            or fragment.path != section.path
            or fragment.position != section.position
            or fragment.fragment_ref in fragment_refs
            or section.position.start.byte_offset <= prior_end
        ):
            raise ValueError("structural Fragment lineage must match source order")
        if _expected_point(canonical_text, section.position.start.byte_offset) != (
            section.position.start
        ) or _expected_point(canonical_text, section.position.end.byte_offset) != (
            section.position.end
        ):
            raise ValueError("structural source coordinates must match UTF-8 offsets")
        gap_start = 0 if prior_end < 0 else prior_end
        source_gap = canonical_bytes[
            gap_start : section.position.start.byte_offset
        ]
        if any(byte != 0x0A for byte in source_gap):
            raise ValueError("structural sections cannot omit canonical content")
        source_text = canonical_bytes[
            section.position.start.byte_offset : section.position.end.byte_offset
        ].decode("utf-8")
        if source_text != fragment.source_text:
            raise ValueError("structural Fragment source text must match its span")
        _validate_section_source(section, source_text)
        if section.kind is SectionKind.HEADING:
            assert section.level is not None
            if section.level > len(headings) + 1:
                raise ValueError("structural headings cannot skip a level")
            headings = headings[: section.level - 1]
            parent_path = (
                headings[-1].path.segments if headings else ("document",)
            )
        else:
            if not headings:
                raise ValueError("structural content requires a parent heading")
            parent_path = headings[-1].path.segments
        counter_key = (parent_path, section.kind)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        expected_path = StructuralPath(
            parent_path + (f"{section.kind.value}[{counters[counter_key]}]",)
        )
        kind_ordinals[section.kind] = kind_ordinals.get(section.kind, 0) + 1
        expected_ref = f"fragment:{section.kind.value}:{kind_ordinals[section.kind]}"
        if (
            section.path != expected_path
            or fragment.parent_headings != tuple(headings)
            or fragment.fragment_ref != expected_ref
            or fragment.search_phrases
            != _expected_search_phrases(section, source_text)
        ):
            raise ValueError("structural Fragment derivation must be exact")
        if fragment.contextual_text != _expected_contextual_text(fragment):
            raise ValueError(
                "structural Fragment context must be exact heading ancestry"
            )
        if any(
            heading.position.start.byte_offset >= section.position.start.byte_offset
            for heading in fragment.parent_headings
        ):
            raise ValueError("Fragment parent headings must precede child content")
        if section.kind is SectionKind.HEADING:
            headings.append(section)
        fragment_refs.add(fragment.fragment_ref)
        prior_end = section.position.end.byte_offset
    if canonical_bytes[prior_end:] != b"\n":
        raise ValueError("structural sections cannot omit trailing canonical content")


def _validate_issue_22_content(
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
) -> None:
    if "\r" in canonical_text or canonical_text.startswith("\ufeff"):
        raise ValueError("canonical Markdown contains noncanonical transport text")
    if canonical_text.endswith("\n\n"):
        raise ValueError("canonical Markdown must end with exactly one newline")
    lines = canonical_text.removesuffix("\n").split("\n")
    if len(lines) != 3 or lines[1] != "" or not lines[2]:
        raise ValueError("canonical Markdown must contain the supported shape")
    if (
        not lines[0].startswith("# ")
        or not lines[0][2:]
        or lines[0][2:] != lines[0][2:].strip()
        or lines[2] != lines[2].strip()
    ):
        raise ValueError("canonical Markdown must contain a level-one heading")
    heading_line = lines[0]
    paragraph_line = lines[2]
    if any(
        unsupported_markdown_construct(line, supported_heading=index == 0)
        is not None
        for index, line in ((0, heading_line), (2, paragraph_line))
    ):
        raise ValueError("canonical text contains an unsupported Markdown construct")
    heading_end = len(heading_line.encode("utf-8"))
    paragraph_start = heading_end + 2
    paragraph_end = paragraph_start + len(paragraph_line.encode("utf-8"))
    heading, paragraph = sections
    expected_heading = (
        heading.text == heading_line[2:]
        and heading.level == 1
        and heading.path.segments == ("document", "heading[1]")
        and heading.position.start == SourcePoint(1, 1, 0)
        and heading.position.end
        == SourcePoint(1, len(heading_line) + 1, heading_end)
    )
    expected_paragraph = (
        paragraph.text == paragraph_line
        and paragraph.level is None
        and paragraph.path.segments
        == ("document", "heading[1]", "paragraph[1]")
        and paragraph.position.start == SourcePoint(3, 1, paragraph_start)
        and paragraph.position.end
        == SourcePoint(3, len(paragraph_line) + 1, paragraph_end)
    )
    if not expected_heading or not expected_paragraph:
        raise ValueError("parsed sections must exactly match canonical text")


def canonicalize_parsed_document(document: ParsedDocument) -> bytes:
    """Return exact RFC 8785 bytes including the verified compilation digest."""

    if type(document) is not ParsedDocument:
        raise TypeError("canonical serialization requires ParsedDocument")
    canonical = _document_without_digest(document)
    canonical["compilationDigest"] = document.compilation_digest
    return rfc8785.dumps(cast(Any, canonical))
