-- Add async criteria extraction tracking and structured analysis JSON to job_criteria.
-- Safe to run on existing data: all new columns are nullable except status (has a default).
ALTER TABLE cv_analyzer.job_criteria
    ADD COLUMN IF NOT EXISTS criteria_extraction_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (criteria_extraction_status IN ('pending', 'processing', 'completed', 'failed')),
    ADD COLUMN IF NOT EXISTS criteria_extraction_error  TEXT,
    ADD COLUMN IF NOT EXISTS criteria_extracted_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS analysis_json              JSONB;

-- Mark rows that were already extracted (ai_generated_at is set) as completed.
UPDATE cv_analyzer.job_criteria
SET criteria_extraction_status = 'completed',
    criteria_extracted_at      = ai_generated_at
WHERE ai_generated_at IS NOT NULL
  AND criteria_extraction_status = 'pending';
