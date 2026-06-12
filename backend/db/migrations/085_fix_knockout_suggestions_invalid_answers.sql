-- 085_fix_knockout_suggestions_invalid_answers.sql
--
-- Backfill: correct rows in application_knockout_answer_suggestions where the
-- AI returned a non-null suggested_answer alongside verification_status = 'not_found'
-- or confidence = 0 with no evidence_text.  These rows were stored before the backend
-- normalization Rules 3 and 4 were enforced (deployed after migration 084).
--
-- Canonical not_found state:
--   suggested_answer    = NULL
--   suggested_source    = 'not_found'
--   confidence          = 0
--   evidence_text       = NULL
--   verification_status = 'not_found'
--   (answer_method is NOT NULL DEFAULT 'ai_inference' — left as-is)
--
-- Idempotent: safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

-- Case A: vstatus = not_found but answer is present (primary production bug)
UPDATE application_knockout_answer_suggestions
SET suggested_answer    = NULL,
    suggested_source    = 'not_found',
    confidence          = 0.0,
    evidence_text       = NULL,
    verification_status = 'not_found'
WHERE status            = 'pending'
  AND verification_status = 'not_found'
  AND suggested_answer  IS NOT NULL;

-- Case B: confidence = 0 AND no evidence BUT answer is present (AI speculation)
UPDATE application_knockout_answer_suggestions
SET suggested_answer    = NULL,
    suggested_source    = 'not_found',
    confidence          = 0.0,
    evidence_text       = NULL,
    verification_status = 'not_found'
WHERE status            = 'pending'
  AND confidence        = 0
  AND (evidence_text IS NULL OR trim(evidence_text) = '')
  AND suggested_answer  IS NOT NULL;

COMMIT;
