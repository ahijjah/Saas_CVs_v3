-- 086_fix_knockout_answer_method_not_found.sql
--
-- Backfill: set answer_method = 'not_found' for suggestion rows where
-- suggested_source='not_found' AND verification_status='not_found'.
-- These rows were created before _force_not_found() was updated to set
-- method='not_found' (previously defaulted to 'ai_inference' from column DEFAULT).
--
-- Idempotent: safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

UPDATE application_knockout_answer_suggestions
SET answer_method = 'not_found'
WHERE suggested_source    = 'not_found'
  AND verification_status = 'not_found';

COMMIT;
