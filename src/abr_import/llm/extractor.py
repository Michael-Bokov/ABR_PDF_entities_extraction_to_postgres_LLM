from __future__ import annotations

import logging

from abr_import.extract.parsers import ParsedAbbreviation, ParsedTerm
from abr_import.llm.chunking import chunk_text_by_pages
from abr_import.llm.ollama_client import OllamaError, generate_json, parse_json_response
from abr_import.ocr.engine import OcrPage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты извлекаешь структурированные данные из OCR-текста российских нормативных документов (ГОСТ, СТ).
Отвечай ТОЛЬКО валидным JSON без markdown.
Не выдумывай: включай только явные пары из текста.
Если перечня нет — верни пустые массивы."""

USER_TEMPLATE = """Из фрагмента OCR (страницы {start_page}-{end_page}) извлеки:

1. "abbreviations" — сокращения: {{"short_form": "...", "full_form": "..."}}
2. "terms" — термины и определения: {{"term": "...", "definition": "..."}}

Правила:
- Сокращение: короткая аббревиатура и расшифровка.
- Термин: слово/словосочетание и определение (может быть на нескольких строках OCR — объедини).
- Игнорируй основной текст стандарта, если это не перечень.
- Дубликаты не повторяй.

Текст OCR:
{text}
"""


def _parse_llm_payload(
    data: dict,
    *,
    start_page: int,
    end_page: int,
) -> tuple[list[ParsedAbbreviation], list[ParsedTerm]]:
    abbreviations: list[ParsedAbbreviation] = []
    terms: list[ParsedTerm] = []

    for item in data.get("abbreviations") or []:
        if not isinstance(item, dict):
            continue
        short = str(item.get("short_form", "")).strip()
        full = str(item.get("full_form", "")).strip()
        if short and full:
            abbreviations.append(
                ParsedAbbreviation(
                    short_form=short,
                    full_form=full,
                    page_number=start_page,
                    source_line=f"[LLM стр.{start_page}-{end_page}]",
                )
            )

    for item in data.get("terms") or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        definition = str(item.get("definition", "")).strip()
        if term and definition:
            terms.append(
                ParsedTerm(
                    term=term,
                    definition=definition,
                    page_number=start_page,
                    source_line=f"[LLM стр.{start_page}-{end_page}]",
                )
            )

    return abbreviations, terms


def _dedupe_abbr(items: list[ParsedAbbreviation]) -> list[ParsedAbbreviation]:
    seen: set[tuple[str, str]] = set()
    out: list[ParsedAbbreviation] = []
    for x in items:
        k = (x.short_form.lower(), x.full_form.lower())
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _dedupe_terms(items: list[ParsedTerm]) -> list[ParsedTerm]:
    seen: set[tuple[str, str]] = set()
    out: list[ParsedTerm] = []
    for x in items:
        k = (x.term.lower(), x.definition.lower())
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def extract_with_ollama(
    ocr_pages: list[OcrPage],
    *,
    base_url: str,
    model: str,
    pages_per_chunk: int = 4,
    max_chars_per_chunk: int = 12000,
    request_timeout: int = 600,
) -> tuple[list[ParsedAbbreviation], list[ParsedTerm]]:
    chunks = chunk_text_by_pages(
        ocr_pages,
        pages_per_chunk=pages_per_chunk,
        max_chars=max_chars_per_chunk,
    )
    if not chunks:
        return [], []

    all_abbr: list[ParsedAbbreviation] = []
    all_terms: list[ParsedTerm] = []

    for start_page, end_page, text in chunks:
        prompt = USER_TEMPLATE.format(
            start_page=start_page,
            end_page=end_page,
            text=text[:max_chars_per_chunk],
        )
        logger.info("LLM: страницы %d-%d (%d символов)", start_page, end_page, len(text))
        try:
            raw = generate_json(
                base_url=base_url,
                model=model,
                prompt=prompt,
                system=SYSTEM_PROMPT,
                timeout=request_timeout,
            )
            data = parse_json_response(raw)
            abbr, terms = _parse_llm_payload(
                data, start_page=start_page, end_page=end_page
            )
            all_abbr.extend(abbr)
            all_terms.extend(terms)
            logger.info(
                "LLM чанк %d-%d: +%d сокращ., +%d терминов",
                start_page,
                end_page,
                len(abbr),
                len(terms),
            )
        except OllamaError:
            raise
        except Exception as exc:
            logger.warning("LLM чанк %d-%d пропущен: %s", start_page, end_page, exc)

    return _dedupe_abbr(all_abbr), _dedupe_terms(all_terms)
