from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from abr_import.ocr.engine import OcrLine, OcrPage


class SectionKind(str, Enum):
    ABBREVIATIONS = "abbreviations"
    TERMS = "terms"


ABBREVIATION_HEADERS = [
    r"перечень\s+сокращ",
    r"перечень\s+принятых\s+сокращ",
    r"список\s+сокращ",
    r"сокращени[яй]",
    r"условные\s+обознач",
    r"принятых\s+сокращ",
]

TERM_HEADERS = [
    r"термины\s+и\s+определ",
    r"термины,?\s+определ",
    r"термины\s+и\s+их\s+определ",
    r"глоссарий",
    r"определени[яй]\s+термин",
    r"термины\s*$",
    r"определени[яй]\s*$",
]

SECTION_STOP_HEADERS = [
    r"содержание",
    r"введение",
    r"общие\s+полож",
    r"приложени[ея]",
    r"литератур",
    r"библиограф",
    r"список\s+источник",
    r"термины\s+и\s+определ",
    r"сокращени[яй]",
    r"перечень\s+сокращ",
    r"аннотация",
    r"реферат",
    r"оглавление",
    r"нормативн",
    r"гост\s+\d",
]

# Ключевые подстроки для OCR с ошибками (без пробелов)
_ABBR_KEYWORDS = ("сокращ", "обознач", "переченсокращ")
_TERM_KEYWORDS = ("термин", "определ", "глоссар")


@dataclass
class SectionSpan:
    kind: SectionKind
    start_page: int
    end_page: int
    start_line_idx: int
    end_line_idx: int | None


def _normalize_header(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _compact(text: str) -> str:
    return _normalize_header(text).replace(" ", "")


def _matches_any(text: str, patterns: list[str]) -> bool:
    norm = _normalize_header(text)
    if len(norm) > 120:
        return False
    return any(re.search(p, norm) for p in patterns)


def _fuzzy_keyword_header(line: str, kind: SectionKind) -> bool:
    compact = _compact(line)
    if len(compact) > 80:
        return False
    keywords = _ABBR_KEYWORDS if kind == SectionKind.ABBREVIATIONS else _TERM_KEYWORDS
    return any(kw in compact for kw in keywords)


def _is_section_header(line: str, kind: SectionKind) -> bool:
    return _matches_any(line, _header_patterns(kind)) or _fuzzy_keyword_header(
        line, kind
    )


def _header_patterns(kind: SectionKind) -> list[str]:
    return ABBREVIATION_HEADERS if kind == SectionKind.ABBREVIATIONS else TERM_HEADERS


def _is_stop_header(line: str, current: SectionKind) -> bool:
    norm = _normalize_header(line)
    if len(norm) > 120:
        return False
    if current == SectionKind.ABBREVIATIONS and _is_section_header(line, current):
        return False
    if current == SectionKind.TERMS and _is_section_header(line, current):
        return False
    return _matches_any(line, SECTION_STOP_HEADERS)


@dataclass
class PageLines:
    page_number: int
    lines: list[str]


def pages_to_line_stream(pages: list[OcrPage]) -> list[PageLines]:
    stream: list[PageLines] = []
    for page in pages:
        lines = [ln.text for ln in page.lines if ln.text.strip()]
        stream.append(PageLines(page_number=page.page_number, lines=lines))
    return stream


def all_document_lines(pages: list[OcrPage]) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for pl in pages_to_line_stream(pages):
        for line in pl.lines:
            result.append((pl.page_number, line))
    return result


def find_section_spans(pages: list[OcrPage]) -> dict[SectionKind, SectionSpan | None]:
    stream = pages_to_line_stream(pages)
    flat: list[tuple[int, int, str]] = []
    for pl in stream:
        for i, line in enumerate(pl.lines):
            flat.append((pl.page_number, i, line))

    spans: dict[SectionKind, SectionSpan | None] = {
        SectionKind.ABBREVIATIONS: None,
        SectionKind.TERMS: None,
    }

    for kind in (SectionKind.ABBREVIATIONS, SectionKind.TERMS):
        start_idx: int | None = None
        for idx, (page_no, line_idx, line) in enumerate(flat):
            if start_idx is None:
                if _is_section_header(line, kind):
                    start_idx = idx + 1
                continue
            if _is_stop_header(line, kind):
                start_page, start_line, _ = flat[start_idx]
                end_page, end_line, _ = (
                    flat[idx - 1] if idx > start_idx else flat[start_idx]
                )
                spans[kind] = SectionSpan(
                    kind=kind,
                    start_page=start_page,
                    end_page=end_page,
                    start_line_idx=start_line,
                    end_line_idx=end_line,
                )
                break
        else:
            if start_idx is not None and start_idx < len(flat):
                start_page, start_line, _ = flat[start_idx]
                end_page, end_line, _ = flat[-1]
                spans[kind] = SectionSpan(
                    kind=kind,
                    start_page=start_page,
                    end_page=end_page,
                    start_line_idx=start_line,
                    end_line_idx=end_line,
                )

    return spans


def extract_section_text(
    pages: list[OcrPage],
    span: SectionSpan | None,
) -> list[tuple[int, str]]:
    if span is None:
        return []

    stream = pages_to_line_stream(pages)
    result: list[tuple[int, str]] = []
    for pl in stream:
        if pl.page_number < span.start_page or pl.page_number > span.end_page:
            continue
        for i, line in enumerate(pl.lines):
            if pl.page_number == span.start_page and i < span.start_line_idx:
                continue
            if (
                pl.page_number == span.end_page
                and span.end_line_idx is not None
                and i > span.end_line_idx
            ):
                continue
            if line.strip():
                result.append((pl.page_number, line))
    return result


def extract_section_blocks(
    pages: list[OcrPage],
    span: SectionSpan | None,
) -> list[tuple[int, list[OcrLine]]]:
    if span is None:
        return []

    blocks: list[tuple[int, list[OcrLine]]] = []
    for page in pages:
        if page.page_number < span.start_page or page.page_number > span.end_page:
            continue
        blocks.append((page.page_number, list(page.lines)))
    return blocks
