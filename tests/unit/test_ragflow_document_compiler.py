from __future__ import annotations

import ast
import base64
import io
import json
import subprocess
import sys
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from xml.sax.saxutils import escape

import pytest
import rfc8785
from docx import Document
from docx.document import Document as DocumentType
from docx.opc.constants import CONTENT_TYPE, RELATIONSHIP_TYPE
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from pypdf import PdfWriter
from pypdf.generic import Destination

from adapters.parsers import ragflow_documents as ragflow_document_adapter
from adapters.parsers.ragflow_documents import compile_document_bytes
from applications.document_compiler_runner import (
    ArtifactSource,
    BytesArtifactSource,
    _document_runner_environment,
    compile_in_local_document_runner,
)
from engine.supply import (
    DOCX_CONFIG_V1,
    MAX_FORMAT_DOCUMENT_UNITS,
    PDF_TEXT_OUTLINE_V1,
    CompilationProfileRef,
    DocumentCompilationFailure,
    DocumentCompilationFailureCode,
    DocumentStructuralKind,
    DocxXmlLocator,
    ParsedDocument,
    PdfRegionLocator,
    StructuralUnit,
    canonicalize_parsed_document,
    deserialize_parsed_document,
)
from eval._compiler_acceptance import acceptance_context
from third_party.ragflow.deepdoc.parser import utils as ragflow_pdf_utils
from third_party.ragflow.deepdoc.parser.utils import RawPdfOutline

REPOSITORY_ROOT = Path(__file__).parents[2]
type _DocumentOutcome = (
    ParsedDocument[CompilationProfileRef] | DocumentCompilationFailure
)


@dataclass
class _CountingArtifact(ArtifactSource):
    payload: bytes
    reads: int = 0

    def read(self) -> bytes:
        self.reads += 1
        return self.payload


def _docx_fixture(*, with_image: bool = False) -> bytes:
    document = Document()
    document.add_heading("Architecture", level=1)
    document.add_paragraph("First paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Key"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "parser"
    table.cell(1, 1).text = "registered"
    document.add_paragraph("Last paragraph.")
    if with_image:
        from docx.oxml import parse_xml
        from docx.oxml.ns import nsdecls

        run = document.add_paragraph().add_run()
        run._r.append(parse_xml(f"<pic:pic {nsdecls('pic')}></pic:pic>"))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _save_docx(document: DocumentType) -> bytes:
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _compile_docx_at_public_seams(source: bytes) -> tuple[
    _DocumentOutcome, _DocumentOutcome
]:
    return (
        compile_document_bytes(
            source,
            CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
        ),
        compile_in_local_document_runner(
            BytesArtifactSource(source),
            DOCX_CONFIG_V1,
            acceptance_context=acceptance_context(),
        ),
    )


def _docx_with_unsupported_body_container() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    content_control = OxmlElement("w:sdt")
    content = OxmlElement("w:sdtContent")
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Content control text must not disappear."
    run.append(text)
    paragraph.append(run)
    content.append(paragraph)
    content_control.append(content)
    document.element.body.insert(-1, content_control)
    return _save_docx(document)


def _docx_with_tracked_insertion() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.add_paragraph()
    insertion = OxmlElement("w:ins")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Tracked insertion must not disappear."
    run.append(text)
    insertion.append(run)
    paragraph._p.append(insertion)
    return _save_docx(document)


def _docx_with_wrapped_text(
    wrapper_tag: str,
    hidden_text: str,
    *,
    in_header: bool,
) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = (
        document.sections[0].header.paragraphs[0]
        if in_header
        else document.add_paragraph()
    )
    wrapper = OxmlElement(wrapper_tag)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = hidden_text
    run.append(text)
    wrapper.append(run)
    paragraph._p.append(wrapper)
    return _save_docx(document)


def _docx_with_wrapped_table_cell_text() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    wrapper = OxmlElement("w:dir")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Table-cell bidirectional text must not disappear."
    run.append(text)
    wrapper.append(run)
    paragraph._p.append(wrapper)
    return _save_docx(document)


def _docx_with_admitted_run_text() -> bytes:
    document = Document()
    run = document.add_paragraph().add_run("Before")
    run.add_tab()
    run.add_text("Middle")
    run.add_break()
    run._r.append(OxmlElement("w:noBreakHyphen"))
    run.add_text("After")
    return _save_docx(document)


def _docx_with_visible_token_outside_run(token_tag: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.add_paragraph()
    token = OxmlElement(token_tag)
    if token_tag == "w:t":
        token.text = "Silently omitted direct text."
    paragraph._p.append(token)
    return _save_docx(document)


def _docx_with_wrapped_footnote_text(
    wrapper_tag: str,
    hidden_text: str,
    *,
    content_type: str = CONTENT_TYPE.WML_FOOTNOTES,
    with_drawing: bool = False,
) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    if wrapper_tag not in {"w:fldSimple", "w:smartTag"}:
        raise ValueError("test fixture requires a supported wrapper tag")
    drawing_xml = "<w:drawing/>" if with_drawing else ""
    footnotes_xml = (
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        '<w:footnote w:id="1"><w:p>'
        f"<{wrapper_tag}><w:r><w:t>{escape(hidden_text)}</w:t></w:r>"
        f"</{wrapper_tag}>{drawing_xml}"
        "</w:p></w:footnote></w:footnotes>"
    ).encode()
    footnotes_part = Part(
        PackURI("/word/footnotes.xml"),
        content_type,
        footnotes_xml,
        document.part.package,
    )
    document.part.relate_to(footnotes_part, RELATIONSHIP_TYPE.FOOTNOTES)
    return _save_docx(document)


def _relabel_docx_part_as_binary(source: bytes, part_name: str) -> bytes:
    output = io.BytesIO()
    part_name_token = f'PartName="/{part_name}" ContentType="'.encode()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                content_type_start = member_bytes.index(part_name_token) + len(
                    part_name_token
                )
                content_type_end = member_bytes.index(b'"', content_type_start)
                member_bytes = (
                    member_bytes[:content_type_start]
                    + b"application/octet-stream"
                    + member_bytes[content_type_end:]
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_relabeled_header_xml(payload_kind: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.sections[0].header.paragraphs[0]
    if payload_kind in {"w:fldSimple", "w:smartTag"}:
        wrapper = OxmlElement(payload_kind)
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "Relabeled header text must not disappear."
        run.append(text)
        wrapper.append(run)
        paragraph._p.append(wrapper)
    elif payload_kind == "w:drawing":
        paragraph.add_run()._r.append(OxmlElement("w:drawing"))
    else:
        raise ValueError("unknown relabeled header payload")
    return _relabel_docx_part_as_binary(_save_docx(document), "word/header1.xml")


def _docx_with_relabeled_related_xml(part_kind: str, payload_kind: str) -> bytes:
    if part_kind == "header":
        return _docx_with_relabeled_header_xml(payload_kind)
    if part_kind != "footnotes":
        raise ValueError("unknown relabeled related part")
    source = _docx_with_wrapped_footnote_text(
        payload_kind if payload_kind != "w:drawing" else "w:fldSimple",
        "Relabeled footnote text must not disappear.",
        with_drawing=payload_kind == "w:drawing",
    )
    return _relabel_docx_part_as_binary(source, "word/footnotes.xml")


def _docx_with_visible_header_text() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    document.sections[0].header.paragraphs[0].text = (
        "Visible header text must not disappear."
    )
    return _save_docx(document)


def _docx_with_visible_footnote_text() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    footnotes_xml = (
        b'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/'
        b'wordprocessingml/2006/main">'
        b'<w:footnote w:id="1"><w:p><w:r>'
        b"<w:t>Visible footnote text must not disappear.</w:t>"
        b"</w:r></w:p></w:footnote></w:footnotes>"
    )
    footnotes_part = Part(
        PackURI("/word/footnotes.xml"),
        CONTENT_TYPE.WML_FOOTNOTES,
        footnotes_xml,
        document.part.package,
    )
    document.part.relate_to(footnotes_part, RELATIONSHIP_TYPE.FOOTNOTES)
    return _save_docx(document)


def _docx_with_malformed_footnotes_xml(*, with_header_drawing: bool) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    if with_header_drawing:
        document.sections[0].header.paragraphs[0].add_run()._r.append(
            OxmlElement("w:drawing")
        )
    footnotes_part = Part(
        PackURI("/word/footnotes.xml"),
        "application/xml",
        b"<w:footnotes>",
        document.part.package,
    )
    document.part.relate_to(footnotes_part, RELATIONSHIP_TYPE.FOOTNOTES)
    return _save_docx(document)


def _docx_with_malformed_part_media_type() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    generic_part = Part(
        PackURI("/word/generic.xml"),
        "application/xml; charset",
        b"<generic/>",
        document.part.package,
    )
    document.part.relate_to(generic_part, RELATIONSHIP_TYPE.CUSTOM_XML)
    return _save_docx(document)


def _docx_with_binary_ole_part(*, content_type: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    binary_part = Part(
        PackURI("/word/embeddings/object1.bin"),
        content_type,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1ContextEngine OLE fixture",
        document.part.package,
    )
    document.part.relate_to(binary_part, RELATIONSHIP_TYPE.OLE_OBJECT)
    return _save_docx(document)


def _docx_with_unsupported_drawing(*, in_header: bool) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = (
        document.sections[0].header.paragraphs[0]
        if in_header
        else document.add_paragraph()
    )
    paragraph.add_run()._r.append(OxmlElement("w:drawing"))
    return _save_docx(document)


def _docx_with_nested_table() -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Outer cell"
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Nested cell must not disappear."
    return _save_docx(document)


def _docx_with_boundary_whitespace() -> bytes:
    document = Document()
    document.add_paragraph("  leading and trailing  ")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "  cell boundary  "
    return _save_docx(document)


def _docx_with_horizontally_merged_cells() -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged"
    return _save_docx(document)


def _docx_with_office_math() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.add_paragraph()
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = "Office Math must not disappear."
    run.append(text)
    math.append(run)
    paragraph._p.append(math)
    return _save_docx(document)


def _docx_with_misplaced_footnote_reference() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    document.add_paragraph()._p.append(OxmlElement("w:footnoteRef"))
    return _save_docx(document)


def _docx_with_property_subtree_payload(payload_kind: str) -> bytes:
    document = Document()
    paragraph = document.add_paragraph("Retained body text.")
    properties = paragraph._p.get_or_add_pPr()
    if payload_kind == "footnote-reference":
        properties.append(OxmlElement("w:footnoteRef"))
    elif payload_kind == "simple-field":
        properties.append(OxmlElement("w:fldSimple"))
    elif payload_kind == "character-data":
        properties.text = "Property character data must not disappear."
    else:
        raise ValueError("unknown property-subtree test payload")
    return _save_docx(document)


def _docx_with_misplaced_table_structure(node_tag: str) -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Represented cell"
    properties = table._tbl.tblPr
    if node_tag == "w:tr":
        row = OxmlElement("w:tr")
        properties.append(row)
        parent = row
    elif node_tag == "w:tc":
        parent = properties
    else:
        raise ValueError("unknown misplaced table test node")
    cell = OxmlElement("w:tc")
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Misplaced table content must not disappear."
    run.append(text)
    paragraph.append(run)
    cell.append(paragraph)
    parent.append(cell)
    return _save_docx(document)


def _docx_with_nonbody_office_math() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.sections[0].header.paragraphs[0]
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = "Header Office Math must not disappear."
    run.append(text)
    math.append(run)
    paragraph._p.append(math)
    return _save_docx(document)


def _docx_with_unadmitted_structural_character_data() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    paragraph = document.add_paragraph()._p
    paragraph.append(OxmlElement("w:pPr"))
    paragraph[0].tail = "Direct paragraph data must not disappear."
    return _save_docx(document)


def _docx_with_nested_payload_in_run_leaf() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    text = OxmlElement("w:t")
    text.text = "Represented text"
    text.append(OxmlElement("w:footnoteRef"))
    document.add_paragraph().add_run()._r.append(text)
    return _save_docx(document)


def _docx_with_document_sibling_office_math() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = "Document-level Office Math must not disappear."
    run.append(text)
    math.append(run)
    document.element.insert(0, math)
    return _save_docx(document)


def _docx_with_orphan_xml_members(*members: tuple[str, bytes]) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            target_archive.writestr(member, source_archive.read(member.filename))
        for member_name, member_bytes in members:
            target_archive.writestr(member_name, member_bytes)
    return output.getvalue()


def _docx_with_orphan_visible_text() -> bytes:
    return _docx_with_orphan_xml_members(
        (
            "word/orphan-visible.xml",
            b'<w:orphan xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"><w:p><w:r>'
            b"<w:t>Orphan text must not disappear.</w:t>"
            b"</w:r></w:p></w:orphan>",
        )
    )


def _docx_with_orphan_drawing_and_malformed_xml() -> bytes:
    return _docx_with_orphan_xml_members(
        (
            "word/orphan-drawing.xml",
            b'<w:orphan xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"><w:drawing/></w:orphan>',
        ),
        ("word/orphan-malformed.xml", b"<w:orphan>"),
    )


def _docx_with_orphan_malformed_xml() -> bytes:
    return _docx_with_orphan_xml_members(
        ("word/orphan-malformed.xml", b"<w:orphan>")
    )


def _docx_with_archive_name_collision(*, case_varied: bool) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        document_xml = archive.read("word/document.xml")
    duplicate_name = "word/DOCUMENT.XML" if case_varied else "word/document.xml"
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            target_archive.writestr(member, source_archive.read(member.filename))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            target_archive.writestr(duplicate_name, document_xml)
    return output.getvalue()


def _docx_with_manifest_key_collision(*, declaration_kind: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        manifest = archive.read("[Content_Types].xml")
    if declaration_kind == "default":
        declaration = b'<Default Extension="XML" ContentType="application/xml"/>'
    elif declaration_kind == "override":
        declaration = (
            b'<Override PartName="/WORD/DOCUMENT.XML" '
            b'ContentType="application/xml"/>'
        )
    else:
        raise ValueError("unknown manifest collision kind")
    replaced_manifest = manifest.replace(b"</Types>", declaration + b"</Types>")
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                member_bytes = replaced_manifest
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_malformed_content_type_manifest(
    manifest_kind: str, *, with_drawing: bool = False
) -> bytes:
    document = Document()
    paragraph = document.add_paragraph("Retained body text.")
    if with_drawing:
        paragraph.add_run()._r.append(OxmlElement("w:drawing"))
    source = _save_docx(document)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                if manifest_kind == "root-unknown-attribute":
                    member_bytes = member_bytes.replace(
                        b"<Types xmlns=",
                        b'<Types hostile="1" xmlns=',
                        1,
                    )
                elif manifest_kind == "root-character-data":
                    member_bytes = member_bytes.replace(b'">', b'">payload', 1)
                elif manifest_kind == "default-unknown-attribute":
                    member_bytes = member_bytes.replace(
                        b"<Default ",
                        b'<Default hostile="1" ',
                        1,
                    )
                elif manifest_kind == "override-unknown-attribute":
                    member_bytes = member_bytes.replace(
                        b"<Override ",
                        b'<Override hostile="1" ',
                        1,
                    )
                elif manifest_kind == "declaration-character-data":
                    member_bytes = member_bytes.replace(
                        b"/>",
                        b">payload</Default>",
                        1,
                    )
                elif manifest_kind == "nested-foreign-payload":
                    member_bytes = member_bytes.replace(
                        b"/>",
                        b'><hostile:payload xmlns:hostile="urn:context-engine:'
                        b'hostile">payload</hostile:payload></Default>',
                        1,
                    )
                else:
                    raise ValueError("unknown malformed manifest kind")
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_unused_malformed_content_type_declaration(
    declaration_kind: str, *, with_drawing: bool = False
) -> bytes:
    document = Document()
    paragraph = document.add_paragraph("Retained body text.")
    if with_drawing:
        paragraph.add_run()._r.append(OxmlElement("w:drawing"))
    if declaration_kind == "media-type":
        declaration = (
            b'<Default Extension="unused" ContentType="application/xml; charset"/>'
        )
    elif declaration_kind == "extension":
        declaration = (
            b'<Default Extension="unused extension" ContentType="application/xml"/>'
        )
    else:
        raise ValueError("unknown malformed declaration kind")
    source = _save_docx(document)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                member_bytes = member_bytes.replace(
                    b"</Types>", declaration + b"</Types>"
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_raw_visual_and_malformed_related_xml() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.").add_run()._r.append(
        OxmlElement("w:drawing")
    )
    source = _save_docx(document)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "word/styles.xml":
                member_bytes = b"<w:styles>"
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_unknown_related_xml() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    custom_part = Part(
        PackURI("/word/customData.xml"),
        "application/xml",
        b'<customData xmlns="urn:example:unknown">'
        b"Unknown related XML must not disappear."
        b"</customData>",
        document.part.package,
    )
    document.part.relate_to(custom_part, RELATIONSHIP_TYPE.CUSTOM_XML)
    return _save_docx(document)


def _docx_with_misordered_table_structure(level: str) -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Represented cell"
    if level == "table":
        properties = table._tbl.tblPr
        table._tbl.remove(properties)
        table._tbl.append(properties)
    elif level == "row":
        row = table.rows[0]._tr
        properties = OxmlElement("w:trPr")
        row.append(properties)
    elif level == "cell":
        cell = table.cell(0, 0)._tc
        properties = cell.tcPr
        cell.remove(properties)
        cell.append(properties)
    else:
        raise ValueError("unknown table-order test level")
    return _save_docx(document)


def _docx_with_duplicate_table_structure(structure_tag: str) -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Represented cell"
    if structure_tag == "w:tblGrid":
        table._tbl.insert(2, OxmlElement(structure_tag))
    elif structure_tag == "w:trPr":
        row = table.rows[0]._tr
        row.insert(0, OxmlElement(structure_tag))
        row.insert(1, OxmlElement(structure_tag))
    elif structure_tag == "w:tcPr":
        cell = table.cell(0, 0)._tc
        cell.insert(1, OxmlElement(structure_tag))
    else:
        raise ValueError("unknown duplicate table structure")
    return _save_docx(document)


def _docx_with_legacy_horizontal_merge() -> bytes:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Merged semantics"
    table.cell(0, 0)._tc.get_or_add_tcPr().append(OxmlElement("w:hMerge"))
    return _save_docx(document)


def _docx_with_unsafe_manifest_part_name() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    output = io.BytesIO()
    declaration = (
        b'<Override PartName="/word/%2e%2e/unrepresented.xml" '
        b'ContentType="application/xml"/>'
    )
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                member_bytes = member_bytes.replace(
                    b"</Types>", declaration + b"</Types>"
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_unsafe_archive_member_name() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    output = io.BytesIO()
    declaration = (
        b'<Override PartName="/word/../unrepresented.bin" '
        b'ContentType="application/octet-stream"/>'
    )
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                member_bytes = member_bytes.replace(
                    b"</Types>", declaration + b"</Types>"
                )
            target_archive.writestr(member, member_bytes)
        target_archive.writestr("word/../unrepresented.bin", b"binary")
    return output.getvalue()


def _docx_with_orphan_binary_member() -> bytes:
    return _docx_with_orphan_xml_members(
        ("word/orphan.bin", b"unrepresented binary bytes")
    )


def _docx_without_root_document_relationship() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "_rels/.rels":
                member_bytes = (
                    b'<Relationships xmlns="http://schemas.openxmlformats.org/'
                    b'package/2006/relationships"/>'
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_hostile_root_document_relationship(relationship_kind: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    office_document_type = (
        b"http://schemas.openxmlformats.org/officeDocument/2006/"
        b"relationships/officeDocument"
    )
    expected_relationship = (
        b'<Relationship Id="rId1" Type="'
        + office_document_type
        + b'" Target="word/document.xml"/>'
    )
    if relationship_kind == "wrong-type":
        hostile_relationship = expected_relationship.replace(
            office_document_type,
            b"urn:context-engine:not-office-document",
        )
    elif relationship_kind == "missing-type":
        hostile_relationship = expected_relationship.replace(
            b' Type="' + office_document_type + b'"',
            b"",
        )
    elif relationship_kind == "missing-id":
        hostile_relationship = expected_relationship.replace(b' Id="rId1"', b"")
    elif relationship_kind == "duplicate-id":
        hostile_relationship = expected_relationship.replace(b'rId1', b'rId3')
    elif relationship_kind == "invalid-target-mode":
        hostile_relationship = expected_relationship.replace(
            b' Target="word/document.xml"',
            b' Target="word/document.xml" TargetMode="Neither"',
        )
    else:
        raise ValueError("unknown hostile relationship kind")

    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "_rels/.rels":
                assert expected_relationship in member_bytes
                member_bytes = member_bytes.replace(
                    expected_relationship,
                    hostile_relationship,
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_text_in_known_inert_member() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    payload = (
        b'<hostile:payload xmlns:hostile="urn:context-engine:hostile">'
        b"Known-inert payload text must not disappear."
        b"</hostile:payload>"
    )
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "word/styles.xml":
                assert b"</w:styles>" in member_bytes
                member_bytes = member_bytes.replace(
                    b"</w:styles>",
                    payload + b"</w:styles>",
                )
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_aliased_manifest_part_name(alias: str) -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    source = _save_docx(document)
    expected = b'PartName="/word/document.xml"'
    replacement = f'PartName="{alias}"'.encode()
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as source_archive,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_archive,
    ):
        for member in source_archive.infolist():
            member_bytes = source_archive.read(member.filename)
            if member.filename == "[Content_Types].xml":
                assert expected in member_bytes
                member_bytes = member_bytes.replace(expected, replacement)
            target_archive.writestr(member, member_bytes)
    return output.getvalue()


def _docx_with_unknown_main_document_sibling() -> bytes:
    document = Document()
    document.add_paragraph("Retained body text.")
    document.element.insert(0, OxmlElement("w:unknown"))
    return _save_docx(document)


def _docx_fixture_with_blank_source_block() -> bytes:
    document = Document()
    document.add_heading("Architecture", level=1)
    document.add_paragraph("")
    document.add_paragraph("After blank source block.")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_outline_fixture() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    root = writer.add_outline_item("Overview", 0)
    writer.add_outline_item("Details", 1, parent=root)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_outline_fixture_for_same_page(*, shifted: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if shifted:
        page.mediabox.lower_left = (-10, -10)
        page.mediabox.upper_right = (602, 782)
    writer.add_outline_item("First", 0)
    writer.add_outline_item("Second", 0)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_unknown_profile_refuses_before_artifact_bytes_are_opened() -> None:
    artifact = _CountingArtifact(b"must not be read")

    outcome = compile_in_local_document_runner(
        artifact,
        "pdf-layout-ocr-v1",
        acceptance_context=acceptance_context(),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.UNKNOWN_PROFILE
    assert artifact.reads == 0


def test_child_unknown_profile_returns_closed_failure() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "applications.document_compiler_runner",
            "--profile",
            "pdf-layout-ocr-v1",
        ],
        input=b"must not be parsed",
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert json.loads(completed.stdout) == {
        "outcome": "failure",
        "failure": {"code": "unknown_profile"},
    }


def test_child_enforces_artifact_bound_with_a_closed_refusal() -> None:
    from applications.document_compiler_runner import MAX_DOCUMENT_ARTIFACT_BYTES

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "applications.document_compiler_runner",
            "--profile",
            DOCX_CONFIG_V1,
        ],
        input=b"x" * (MAX_DOCUMENT_ARTIFACT_BYTES + 1),
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert json.loads(completed.stdout) == {
        "outcome": "failure",
        "failure": {"code": "artifact_bound_exceeded"},
    }


def test_owned_document_runner_has_no_network_database_or_model_imports() -> None:
    paths = (
        REPOSITORY_ROOT / "adapters/parsers/ragflow_documents.py",
        REPOSITORY_ROOT / "applications/document_compiler_runner.py",
        REPOSITORY_ROOT / "third_party/ragflow/deepdoc/parser/docx_parser.py",
        REPOSITORY_ROOT / "third_party/ragflow/deepdoc/parser/utils.py",
    )
    forbidden = {
        "common",
        "huggingface_hub",
        "httpx",
        "os",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "urllib",
    }
    for path in paths:
        imports: set[str] = set()
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None
                imports.add(node.module.partition(".")[0])
        assert imports.isdisjoint(forbidden), path


def test_docx_profile_preserves_ooxml_block_order_and_typed_locators() -> None:
    source = _docx_fixture()

    outcome = compile_document_bytes(
        source,
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    assert [unit.kind for unit in outcome.units] == [
        DocumentStructuralKind.HEADING,
        DocumentStructuralKind.PARAGRAPH,
        DocumentStructuralKind.TABLE,
        DocumentStructuralKind.PARAGRAPH,
    ]
    assert [unit.text for unit in outcome.units] == [
        "Architecture",
        "First paragraph.",
        "Key\tValue\nparser\tregistered",
        "Last paragraph.",
    ]
    assert all(
        type(locator) is DocxXmlLocator
        for unit in outcome.units
        for locator in unit.locators
    )
    docx_locators = tuple(unit.locators[0] for unit in outcome.units)
    assert all(type(locator) is DocxXmlLocator for locator in docx_locators)
    assert tuple(
        locator.block_ordinal
        for locator in docx_locators
        if type(locator) is DocxXmlLocator
    ) == (0, 1, 2, 3)
    assert outcome.provenance.config_version == DOCX_CONFIG_V1


def test_docx_image_is_an_honest_typed_refusal() -> None:
    outcome = compile_document_bytes(
        _docx_fixture(with_image=True),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


@pytest.mark.parametrize(
    "source_builder",
    (
        _docx_with_unsupported_body_container,
        _docx_with_tracked_insertion,
        _docx_with_nested_table,
    ),
    ids=("content-control", "tracked-insertion", "nested-table"),
)
def test_docx_refuses_source_content_it_cannot_preserve(
    source_builder: Callable[[], bytes],
) -> None:
    outcome = compile_document_bytes(
        source_builder(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    ("wrapper_tag", "hidden_text"),
    (
        ("w:fldSimple", "Simple field text must not disappear."),
        ("w:smartTag", "Smart tag text must not disappear."),
    ),
    ids=("simple-field", "smart-tag"),
)
@pytest.mark.parametrize("in_header", (False, True), ids=("body", "header"))
def test_docx_wrapped_text_refuses_at_parser_and_runner_seams(
    wrapper_tag: str,
    hidden_text: str,
    in_header: bool,
) -> None:
    source = _docx_with_wrapped_text(
        wrapper_tag,
        hidden_text,
        in_header=in_header,
    )
    outcomes = _compile_docx_at_public_seams(source)

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    ("wrapper_tag", "hidden_text"),
    (
        ("w:fldSimple", "Footnote field text must not disappear."),
        ("w:smartTag", "Footnote smart tag text must not disappear."),
    ),
    ids=("simple-field", "smart-tag"),
)
def test_docx_wrapped_footnote_text_refuses_at_parser_and_runner_seams(
    wrapper_tag: str,
    hidden_text: str,
) -> None:
    source = _docx_with_wrapped_footnote_text(wrapper_tag, hidden_text)
    outcomes = _compile_docx_at_public_seams(source)

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("part_kind", ("header", "footnotes"))
@pytest.mark.parametrize(
    "payload_kind",
    ("w:fldSimple", "w:smartTag", "w:drawing"),
    ids=("simple-field", "smart-tag", "drawing"),
)
def test_docx_relabeled_related_xml_cannot_bypass_package_scanning(
    part_kind: str,
    payload_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_relabeled_related_xml(part_kind, payload_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is (
            DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED
            if payload_kind == "w:drawing"
            else DocumentCompilationFailureCode.INVALID_ARTIFACT
        )


@pytest.mark.parametrize(
    ("wrapper_tag", "hidden_text"),
    (
        ("w:fldSimple", "Case-varied footnote field text must not disappear."),
        ("w:smartTag", "Case-varied footnote smart tag text must not disappear."),
    ),
    ids=("simple-field", "smart-tag"),
)
@pytest.mark.parametrize(
    "content_type",
    (
        "Application/XML",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+XmL",
        "application/xml; charset=UTF-8",
    ),
    ids=("uppercase-base-xml", "mixed-case-xml-suffix", "parameterized-xml"),
)
def test_docx_case_varied_xml_media_types_still_refuse_wrapped_footnotes(
    wrapper_tag: str,
    hidden_text: str,
    content_type: str,
) -> None:
    source = _docx_with_wrapped_footnote_text(
        wrapper_tag,
        hidden_text,
        content_type=content_type,
    )
    outcomes = _compile_docx_at_public_seams(source)

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "source_builder",
    (
        _docx_with_visible_header_text,
        _docx_with_visible_footnote_text,
        lambda: _docx_with_wrapped_text(
            "w:dir",
            "Bidirectional text must not disappear.",
            in_header=False,
        ),
        _docx_with_wrapped_table_cell_text,
    ),
    ids=("header", "footnote", "unknown-body-container", "table-cell-container"),
)
def test_docx_refuses_visible_text_it_cannot_represent_at_both_seams(
    source_builder: Callable[[], bytes],
) -> None:
    source = source_builder()
    outcomes = _compile_docx_at_public_seams(source)

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_preserves_admitted_run_text_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_admitted_run_text())

    for outcome in outcomes:
        assert type(outcome) is ParsedDocument
        assert outcome.units is not None
        assert [unit.text for unit in outcome.units] == ["Before\tMiddle\n-After"]


def test_docx_preserves_boundary_whitespace_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_boundary_whitespace())

    for outcome in outcomes:
        assert type(outcome) is ParsedDocument
        assert outcome.units is not None
        assert [unit.text for unit in outcome.units] == [
            "  leading and trailing  ",
            "  cell boundary  ",
        ]
        assert outcome.units[1].table_cells == (("  cell boundary  ",),)


def test_docx_refuses_horizontally_merged_cells_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_horizontally_merged_cells())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "source_builder",
    (
        _docx_with_office_math,
        _docx_with_misplaced_footnote_reference,
        _docx_with_orphan_visible_text,
        _docx_with_orphan_malformed_xml,
    ),
    ids=("office-math", "misplaced-control", "orphan-text", "orphan-malformed"),
)
def test_docx_closed_grammar_refuses_unrepresented_xml_at_both_seams(
    source_builder: Callable[[], bytes],
) -> None:
    outcomes = _compile_docx_at_public_seams(source_builder())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_orphan_drawing_preserves_visual_refusal_precedence_at_both_seams(
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_orphan_drawing_and_malformed_xml()
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


@pytest.mark.parametrize(
    "payload_kind",
    ("footnote-reference", "simple-field", "character-data"),
)
def test_docx_closed_grammar_refuses_property_subtree_payloads_at_both_seams(
    payload_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_property_subtree_payload(payload_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("node_tag", ("w:tr", "w:tc"), ids=("row", "cell"))
def test_docx_closed_grammar_refuses_misplaced_table_structure_at_both_seams(
    node_tag: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_misplaced_table_structure(node_tag)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_refuses_nonbody_office_math_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_nonbody_office_math())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "source_builder",
    (
        _docx_with_unadmitted_structural_character_data,
        _docx_with_nested_payload_in_run_leaf,
        _docx_with_document_sibling_office_math,
    ),
    ids=("structural-character-data", "nested-run-leaf", "document-sibling"),
)
def test_docx_closed_grammar_refuses_recursive_structure_bypasses_at_both_seams(
    source_builder: Callable[[], bytes],
) -> None:
    outcomes = _compile_docx_at_public_seams(source_builder())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("case_varied", (False, True), ids=("exact", "casefold"))
def test_docx_refuses_duplicate_archive_names_at_both_seams(
    case_varied: bool,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_archive_name_collision(case_varied=case_varied)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("declaration_kind", ("default", "override"))
def test_docx_refuses_casefolded_manifest_key_collisions_at_both_seams(
    declaration_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_manifest_key_collision(declaration_kind=declaration_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "manifest_kind",
    (
        "root-unknown-attribute",
        "root-character-data",
        "default-unknown-attribute",
        "override-unknown-attribute",
        "declaration-character-data",
        "nested-foreign-payload",
    ),
)
def test_docx_refuses_malformed_content_type_manifest_at_both_seams(
    manifest_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_malformed_content_type_manifest(manifest_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_manifest_failure_preserves_visual_precedence_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_malformed_content_type_manifest(
            "root-unknown-attribute",
            with_drawing=True,
        )
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


@pytest.mark.parametrize("declaration_kind", ("media-type", "extension"))
def test_docx_refuses_unused_malformed_content_type_declarations_at_both_seams(
    declaration_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_unused_malformed_content_type_declaration(declaration_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("declaration_kind", ("media-type", "extension"))
def test_docx_unused_manifest_failure_preserves_visual_precedence_at_both_seams(
    declaration_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_unused_malformed_content_type_declaration(
            declaration_kind,
            with_drawing=True,
        )
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


def test_docx_raw_inventory_preserves_visual_precedence_before_document_load(
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_raw_visual_and_malformed_related_xml()
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


def test_docx_refuses_unknown_related_xml_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_unknown_related_xml())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("level", ("table", "row", "cell"))
def test_docx_refuses_misordered_table_structure_at_both_seams(
    level: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_misordered_table_structure(level)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize("structure_tag", ("w:tblGrid", "w:trPr", "w:tcPr"))
def test_docx_refuses_duplicate_table_structure_at_both_seams(
    structure_tag: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_duplicate_table_structure(structure_tag)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_refuses_legacy_horizontal_merge_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_legacy_horizontal_merge())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "source_builder",
    (_docx_with_unsafe_manifest_part_name, _docx_with_unsafe_archive_member_name),
    ids=("manifest-part-name", "archive-member-name"),
)
def test_docx_refuses_unsafe_package_paths_at_both_seams(
    source_builder: Callable[[], bytes],
) -> None:
    outcomes = _compile_docx_at_public_seams(source_builder())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "source_builder",
    (
        _docx_with_orphan_binary_member,
        _docx_without_root_document_relationship,
        _docx_with_unknown_main_document_sibling,
    ),
    ids=("orphan-binary", "unrelated-main", "unknown-main-sibling"),
)
def test_docx_refuses_unrepresented_package_inventory_at_both_seams(
    source_builder: Callable[[], bytes],
) -> None:
    outcomes = _compile_docx_at_public_seams(source_builder())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "relationship_kind",
    (
        "wrong-type",
        "missing-type",
        "missing-id",
        "duplicate-id",
        "invalid-target-mode",
    ),
)
def test_docx_refuses_hostile_root_document_relationships_at_both_seams(
    relationship_kind: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_hostile_root_document_relationship(relationship_kind)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_refuses_text_in_known_inert_member_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_text_in_known_inert_member())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "part_name",
    (
        "/word/./document.xml",
        "/word//document.xml",
        "/word/document.xml?alias=1",
        "/word/document.xml#alias",
    ),
    ids=("dot-segment", "double-slash", "query", "fragment"),
)
def test_docx_refuses_aliased_manifest_part_names_at_both_seams(
    part_name: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_aliased_manifest_part_name(part_name)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    "token_tag",
    ("w:t", "w:tab", "w:ptab", "w:br", "w:cr", "w:noBreakHyphen"),
    ids=("text", "tab", "position-tab", "break", "carriage-return", "no-break-hyphen"),
)
def test_docx_refuses_visible_tokens_outside_runs_at_both_seams(
    token_tag: str,
) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_visible_token_outside_run(token_tag)
    )

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


@pytest.mark.parametrize(
    ("with_header_drawing", "expected_code"),
    (
        (True, DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED),
        (False, DocumentCompilationFailureCode.INVALID_ARTIFACT),
    ),
    ids=("visual-first", "malformed-only"),
)
def test_docx_malformed_generic_xml_preserves_visual_refusal_precedence(
    with_header_drawing: bool,
    expected_code: DocumentCompilationFailureCode,
) -> None:
    source = _docx_with_malformed_footnotes_xml(
        with_header_drawing=with_header_drawing
    )
    outcomes = _compile_docx_at_public_seams(source)

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is expected_code


def test_docx_refuses_malformed_package_part_media_type_at_both_seams() -> None:
    outcomes = _compile_docx_at_public_seams(_docx_with_malformed_part_media_type())

    for outcome in outcomes:
        assert type(outcome) is DocumentCompilationFailure
        assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_docx_generic_xml_part_preserves_visual_refusal_precedence() -> None:
    outcome = compile_document_bytes(
        _docx_with_wrapped_footnote_text(
            "w:fldSimple",
            "Footnote field text must not disappear.",
            with_drawing=True,
        ),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


@pytest.mark.parametrize(
    "content_type",
    ("application/octet-stream", 'Application/Octet-Stream; profile="xml-looking"'),
    ids=("bare", "parameterized"),
)
def test_docx_package_scan_does_not_parse_binary_parts(content_type: str) -> None:
    outcomes = _compile_docx_at_public_seams(
        _docx_with_binary_ole_part(content_type=content_type)
    )

    for outcome in outcomes:
        assert type(outcome) is ParsedDocument
        assert outcome.units is not None
        assert [unit.text for unit in outcome.units] == ["Retained body text."]


@pytest.mark.parametrize("in_header", (False, True))
def test_docx_refuses_unsupported_drawings_in_every_package_part(
    in_header: bool,
) -> None:
    outcome = compile_document_bytes(
        _docx_with_unsupported_drawing(in_header=in_header),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.FIGURE_NOT_SUPPORTED


def test_docx_locator_ordinal_is_the_ooxml_source_ordinal_not_output_index() -> None:
    outcome = compile_document_bytes(
        _docx_fixture_with_blank_source_block(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    locators = tuple(unit.locators[0] for unit in outcome.units)
    assert tuple(
        locator.block_ordinal
        for locator in locators
        if type(locator) is DocxXmlLocator
    ) == (0, 2)


def test_pdf_outline_profile_emits_source_order_and_pdf_region_locators() -> None:
    outcome = compile_document_bytes(
        _pdf_outline_fixture(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    assert [unit.text for unit in outcome.units] == ["Overview", "Details"]
    assert [unit.heading_level for unit in outcome.units] == [1, 2]
    assert all(unit.kind is DocumentStructuralKind.HEADING for unit in outcome.units)
    assert all(
        type(unit.locators[0]) is PdfRegionLocator for unit in outcome.units
    )
    pdf_locators = tuple(unit.locators[0] for unit in outcome.units)
    assert [
        locator.page_number
        for locator in pdf_locators
        if type(locator) is PdfRegionLocator
    ] == [1, 2]


def test_pdf_outline_normalizes_shifted_media_box_coordinates() -> None:
    outcome = compile_document_bytes(
        _pdf_outline_fixture_for_same_page(shifted=True),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is ParsedDocument
    assert outcome.units is not None
    locator = outcome.units[0].locators[0]
    assert type(locator) is PdfRegionLocator
    assert locator.bbox_points == (0.0, 0.0, 612.0, 792.0)


def test_pdf_outline_bounds_page_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=20_001, height=20_001)
    writer.add_outline_item("Oversized", 0)
    output = io.BytesIO()
    writer.write(output)

    def reject_render(_page: object) -> str:
        raise AssertionError("oversized PDF page was rendered")

    monkeypatch.setattr(ragflow_pdf_utils, "_page_render_digest", reject_render)
    outcome = compile_document_bytes(
        output.getvalue(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED


def test_pdf_outline_rejects_non_finite_page_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _pdf_outline_fixture_for_same_page()
    actual_reader = cast(Any, ragflow_pdf_utils).PdfReader(io.BytesIO(source))

    class NonFinitePage:
        mediabox = (float("nan"), 0.0, 612.0, 792.0)

    class NonFiniteReader:
        outline = actual_reader.outline
        pages = (NonFinitePage(),)

        def get_destination_page_number(self, node: Destination) -> int:
            page_number = actual_reader.get_destination_page_number(node)
            assert type(page_number) is int
            return page_number

    def reject_render(_page: object) -> str:
        raise AssertionError("non-finite PDF page was rendered")

    monkeypatch.setattr(
        ragflow_pdf_utils, "PdfReader", lambda _source: NonFiniteReader()
    )
    monkeypatch.setattr(ragflow_pdf_utils, "_page_render_digest", reject_render)
    outcome = compile_document_bytes(
        source,
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED


def test_pdf_locator_constructor_enforces_pixel_area_bound() -> None:
    digest = "0" * 64

    with pytest.raises(ValueError, match="pixel-area hard bound"):
        PdfRegionLocator(
            artifact_digest=digest,
            page_number=1,
            bbox_points=(0.0, 0.0, 10_000.0, 5_000.0),
            page_render_digest=digest,
            extraction_method="pypdf-outline-v1",
        )


def test_pdf_outline_closes_document_and_renders_each_page_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfium_module = cast(Any, ragflow_pdf_utils).pdfium
    original_document = pdfium_module.PdfDocument
    render_calls = 0

    class TrackingDocument:
        def __init__(self, source: bytes) -> None:
            self._document = original_document(source)
            self.closed = False
            wrappers.append(self)

        def __getitem__(self, index: int) -> object:
            return self._document[index]

        def close(self) -> None:
            self.closed = True
            self._document.close()

    wrappers: list[TrackingDocument] = []

    def recording_digest(page: object) -> str:
        nonlocal render_calls
        render_calls += 1
        cast(Any, page).close()
        return "0" * 64

    monkeypatch.setattr(pdfium_module, "PdfDocument", TrackingDocument)
    monkeypatch.setattr(ragflow_pdf_utils, "_page_render_digest", recording_digest)
    outcome = compile_document_bytes(
        _pdf_outline_fixture_for_same_page(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is ParsedDocument
    assert render_calls == 1
    assert len(wrappers) == 1
    assert wrappers[0].closed is True


def test_pdf_without_outline_is_an_honest_typed_refusal() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)

    outcome = compile_document_bytes(
        output.getvalue(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.OUTLINE_UNAVAILABLE


def test_page_render_digest_is_identical_for_same_pixels_in_distinct_pdfs() -> None:
    first = _pdf_outline_fixture()
    second = first + b"\n% byte-distinct container\n"
    outcomes = tuple(
        compile_document_bytes(
            source,
            CompilationProfileRef(
                "context-engine-pdf-outline-v1",
                PDF_TEXT_OUTLINE_V1,
            ),
        )
        for source in (first, second)
    )

    assert all(type(outcome) is ParsedDocument for outcome in outcomes)
    documents = tuple(
        outcome for outcome in outcomes if type(outcome) is ParsedDocument
    )
    assert documents[0].artifact_digest != documents[1].artifact_digest
    assert documents[0].units is not None and documents[1].units is not None
    first_locator = documents[0].units[0].locators[0]
    second_locator = documents[1].units[0].locators[0]
    assert type(first_locator) is PdfRegionLocator
    assert type(second_locator) is PdfRegionLocator
    assert first_locator.page_render_digest == second_locator.page_render_digest
    assert documents[0].content_hash == documents[1].content_hash
    assert documents[0].compilation_digest != documents[1].compilation_digest


@pytest.mark.parametrize(
    ("profile_ref", "fixture"),
    (
        (DOCX_CONFIG_V1, _docx_fixture),
        (PDF_TEXT_OUTLINE_V1, _pdf_outline_fixture),
    ),
)
def test_profile_digest_is_identical_across_two_fresh_processes(
    profile_ref: str,
    fixture: object,
) -> None:
    assert callable(fixture)
    source = fixture()
    canonical_documents: list[bytes] = []
    for hash_seed, thread_count in (("17", "1"), ("941", "2")):
        environment = _document_runner_environment(
            hash_seed=hash_seed,
            thread_count=thread_count,
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "applications.document_compiler_runner",
                "--profile",
                profile_ref,
            ],
            input=source,
            capture_output=True,
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
            timeout=30,
        )
        envelope = json.loads(completed.stdout)
        assert envelope["outcome"] == "parsed"
        canonical_documents.append(
            base64.b64decode(envelope["document"], validate=True)
        )

    assert canonical_documents[0] == canonical_documents[1]
    first = deserialize_parsed_document(canonical_documents[0])
    second = deserialize_parsed_document(canonical_documents[1])
    assert first.compilation_digest == second.compilation_digest


def test_local_runner_uses_the_shared_deterministic_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_environment: dict[str, str] | None = None

    def recording_run(*args: object, **kwargs: object) -> SimpleNamespace:
        del args
        nonlocal observed_environment
        observed_environment = cast(dict[str, str], kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "outcome": "failure",
                    "failure": {
                        "code": DocumentCompilationFailureCode.INVALID_ARTIFACT.value
                    },
                }
            ).encode("utf-8"),
        )

    monkeypatch.setattr(subprocess, "run", recording_run)
    outcome = compile_in_local_document_runner(
        BytesArtifactSource(b"invalid docx"),
        DOCX_CONFIG_V1,
        acceptance_context=acceptance_context(),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT
    assert observed_environment == _document_runner_environment()


@pytest.mark.parametrize("hash_seed", ("4294967296", "99999999999999999999"))
def test_document_runner_environment_rejects_out_of_range_hash_seed(
    hash_seed: str,
) -> None:
    with pytest.raises(ValueError, match="hash seed is out of range"):
        _document_runner_environment(hash_seed=hash_seed)


@pytest.mark.parametrize(
    ("field", "value"),
    (("hash_seed", "١"), ("thread_count", "１")),
)
def test_document_runner_environment_rejects_non_ascii_decimal_controls(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="controls must be decimal integers"):
        _document_runner_environment(**{field: value})


@pytest.mark.parametrize("profile_ref", (DOCX_CONFIG_V1, PDF_TEXT_OUTLINE_V1))
def test_malformed_artifact_is_a_closed_typed_refusal(profile_ref: str) -> None:
    outcome = compile_in_local_document_runner(
        BytesArtifactSource(b"not the declared format"),
        profile_ref,
        acceptance_context=acceptance_context(),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT


def test_format_neutral_constructor_rejects_more_than_the_hard_unit_bound() -> None:
    digest = "0" * 64
    unit = StructuralUnit(
        ordinal=0,
        kind=DocumentStructuralKind.PARAGRAPH,
        text="bounded",
        locators=(
            DocxXmlLocator(
                artifact_digest=digest,
                part_uri="/word/document.xml",
                block_ordinal=0,
                xml_digest=digest,
            ),
        ),
    )
    units = tuple(
        StructuralUnit(
            ordinal=ordinal,
            kind=unit.kind,
            text=unit.text,
            locators=(
                DocxXmlLocator(
                    artifact_digest=digest,
                    part_uri="/word/document.xml",
                    block_ordinal=ordinal,
                    xml_digest=digest,
                ),
            ),
        )
        for ordinal in range(MAX_FORMAT_DOCUMENT_UNITS + 1)
    )

    with pytest.raises(ValueError, match="structural-unit hard bound"):
        ParsedDocument.format_neutral(
            artifact_digest=digest,
            profile=CompilationProfileRef(
                "context-engine-docx-v1",
                DOCX_CONFIG_V1,
            ),
            units=units,
        )


def test_format_text_bound_counts_copied_heading_ancestry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engine.supply.documents as document_contracts

    monkeypatch.setattr(document_contracts, "MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS", 10)
    digest = "0" * 64
    units = (
        StructuralUnit(
            ordinal=0,
            kind=DocumentStructuralKind.HEADING,
            text="12345",
            locators=(DocxXmlLocator(digest, "/word/document.xml", 0, digest),),
            heading_level=1,
        ),
        StructuralUnit(
            ordinal=1,
            kind=DocumentStructuralKind.PARAGRAPH,
            text="1",
            locators=(DocxXmlLocator(digest, "/word/document.xml", 1, digest),),
            heading_ancestry=("12345",),
        ),
    )

    with pytest.raises(ValueError, match="text hard bound"):
        ParsedDocument.format_neutral(
            artifact_digest=digest,
            profile=CompilationProfileRef(
                "context-engine-docx-v1",
                DOCX_CONFIG_V1,
            ),
            units=units,
        )


def test_docx_compiler_types_ancestry_only_overflow_as_document_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engine.supply.documents as document_contracts

    monkeypatch.setattr(document_contracts, "MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS", 10)
    monkeypatch.setattr(
        ragflow_document_adapter,
        "MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS",
        10,
    )
    document = Document()
    document.add_heading("12345", level=1)
    document.add_paragraph("1")

    outcome = compile_document_bytes(
        _save_docx(document),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED


def test_pdf_compiler_types_ancestry_only_overflow_as_document_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engine.supply.documents as document_contracts

    monkeypatch.setattr(document_contracts, "MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS", 10)
    monkeypatch.setattr(
        ragflow_document_adapter,
        "MAX_FORMAT_DOCUMENT_TEXT_CHARACTERS",
        10,
    )
    monkeypatch.setattr(
        ragflow_document_adapter,
        "extract_pdf_outlines",
        lambda *_args, **_kwargs: (
            RawPdfOutline("12345", 0, 1, (0.0, 0.0, 1.0, 1.0), "0" * 64),
            RawPdfOutline("1", 1, 1, (0.0, 0.0, 1.0, 1.0), "0" * 64),
        ),
    )

    outcome = compile_document_bytes(
        _pdf_outline_fixture_for_same_page(),
        CompilationProfileRef(
            "context-engine-pdf-outline-v1",
            PDF_TEXT_OUTLINE_V1,
        ),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.DOCUMENT_BOUND_EXCEEDED


@pytest.mark.parametrize(
    "profile",
    (
        CompilationProfileRef("context-engine-pdf-outline-v1", DOCX_CONFIG_V1),
        CompilationProfileRef("unsupported-compiler-v1", DOCX_CONFIG_V1),
        CompilationProfileRef("context-engine-docx-v1", PDF_TEXT_OUTLINE_V1),
        CompilationProfileRef("unsupported-compiler-v1", PDF_TEXT_OUTLINE_V1),
    ),
)
def test_raw_compiler_refuses_unsupported_exact_profile_identity(
    profile: CompilationProfileRef,
) -> None:
    outcome = compile_document_bytes(_docx_fixture(), profile)

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.UNKNOWN_PROFILE


def test_format_constructor_rejects_wrong_compiler_and_cross_artifact_locator() -> (
    None
):
    artifact_digest = "1" * 64
    unit = StructuralUnit(
        ordinal=0,
        kind=DocumentStructuralKind.PARAGRAPH,
        text="bounded",
        locators=(
            DocxXmlLocator(
                artifact_digest="2" * 64,
                part_uri="/word/document.xml",
                block_ordinal=0,
                xml_digest="3" * 64,
            ),
        ),
    )
    with pytest.raises(ValueError, match="compiler/profile identity"):
        ParsedDocument.format_neutral(
            artifact_digest=artifact_digest,
            profile=CompilationProfileRef("forged-compiler", DOCX_CONFIG_V1),
            units=(unit,),
        )
    with pytest.raises(ValueError, match="bind the document artifact"):
        ParsedDocument.format_neutral(
            artifact_digest=artifact_digest,
            profile=CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
            units=(unit,),
        )


def test_deserializer_rejects_forged_compiler_and_locator_artifact() -> None:
    compiled = compile_document_bytes(
        _docx_fixture(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )
    assert type(compiled) is ParsedDocument
    canonical = json.loads(canonicalize_parsed_document(compiled))
    canonical["profile"]["compilerRef"] = "forged-compiler"
    with pytest.raises(ValueError, match="compiler/profile identity"):
        deserialize_parsed_document(rfc8785.dumps(canonical))
    canonical["profile"]["compilerRef"] = "context-engine-docx-v1"
    canonical["units"][0]["unexpected"] = "must refuse"
    with pytest.raises(ValueError, match="unit has unexpected fields"):
        deserialize_parsed_document(rfc8785.dumps(canonical))
    del canonical["units"][0]["unexpected"]
    canonical["units"][0]["locators"][0]["artifactDigest"] = "f" * 64
    with pytest.raises(ValueError, match="bind the document artifact"):
        deserialize_parsed_document(rfc8785.dumps(canonical))


def test_unleased_document_runner_requires_private_acceptance_capability() -> None:
    unchecked_runner = cast(Any, compile_in_local_document_runner)
    outcome = unchecked_runner(
        BytesArtifactSource(_docx_fixture()),
        DOCX_CONFIG_V1,
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.RUNNER_UNAVAILABLE


def test_raw_compiler_converts_constructor_rejection_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_constructor(*args: object, **kwargs: object) -> ParsedDocument:
        raise ValueError("domain constructor rejected parser output")

    monkeypatch.setattr(ParsedDocument, "format_neutral", reject_constructor)
    outcome = compile_document_bytes(
        _docx_fixture(),
        CompilationProfileRef("context-engine-docx-v1", DOCX_CONFIG_V1),
    )

    assert type(outcome) is DocumentCompilationFailure
    assert outcome.code is DocumentCompilationFailureCode.INVALID_ARTIFACT
