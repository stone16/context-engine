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
#  Modified by ContextEngine: get_text and RAGFlow codec coupling were removed;
#  outline extraction no longer swallows parser exceptions and binds rendered
#  page pixels through the separately licensed pypdfium2 runtime.

"""Patched RAGFlow PDF outline extraction only."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from typing import cast

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.generic import Destination


@dataclass(frozen=True, slots=True)
class RawPdfOutline:
    """Outline metadata whose bbox is a normalized zero-origin page extent."""

    title: str
    depth: int
    page_number: int
    page_bbox_points: tuple[float, float, float, float]
    page_render_digest: str


class PdfPageBoundExceeded(ValueError):
    """A PDF page cannot be rendered within the caller-owned hard bounds."""


def extract_pdf_outlines(
    source: bytes,
    *,
    max_page_dimension_points: float,
    max_page_pixel_area: float,
) -> tuple[RawPdfOutline, ...]:
    """Extract source-ordered outline entries or propagate a parser failure."""

    if type(source) is not bytes:
        raise TypeError("PDF outline source must be exact bytes")
    pdf = PdfReader(BytesIO(source))
    outlines: list[RawPdfOutline] = []
    render_digests: dict[int, str] = {}
    render_document = pdfium.PdfDocument(source)
    try:
        def render_digest(page_index: int) -> str:
            digest = render_digests.get(page_index)
            if digest is None:
                digest = _page_render_digest(render_document[page_index])
                render_digests[page_index] = digest
            return digest

        def dfs(nodes: list[object], depth: int) -> None:
            for node in nodes:
                if isinstance(node, list):
                    dfs(cast(list[object], node), depth + 1)
                    continue
                if not isinstance(node, Destination):
                    raise ValueError("PDF outline node is not a destination")
                title = node.title.strip()
                page_index = pdf.get_destination_page_number(node)
                page_number = page_index + 1
                if not title or not 1 <= page_number <= len(pdf.pages):
                    raise ValueError("PDF outline entry is outside the closed profile")
                if depth > 5:
                    raise ValueError("PDF outline depth exceeds the closed profile")
                page = pdf.pages[page_index]
                raw_bbox = tuple(float(value) for value in page.mediabox)
                if len(raw_bbox) != 4:
                    raise ValueError("PDF page media box is outside the closed profile")
                x0, y0, x1, y1 = raw_bbox
                width = max(x0, x1) - min(x0, x1)
                height = max(y0, y1) - min(y0, y1)
                if (
                    not isfinite(width)
                    or not isfinite(height)
                    or width <= 0
                    or height <= 0
                    or width > max_page_dimension_points
                    or height > max_page_dimension_points
                    or width * height > max_page_pixel_area
                ):
                    raise PdfPageBoundExceeded(
                        "PDF page exceeds the render hard bound"
                    )
                outlines.append(
                    RawPdfOutline(
                        title=title,
                        depth=depth,
                        page_number=page_number,
                        # PdfRegionLocator is zero-origin; the MediaBox offset is
                        # intentionally discarded while its extent is retained.
                        page_bbox_points=cast(
                            tuple[float, float, float, float],
                            (0.0, 0.0, width, height),
                        ),
                        page_render_digest=render_digest(page_index),
                    )
                )

        dfs(cast(list[object], pdf.outline), 0)
    finally:
        render_document.close()
    return tuple(outlines)


def _page_render_digest(page: pdfium.PdfPage) -> str:
    """Hash deterministic 72-DPI BGR pixels plus their exact raster shape."""

    from hashlib import sha256

    bitmap = page.render(scale=1, may_draw_forms=True)
    try:
        header = (
            f"{bitmap.width}x{bitmap.height}:{bitmap.stride}:"
            f"{bitmap.n_channels}:{bitmap.format}\x00"
        ).encode("ascii")
        return sha256(header + bytes(bitmap.buffer)).hexdigest()
    finally:
        bitmap.close()
        page.close()


__all__ = ["PdfPageBoundExceeded", "RawPdfOutline", "extract_pdf_outlines"]
