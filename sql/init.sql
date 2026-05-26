-- Схема для импорта перечней из PDF-документов

CREATE TYPE document_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed',
    'skipped'
);

CREATE TABLE documents (
    id              BIGSERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    title           TEXT NOT NULL,
    file_hash       CHAR(64) NOT NULL,
    file_size       BIGINT NOT NULL,
    page_count      INTEGER,
    status          document_status NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    source_path     TEXT,
    processed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_documents_file_hash UNIQUE (file_hash)
);

CREATE INDEX idx_documents_title ON documents (title);
CREATE INDEX idx_documents_status ON documents (status);
CREATE INDEX idx_documents_filename ON documents (filename);

CREATE TABLE abbreviations (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    short_form      TEXT NOT NULL,
    full_form       TEXT NOT NULL,
    page_number     INTEGER,
    source_line     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_abbreviations_document ON abbreviations (document_id);
CREATE INDEX idx_abbreviations_short ON abbreviations (short_form);

CREATE TABLE terms (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    term            TEXT NOT NULL,
    definition      TEXT NOT NULL,
    page_number     INTEGER,
    source_line     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_terms_document ON terms (document_id);
CREATE INDEX idx_terms_term ON terms (term);

CREATE TABLE processing_log (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT REFERENCES documents (id) ON DELETE SET NULL,
    level           TEXT NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_processing_log_document ON processing_log (document_id);
