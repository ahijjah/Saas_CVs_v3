-- 007_scoring_batch.sql
-- Adds queued status, scoring_batch_id and queued_at to support atomic
-- claim-before-enqueue and stuck-job recovery.
SET search_path = cv_analyzer;

ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_processing_status_check;
ALTER TABLE applications
    ADD CONSTRAINT applications_processing_status_check
    CHECK (processing_status IN ('pending', 'queued', 'processing', 'scored', 'failed', 'low_match'));

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS scoring_batch_id UUID,
    ADD COLUMN IF NOT EXISTS queued_at        TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_applications_scoring_batch
    ON applications (scoring_batch_id) WHERE scoring_batch_id IS NOT NULL;
