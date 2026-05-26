from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from abr_import.config import Settings
from abr_import.db.models import Document, DocumentStatus
from abr_import.db.repository import DocumentRepository
from abr_import.db.session import session_scope
from abr_import.extract.parsers import extract_lists
from abr_import.extract.sections import find_section_spans
from abr_import.llm.extractor import extract_with_ollama
from abr_import.llm.ollama_client import OllamaError
from abr_import.ocr.engine import OcrEngine, OcrNotAvailableError, OcrPage
from abr_import.ocr.pdf_reader import iter_pdf_pages
from abr_import.pipeline.zip_loader import discover_pdfs, fix_mojibake_unicode
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def document_title_from_path(path: Path) -> str:
    return fix_mojibake_unicode(path.stem)


class ImportProcessor:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        ocr: OcrEngine | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.ocr = ocr or OcrEngine(
            lang=settings.ocr_lang,
            use_gpu=settings.use_gpu,
        )

    def startup_recovery(self) -> None:
        with session_scope(self.session_factory) as session:
            repo = DocumentRepository(session)
            n = repo.reset_stale_processing(self.settings)
            if n:
                logger.warning("Сброшено зависших документов: %d", n)

    def register_all_pdfs(self) -> int:
        pdfs = discover_pdfs(
            self.settings.input_dir,
            filename_encoding=self.settings.zip_filename_encoding,
            force_reextract=self.settings.zip_force_reextract,
        )
        count = 0
        with session_scope(self.session_factory) as session:
            repo = DocumentRepository(session)
            for pdf_path in pdfs:
                fhash = file_sha256(pdf_path)
                repo.register_document(
                    filename=fix_mojibake_unicode(pdf_path.name),
                    title=document_title_from_path(pdf_path),
                    file_hash=fhash,
                    file_size=pdf_path.stat().st_size,
                    source_path=str(pdf_path),
                )
                count += 1
        return count

    def process_pending(self) -> int:
        processed = 0
        with session_scope(self.session_factory) as session:
            repo = DocumentRepository(session)
            pending = repo.list_pending()

        for doc in pending:
            if self._process_document(doc):
                processed += 1
        return processed

    def _extract_lists(self, ocr_pages: list[OcrPage]) -> tuple[list, list]:
        if self.settings.extractor == "llm":
            return extract_with_ollama(
                ocr_pages,
                base_url=self.settings.ollama_base_url,
                model=self.settings.ollama_model,
                pages_per_chunk=self.settings.llm_chunk_pages,
                max_chars_per_chunk=self.settings.llm_max_chars,
                request_timeout=self.settings.llm_timeout_sec,
            )
        spans = find_section_spans(ocr_pages)
        return extract_lists(ocr_pages, spans)

    def _process_document(self, doc: Document) -> bool:
        path = Path(doc.source_path) if doc.source_path else None
        if not path or not path.exists():
            with session_scope(self.session_factory) as session:
                repo = DocumentRepository(session)
                d = session.get(Document, doc.id)
                if d:
                    repo.mark_failed(d, f"Файл не найден: {doc.source_path}")
            return False

        with session_scope(self.session_factory) as session:
            repo = DocumentRepository(session)
            d = session.get(Document, doc.id)
            if not d:
                return False
            if d.status == DocumentStatus.COMPLETED:
                logger.info("Пропуск (уже обработан): %s", d.title)
                return False
            repo.mark_processing(d)
            doc_id = d.id
            title = d.title

        try:
            logger.info(
                "Обработка: %s (OCR cpu=%s, extractor=%s)",
                title,
                not self.settings.use_gpu,
                self.settings.extractor,
            )
            page_count, pages = iter_pdf_pages(
                path,
                dpi=self.settings.pdf_dpi,
                max_pages=self.settings.max_pages_per_doc,
            )
            ocr_pages = self.ocr.recognize_pages(pages)
            self._validate_ocr_pages(ocr_pages, page_count=page_count)

            abbreviations, terms = self._extract_lists(ocr_pages)

            ocr_line_count = sum(len(p.lines) for p in ocr_pages)
            log_msg = (
                f"Готово: сокращений={len(abbreviations)}, терминов={len(terms)}, "
                f"строк OCR={ocr_line_count}, extractor={self.settings.extractor}"
            )

            with session_scope(self.session_factory) as session:
                repo = DocumentRepository(session)
                d = session.get(Document, doc_id)
                if not d:
                    return False
                repo.mark_completed(
                    d,
                    page_count=page_count,
                    abbreviations=abbreviations,
                    terms=terms,
                )
                repo.log(doc_id, log_msg)
                if len(abbreviations) == 0 and len(terms) == 0:
                    repo.log(
                        doc_id,
                        "Перечни не извлечены (пустой результат LLM/правил)",
                        level="warning",
                    )
                self._archive_file(path)

            logger.info(
                "Завершено %s: %d сокращений, %d терминов",
                title,
                len(abbreviations),
                len(terms),
            )
            return True

        except OcrNotAvailableError as exc:
            logger.error("Paddle/OCR недоступен: %s", exc)
            self._fail_document(doc_id, str(exc))
            return False
        except OllamaError as exc:
            logger.error("Ollama: %s", exc)
            self._fail_document(doc_id, str(exc))
            return False
        except Exception as exc:
            logger.exception("Ошибка обработки %s", title)
            self._fail_document(doc_id, str(exc))
            return False

    @staticmethod
    def _validate_ocr_pages(ocr_pages: list[OcrPage], *, page_count: int) -> None:
        line_count = sum(len(p.lines) for p in ocr_pages)
        if line_count == 0:
            raise RuntimeError(
                f"OCR не распознал текст ни на одной из {page_count} страниц. "
                "Проверьте Paddle (CPU) и качество скана."
            )

    def _fail_document(self, doc_id: int, message: str) -> None:
        with session_scope(self.session_factory) as session:
            repo = DocumentRepository(session)
            d = session.get(Document, doc_id)
            if d:
                repo.mark_failed(d, message)
                repo.log(doc_id, message, level="error")

    def _archive_file(self, path: Path) -> None:
        archive = self.settings.archive_dir
        archive.mkdir(parents=True, exist_ok=True)
        dest = archive / path.name
        if dest.exists():
            dest = archive / f"{path.stem}_{file_sha256(path)[:8]}{path.suffix}"
        try:
            shutil.move(str(path), str(dest))
        except OSError as exc:
            logger.warning("Не удалось переместить в архив %s: %s", path, exc)
