from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    input_dir: Path
    archive_dir: Path
    ocr_lang: str
    use_gpu: bool
    ocr_batch_size: int
    pdf_dpi: int
    max_pages_per_doc: int | None
    stale_processing_minutes: int
    poll_interval_sec: int
    zip_filename_encoding: str
    zip_force_reextract: bool
    # llm | rules — способ извлечения перечней
    extractor: str
    ollama_base_url: str
    ollama_model: str
    llm_chunk_pages: int
    llm_max_chars: int
    llm_timeout_sec: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://abr:abr@localhost:5432/abr_import",
            ),
            input_dir=Path(os.environ.get("INPUT_DIR", "/data/input")),
            archive_dir=Path(os.environ.get("ARCHIVE_DIR", "/data/archive")),
            ocr_lang=os.environ.get("OCR_LANG", "ru"),
            use_gpu=os.environ.get("USE_GPU", "false").lower() in ("1", "true", "yes"),
            ocr_batch_size=int(os.environ.get("OCR_BATCH_SIZE", "1")),
            pdf_dpi=int(os.environ.get("PDF_DPI", "200")),
            max_pages_per_doc=(
                int(v) if (v := os.environ.get("MAX_PAGES_PER_DOC")) else None
            ),
            stale_processing_minutes=int(
                os.environ.get("STALE_PROCESSING_MINUTES", "120")
            ),
            poll_interval_sec=int(os.environ.get("POLL_INTERVAL_SEC", "10")),
            zip_filename_encoding=os.environ.get(
                "ZIP_FILENAME_ENCODING", "auto"
            ).strip().lower(),
            zip_force_reextract=os.environ.get(
                "ZIP_FORCE_REEXTRACT", "false"
            ).lower() in ("1", "true", "yes"),
            extractor=os.environ.get("EXTRACTOR", "llm").strip().lower(),
            ollama_base_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
            ollama_model=os.environ.get(
                "OLLAMA_MODEL", "llama3.1:8b"
            ),
            llm_chunk_pages=int(os.environ.get("LLM_CHUNK_PAGES", "4")),
            llm_max_chars=int(os.environ.get("LLM_MAX_CHARS", "12000")),
            llm_timeout_sec=int(os.environ.get("LLM_TIMEOUT_SEC", "600")),
        )
