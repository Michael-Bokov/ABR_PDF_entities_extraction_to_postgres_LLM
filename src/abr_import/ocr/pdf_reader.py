from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass(frozen=True)
class PageImage:
    page_number: int
    width: int
    height: int
    rgb_bytes: bytes


def iter_pdf_pages(
    pdf_path: Path,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
) -> tuple[int, list[PageImage]]:
    """Рендер страниц PDF в RGB для OCR (сканы без текстового слоя)."""
    doc = fitz.open(pdf_path)
    try:
        total = doc.page_count
        limit = min(total, max_pages) if max_pages else total
        pages: list[PageImage] = []
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for idx in range(limit):
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(
                PageImage(
                    page_number=idx + 1,
                    width=pix.width,
                    height=pix.height,
                    rgb_bytes=pix.samples,
                )
            )
        return total, pages
    finally:
        doc.close()
