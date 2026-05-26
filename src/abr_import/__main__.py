from __future__ import annotations

import argparse
import logging
import sys
import time

from abr_import.config import Settings
from abr_import.db.repository import DocumentRepository
from abr_import.db.session import create_session_factory
from abr_import.llm.ollama_client import OllamaError, check_ollama
from abr_import.ocr.engine import OcrEngine, OcrNotAvailableError, verify_paddle_stack
from abr_import.pipeline.processor import ImportProcessor


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def cmd_test_pdf(settings: Settings, pdf_path: str) -> int:
    from pathlib import Path

    from abr_import.extract.parsers import extract_lists
    from abr_import.extract.sections import SectionKind, find_section_spans
    from abr_import.llm.extractor import extract_with_ollama
    from abr_import.ocr.pdf_reader import iter_pdf_pages
    from abr_import.pipeline.zip_loader import fix_mojibake_unicode

    path = Path(pdf_path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 1

    print(f"Файл: {path.name} -> title: {fix_mojibake_unicode(path.stem)}")
    engine = OcrEngine(lang=settings.ocr_lang, use_gpu=settings.use_gpu)
    page_count, pages = iter_pdf_pages(path, dpi=settings.pdf_dpi)
    print(f"Страниц: {page_count}")
    ocr_pages = engine.recognize_pages(pages)
    line_count = sum(len(p.lines) for p in ocr_pages)
    print(f"Строк OCR: {line_count}")
    if line_count and ocr_pages[0].lines:
        print("Примеры OCR (первые 5 строк стр.1):")
        for ln in ocr_pages[0].lines[:5]:
            print(f"  {ln.text[:100]}")

    if settings.extractor == "llm":
        print(f"Извлечение: LLM ({settings.ollama_model})")
        abbr, terms = extract_with_ollama(
            ocr_pages,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            pages_per_chunk=settings.llm_chunk_pages,
            max_chars_per_chunk=settings.llm_max_chars,
            request_timeout=settings.llm_timeout_sec,
        )
    else:
        spans = find_section_spans(ocr_pages)
        for kind in SectionKind:
            print(f"  раздел {kind.value}: {spans.get(kind)}")
        abbr, terms = extract_lists(ocr_pages, spans)

    print(f"Сокращений: {len(abbr)}, терминов: {len(terms)}")
    for a in abbr[:5]:
        print(f"  [A] {a.short_form} — {a.full_form[:60]}")
    for t in terms[:5]:
        print(f"  [T] {t.term}: {t.definition[:60]}")
    return 0


def cmd_check(settings: Settings) -> int:
    try:
        version = verify_paddle_stack(settings.use_gpu)
        print(f"OK: paddle {version} (use_gpu={settings.use_gpu})")
        from paddleocr import PaddleOCR

        PaddleOCR(lang=settings.ocr_lang, use_angle_cls=True)
        print("OK: PaddleOCR")
    except OcrNotAvailableError as exc:
        print(f"OCR ОШИБКА: {exc}")
        return 1

    if settings.extractor == "llm":
        try:
            check_ollama(settings.ollama_base_url, settings.ollama_model)
            print(f"OK: Ollama {settings.ollama_base_url} модель {settings.ollama_model}")
        except OllamaError as exc:
            print(f"LLM ОШИБКА: {exc}")
            return 1
    return 0


def cmd_worker(settings: Settings) -> None:
    try:
        paddle_ver = verify_paddle_stack(settings.use_gpu)
    except OcrNotAvailableError as exc:
        logging.getLogger(__name__).error("%s", exc)
        sys.exit(1)

    if settings.extractor == "llm":
        try:
            check_ollama(settings.ollama_base_url, settings.ollama_model)
        except OllamaError as exc:
            logging.getLogger(__name__).error("%s", exc)
            sys.exit(1)

    factory = create_session_factory(settings)
    processor = ImportProcessor(settings, factory)
    processor.startup_recovery()

    logging.getLogger(__name__).info(
        "Воркер: input=%s, ocr_gpu=%s, extractor=%s, ollama=%s",
        settings.input_dir,
        settings.use_gpu,
        settings.extractor,
        settings.ollama_model if settings.extractor == "llm" else "-",
    )

    while True:
        processor.register_all_pdfs()
        n = processor.process_pending()
        if n == 0:
            time.sleep(settings.poll_interval_sec)
        else:
            logging.getLogger(__name__).info("Обработано документов: %d", n)


def cmd_query(settings: Settings, title: str) -> int:
    factory = create_session_factory(settings)
    from abr_import.db.session import session_scope

    with session_scope(factory) as session:
        repo = DocumentRepository(session)
        doc, abbrs, terms = repo.get_lists_by_title(title)

    if not doc:
        print(f"Документ не найден: {title}")
        return 1

    print(f"Документ: {doc.title} ({doc.filename})")
    print(f"Статус: {doc.status.value}")
    print(f"\nСокращения ({len(abbrs)}):")
    for a in abbrs:
        print(f"  {a.short_form} — {a.full_form}")

    print(f"\nТермины ({len(terms)}):")
    for t in terms:
        print(f"  {t.term}: {t.definition[:120]}{'...' if len(t.definition) > 120 else ''}")
    return 0


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Импорт перечней из PDF в PostgreSQL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker", help="Фоновая обработка PDF")
    sub.add_parser("check", help="Проверка OCR и Ollama")
    t = sub.add_parser("test-pdf", help="OCR + извлечение одного PDF")
    t.add_argument("pdf", help="Путь к PDF")
    q = sub.add_parser("query", help="Перечни по названию документа")
    q.add_argument("title", help="Имя файла без .pdf")

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "worker":
        cmd_worker(settings)
    elif args.command == "check":
        sys.exit(cmd_check(settings))
    elif args.command == "test-pdf":
        sys.exit(cmd_test_pdf(settings, args.pdf))
    elif args.command == "query":
        sys.exit(cmd_query(settings, args.title))


if __name__ == "__main__":
    main()
