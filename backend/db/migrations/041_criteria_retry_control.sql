BEGIN;
SET search_path TO cv_analyzer;

-- Retry tracking columns on job_criteria
ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS criteria_extraction_retry_count        INTEGER      NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS criteria_last_failed_description_hash  TEXT         NULL,
    ADD COLUMN IF NOT EXISTS criteria_last_failed_at                TIMESTAMPTZ  NULL;

-- System-wide AI retry limit (editable by Super Admin via platform-config UI)
INSERT INTO system_config (key, value, type, category, description, editable)
VALUES (
    'criteria_extraction_max_retries',
    '3',
    'number',
    'ai',
    'Maximum number of user-initiated AI criteria extraction retries allowed per job. Each successful retry dispatch counts toward this limit.',
    true
)
ON CONFLICT (key) DO NOTHING;

COMMIT;
