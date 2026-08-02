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
from io import BytesIO
from typing import Final, cast

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

_PARAGRAPH_TAG: Final = qn("w:p")
_TABLE_TAG: Final = qn("w:tbl")


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
        assert isinstance(document, DocumentType)
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
                            xml=cast(bytes, child.xml.encode("utf-8")),
                            has_figure=has_figure,
                        )
                    )
            elif child.tag == _TABLE_TAG:
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
                            xml=cast(bytes, child.xml.encode("utf-8")),
                            table_cells=rows,
                            has_figure=bool(child.xpath(".//pic:pic")),
                        )
                    )
        return tuple(blocks)


__all__ = ["RAGFlowDocxParser", "RawDocxBlock"]
