-- Migration 031: Operational timestamps and job-code-source tracking for email_ingest_log
--
-- Adds five timestamp columns for per-message processing instrumentation and one
-- categorical column to record where the job code was discovered.
-- All columns are nullable so existing rows continue to work unchanged.

ALTER TABLE email_ingest_log
    ADD COLUMN IF NOT EXISTS processing_started_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS processed_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS processing_duration_ms INTEGER,
    ADD COLUMN IF NOT EXISTS scoring_enqueued_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS notification_queued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS job_code_source        VARCHAR(20);

-- Partial index to find messages that started but never completed (stuck detection)
CREATE INDEX IF NOT EXISTS idx_email_ingest_log_stuck
    ON email_ingest_log (processing_started_at)
    WHERE processed_at IS NULL AND processing_started_at IS NOT NULL;

COMMENT ON COLUMN email_ingest_log.processing_started_at  IS 'Timestamp captured at the very start of message processing (before routing)';
COMMENT ON COLUMN email_ingest_log.processed_at           IS 'Timestamp when intake processing finished (success or handled rejection)';
COMMENT ON COLUMN email_ingest_log.processing_duration_ms IS 'Total intake duration in milliseconds (processed_at - processing_started_at)';
COMMENT ON COLUMN email_ingest_log.scoring_enqueued_at    IS 'Timestamp when score_cv_task.delay() was called for the first successful attachment';
COMMENT ON COLUMN email_ingest_log.notification_queued_at IS 'Timestamp when queue_candidate_notification completed';
COMMENT ON COLUMN email_ingest_log.job_code_source        IS 'Where job code was found: subject | text_body | html_body';
