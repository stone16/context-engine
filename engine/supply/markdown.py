"""Typed contracts for the first deterministic Markdown compiler seam."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Final, cast

import rfc8785

MARKDOWN_COMPILER_VERSION: Final = "context-engine-markdown-v1"
MARKDOWN_CANONICALIZATION_PROFILE: Final = "markdown-heading-paragraph-v1"
MARKDOWN_CONTENT_HASH_PROFILE: Final = "sha256-canonical-utf8-v1"
MARKDOWN_COMPILATION_DIGEST_PROFILE: Final = "rfc8785-sha256-v1"
_COMPILATION_DIGEST_DOMAIN: Final = b"context-engine.markdown-compilation.v1\x00"
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
        if self.end.byte_offset < self.start.byte_offset:
            raise ValueError("source span end must not precede its start")


@dataclass(frozen=True, slots=True)
class StructuralPath:
    """Stable structural address within the narrow parsed document."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.segments) is not tuple
            or not self.segments
            or any(type(segment) is not str or not segment for segment in self.segments)
        ):
            raise ValueError("structural path requires nonblank string segments")


class SectionKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """One typed source-ordered section in the supported Markdown shape."""

    kind: SectionKind
    text: str
    path: StructuralPath
    position: SourceSpan
    level: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not SectionKind:
            raise TypeError("parsed section kind must be SectionKind")
        if type(self.text) is not str or not self.text or "\n" in self.text:
            raise ValueError("parsed section text must be one nonblank line")
        if type(self.path) is not StructuralPath:
            raise TypeError("parsed section path must be StructuralPath")
        if type(self.position) is not SourceSpan:
            raise TypeError("parsed section position must be SourceSpan")
        if self.kind is SectionKind.HEADING:
            if self.level != 1:
                raise ValueError("the narrow compiler supports only heading level one")
        elif self.level is not None:
            raise ValueError("paragraph sections have no heading level")


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
    canonicalization_profile: str = MARKDOWN_CANONICALIZATION_PROFILE
    content_hash_profile: str = MARKDOWN_CONTENT_HASH_PROFILE
    compilation_digest_profile: str = MARKDOWN_COMPILATION_DIGEST_PROFILE

    def __post_init__(self) -> None:
        _require_version(self.compiler_version)
        _require_version(self.config_version)
        if self.canonicalization_profile != MARKDOWN_CANONICALIZATION_PROFILE:
            raise ValueError("canonicalization profile must use the active version")
        if self.content_hash_profile != MARKDOWN_CONTENT_HASH_PROFILE:
            raise ValueError("content hash profile must use the active version")
        if self.compilation_digest_profile != MARKDOWN_COMPILATION_DIGEST_PROFILE:
            raise ValueError("compilation digest profile must use the active version")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Immutable deterministic result for one supported Markdown document."""

    canonical_text: str
    sections: tuple[ParsedSection, ...]
    content_hash: str
    compilation_digest: str
    provenance: CompilationProvenance
    warnings: tuple[CompilationWarning, ...] = ()

    def __post_init__(self) -> None:
        if type(self.canonical_text) is not str or not self.canonical_text.endswith(
            "\n"
        ):
            raise ValueError("parsed document requires final-newline canonical text")
        if (
            type(self.sections) is not tuple
            or len(self.sections) != 2
            or any(type(section) is not ParsedSection for section in self.sections)
            or tuple(section.kind for section in self.sections)
            != (SectionKind.HEADING, SectionKind.PARAGRAPH)
        ):
            raise ValueError("parsed document requires one heading and one paragraph")
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
        _validate_issue_22_content(self.canonical_text, self.sections)
        expected_content_hash = sha256(self.canonical_text.encode("utf-8")).hexdigest()
        if self.content_hash != expected_content_hash:
            raise ValueError("content hash must match canonical text")
        expected_compilation_digest = _compilation_digest(
            canonical_text=self.canonical_text,
            sections=self.sections,
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
        compilation_digest = _compilation_digest(
            canonical_text=canonical_text,
            sections=sections,
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
        )


class CompilationFailureCode(StrEnum):
    INVALID_UTF8 = "invalid_utf8"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    UNSUPPORTED_DOCUMENT_SHAPE = "unsupported_document_shape"


class UnsupportedConstruct(StrEnum):
    BLOCKQUOTE = "blockquote"
    CODE_BLOCK = "code_block"
    CONTROL_CHARACTER = "control_character"
    EMPHASIS = "emphasis"
    FRONTMATTER_OR_RULE = "frontmatter_or_rule"
    HTML = "html"
    INLINE_CODE = "inline_code"
    LINK_OR_IMAGE = "link_or_image"
    LIST = "list"
    NESTED_HEADING = "nested_heading"
    TABLE = "table"


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
    return document


def _compilation_document(
    *,
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    content_hash: str,
    provenance: CompilationProvenance,
    warnings: tuple[CompilationWarning, ...],
) -> dict[str, object]:
    return {
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


def _document_without_digest(document: ParsedDocument) -> dict[str, object]:
    return _compilation_document(
        canonical_text=document.canonical_text,
        sections=document.sections,
        content_hash=document.content_hash,
        provenance=document.provenance,
        warnings=document.warnings,
    )


def _compilation_digest(
    *,
    canonical_text: str,
    sections: tuple[ParsedSection, ...],
    content_hash: str,
    provenance: CompilationProvenance,
    warnings: tuple[CompilationWarning, ...],
) -> str:
    document = _compilation_document(
        canonical_text=canonical_text,
        sections=sections,
        content_hash=content_hash,
        provenance=provenance,
        warnings=warnings,
    )
    return sha256(
        _COMPILATION_DIGEST_DOMAIN + rfc8785.dumps(cast(Any, document))
    ).hexdigest()


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
    if not lines[0].startswith("# ") or not lines[0][2:]:
        raise ValueError("canonical Markdown must contain a level-one heading")
    heading_line = lines[0]
    paragraph_line = lines[2]
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
