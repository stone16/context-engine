"""Raw transforms for approved RAGFlow DOCX and PDF-outline regions."""

from __future__ import annotations

import zipfile
from hashlib import sha256
from io import BytesIO
from typing import Final

from pypdf import PdfReader

from engine.supply.documents import (
    DOCX_COMPILER_V1,
    DOCX_CONFIG_V1,
    MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS,
    MAX_FORMAT_DOCUMENT_UNITS,
    MAX_FORMAT_TABLE_CELLS,
    PDF_OUTLINE_COMPILER_V1,
    PDF_TEXT_OUTLINE_V1,
    CompilationProfileRef,
    DocumentCompilationFailure,
    DocumentCompilationFailureCode,
    DocumentStructuralKind,
    DocxXmlLocator,
    PdfRegionLocator,
    StructuralUnit,
)
from engine.supply.markdown import ParsedDocument
from third_party.ragflow.deepdoc.parser.docx_parser import RAGFlowDocxParser
from third_party.ragflow.deepdoc.parser.utils import extract_pdf_outlines

_SUPPORTED_PROFILES: Final = {
    DOCX_CONFIG_V1: CompilationProfileRef(DOCX_COMPILER_V1, DOCX_CONFIG_V1),
    PDF_TEXT_OUTLINE_V1: CompilationProfileRef(
        PDF_OUTLINE_COMPILER_V1,
        PDF_TEXT_OUTLINE_V1,
    ),
}
MAX_DOCX_PACKAGE_MEMBERS: Final = 4_096
MAX_DOCX_UNCOMPRESSED_BYTES: Final = 128 * 1024 * 1024
MAX_PDF_PAGES: Final = 2_000
_DOCX_PART_URI: Final = "/word/document.xml"
_PDF_OUTLINE_EXTRACTION_METHOD: Final = "pypdf-outline-v1"


def _failure(code: DocumentCompilationFailureCode) -> DocumentCompilationFailure:
    return DocumentCompilationFailure(code)


def profile_for_ref(profile_ref: object) -> CompilationProfileRef | None:
    """Select a closed profile without touching artifact bytes."""

    if type(profile_ref) is not str:
        return None
    return _SUPPORTED_PROFILES.get(profile_ref)


def _docx_kind(style_name: str | None) -> tuple[DocumentStructuralKind, int | None]:
    if style_name is not None:
        normalized = style_name.casefold().replace(" ", "")
        if normalized.startswith("heading") and normalized[7:].isdigit():
            level = int(normalized[7:])
            if 1 <= level <= 6:
                return DocumentStructuralKind.HEADING, level
        if normalized.startswith("list"):
            return DocumentStructuralKind.LIST, None
    return DocumentStructuralKind.PARAGRAPH, None


def _valid_docx_package(source: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(source)) as archive:
            entries = archive.infolist()
            return (
                len(entries) <= MAX_DOCX_PACKAGE_MEMBERS
                and "word/document.xml" in archive.namelist()
                and all(not entry.flag_bits & 1 for entry in entries)
                and sum(entry.file_size for entry in entries)
                <= MAX_DOCX_UNCOMPRESSED_BYTES
            )
    except (OSError, zipfile.BadZipFile):
        return False


def _compile_docx(
    source: bytes,
    profile: CompilationProfileRef,
) -> ParsedDocument[CompilationProfileRef] | DocumentCompilationFailure:
    if not _valid_docx_package(source):
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    try:
        blocks = RAGFlowDocxParser()(source)
    except Exception:
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    if not blocks:
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    if len(blocks) > MAX_FORMAT_DOCUMENT_UNITS:
        return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
    if sum(len(block.text) for block in blocks) > MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS:
        return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
    if sum(len(row) for block in blocks for row in block.table_cells) > (
        MAX_FORMAT_TABLE_CELLS
    ):
        return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
    if any(block.has_figure for block in blocks):
        return _failure(DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED)
    artifact_digest = sha256(source).hexdigest()
    headings: list[tuple[int, str]] = []
    units: list[StructuralUnit] = []
    for unit_ordinal, block in enumerate(blocks):
        if block.kind == "table":
            kind = DocumentStructuralKind.TABLE
            level = None
        else:
            kind, level = _docx_kind(block.style_name)
        if kind is DocumentStructuralKind.HEADING:
            assert level is not None
            headings[:] = [heading for heading in headings if heading[0] < level]
            ancestry = tuple(text for _, text in headings)
            headings.append((level, block.text))
        else:
            ancestry = tuple(text for _, text in headings)
        units.append(
            StructuralUnit(
                ordinal=unit_ordinal,
                kind=kind,
                text=block.text,
                locators=(
                    DocxXmlLocator(
                        artifact_digest=artifact_digest,
                        part_uri=_DOCX_PART_URI,
                        block_ordinal=block.block_ordinal,
                        xml_digest=sha256(block.xml).hexdigest(),
                    ),
                ),
                heading_level=level,
                heading_ancestry=ancestry,
                table_cells=block.table_cells,
            )
        )
    return ParsedDocument.format_neutral(
        artifact_digest=artifact_digest,
        profile=profile,
        units=tuple(units),
    )


def _compile_pdf_outline(
    source: bytes,
    profile: CompilationProfileRef,
) -> ParsedDocument[CompilationProfileRef] | DocumentCompilationFailure:
    try:
        if len(PdfReader(BytesIO(source)).pages) > MAX_PDF_PAGES:
            return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
        outlines = extract_pdf_outlines(source)
    except Exception:
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    if not outlines:
        return _failure(DocumentCompilationFailureCode.OUTLINE_UNAVAILABLE)
    if len(outlines) > MAX_FORMAT_DOCUMENT_UNITS:
        return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
    if sum(len(outline.title) for outline in outlines) > (
        MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS
    ):
        return _failure(DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED)
    artifact_digest = sha256(source).hexdigest()
    ancestry: list[str] = []
    units: list[StructuralUnit] = []
    for ordinal, outline in enumerate(outlines):
        ancestry[:] = ancestry[: outline.depth]
        units.append(
            StructuralUnit(
                ordinal=ordinal,
                kind=DocumentStructuralKind.HEADING,
                text=outline.title,
                locators=(
                    PdfRegionLocator(
                        artifact_digest=artifact_digest,
                        page_number=outline.page_number,
                        bbox_points=outline.page_bbox_points,
                        page_render_digest=outline.page_render_digest,
                        extraction_method=_PDF_OUTLINE_EXTRACTION_METHOD,
                    ),
                ),
                heading_level=outline.depth + 1,
                heading_ancestry=tuple(ancestry),
            )
        )
        ancestry.append(outline.title)
    return ParsedDocument.format_neutral(
        artifact_digest=artifact_digest,
        profile=profile,
        units=tuple(units),
    )


def compile_document_bytes(
    source: bytes,
    profile: CompilationProfileRef,
) -> ParsedDocument[CompilationProfileRef] | DocumentCompilationFailure:
    """Compile bytes selected by the owned runner; no raw exception escapes."""

    try:
        if type(source) is not bytes or type(profile) is not CompilationProfileRef:
            raise TypeError("document compiler requires nominal inputs")
        if profile.profile_ref == DOCX_CONFIG_V1:
            return _compile_docx(source, profile)
        if profile.profile_ref == PDF_TEXT_OUTLINE_V1:
            return _compile_pdf_outline(source, profile)
    except Exception:
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    return _failure(DocumentCompilationFailureCode.UNKNOWN_PROFILE)


__all__: list[str] = []
