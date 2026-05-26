from __future__ import annotations

from abr_import.ocr.engine import OcrPage


def pages_to_text(ocr_pages: list[OcrPage]) -> str:
    parts: list[str] = []
    for page in ocr_pages:
        lines = [ln.text for ln in page.lines if ln.text.strip()]
        if not lines:
            continue
        parts.append(f"--- Страница {page.page_number} ---")
        parts.extend(lines)
    return "\n".join(parts)


def chunk_text_by_pages(
    ocr_pages: list[OcrPage],
    *,
    pages_per_chunk: int = 4,
    max_chars: int = 12000,
) -> list[tuple[int, int, str]]:
    """
    Режет документ на фрагменты для LLM (до 44+ страниц).

    Returns: (start_page, end_page, text)
    """
    if not ocr_pages:
        return []

    chunks: list[tuple[int, int, str]] = []
    batch: list[OcrPage] = []

    def flush(batch_pages: list[OcrPage]) -> None:
        if not batch_pages:
            return
        text = pages_to_text(batch_pages)
        if not text.strip():
            return
        if len(text) > max_chars:
            mid = len(batch_pages) // 2
            flush(batch_pages[:mid])
            flush(batch_pages[mid:])
            return
        chunks.append((batch_pages[0].page_number, batch_pages[-1].page_number, text))

    for page in ocr_pages:
        batch.append(page)
        if len(batch) >= pages_per_chunk:
            flush(batch)
            batch = []
    flush(batch)
    return chunks
