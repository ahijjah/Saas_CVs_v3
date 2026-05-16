-- Store the original AI-generated criteria so user edits can be compared.
BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS original_analysis_json JSONB;

-- Backfill: for all jobs that already have AI-extracted criteria,
-- treat the current analysis_json as the original baseline.
UPDATE job_criteria
    SET original_analysis_json = analysis_json
WHERE original_analysis_json IS NULL
  AND analysis_json IS NOT NULL;

COMMIT;
