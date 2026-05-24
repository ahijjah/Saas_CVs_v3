BEGIN;
SET search_path = cv_analyzer;

-- Extend stopped_reason to include duplicate_blocked.
-- Previously reserved in migration 050 comment as "future phase".
ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_stopped_reason_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_stopped_reason_check
    CHECK (stopped_reason IN (
        'security_blocked',
        'extraction_failed',
        'processing_error',
        'duplicate_blocked',
        'other'
    ));

-- Partial index for fast per-job lookup of duplicate-blocked applications.
CREATE INDEX IF NOT EXISTS idx_applications_duplicate_blocked
    ON applications (tenant_id, job_id)
    WHERE stopped_reason = 'duplicate_blocked';

COMMIT;
