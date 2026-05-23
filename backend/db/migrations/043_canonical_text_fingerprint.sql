-- Migration 043: Canonical cross-format text fingerprint + duplicate log enhancements
--
-- Adds canonical_text_fingerprint to application_files for robust cross-format
-- duplicate detection (PDF vs DOCX exports of the same CV).  Unlike
-- normalized_text_hash which preserves token order, the canonical fingerprint
-- sorts and deduplicates tokens so extraction-order differences between parsers
-- produce the same hash.
--
-- Also extends duplicate_application_logs with:
--   duplicate_application_id — audit trail: the application_id that was detected
--     as a duplicate (before the transient record is deleted from applications)
-- And relaxes the NOT NULL constraint on duplicate_email so scoring-pipeline
-- duplicate handling works even when candidate_email is not yet known.

BEGIN;

SET search_path = cv_analyzer;

-- 1. Canonical cross-format fingerprint column ─────────────────────────────

ALTER TABLE application_files
    ADD COLUMN IF NOT EXISTS canonical_text_fingerprint VARCHAR(64);

-- Partial index: O(1) per-job duplicate lookup; NULL rows excluded.
CREATE INDEX IF NOT EXISTS idx_app_files_canonical_fp
    ON application_files (canonical_text_fingerprint)
    WHERE canonical_text_fingerprint IS NOT NULL;

-- 2. Duplicate log: audit-trail column ────────────────────────────────────
-- Stores the application_id of the duplicate entry that was detected and
-- subsequently deleted from the applications table.

ALTER TABLE duplicate_application_logs
    ADD COLUMN IF NOT EXISTS duplicate_application_id UUID;

-- 3. Relax NOT NULL on duplicate_email ────────────────────────────────────
-- The scoring pipeline may detect a duplicate before candidate_email has been
-- extracted from the CV (email is populated by AI scoring, which is skipped
-- for duplicates).

ALTER TABLE duplicate_application_logs
    ALTER COLUMN duplicate_email DROP NOT NULL;

COMMIT;
