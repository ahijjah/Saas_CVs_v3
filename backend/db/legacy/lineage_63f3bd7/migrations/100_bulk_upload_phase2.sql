-- Migration 100: E-01 Phase 2 — Bulk Upload Processing Columns
--
-- Adds three columns to bulk_upload_batches to support ZIP metadata
-- persistence and column-mapping storage introduced in Phase 2:
--
--   zip_file_metadata    — JSONB list of file records extracted from the ZIP
--   excel_column_mapping — JSONB detected column-to-field mapping
--   unmatched_cv_count   — CVs in ZIP not referenced by any Excel row
--
-- All columns default to null / 0, so existing batch rows are unaffected.
-- Idempotent: ADD COLUMN IF NOT EXISTS guards.

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE bulk_upload_batches
    ADD COLUMN IF NOT EXISTS zip_file_metadata    JSONB,
    ADD COLUMN IF NOT EXISTS excel_column_mapping JSONB,
    ADD COLUMN IF NOT EXISTS unmatched_cv_count   INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN bulk_upload_batches.zip_file_metadata
    IS 'JSONB array of file records from the uploaded ZIP: [{filename, normalized_name, stem, extension, is_supported, size_bytes, zip_path}]. Set when ZIP is uploaded.';

COMMENT ON COLUMN bulk_upload_batches.excel_column_mapping
    IS 'JSONB: detected Excel header-to-field mapping {cv_filename_col, name_col, email_col, phone_col, extra_cols}. Set when Excel is validated.';

COMMENT ON COLUMN bulk_upload_batches.unmatched_cv_count
    IS 'Number of CV files present in the ZIP but not referenced by any Excel row. Set after validation.';

COMMIT;
