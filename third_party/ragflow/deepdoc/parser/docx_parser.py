#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#  Modified by ContextEngine: dependency-entangled classification and image
#  behavior were removed; the parser now emits source-ordered raw OOXML blocks.

"""Patched RAGFlow DOCX block extraction with no application dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from io import BytesIO
from typing import Any, Final

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_PARAGRAPH_TAG: Final = qn("w:p")
_TABLE_TAG: Final = qn("w:tbl")
_SECTION_PROPERTIES_TAG: Final = qn("w:sectPr")
_RUN_TAG: Final = qn("w:r")
_RUN_PROPERTIES_TAG: Final = qn("w:rPr")
_TEXT_TAG: Final = qn("w:t")
_TAB_TAGS: Final = frozenset({qn("w:tab"), qn("w:ptab")})
_BREAK_TAGS: Final = frozenset({qn("w:br"), qn("w:cr")})
_BREAK_TYPE_ATTRIBUTE: Final = qn("w:type")
_NO_BREAK_HYPHEN_TAG: Final = qn("w:noBreakHyphen")
_NON_VISIBLE_RUN_TAGS: Final = frozenset(
    {
        qn("w:fldChar"),
        qn("w:instrText"),
        qn("w:lastRenderedPageBreak"),
        _RUN_PROPERTIES_TAG,
    }
)
_ADMITTED_RUN_TAGS: Final = (
    frozenset({_TEXT_TAG, _NO_BREAK_HYPHEN_TAG})
    | _TAB_TAGS
    | _BREAK_TAGS
    | _NON_VISIBLE_RUN_TAGS
)
_UNSUPPORTED_CONTENT_TAGS: Final = frozenset(
    {
        qn("w:customXml"),
        qn("w:del"),
        qn("w:fldSimple"),
        qn("w:ins"),
        qn("w:moveFrom"),
        qn("w:moveTo"),
        qn("w:sdt"),
        qn("w:smartTag"),
    }
)
_UNSUPPORTED_VISUAL_TAGS: Final = frozenset(
    {qn("pic:pic"), qn("w:drawing"), qn("w:object"), qn("w:pict")}
)
_XML_CONTENT_TYPES: Final = frozenset({"application/xml", "text/xml"})


class UnsupportedDocxFigureError(ValueError):
    """The closed DOCX profile encountered an unsupported visual object."""


def _contains_tag(element: Any, tags: frozenset[str]) -> bool:
    return any(node.tag in tags for node in element.iter())


def _is_xml_content_type(content_type: object) -> bool:
    if type(content_type) is not str:
        return False
    parsed = policy.default.header_factory("Content-Type", content_type)
    if parsed.defects:
        raise ValueError("DOCX package part has a malformed media type")
    media_type = f"{parsed.maintype}/{parsed.subtype}"
    return media_type in _XML_CONTENT_TYPES or parsed.subtype.endswith("+xml")


def _package_xml_elements(document: DocumentType) -> tuple[tuple[Any, ...], bool]:
    elements: list[Any] = []
    has_malformed_part = False
    for part in document.part.package.parts:
        try:
            is_xml = _is_xml_content_type(part.content_type)
        except ValueError:
            has_malformed_part = True
            continue
        if not is_xml:
            continue
        try:
            element = getattr(part, "element", None)
            elements.append(element if element is not None else parse_xml(part.blob))
        except Exception:
            has_malformed_part = True
    return tuple(elements), has_malformed_part


def _elements_contain_tag(elements: tuple[Any, ...], tags: frozenset[str]) -> bool:
    return any(_contains_tag(element, tags) for element in elements)


def _ooxml_visible_text(element: Any) -> str:
    text: list[str] = []
    for run in element.iter(_RUN_TAG):
        for node in run.iterdescendants():
            if node.tag == _TEXT_TAG:
                text.append(node.text or "")
            elif node.tag in _TAB_TAGS:
                text.append("\t")
            elif node.tag == _NO_BREAK_HYPHEN_TAG:
                text.append("-")
            elif node.tag in _BREAK_TAGS and (
                node.tag == qn("w:cr")
                or node.get(_BREAK_TYPE_ATTRIBUTE) in (None, "textWrapping")
            ):
                text.append("\n")
    return "".join(text)


def _contains_unadmitted_run_content(element: Any) -> bool:
    return any(
        child.tag not in _ADMITTED_RUN_TAGS
        for run in element.iter(_RUN_TAG)
        for child in run.iterchildren()
    )


def _contains_unrepresented_package_text(
    elements: tuple[Any, ...], document: DocumentType
) -> bool:
    for element in elements:
        if element is document.element:
            if any(
                child is not document.element.body and _ooxml_visible_text(child)
                for child in element.iterchildren()
            ):
                return True
        elif _ooxml_visible_text(element):
            return True
    return False


def _body_paragraph_text_is_lossless(document: DocumentType) -> bool:
    body = document.element.body
    return all(
        any(ancestor.tag == _PARAGRAPH_TAG for ancestor in run.iterancestors())
        for run in body.iter(_RUN_TAG)
    ) and all(
        Paragraph(paragraph, document).text == _ooxml_visible_text(paragraph)
        for paragraph in body.iter(_PARAGRAPH_TAG)
    )


@dataclass(frozen=True, slots=True)
class RawDocxBlock:
    """One bounded block in OOXML body order."""

    kind: str
    block_ordinal: int
    text: str
    style_name: str | None
    xml: bytes
    table_cells: tuple[tuple[str, ...], ...] = ()
    has_figure: bool = False


class RAGFlowDocxParser:
    """Narrow patched parser retained from the approved RAGFlow source region."""

    def __call__(self, source: bytes) -> tuple[RawDocxBlock, ...]:
        if type(source) is not bytes:
            raise TypeError("DOCX parser source must be exact bytes")
        document = Document(BytesIO(source))
        if not isinstance(document, DocumentType):
            raise ValueError("DOCX parser did not construct an exact document")
        package_elements, has_malformed_part = _package_xml_elements(document)
        if _elements_contain_tag(package_elements, _UNSUPPORTED_VISUAL_TAGS):
            raise UnsupportedDocxFigureError(
                "DOCX profile does not admit visual objects"
            )
        if has_malformed_part:
            raise ValueError("DOCX contains a malformed XML package part")
        if _elements_contain_tag(package_elements, _UNSUPPORTED_CONTENT_TAGS):
            raise ValueError("DOCX contains an unsupported content container")
        if any(_contains_unadmitted_run_content(e) for e in package_elements):
            raise ValueError("DOCX contains unsupported run content")
        if _contains_unrepresented_package_text(package_elements, document):
            raise ValueError("DOCX contains text outside the represented body")
        if not _body_paragraph_text_is_lossless(document):
            raise ValueError("DOCX paragraph text cannot be represented losslessly")
        blocks: list[RawDocxBlock] = []
        for block_ordinal, child in enumerate(document.element.body.iterchildren()):
            if child.tag == _PARAGRAPH_TAG:
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                has_figure = bool(child.xpath(".//pic:pic"))
                if text or has_figure:
                    style_name = (
                        paragraph.style.name
                        if paragraph.style is not None
                        else None
                    )
                    blocks.append(
                        RawDocxBlock(
                            kind="paragraph",
                            block_ordinal=block_ordinal,
                            text=text,
                            style_name=style_name,
                            xml=child.xml.encode("utf-8"),
                            has_figure=has_figure,
                        )
                    )
            elif child.tag == _TABLE_TAG:
                if any(node.tag == _TABLE_TAG for node in child.iterdescendants()):
                    raise ValueError("DOCX profile does not admit nested tables")
                table = Table(child, document)
                rows = tuple(
                    tuple(cell.text.strip() for cell in row.cells)
                    for row in table.rows
                )
                if rows:
                    blocks.append(
                        RawDocxBlock(
                            kind="table",
                            block_ordinal=block_ordinal,
                            text="\n".join("\t".join(row) for row in rows),
                            style_name=None,
                            xml=child.xml.encode("utf-8"),
                            table_cells=rows,
                            has_figure=bool(child.xpath(".//pic:pic")),
                        )
                    )
            elif child.tag != _SECTION_PROPERTIES_TAG:
                raise ValueError("DOCX contains an unsupported body element")
        return tuple(blocks)


__all__ = ["RAGFlowDocxParser", "RawDocxBlock", "UnsupportedDocxFigureError"]
