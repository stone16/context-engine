"""Typed contracts for the first deterministic Markdown compiler seam."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Final, cast

import rfc8785

MARKDOWN_COMPILER_V1_VERSION: Final = "context-engine-markdown-v1"
MARKDOWN_COMPILER_VERSION: Final = "context-engine-markdown-v2"
MARKDOWN_COMPILER_V3_VERSION: Final = "context-engine-markdown-v3"
ACTIVE_FILE_IMPORT_MARKDOWN_CONFIG_VERSION: Final = "markdown-config-v1"
MARKDOWN_CANONICALIZATION_V1_PROFILE: Final = "markdown-heading-paragraph-v1"
MARKDOWN_CANONICALIZATION_PROFILE: Final = "markdown-structural-units-v2"
MARKDOWN_CONTENT_HASH_PROFILE: Final = "sha256-canonical-utf8-v1"
MARKDOWN_COMPILATION_DIGEST_V1_PROFILE: Final = "rfc8785-sha256-v1"
MARKDOWN_COMPILATION_DIGEST_PROFILE: Final = "rfc8785-sha256-v2"
MARKDOWN_RICH_CANONICALIZATION_PROFILE: Final = "markdown-rich-structural-v3"
MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE: Final = "rfc8785-sha256-v3"
MARKDOWN_CODE_LANGUAGE_MAX_LENGTH: Final = 64
MARKDOWN_RICH_TOKEN_CEILING: Final = 2048
_COMPILATION_DIGEST_V1_DOMAIN: Final = b"context-engine.markdown-compilation.v1\x00"
_COMPILATION_DIGEST_DOMAIN: Final = b"context-engine.markdown-compilation.v2\x00"
_COMPILATION_DIGEST_V3_DOMAIN: Final = b"context-engine.markdown-compilation.v3\x00"
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
    token_ceiling: int | None = None

    def __post_init__(self) -> None:
        _require_version(self.version)
        if self.version == "markdown-config-v3":
            if self.token_ceiling is None:
                object.__setattr__(self, "token_ceiling", MARKDOWN_RICH_TOKEN_CEILING)
            elif type(self.token_ceiling) is not int or self.token_ceiling < 1:
                raise ValueError("rich Markdown token ceiling must be positive")
        elif self.token_ceiling is not None:
            raise ValueError("only rich Markdown config records a token ceiling")


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
    """End-exclusive source span over the compiler's UTF-8 representation."""

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
        if type(self.text) is not str or not self.text or self.text.isspace():
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
            if len(self.table_header) < 1:
                raise ValueError("table header must carry at least one cell")
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
    token_ceiling: int | None = None

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
            (
                MARKDOWN_RICH_CANONICALIZATION_PROFILE,
                MARKDOWN_RICH_COMPILATION_DIGEST_PROFILE,
            ),
        }:
            raise ValueError("Markdown canonicalization and digest profiles must match")
        if self.content_hash_profile != MARKDOWN_CONTENT_HASH_PROFILE:
            raise ValueError("content hash profile must use the active version")
        if self.is_rich_v3:
            if type(self.token_ceiling) is not int or self.token_ceiling < 1:
                raise ValueError("rich provenance requires a positive token ceiling")
        elif self.token_ceiling is not None:
            raise ValueError("only rich provenance records a token ceiling")

    @property
    def is_structural_v2(self) -> bool:
        return self.canonicalization_profile == MARKDOWN_CANONICALIZATION_PROFILE

    @property
    def is_rich_v3(self) -> bool:
        return self.canonicalization_profile == MARKDOWN_RICH_CANONICALIZATION_PROFILE


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
        if type(self.canonical_text) is not str or not self.canonical_text:
            raise ValueError("parsed document requires nonempty canonical text")
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
        if not self.provenance.is_rich_v3 and not self.canonical_text.endswith("\n"):
            raise ValueError("parsed document requires final-newline canonical text")
        if type(self.warnings) is not tuple or any(
            type(warning) is not CompilationWarning for warning in self.warnings
        ):
            raise TypeError("parsed document warnings must be typed immutable values")
        if self.warnings:
            raise ValueError("the active Markdown compiler emits no warnings")
        if self.provenance.is_rich_v3:
            assert self.provenance.token_ceiling is not None
            _validate_rich_content(
                self.canonical_text,
                self.sections,
                self.fragments,
                self.provenance.token_ceiling,
            )
        elif self.provenance.is_structural_v2:
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

    @classmethod
    def rich_v3(
        cls,
        *,
        canonical_text: str,
        sections: tuple[ParsedSection, ...],
        fragments: tuple[CompiledFragment, ...],
        provenance: CompilationProvenance,
    ) -> ParsedDocument:
        """Build one self-validating rich Markdown compilation result."""

        if cls is not ParsedDocument or not provenance.is_rich_v3:
            raise TypeError("rich ParsedDocument construction requires v3 provenance")
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
_RICH_ATX_HEADING_PATTERN: Final = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$"
)
_RICH_SETEXT_PATTERN: Final = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_RICH_LIST_ITEM_PATTERN: Final = re.compile(
    r"^([ \t]*)(?:[-+*]|[0-9]+[.)])[ \t]+(.+)$"
)
_RICH_FENCE_PATTERN: Final = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?:.*)$"
)
_RICH_WIKILINK_PATTERN: Final = re.compile(r"!?\[\[[^]\r\n]+]]")
_RICH_FOOTNOTE_PATTERN: Final = re.compile(r"\[\^[^]\r\n]+](?::)?")
_RICH_INLINE_MATH_PATTERN: Final = re.compile(
    r"(?<!\\)\$(?!\s).+?(?<!\s)(?<!\\)\$"
)
_RICH_INLINE_CODE_PATTERN: Final = re.compile(r"`+[^`\r\n]+?`+")
_RICH_AUTOLINK_PATTERN: Final = re.compile(
    r"<https?://[^>\s]+>",
    re.IGNORECASE,
)
_RICH_INLINE_LINK_PATTERN: Final = re.compile(
    r"!?\[[^]\r\n]*]\([^()\r\n]+\)"
)
_RICH_REFERENCE_LINK_PATTERN: Final = re.compile(
    r"(?:!?\[[^]\r\n]*]\[[^]\r\n]*]|\[[^]\r\n]+]:[ \t]*\S+)"
)
_RICH_STRIKETHROUGH_PATTERN: Final = re.compile(r"~~(?=\S).+?(?<=\S)~~")
_RICH_ANGLE_LITERAL_PATTERN: Final = re.compile(r"<[^<>\r\n]+>")
_RICH_HTML_OPEN_PATTERN: Final = re.compile(
    r"^ {0,3}<(?P<tag>section|div|details|summary|table|thead|tbody|tr|"
    r"th|td|p|ul|ol|li)\b[^>]*>",
    re.IGNORECASE,
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


def unsupported_rich_markdown_inline(line: str) -> UnsupportedConstruct | None:
    """Classify inline syntax outside the accepted rich-v3 grammar."""

    if type(line) is not str:
        raise TypeError("rich Markdown inline classification requires exact text")
    masked = line.rstrip(" \t")
    if masked.endswith("\\"):
        masked = masked[:-1] + "x"
    for pattern in (
        _RICH_WIKILINK_PATTERN,
        _RICH_FOOTNOTE_PATTERN,
        _RICH_INLINE_MATH_PATTERN,
        _RICH_INLINE_CODE_PATTERN,
        _EMPHASIS_PATTERN,
        _RICH_AUTOLINK_PATTERN,
        _RICH_INLINE_LINK_PATTERN,
        _RICH_REFERENCE_LINK_PATTERN,
        _RICH_STRIKETHROUGH_PATTERN,
        _RICH_ANGLE_LITERAL_PATTERN,
    ):
        masked = pattern.sub(lambda match: "x" * len(match.group()), masked)
    construct = unsupported_markdown_construct(masked, supported_heading=False)
    return None if construct is UnsupportedConstruct.LIST else construct


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
    if provenance.is_rich_v3:
        provenance_value = document["provenance"]
        assert isinstance(provenance_value, dict)
        provenance_value["tokenCeiling"] = provenance.token_ceiling
    if provenance.is_structural_v2 or provenance.is_rich_v3:
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
    if provenance.is_rich_v3:
        domain = _COMPILATION_DIGEST_V3_DOMAIN
    elif provenance.is_structural_v2:
        domain = _COMPILATION_DIGEST_DOMAIN
    else:
        domain = _COMPILATION_DIGEST_V1_DOMAIN
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


def _expected_rich_search_phrases(
    section: ParsedSection,
    source_text: str,
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((source_text, section.text)))


def _rich_table_cells(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return tuple(cell.strip() for cell in stripped.split("|"))


def _rich_table_source_ranges(source: str) -> tuple[tuple[int, int], ...]:
    raw_lines = source.splitlines(keepends=True) or (source,)
    lines: list[str] = []
    starts: list[int] = []
    offset = 0
    for index, raw_line in enumerate(raw_lines):
        content = raw_line.removesuffix("\n").removesuffix("\r")
        bom_width = 1 if index == 0 and content.startswith("\ufeff") else 0
        lines.append(content[bom_width:])
        starts.append(offset + bom_width)
        offset += len(raw_line)
    ranges: list[tuple[int, int]] = []
    index = 0
    while index + 1 < len(lines):
        header = _rich_table_cells(lines[index])
        separator = _rich_table_cells(lines[index + 1])
        if (
            "|" not in lines[index]
            or len(header) < 2
            or not any(header)
            or len(separator) < 2
            or not all(
                re.fullmatch(r":?-+:?", cell.replace(" ", "")) is not None
                for cell in separator
            )
        ):
            index += 1
            continue
        end = index + 2
        while end < len(lines) and "|" in lines[end]:
            end += 1
        ranges.append(
            (
                starts[index],
                starts[end - 1] + len(lines[end - 1]),
            )
        )
        index = end
    return tuple(ranges)


@dataclass(frozen=True, slots=True)
class _RichSourceBlock:
    kind: SectionKind
    start: int
    end: int
    indivisible: bool = False
    heading_level: int | None = None
    heading_text: str | None = None


def _rich_source_line_layout(source: str) -> tuple[list[str], list[int]]:
    raw_lines = source.splitlines(keepends=True) or [source]
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


def _rich_source_line_end(
    lines: list[str], starts: list[int], index: int
) -> int:
    return starts[index] + len(lines[index])


def _rich_table_starts_at(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    header = _rich_table_cells(lines[index])
    separator = _rich_table_cells(lines[index + 1])
    return (
        len(header) >= 2
        and any(header)
        and len(separator) >= 2
        and all(
            re.fullmatch(r":?-+:?", cell.replace(" ", "")) is not None
            for cell in separator
        )
    )


def _rich_source_blocks(source: str) -> tuple[_RichSourceBlock, ...]:
    """Independently derive v3 block boundaries from the canonical source."""

    lines, starts = _rich_source_line_layout(source)
    blocks: list[_RichSourceBlock] = []
    index = 0
    if lines and lines[0] == "---":
        closing = next(
            (
                candidate
                for candidate in range(1, len(lines))
                if lines[candidate] == "---"
            ),
            None,
        )
        if closing is None or closing == 1 or not any(
            line.strip() for line in lines[1:closing]
        ):
            raise ValueError("rich frontmatter source must be complete and nonempty")
        blocks.append(
            _RichSourceBlock(
                kind=SectionKind.PARAGRAPH,
                start=starts[0],
                end=_rich_source_line_end(lines, starts, closing),
            )
        )
        index = closing + 1

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        atx = _RICH_ATX_HEADING_PATTERN.fullmatch(line)
        if atx is not None:
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.HEADING,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, index),
                    heading_level=len(atx.group(1)),
                    heading_text=atx.group(2).strip(),
                )
            )
            index += 1
            continue
        fence = _RICH_FENCE_PATTERN.match(line)
        if fence is not None:
            marker = fence.group("fence")
            closing = index + 1
            while closing < len(lines) and re.fullmatch(
                rf"[ \t]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*",
                lines[closing],
            ) is None:
                closing += 1
            if closing >= len(lines):
                language = line.lstrip()[len(marker) :].strip()
                if not language and any(
                    candidate.strip() for candidate in lines[index + 1 :]
                ):
                    blocks.append(
                        _RichSourceBlock(
                            kind=SectionKind.PARAGRAPH,
                            start=starts[index],
                            end=_rich_source_line_end(lines, starts, len(lines) - 1),
                        )
                    )
                    index = len(lines)
                    continue
                raise ValueError("rich fenced source must match the closed grammar")
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.FENCED_CODE,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, closing),
                    indivisible=True,
                )
            )
            index = closing + 1
            continue
        if index + 1 < len(lines) and _RICH_SETEXT_PATTERN.fullmatch(
            lines[index + 1]
        ):
            underline = lines[index + 1].lstrip()
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.HEADING,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, index + 1),
                    heading_level=1 if underline.startswith("=") else 2,
                    heading_text=line.strip(),
                )
            )
            index += 2
            continue
        if _rich_table_starts_at(lines, index):
            end = index + 2
            while end < len(lines) and "|" in lines[end]:
                end += 1
            header = _rich_table_cells(lines[index])
            separator = _rich_table_cells(lines[index + 1])
            rows = tuple(
                _rich_table_cells(candidate)
                for candidate in lines[index + 2 : end]
            )
            valid = (
                bool(rows)
                and len(header) >= 2
                and len(separator) == len(header)
                and all(
                    len(row) == len(header) and all(row)
                    for row in (header, *rows)
                )
            )
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.TABLE if valid else SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, end - 1),
                    indivisible=not valid,
                )
            )
            index = end
            continue
        if _RICH_LIST_ITEM_PATTERN.fullmatch(line) is not None:
            end = index + 1
            while end < len(lines):
                candidate = lines[end]
                if _RICH_LIST_ITEM_PATTERN.fullmatch(candidate) is not None:
                    end += 1
                    continue
                if candidate.strip() and candidate.startswith((" ", "\t")):
                    end += 1
                    continue
                break
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.LIST,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, end - 1),
                    indivisible=True,
                )
            )
            index = end
            continue
        if line.lstrip().startswith("<"):
            if _RICH_ANGLE_LITERAL_PATTERN.fullmatch(line.strip()) is not None:
                end = index + 1
            else:
                end = index + 1
                while end < len(lines) and lines[end].strip():
                    end += 1
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, end - 1),
                )
            )
            index = end
            continue
        if line.lstrip().startswith(">"):
            end = index + 1
            while end < len(lines) and lines[end].lstrip().startswith(">"):
                end += 1
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, end - 1),
                )
            )
            index = end
            continue
        if _THEMATIC_BREAK_PATTERN.fullmatch(line) is not None:
            blocks.append(
                _RichSourceBlock(
                    kind=SectionKind.PARAGRAPH,
                    start=starts[index],
                    end=_rich_source_line_end(lines, starts, index),
                )
            )
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip():
            if (
                _RICH_ATX_HEADING_PATTERN.fullmatch(lines[end]) is not None
                or _RICH_FENCE_PATTERN.match(lines[end]) is not None
                or _RICH_LIST_ITEM_PATTERN.fullmatch(lines[end]) is not None
                or lines[end].lstrip().startswith((">", "<"))
                or _THEMATIC_BREAK_PATTERN.fullmatch(lines[end]) is not None
                or _rich_table_starts_at(lines, end)
            ):
                break
            end += 1
        blocks.append(
            _RichSourceBlock(
                kind=SectionKind.PARAGRAPH,
                start=starts[index],
                end=_rich_source_line_end(lines, starts, end - 1),
            )
        )
        index = end
    return tuple(blocks)


def _expected_rich_fragment_layout(
    canonical_text: str,
    token_ceiling: int,
) -> tuple[tuple[SectionKind, int, int], ...]:
    expected: list[tuple[SectionKind, int, int]] = []
    headings: list[tuple[int, str]] = []
    for block in _rich_source_blocks(canonical_text):
        if block.kind is SectionKind.HEADING:
            assert block.heading_level is not None
            headings = [
                heading for heading in headings if heading[0] < block.heading_level
            ]
        source = canonical_text[block.start : block.end]
        ancestry = "\n\n".join(
            f"{'#' * level} {text}" for level, text in headings
        )
        capacity = token_ceiling - len(re.findall(r"\S+", ancestry))
        indivisible = block.indivisible or block.kind in {
            SectionKind.HEADING,
            SectionKind.LIST,
            SectionKind.FENCED_CODE,
            SectionKind.TABLE,
        }
        ranges: tuple[tuple[int, int], ...]
        if indivisible:
            if len(re.findall(r"\S+", source)) > capacity:
                raise ValueError("rich indivisible source exceeds its ceiling")
            ranges = ((0, len(source)),)
        else:
            tokens = tuple(re.finditer(r"\S+", source))
            if not tokens or capacity < 1:
                raise ValueError("rich paragraph cannot fit its ceiling")
            ranges = tuple(
                (
                    tokens[index].start(),
                    tokens[min(index + capacity, len(tokens)) - 1].end(),
                )
                for index in range(0, len(tokens), capacity)
            )
        for relative_start, relative_end in ranges:
            start = len(canonical_text[: block.start + relative_start].encode("utf-8"))
            end = len(canonical_text[: block.start + relative_end].encode("utf-8"))
            expected.append((block.kind, start, end))
        if block.kind is SectionKind.HEADING:
            assert block.heading_level is not None and block.heading_text is not None
            headings.append((block.heading_level, block.heading_text))
    return tuple(expected)


def _expected_rich_section_kind(source: str) -> SectionKind:
    lines = source.splitlines()
    if len(lines) >= 2 and lines[0] == "---" and lines[-1] == "---":
        return SectionKind.PARAGRAPH
    if (
        _RICH_ATX_HEADING_PATTERN.fullmatch(source) is not None
        or len(lines) == 2
        and _RICH_SETEXT_PATTERN.fullmatch(lines[1]) is not None
    ):
        return SectionKind.HEADING
    fence = _RICH_FENCE_PATTERN.match(lines[0]) if lines else None
    if fence is not None and len(lines) >= 3:
        marker = fence.group("fence")
        if re.fullmatch(
            rf"[ \t]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*",
            lines[-1],
        ):
            return SectionKind.FENCED_CODE
    if lines and _RICH_LIST_ITEM_PATTERN.fullmatch(lines[0]) is not None:
        return SectionKind.LIST
    table_ranges = _rich_table_source_ranges(source)
    if table_ranges == ((0, len(source)),):
        header_cells = _rich_table_cells(lines[0])
        separator_cells = _rich_table_cells(lines[1])
        header_width = len(header_cells)
        source_rows = tuple(_rich_table_cells(line) for line in lines[2:])
        if (
            source_rows
            and header_width >= 2
            and len(separator_cells) == header_width
            and all(header_cells)
            and all(len(row) == header_width and all(row) for row in source_rows)
        ):
            return SectionKind.TABLE
    return SectionKind.PARAGRAPH


def _validate_rich_closed_grammar(section: ParsedSection, source: str) -> None:
    if section.kind is not _expected_rich_section_kind(source):
        raise ValueError("rich section kind must match its source grammar")
    lines = source.splitlines()
    metadata_valid = True
    if section.kind is SectionKind.LIST:
        item_matches = tuple(
            match
            for line in lines
            if (match := _RICH_LIST_ITEM_PATTERN.fullmatch(line)) is not None
        )
        metadata_valid = (
            bool(item_matches)
            and section.list_ordered
            is (re.match(r"[0-9]+[.)]", lines[0].lstrip()) is not None)
            and section.list_items
            == tuple(match.group(2).rstrip(" \t") for match in item_matches)
        )
    elif section.kind is SectionKind.FENCED_CODE:
        fence = _RICH_FENCE_PATTERN.match(lines[0]) if lines else None
        assert fence is not None
        marker = fence.group("fence")
        expected_language = lines[0].lstrip()[len(marker) :].strip() or None
        expected_body = "\n".join(lines[1:-1])
        metadata_valid = (
            section.code_language == expected_language
            and section.code_body == expected_body
        )
    elif section.kind is SectionKind.TABLE:
        cells = tuple(_rich_table_cells(line) for line in lines)
        metadata_valid = (
            len(cells) >= 3
            and section.table_header == cells[0]
            and section.table_rows == cells[2:]
        )
    if not metadata_valid:
        raise ValueError("rich section metadata must match its source grammar")
    if section.kind is SectionKind.FENCED_CODE:
        return
    if len(lines) >= 2 and lines[0] == "---" and lines[-1] == "---":
        return
    html_open = _RICH_HTML_OPEN_PATTERN.match(lines[0]) if lines else None
    if (
        html_open is not None
        and re.search(
            rf"</{html_open.group('tag')}[ \t]*>",
            source,
            re.IGNORECASE,
        )
    ):
        return
    if (
        section.kind is SectionKind.PARAGRAPH
        and len(lines) == 1
        and _THEMATIC_BREAK_PATTERN.fullmatch(lines[0]) is not None
    ):
        return
    if (
        section.kind is SectionKind.PARAGRAPH
        and lines
        and _RICH_FENCE_PATTERN.match(lines[0]) is not None
    ):
        marker = _RICH_FENCE_PATTERN.match(lines[0])
        assert marker is not None
        fence_text = marker.group("fence")
        language = lines[0].lstrip()[len(fence_text) :].strip()
        if language or not any(line.strip() for line in lines[1:]):
            raise ValueError("rich section source must match the closed grammar")
        return
    if (
        section.kind is SectionKind.PARAGRAPH
        and _rich_table_source_ranges(source) == ((0, len(source)),)
    ):
        if any(
            unsupported_rich_markdown_inline(line) is not None
            for line in lines
        ):
            raise ValueError("rich section source must match the closed grammar")
        return
    for line in lines:
        inspected = line
        if section.kind is SectionKind.HEADING:
            match = _RICH_ATX_HEADING_PATTERN.fullmatch(line)
            if match is not None:
                inspected = match.group(2).strip()
            elif _RICH_SETEXT_PATTERN.fullmatch(line) is not None:
                continue
        elif section.kind is SectionKind.LIST:
            match = _RICH_LIST_ITEM_PATTERN.fullmatch(line)
            if match is not None:
                inspected = match.group(2)
            elif line.startswith((" ", "\t")):
                inspected = line.lstrip()
        elif line.lstrip().startswith(">"):
            inspected = line.lstrip()[1:].lstrip()
            if inspected.startswith("[!"):
                inspected = _RICH_FOOTNOTE_PATTERN.sub("x", inspected, count=1)
        if unsupported_rich_markdown_inline(inspected) is not None:
            raise ValueError("rich section source must match the closed grammar")


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


def _validate_rich_content(
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    fragments: tuple[CompiledFragment, ...],
    token_ceiling: int,
) -> None:
    if not sections or not fragments or len(sections) != len(fragments):
        raise ValueError("rich Markdown requires one Fragment per parsed section")
    canonical_bytes = canonical_text.encode("utf-8")
    prior_end = 0
    refs: set[str] = set()
    headings: list[ParsedSection] = []
    counters: dict[tuple[tuple[str, ...], SectionKind], int] = {}
    kind_ordinals: dict[SectionKind, int] = {}
    for table_start_character, table_end_character in _rich_table_source_ranges(
        canonical_text
    ):
        table_start = len(
            canonical_text[:table_start_character].encode("utf-8")
        )
        table_end = len(canonical_text[:table_end_character].encode("utf-8"))
        overlapping_sections = tuple(
            section
            for section in sections
            if section.position.start.byte_offset < table_end
            and section.position.end.byte_offset > table_start
        )
        if (
            len(overlapping_sections) != 1
            or overlapping_sections[0].position.start.byte_offset != table_start
            or overlapping_sections[0].position.end.byte_offset != table_end
        ):
            raise ValueError("rich table source must remain atomic")
    expected_layout = tuple(
        (start, end)
        for _, start, end in _expected_rich_fragment_layout(
            canonical_text, token_ceiling
        )
    )
    actual_layout = tuple(
        (
            section.position.start.byte_offset,
            section.position.end.byte_offset,
        )
        for section in sections
    )
    if actual_layout != expected_layout:
        raise ValueError("rich block splitting must be exact")
    for section, fragment in zip(sections, fragments, strict=True):
        if (
            fragment.kind is not section.kind
            or fragment.path != section.path
            or fragment.position != section.position
            or fragment.fragment_ref in refs
            or section.position.start.byte_offset < prior_end
        ):
            raise ValueError("rich Fragment lineage must match source order")
        if _expected_rich_point(
            canonical_text, section.position.start.byte_offset
        ) != (
            section.position.start
        ) or _expected_rich_point(
            canonical_text, section.position.end.byte_offset
        ) != (
            section.position.end
        ):
            raise ValueError("rich source coordinates must match UTF-8 offsets")
        source = canonical_bytes[
            section.position.start.byte_offset : section.position.end.byte_offset
        ].decode("utf-8")
        if source != fragment.source_text:
            raise ValueError("rich Fragment source text must match its span")
        if section.text != source and section.kind is not SectionKind.HEADING:
            raise ValueError("rich section text must match its span")
        _validate_rich_closed_grammar(section, source)
        if section.kind is SectionKind.HEADING:
            assert section.level is not None
            heading_lines = source.splitlines()
            setext = (
                len(heading_lines) == 2
                and re.fullmatch(r"^ {0,3}(=+|-+)[ \t]*$", heading_lines[1])
                is not None
            )
            atx = re.fullmatch(
                r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$",
                source,
            )
            expected_heading_text = (
                heading_lines[0].strip()
                if setext
                else atx.group(2).strip()
                if atx is not None
                else None
            )
            expected_level = (
                1
                if setext and heading_lines[1].lstrip().startswith("=")
                else 2
                if setext
                else len(atx.group(1))
                if atx is not None
                else None
            )
            if section.text != expected_heading_text or section.level != expected_level:
                raise ValueError("rich heading metadata must match its source")
            headings = [
                heading
                for heading in headings
                if heading.level is not None and heading.level < section.level
            ]
        parents = tuple(headings)
        parent_path = parents[-1].path.segments if parents else ("document",)
        counter_key = (parent_path, section.kind)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        expected_path = StructuralPath(
            parent_path + (f"{section.kind.value}[{counters[counter_key]}]",)
        )
        kind_ordinals[section.kind] = kind_ordinals.get(section.kind, 0) + 1
        expected_ref = f"fragment:{section.kind.value}:{kind_ordinals[section.kind]}"
        if (
            section.path != expected_path
            or fragment.parent_headings != parents
            or fragment.fragment_ref != expected_ref
            or fragment.search_phrases
            != _expected_rich_search_phrases(section, source)
        ):
            raise ValueError("rich Fragment derivation must be exact")
        omitted = canonical_bytes[
            prior_end : section.position.start.byte_offset
        ]
        if any(byte not in b" \t\r\n\xef\xbb\xbf" for byte in omitted):
            raise ValueError("rich sections cannot omit non-whitespace source")
        if fragment.contextual_text != _expected_contextual_text(fragment):
            raise ValueError("rich Fragment context must be exact heading ancestry")
        if len(re.findall(r"\S+", fragment.contextual_text)) > token_ceiling:
            raise ValueError("rich Fragment exceeds its provenance-bound ceiling")
        if section.kind is SectionKind.HEADING:
            headings.append(section)
        refs.add(fragment.fragment_ref)
        prior_end = section.position.end.byte_offset
    if any(byte not in b" \t\r\n" for byte in canonical_bytes[prior_end:]):
        raise ValueError("rich sections cannot omit trailing source")


def _expected_rich_point(source_text: str, byte_offset: int) -> SourcePoint:
    prefix = source_text.encode("utf-8")[:byte_offset].decode("utf-8")
    logical = prefix.replace("\r\n", "\n").replace("\r", "\n")
    last_newline = logical.rfind("\n")
    return SourcePoint(
        line=logical.count("\n") + 1,
        column=len(logical[last_newline + 1 :]) + 1,
        byte_offset=byte_offset,
    )


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


def _source_point_from_document(value: object) -> SourcePoint:
    if type(value) is not dict:
        raise ValueError("source point document must be an object")
    document = cast(dict[str, object], value)
    if set(document) != {"line", "column", "byteOffset"}:
        raise ValueError("source point document has unexpected fields")
    return SourcePoint(
        line=cast(int, document["line"]),
        column=cast(int, document["column"]),
        byte_offset=cast(int, document["byteOffset"]),
    )


def _source_span_from_document(value: object) -> SourceSpan:
    if type(value) is not dict:
        raise ValueError("source span document must be an object")
    document = cast(dict[str, object], value)
    if set(document) != {"start", "end"}:
        raise ValueError("source span document has unexpected fields")
    return SourceSpan(
        start=_source_point_from_document(document["start"]),
        end=_source_point_from_document(document["end"]),
    )


def _section_from_document(value: object) -> ParsedSection:
    if type(value) is not dict:
        raise ValueError("section document must be an object")
    document = cast(dict[str, object], value)
    kind = SectionKind(cast(str, document["kind"]))
    path = StructuralPath(tuple(cast(list[str], document["path"])))
    return ParsedSection(
        kind=kind,
        text=cast(str, document["text"]),
        path=path,
        position=_source_span_from_document(document["position"]),
        level=cast(int | None, document.get("level")),
        list_ordered=cast(bool | None, document.get("ordered")),
        list_items=tuple(cast(list[str], document.get("items", []))),
        code_language=cast(str | None, document.get("language")),
        code_body=cast(str | None, document.get("code")),
        table_header=tuple(cast(list[str], document.get("header", []))),
        table_rows=tuple(
            tuple(row) for row in cast(list[list[str]], document.get("rows", []))
        ),
    )


def _fragment_from_document(
    value: object,
    heading_by_key: dict[tuple[str, ...], ParsedSection],
) -> CompiledFragment:
    if type(value) is not dict:
        raise ValueError("Fragment document must be an object")
    document = cast(dict[str, object], value)
    parents: list[ParsedSection] = []
    for parent_value in cast(list[object], document["parentHeadings"]):
        if type(parent_value) is not dict:
            raise ValueError("parent heading document must be an object")
        parent = cast(dict[str, object], parent_value)
        key = tuple(cast(list[str], parent["path"]))
        heading = heading_by_key.get(key)
        if heading is None:
            raise ValueError("Fragment parent heading must name a parsed section")
        parents.append(heading)
    return CompiledFragment(
        fragment_ref=cast(str, document["fragmentRef"]),
        kind=SectionKind(cast(str, document["kind"])),
        path=StructuralPath(tuple(cast(list[str], document["path"]))),
        position=_source_span_from_document(document["position"]),
        source_text=cast(str, document["sourceText"]),
        contextual_text=cast(str, document["contextualText"]),
        parent_headings=tuple(parents),
        search_phrases=tuple(cast(list[str], document["searchPhrases"])),
    )


def deserialize_parsed_document(payload: bytes) -> ParsedDocument:
    """Deserialize runner bytes into the existing self-validating contract."""

    if type(payload) is not bytes:
        raise TypeError("parsed document payload must be exact bytes")
    raw = json.loads(payload)
    if type(raw) is not dict:
        raise ValueError("parsed document payload must contain one object")
    document = cast(dict[str, object], raw)
    provenance_value = document["provenance"]
    if type(provenance_value) is not dict:
        raise ValueError("parsed document provenance must be an object")
    provenance_document = cast(dict[str, object], provenance_value)
    provenance = CompilationProvenance(
        compiler_version=cast(str, provenance_document["compilerVersion"]),
        config_version=cast(str, provenance_document["configVersion"]),
        canonicalization_profile=cast(
            str, provenance_document["canonicalizationProfile"]
        ),
        content_hash_profile=cast(str, provenance_document["contentHashProfile"]),
        compilation_digest_profile=cast(
            str, provenance_document["compilationDigestProfile"]
        ),
        token_ceiling=cast(int | None, provenance_document.get("tokenCeiling")),
    )
    sections = tuple(
        _section_from_document(value)
        for value in cast(list[object], document["sections"])
    )
    heading_by_key = {
        section.path.segments: section
        for section in sections
        if section.kind is SectionKind.HEADING
    }
    fragments = tuple(
        _fragment_from_document(value, heading_by_key)
        for value in cast(list[object], document.get("fragments", []))
    )
    return ParsedDocument(
        canonical_text=cast(str, document["canonicalText"]),
        sections=sections,
        content_hash=cast(str, document["contentHash"]),
        compilation_digest=cast(str, document["compilationDigest"]),
        provenance=provenance,
        fragments=fragments,
    )
