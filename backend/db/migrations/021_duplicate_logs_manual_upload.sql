-- Migration 021: Extend duplicate_application_logs to support manual upload duplicates

BEGIN;

-- attachment_hash was required for email hash-match; manual uploads don't have one
ALTER TABLE duplicate_application_logs
    ALTER COLUMN attachment_hash DROP NOT NULL;

ALTER TABLE duplicate_application_logs
    ADD COLUMN IF NOT EXISTS source                     TEXT,
    ADD COLUMN IF NOT EXISTS submitted_by_user_id       UUID,
    ADD COLUMN IF NOT EXISTS submitted_by_name          TEXT,
    ADD COLUMN IF NOT EXISTS submitted_by_email         TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_reason           TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_similarity_score NUMERIC(5,2);

-- Back-fill source for existing email-based records
UPDATE duplicate_application_logs
SET source = 'email'
WHERE source IS NULL;

COMMIT;
