-- 084_fix_knockout_provenance_not_found.sql
--
-- Backfill: correct rows in application_knockout_answer_suggestions where
-- suggested_source = 'not_found' coexists with a real answer, evidence, or a
-- meaningful verification_status.  These rows were created before the server-side
-- normalization rules were deployed (migration 082 added the columns; the
-- normalization logic was added later and does not retroactively fix old rows).
--
-- Rules applied (mirror the Python normalization in knockout_analysis_service.py):
--   Rule 1: not_found + (suggested_answer IS NOT NULL OR evidence_text IS NOT NULL)
--           → correct source to ai_email if evidence mentions "email", else ai_cv
--   Rule 2: not_found + verification_status IN (verified, inferred, contradiction)
--           → same correction
--   Rule 3: not_found with truly no answer, no evidence, and no meaningful vstatus
--           → enforce: confidence=0, verification_status='not_found' (clean up noise)
--
-- Idempotent: running twice produces the same result.

BEGIN;
SET search_path = cv_analyzer;

-- Rule 1 + 2: source is not_found but there IS real content — infer correct source
UPDATE application_knockout_answer_suggestions
SET suggested_source = CASE
        WHEN evidence_text ILIKE '%email%' THEN 'ai_email'
        ELSE 'ai_cv'
    END,
    updated_at = now()
WHERE suggested_source = 'not_found'
  AND (
      suggested_answer IS NOT NULL
      OR evidence_text IS NOT NULL
      OR verification_status IN ('verified', 'inferred', 'contradiction')
  );

-- Rule 3: true not_found rows — ensure all derived fields are consistently null/zero
UPDATE application_knockout_answer_suggestions
SET suggested_answer    = NULL,
    evidence_text       = NULL,
    confidence          = 0.0,
    verification_status = 'not_found',
    updated_at          = now()
WHERE suggested_source = 'not_found'
  AND (
      suggested_answer    IS NOT NULL
      OR evidence_text    IS NOT NULL
      OR confidence       > 0
      OR verification_status != 'not_found'
  );

COMMIT;
