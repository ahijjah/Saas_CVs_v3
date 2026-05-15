-- Migration 019: Store duplicate CV files in duplicate_application_logs

BEGIN;

ALTER TABLE duplicate_application_logs
    ADD COLUMN IF NOT EXISTS duplicate_file_path      TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_original_filename TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_content_type   TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_file_size_bytes INTEGER;

COMMIT;
