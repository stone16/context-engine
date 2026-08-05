"""Format-neutral ParsedDocument value types admitted by ADR-0094."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Final, cast

import rfc8785

DOCX_CONFIG_V1: Final = "docx-config-v1"
PDF_TEXT_OUTLINE_V1: Final = "pdf-text-outline-v1"
DOCX_COMPILER_V1: Final = "context-engine-docx-v1"
PDF_OUTLINE_COMPILER_V1: Final = "context-engine-pdf-outline-v1"
DOCX_PART_URI: Final = "/word/document.xml"
PDF_OUTLINE_EXTRACTION_METHOD: Final = "pypdf-outline-v1"
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CONTENT_IDENTITY_DOMAIN = b"context-engine.parsed-document-content.v1\x00"
_COMPILATION_IDENTITY_DOMAIN = b"context-engine.parsed-document-compilation.v1\x00"
MAX_FORMAT_DOCUMENT_UNITS = 10_000
MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS = 4_000_000
MAX_FORMAT_UNIT_TEXT_CHARACTERS = 1_000_000
MAX_FORMAT_TABLE_CELLS = 100_000
MAX_DOCX_PART_URI_CHARACTERS = 255
MAX_PDF_PAGE_NUMBER = 2_000
MAX_PDF_PAGE_DIMENSION_POINTS = 20_000.0
MAX_PDF_PAGE_PIXEL_AREA = 40_000_000.0


def _require_digest(field: str, value: object) -> str:
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _require_token(field: str, value: object) -> str:
    if type(value) is not str or _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a bounded opaque token")
    return value


@dataclass(frozen=True, slots=True)
class CompilationProfileRef:
    """Exact format compiler and immutable profile identity."""

    compiler_ref: str
    profile_ref: str

    def __post_init__(self) -> None:
        _require_token("compiler ref", self.compiler_ref)
        _require_token("profile ref", self.profile_ref)

    @property
    def compiler_version(self) -> str:
        return self.compiler_ref

    @property
    def config_version(self) -> str:
        return self.profile_ref

    @property
    def is_structural_v2(self) -> bool:
        return False

    @property
    def is_rich_v3(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class TextByteSpan:
    """Nominal end-exclusive byte locator over canonical source text."""

    source_identity_digest: str
    start: int
    end: int

    def __post_init__(self) -> None:
        _require_digest("source identity digest", self.source_identity_digest)
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("text byte span must be a nonempty ordered range")


@dataclass(frozen=True, slots=True)
class DocxXmlLocator:
    """Nominal locator for one source-ordered OOXML document block."""

    artifact_digest: str
    part_uri: str
    block_ordinal: int
    xml_digest: str

    def __post_init__(self) -> None:
        _require_digest("artifact digest", self.artifact_digest)
        _require_digest("XML digest", self.xml_digest)
        if (
            type(self.part_uri) is not str
            or not self.part_uri.startswith("/")
            or len(self.part_uri) > MAX_DOCX_PART_URI_CHARACTERS
            or self.part_uri != self.part_uri.strip()
            or ".." in self.part_uri.split("/")
        ):
            raise ValueError("DOCX part URI must be an absolute bounded package path")
        if type(self.block_ordinal) is not int or self.block_ordinal < 0:
            raise ValueError("DOCX block ordinal must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class PdfRegionLocator:
    """Nominal one-based PDF page-region locator in points."""

    artifact_digest: str
    page_number: int
    bbox_points: tuple[float, float, float, float]
    page_render_digest: str
    extraction_method: str

    def __post_init__(self) -> None:
        _require_digest("artifact digest", self.artifact_digest)
        _require_digest("page render digest", self.page_render_digest)
        _require_token("PDF extraction method", self.extraction_method)
        if (
            type(self.page_number) is not int
            or not 1 <= self.page_number <= MAX_PDF_PAGE_NUMBER
        ):
            raise ValueError("PDF page number must be within the server hard bound")
        if type(self.bbox_points) is not tuple or len(self.bbox_points) != 4:
            raise TypeError("PDF bbox must contain four exact float values")
        if any(
            type(value) is not float or not math.isfinite(value)
            for value in self.bbox_points
        ):
            raise ValueError("PDF bbox values must be finite floats")
        x0, y0, x1, y1 = self.bbox_points
        if (
            min(x0, y0) < 0
            or x1 <= x0
            or y1 <= y0
            or x1 > MAX_PDF_PAGE_DIMENSION_POINTS
            or y1 > MAX_PDF_PAGE_DIMENSION_POINTS
        ):
            raise ValueError("PDF bbox must be a bounded positive ordered region")


type SourceLocator = TextByteSpan | DocxXmlLocator | PdfRegionLocator


class DocumentStructuralKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FENCED_CODE = "fenced_code"
    FIGURE = "figure"


@dataclass(frozen=True, slots=True)
class StructuralUnit:
    """One source-ordered structural unit and therefore one Fragment input."""

    ordinal: int
    kind: DocumentStructuralKind
    text: str
    locators: tuple[SourceLocator, ...]
    heading_level: int | None = None
    heading_ancestry: tuple[str, ...] = ()
    table_cells: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("structural unit ordinal must be nonnegative")
        if type(self.kind) is not DocumentStructuralKind:
            raise TypeError("structural unit kind must be nominal")
        if (
            type(self.text) is not str
            or not self.text
            or self.text.isspace()
            or len(self.text) > MAX_FORMAT_UNIT_TEXT_CHARACTERS
        ):
            raise ValueError("structural unit text must be exact and nonblank")
        if type(self.locators) is not tuple or not self.locators:
            raise ValueError("structural unit requires at least one typed locator")
        if any(
            type(locator) not in {TextByteSpan, DocxXmlLocator, PdfRegionLocator}
            for locator in self.locators
        ):
            raise TypeError(
                "structural unit locators must use the closed nominal union"
            )
        if self.kind is DocumentStructuralKind.HEADING:
            if type(self.heading_level) is not int or not 1 <= self.heading_level <= 6:
                raise ValueError("heading unit level must be between one and six")
        elif self.heading_level is not None:
            raise ValueError("non-heading unit cannot carry a heading level")
        if type(self.heading_ancestry) is not tuple or any(
            type(value) is not str or not value or value.isspace()
            for value in self.heading_ancestry
        ):
            raise ValueError("heading ancestry must contain exact nonblank text")
        if len(self.heading_ancestry) > 6:
            raise ValueError("heading ancestry exceeds the closed depth bound")
        if self.kind is DocumentStructuralKind.TABLE:
            if (
                type(self.table_cells) is not tuple
                or not self.table_cells
                or any(type(row) is not tuple or not row for row in self.table_cells)
                or any(
                    type(cell) is not str
                    for row in self.table_cells
                    for cell in row
                )
            ):
                raise ValueError("table unit requires typed rows and cells")
            if sum(len(row) for row in self.table_cells) > MAX_FORMAT_TABLE_CELLS:
                raise ValueError("table unit exceeds the cell hard bound")
        elif self.table_cells:
            raise ValueError("only table units carry table cells")


class DocumentCompilationFailureCode(StrEnum):
    UNKNOWN_PROFILE = "unknown_profile"
    INVALID_ARTIFACT = "invalid_artifact"
    ARTIFACT_BOUND_EXCEEDED = "artifact_bound_exceeded"
    DOCUMENT_BOUND_EXCEEDED = "document_bound_exceeded"
    FIGURE_NOT_SUPPORTED = "figure_not_supported"
    OUTLINE_UNAVAILABLE = "outline_unavailable"
    RUNNER_UNAVAILABLE = "runner_unavailable"


@dataclass(frozen=True, slots=True)
class DocumentCompilationFailure:
    """Closed all-or-nothing refusal without partial content."""

    code: DocumentCompilationFailureCode

    def __post_init__(self) -> None:
        if type(self.code) is not DocumentCompilationFailureCode:
            raise TypeError("document compilation failure code must be nominal")


def expected_format_profile(profile: CompilationProfileRef) -> type[SourceLocator]:
    """Validate and return the exact locator family for an admitted profile."""

    if type(profile) is not CompilationProfileRef:
        raise TypeError("parsed document profile must be nominal")
    identity = (profile.compiler_ref, profile.profile_ref)
    if identity == (DOCX_COMPILER_V1, DOCX_CONFIG_V1):
        return DocxXmlLocator
    if identity == (PDF_OUTLINE_COMPILER_V1, PDF_TEXT_OUTLINE_V1):
        return PdfRegionLocator
    raise ValueError("parsed document compiler/profile identity is not supported")


def _locator_document(locator: SourceLocator) -> dict[str, object]:
    if type(locator) is TextByteSpan:
        return {
            "kind": "text_byte_span",
            "sourceIdentityDigest": locator.source_identity_digest,
            "start": locator.start,
            "end": locator.end,
        }
    if type(locator) is DocxXmlLocator:
        return {
            "kind": "docx_xml",
            "artifactDigest": locator.artifact_digest,
            "partUri": locator.part_uri,
            "blockOrdinal": locator.block_ordinal,
            "xmlDigest": locator.xml_digest,
        }
    assert type(locator) is PdfRegionLocator
    return {
        "kind": "pdf_region",
        "artifactDigest": locator.artifact_digest,
        "pageNumber": locator.page_number,
        "bboxPoints": list(locator.bbox_points),
        "pageRenderDigest": locator.page_render_digest,
        "extractionMethod": locator.extraction_method,
    }


def _unit_content_document(unit: StructuralUnit) -> dict[str, object]:
    document: dict[str, object] = {
        "kind": unit.kind.value,
        "text": unit.text,
        "headingAncestry": list(unit.heading_ancestry),
    }
    if unit.heading_level is not None:
        document["headingLevel"] = unit.heading_level
    if unit.table_cells:
        document["tableCells"] = [list(row) for row in unit.table_cells]
    return document


def _unit_compilation_document(unit: StructuralUnit) -> dict[str, object]:
    return {
        "ordinal": unit.ordinal,
        **_unit_content_document(unit),
        "locators": [_locator_document(locator) for locator in unit.locators],
    }


def _content_document(units: tuple[StructuralUnit, ...]) -> dict[str, object]:
    # Artifact and source locators are intentionally excluded: semantic content
    # identity must survive byte-distinct containers with the same parsed content.
    return {"units": [_unit_content_document(unit) for unit in units]}


def format_compilation_document(
    *,
    artifact_digest: str,
    profile: CompilationProfileRef,
    content_hash: str,
    units: tuple[StructuralUnit, ...],
) -> dict[str, object]:
    return {
        "artifactDigest": artifact_digest,
        "profile": {
            "compilerRef": profile.compiler_ref,
            "profileRef": profile.profile_ref,
        },
        "contentHash": content_hash,
        "units": [_unit_compilation_document(unit) for unit in units],
    }


def format_content_hash(units: tuple[StructuralUnit, ...]) -> str:
    return sha256(
        _CONTENT_IDENTITY_DOMAIN + rfc8785.dumps(cast(Any, _content_document(units)))
    ).hexdigest()


def format_compilation_digest(
    *,
    artifact_digest: str,
    profile: CompilationProfileRef,
    content_hash: str,
    units: tuple[StructuralUnit, ...],
) -> str:
    value = format_compilation_document(
        artifact_digest=artifact_digest,
        profile=profile,
        content_hash=content_hash,
        units=units,
    )
    return sha256(
        _COMPILATION_IDENTITY_DOMAIN + rfc8785.dumps(cast(Any, value))
    ).hexdigest()


def validate_format_document(
    *,
    artifact_digest: str,
    content_hash: str,
    compilation_digest: str,
    profile: CompilationProfileRef,
    units: tuple[StructuralUnit, ...],
) -> None:
    _require_digest("artifact digest", artifact_digest)
    _require_digest("content hash", content_hash)
    _require_digest("compilation digest", compilation_digest)
    locator_type = expected_format_profile(profile)
    if type(units) is not tuple or not units:
        raise ValueError("parsed document requires structural units")
    if len(units) > MAX_FORMAT_DOCUMENT_UNITS:
        raise ValueError("parsed document exceeds the structural-unit hard bound")
    if any(type(unit) is not StructuralUnit for unit in units):
        raise TypeError("parsed document units must be nominal")
    if tuple(unit.ordinal for unit in units) != tuple(range(len(units))):
        raise ValueError("parsed document units must be source ordered and contiguous")
    if (
        sum(
            len(unit.text) + sum(len(heading) for heading in unit.heading_ancestry)
            for unit in units
        )
        > MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS
    ):
        raise ValueError("parsed document exceeds the text hard bound")
    locators = tuple(locator for unit in units for locator in unit.locators)
    if any(type(locator) is not locator_type for locator in locators):
        raise ValueError("parsed document profile and locator family must match")
    if locator_type is DocxXmlLocator:
        docx_locators = cast(tuple[DocxXmlLocator, ...], locators)
        if any(
            locator.artifact_digest != artifact_digest for locator in docx_locators
        ):
            raise ValueError("every source locator must bind the document artifact")
        if any(locator.part_uri != DOCX_PART_URI for locator in docx_locators):
            raise ValueError("DOCX profile locators must bind word/document.xml")
        block_ordinals = tuple(locator.block_ordinal for locator in docx_locators)
        if block_ordinals != tuple(sorted(block_ordinals)) or len(
            block_ordinals
        ) != len(set(block_ordinals)):
            raise ValueError("DOCX block locators must preserve unique source order")
    else:
        pdf_locators = cast(tuple[PdfRegionLocator, ...], locators)
        if any(
            locator.artifact_digest != artifact_digest for locator in pdf_locators
        ):
            raise ValueError("every source locator must bind the document artifact")
        if any(
            locator.extraction_method != PDF_OUTLINE_EXTRACTION_METHOD
            for locator in pdf_locators
        ):
            raise ValueError("PDF outline profile extraction method must be exact")
    if content_hash != format_content_hash(units):
        raise ValueError("content hash must match canonical semantic units")
    if compilation_digest != format_compilation_digest(
        artifact_digest=artifact_digest,
        profile=profile,
        content_hash=content_hash,
        units=units,
    ):
        raise ValueError("compilation digest must match the complete provenance chain")


def canonicalize_format_document(
    *,
    artifact_digest: str,
    profile: CompilationProfileRef,
    content_hash: str,
    compilation_digest: str,
    units: tuple[StructuralUnit, ...],
) -> bytes:
    value = format_compilation_document(
        artifact_digest=artifact_digest,
        profile=profile,
        content_hash=content_hash,
        units=units,
    )
    value["compilationDigest"] = compilation_digest
    return rfc8785.dumps(cast(Any, value))


def _locator_from_document(value: object) -> SourceLocator:
    if type(value) is not dict:
        raise ValueError("source locator must be an object")
    document = cast(dict[str, object], value)
    kind = document.get("kind")
    if kind == "text_byte_span":
        return TextByteSpan(
            source_identity_digest=cast(str, document["sourceIdentityDigest"]),
            start=cast(int, document["start"]),
            end=cast(int, document["end"]),
        )
    if kind == "docx_xml":
        return DocxXmlLocator(
            artifact_digest=cast(str, document["artifactDigest"]),
            part_uri=cast(str, document["partUri"]),
            block_ordinal=cast(int, document["blockOrdinal"]),
            xml_digest=cast(str, document["xmlDigest"]),
        )
    if kind == "pdf_region":
        raw_bbox = cast(list[object], document["bboxPoints"])
        if len(raw_bbox) != 4 or any(
            type(item) not in {int, float} for item in raw_bbox
        ):
            raise ValueError("PDF bbox must contain four numeric values")
        return PdfRegionLocator(
            artifact_digest=cast(str, document["artifactDigest"]),
            page_number=cast(int, document["pageNumber"]),
            bbox_points=(
                float(cast(int | float, raw_bbox[0])),
                float(cast(int | float, raw_bbox[1])),
                float(cast(int | float, raw_bbox[2])),
                float(cast(int | float, raw_bbox[3])),
            ),
            page_render_digest=cast(str, document["pageRenderDigest"]),
            extraction_method=cast(str, document["extractionMethod"]),
        )
    raise ValueError("source locator kind is not supported")


def deserialize_format_document(
    payload: bytes,
) -> tuple[str, str, str, CompilationProfileRef, tuple[StructuralUnit, ...]]:
    """Parse canonical runner bytes for construction by ParsedDocument."""

    if type(payload) is not bytes:
        raise TypeError("parsed document payload must be exact bytes")
    raw = json.loads(payload)
    if type(raw) is not dict:
        raise ValueError("parsed document payload must contain one object")
    document = cast(dict[str, object], raw)
    if set(document) != {
        "artifactDigest",
        "compilationDigest",
        "contentHash",
        "profile",
        "units",
    }:
        raise ValueError("format document payload has unexpected fields")
    profile_value = document["profile"]
    if type(profile_value) is not dict:
        raise ValueError("parsed document profile must be an object")
    profile_document = cast(dict[str, object], profile_value)
    if set(profile_document) != {"compilerRef", "profileRef"}:
        raise ValueError("parsed document profile has unexpected fields")
    units_value = document["units"]
    if type(units_value) is not list or any(
        type(value) is not dict for value in units_value
    ):
        raise ValueError("parsed document units must be objects")
    required_unit_fields = {"ordinal", "kind", "text", "locators", "headingAncestry"}
    optional_unit_fields = {"headingLevel", "tableCells"}
    unit_documents = tuple(cast(dict[str, object], value) for value in units_value)
    if any(
        not required_unit_fields <= set(unit)
        or not set(unit) <= required_unit_fields | optional_unit_fields
        for unit in unit_documents
    ):
        raise ValueError("parsed document unit has unexpected fields")
    units = tuple(
        StructuralUnit(
            ordinal=cast(int, unit["ordinal"]),
            kind=DocumentStructuralKind(cast(str, unit["kind"])),
            text=cast(str, unit["text"]),
            locators=tuple(
                _locator_from_document(locator)
                for locator in cast(list[object], unit["locators"])
            ),
            heading_level=cast(int | None, unit.get("headingLevel")),
            heading_ancestry=tuple(cast(list[str], unit["headingAncestry"])),
            table_cells=tuple(
                tuple(row) for row in cast(list[list[str]], unit.get("tableCells", []))
            ),
        )
        for unit in unit_documents
    )
    return (
        cast(str, document["artifactDigest"]),
        cast(str, document["contentHash"]),
        cast(str, document["compilationDigest"]),
        CompilationProfileRef(
            compiler_ref=cast(str, profile_document["compilerRef"]),
            profile_ref=cast(str, profile_document["profileRef"]),
        ),
        units,
    )
