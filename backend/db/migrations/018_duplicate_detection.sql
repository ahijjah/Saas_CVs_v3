-- Migration 018: CV duplicate detection fields on applications
-- Adds 5 columns to track intra-job CV duplication without blocking submissions.

BEGIN;

SET search_path = cv_analyzer;

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS duplicate_status
        VARCHAR(30) NOT NULL DEFAULT 'not_duplicate'
        CHECK (duplicate_status IN ('not_duplicate', 'possible_duplicate')),
    ADD COLUMN IF NOT EXISTS duplicate_reference_application_id
        UUID REFERENCES applications (application_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS duplicate_similarity_score
        NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS duplicate_reason
        TEXT,
    ADD COLUMN IF NOT EXISTS duplicate_checked_at
        TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_applications_dup_status
    ON applications (tenant_id, job_id, duplicate_status)
    WHERE duplicate_status = 'possible_duplicate';

COMMIT;
