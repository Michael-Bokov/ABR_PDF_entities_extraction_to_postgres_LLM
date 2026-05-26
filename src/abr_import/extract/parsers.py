from __future__ import annotations

import re
from dataclasses import dataclass

from abr_import.extract.sections import (
    SectionKind,
    SectionSpan,
    all_document_lines,
    extract_section_text,
)
from abr_import.ocr.engine import OcrPage, OcrLine

# Разделители: тире, дефис, двоеточие, табуляция
PAIR_SEP = re.compile(
    r"\s*[-–—:]\s*|\s{2,}|\t",
    re.UNICODE,
)

# Сокращение: короткий левый фрагмент (АСУ, НД и т.д.)
ABBR_KEY_MAX = 20
TERM_KEY_MAX = 120

# Нумерация в начале строки
NUM_PREFIX = re.compile(r"^\s*\d+[\.\)]\s*", re.UNICODE)


@dataclass(frozen=True)
class ParsedAbbreviation:
    short_form: str
    full_form: str
    page_number: int | None = None
    source_line: str | None = None


@dataclass(frozen=True)
class ParsedTerm:
    term: str
    definition: str
    page_number: int | None = None
    source_line: str | None = None


def _clean_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _parse_pair_line(
    line: str,
    *,
    page_number: int,
    key_max_len: int = 80,
) -> tuple[str, str] | None:
    line = NUM_PREFIX.sub("", line).strip()
    if len(line) < 3:
        return None

    parts = PAIR_SEP.split(line, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return _clean_key(parts[0]), _clean_key(parts[1])

    # «Ключ — значение» в одной строке без явного разделителя: короткий префикс
    if " - " in line:
        left, right = line.split(" - ", 1)
        if left.strip() and right.strip() and len(left) <= key_max_len:
            return _clean_key(left), _clean_key(right)

    return None


def parse_lines_to_pairs(
    lines: list[tuple[int, str]],
    *,
    kind: SectionKind,
) -> list[ParsedAbbreviation] | list[ParsedTerm]:
    results: list = []
    seen: set[tuple[str, str]] = set()

    for page_no, line in lines:
        pair = _parse_pair_line(line, page_number=page_no)
        if not pair:
            continue
        key, value = pair
        dedup_key = (key.lower(), value.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if kind == SectionKind.ABBREVIATIONS:
            results.append(
                ParsedAbbreviation(
                    short_form=key,
                    full_form=value,
                    page_number=page_no,
                    source_line=line,
                )
            )
        else:
            results.append(
                ParsedTerm(
                    term=key,
                    definition=value,
                    page_number=page_no,
                    source_line=line,
                )
            )
    return results


def parse_table_columns(
    page_blocks: list[tuple[int, list[OcrLine]]],
    *,
    kind: SectionKind,
) -> list[ParsedAbbreviation] | list[ParsedTerm]:
    """Две колонки по кластеризации X (типичные таблицы в PDF)."""
    results: list = []
    seen: set[tuple[str, str]] = set()

    for page_no, lines in page_blocks:
        if not lines:
            continue
        xs = [ln.x_min for ln in lines]
        if not xs:
            continue
        mid = (min(xs) + max(xs)) / 2
        # уточняем границу: медиана x_min
        sorted_x = sorted(ln.x_min for ln in lines)
        mid = sorted_x[len(sorted_x) // 2]

        left: dict[float, OcrLine] = {}
        right: dict[float, OcrLine] = {}
        for ln in lines:
            bucket = left if ln.x_min < mid else right
            y = round(ln.y_center / 8) * 8
            prev = bucket.get(y)
            if prev is None or ln.x_min < prev.x_min:
                bucket[y] = ln

        for y in sorted(set(left) & set(right)):
            key = left[y].text.strip()
            val = right[y].text.strip()
            if not key or not val:
                continue
            dedup = (key.lower(), val.lower())
            if dedup in seen:
                continue
            seen.add(dedup)
            if kind == SectionKind.ABBREVIATIONS:
                results.append(
                    ParsedAbbreviation(
                        short_form=_clean_key(key),
                        full_form=_clean_key(val),
                        page_number=page_no,
                        source_line=f"{key} | {val}",
                    )
                )
            else:
                results.append(
                    ParsedTerm(
                        term=_clean_key(key),
                        definition=_clean_key(val),
                        page_number=page_no,
                        source_line=f"{key} | {val}",
                    )
                )
    return results


def extract_lists(
    pages: list[OcrPage],
    spans: dict[SectionKind, SectionSpan | None],
) -> tuple[list[ParsedAbbreviation], list[ParsedTerm]]:
    from abr_import.extract.sections import extract_section_blocks

    abbr_lines = extract_section_text(pages, spans.get(SectionKind.ABBREVIATIONS))
    term_lines = extract_section_text(pages, spans.get(SectionKind.TERMS))

    abbreviations: list[ParsedAbbreviation] = parse_lines_to_pairs(
        abbr_lines, kind=SectionKind.ABBREVIATIONS
    )
    terms: list[ParsedTerm] = parse_lines_to_pairs(
        term_lines, kind=SectionKind.TERMS
    )

    # Дополняем из табличной вёрстки, если мало строковых пар
    if len(abbreviations) < 2:
        blocks = extract_section_blocks(
            pages, spans.get(SectionKind.ABBREVIATIONS)
        )
        table_abbr: list[ParsedAbbreviation] = parse_table_columns(
            blocks, kind=SectionKind.ABBREVIATIONS
        )
        abbreviations = _merge_abbr(abbreviations, table_abbr)

    if len(terms) < 2:
        blocks = extract_section_blocks(pages, spans.get(SectionKind.TERMS))
        table_terms: list[ParsedTerm] = parse_table_columns(
            blocks, kind=SectionKind.TERMS
        )
        terms = _merge_terms(terms, table_terms)

    # Запасной режим: разделы не найдены (часто у ГОСТ/сканов) — ищем пары по всему документу
    if len(abbreviations) < 2 and len(terms) < 2:
        abbreviations, terms = _fallback_full_document_scan(pages)

    return abbreviations, terms


def _fallback_full_document_scan(
    pages: list[OcrPage],
) -> tuple[list[ParsedAbbreviation], list[ParsedTerm]]:
    lines = all_document_lines(pages)
    abbreviations: list[ParsedAbbreviation] = []
    terms: list[ParsedTerm] = []
    seen_a: set[tuple[str, str]] = set()
    seen_t: set[tuple[str, str]] = set()

    for page_no, line in lines:
        pair = _parse_pair_line(
            line, page_number=page_no, key_max_len=TERM_KEY_MAX
        )
        if not pair:
            continue
        key, value = pair
        if len(value) < 4:
            continue

        # Короткий ключ → сокращение; длиннее → термин
        if len(key) <= ABBR_KEY_MAX and _looks_like_abbrev(key):
            dk = (key.lower(), value.lower())
            if dk not in seen_a:
                seen_a.add(dk)
                abbreviations.append(
                    ParsedAbbreviation(
                        short_form=key,
                        full_form=value,
                        page_number=page_no,
                        source_line=line,
                    )
                )
        if len(key) >= 3 and len(key) <= TERM_KEY_MAX:
            dk = (key.lower(), value.lower())
            if dk not in seen_t:
                seen_t.add(dk)
                terms.append(
                    ParsedTerm(
                        term=key,
                        definition=value,
                        page_number=page_no,
                        source_line=line,
                    )
                )

    return abbreviations, terms


def _looks_like_abbrev(key: str) -> bool:
    k = key.strip()
    if len(k) < 1 or len(k) > ABBR_KEY_MAX:
        return False
    if re.fullmatch(r"[\d\W]+", k, re.UNICODE):
        return False
    upper_ratio = sum(1 for c in k if c.isupper()) / max(len(k), 1)
    return upper_ratio > 0.4 or len(k) <= 6


def _merge_abbr(
    a: list[ParsedAbbreviation], b: list[ParsedAbbreviation]
) -> list[ParsedAbbreviation]:
    seen = {(x.short_form.lower(), x.full_form.lower()) for x in a}
    out = list(a)
    for item in b:
        k = (item.short_form.lower(), item.full_form.lower())
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _merge_terms(a: list[ParsedTerm], b: list[ParsedTerm]) -> list[ParsedTerm]:
    seen = {(x.term.lower(), x.definition.lower()) for x in a}
    out = list(a)
    for item in b:
        k = (item.term.lower(), item.definition.lower())
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out