from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from abr_import.config import Settings
from abr_import.db.models import Abbreviation, Document, DocumentStatus, ProcessingLog, Term
from abr_import.extract.parsers import ParsedAbbreviation, ParsedTerm


class DocumentRepository:
    def __init__(self, session: Session):
        self._session = session

    def get_by_hash(self, file_hash: str) -> Document | None:
        return self._session.scalar(
            select(Document).where(Document.file_hash == file_hash)
        )

    def get_by_title(self, title: str) -> Document | None:
        return self._session.scalar(
            select(Document).where(Document.title == title)
        )

    def get_by_filename(self, filename: str) -> Document | None:
        return self._session.scalar(
            select(Document).where(Document.filename == filename)
        )

    def register_document(
        self,
        *,
        filename: str,
        title: str,
        file_hash: str,
        file_size: int,
        source_path: str,
    ) -> Document:
        existing = self.get_by_hash(file_hash)
        if existing:
            existing.filename = filename
            existing.title = title
            existing.source_path = source_path
            existing.file_size = file_size
            return existing

        doc = Document(
            filename=filename,
            title=title,
            file_hash=file_hash,
            file_size=file_size,
            source_path=source_path,
            status=DocumentStatus.PENDING,
        )
        self._session.add(doc)
        self._session.flush()
        return doc

    def should_process(self, doc: Document) -> bool:
        return doc.status in (
            DocumentStatus.PENDING,
            DocumentStatus.FAILED,
            DocumentStatus.PROCESSING,
        )

    def mark_processing(self, doc: Document) -> None:
        doc.status = DocumentStatus.PROCESSING
        doc.error_message = None
        doc.updated_at = datetime.now(timezone.utc)

    def mark_completed(
        self,
        doc: Document,
        *,
        page_count: int,
        abbreviations: list[ParsedAbbreviation],
        terms: list[ParsedTerm],
    ) -> None:
        self._session.execute(
            delete(Abbreviation).where(Abbreviation.document_id == doc.id)
        )
        self._session.execute(delete(Term).where(Term.document_id == doc.id))

        for item in abbreviations:
            self._session.add(
                Abbreviation(
                    document_id=doc.id,
                    short_form=item.short_form,
                    full_form=item.full_form,
                    page_number=item.page_number,
                    source_line=item.source_line,
                )
            )
        for item in terms:
            self._session.add(
                Term(
                    document_id=doc.id,
                    term=item.term,
                    definition=item.definition,
                    page_number=item.page_number,
                    source_line=item.source_line,
                )
            )

        doc.status = DocumentStatus.COMPLETED
        doc.page_count = page_count
        doc.processed_at = datetime.now(timezone.utc)
        doc.error_message = None

    def mark_failed(self, doc: Document, message: str) -> None:
        doc.status = DocumentStatus.FAILED
        doc.error_message = message[:4000]

    def mark_skipped(self, doc: Document, message: str) -> None:
        doc.status = DocumentStatus.SKIPPED
        doc.error_message = message[:4000]

    def log(self, document_id: int | None, message: str, level: str = "info") -> None:
        self._session.add(
            ProcessingLog(document_id=document_id, level=level, message=message)
        )

    def reset_stale_processing(self, settings: Settings) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=settings.stale_processing_minutes
        )
        result = self._session.execute(
            update(Document)
            .where(
                Document.status == DocumentStatus.PROCESSING,
                Document.updated_at < cutoff,
            )
            .values(
                status=DocumentStatus.PENDING,
                error_message="Сброшено: зависшая обработка",
            )
        )
        return result.rowcount or 0

    def list_pending(self) -> list[Document]:
        return list(
            self._session.scalars(
                select(Document)
                .where(
                    Document.status.in_(
                        [
                            DocumentStatus.PENDING,
                            DocumentStatus.FAILED,
                        ]
                    )
                )
                .order_by(Document.created_at)
            )
        )

    def get_lists_by_title(self, title: str) -> tuple[Document | None, list[Abbreviation], list[Term]]:
        doc = self.get_by_title(title)
        if not doc:
            return None, [], []
        abbrs = list(
            self._session.scalars(
                select(Abbreviation)
                .where(Abbreviation.document_id == doc.id)
                .order_by(Abbreviation.id)
            )
        )
        terms = list(
            self._session.scalars(
                select(Term).where(Term.document_id == doc.id).order_by(Term.id)
            )
        )
        return doc, abbrs, terms
