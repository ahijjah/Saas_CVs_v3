BEGIN;
SET search_path = cv_analyzer;

-- ── 1. bulk_upload_rows — processing tracking columns ─────────────────────────

ALTER TABLE bulk_upload_rows
    ADD COLUMN IF NOT EXISTS processing_failure_reason TEXT;

COMMENT ON COLUMN bulk_upload_rows.processing_failure_reason
    IS 'Failure reason from the scoring pipeline, populated from applications.stopped_reason '
       '(e.g. extraction_failed, duplicate_rejected, ai_processing_failed, '
       'scoring_failed, unexpected_error). NULL until processing fails.';

-- ── 2. bulk_upload_batches — per-status processing counters ──────────────────

ALTER TABLE bulk_upload_batches
    ADD COLUMN IF NOT EXISTS queued_row_count     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS processing_row_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN bulk_upload_batches.queued_row_count
    IS 'Rows whose Celery scoring task has been enqueued (processing_status=queued).';

COMMENT ON COLUMN bulk_upload_batches.processing_row_count
    IS 'Rows actively being scored by a Celery worker (processing_status=processing).';

COMMIT;
