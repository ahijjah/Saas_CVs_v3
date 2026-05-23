-- Migration 042: Exact content duplicate detection
--
-- Adds normalized_text_hash to application_files so the scoring pipeline
-- can detect identical CVs submitted in different formats (PDF vs DOCX,
-- re-exports, renamed files) without fuzzy scanning.
--
-- Also expands the duplicate_status CHECK on applications to include the
-- new 'exact_duplicate' state, which is set when a normalized_text_hash
-- collision is found during pre-screening (before gatekeeper / AI scoring).

BEGIN;

SET search_path = cv_analyzer;

-- 1. Add normalized content fingerprint column ─────────────────────────────

ALTER TABLE application_files
    ADD COLUMN IF NOT EXISTS normalized_text_hash VARCHAR(64);

-- Index enables O(1) lookup during scoring-pipeline duplicate check.
-- Partial index: only rows that have been hashed (NULL excluded).
CREATE INDEX IF NOT EXISTS idx_app_files_norm_hash
    ON application_files (normalized_text_hash)
    WHERE normalized_text_hash IS NOT NULL;

-- 2. Expand duplicate_status CHECK ────────────────────────────────────────
--
-- Migration 018 added the column with an inline (unnamed) CHECK.
-- PostgreSQL auto-names inline column-level CHECK constraints as
-- <table>_<column>_check. Drop by that name and replace with an
-- explicit named constraint so future migrations can refer to it.
--
-- IF NOT EXISTS is not available for DROP CONSTRAINT — use DO block.

DO $$
BEGIN
    -- Drop the old 2-value check if it exists under either possible name
    ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_duplicate_status_check;
EXCEPTION WHEN undefined_object THEN NULL;
END$$;

ALTER TABLE applications
    ADD CONSTRAINT applications_duplicate_status_check
    CHECK (duplicate_status IN ('not_duplicate', 'possible_duplicate', 'exact_duplicate'));

-- Partial index for quick recruiter queries on exact duplicates
CREATE INDEX IF NOT EXISTS idx_applications_exact_dup
    ON applications (tenant_id, job_id, duplicate_status)
    WHERE duplicate_status = 'exact_duplicate';

-- 3. Backfill note ─────────────────────────────────────────────────────────
-- Existing application_files rows will have normalized_text_hash = NULL.
-- The scoring pipeline will populate it on the next scoring run for new
-- uploads.  Existing applications are unaffected (they will remain
-- duplicate_status = 'not_duplicate' until re-scored).

COMMIT;
