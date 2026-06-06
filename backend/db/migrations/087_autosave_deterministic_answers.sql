-- 087_autosave_deterministic_answers.sql
--
-- High-confidence deterministic extraction answers are now auto-saved as final
-- answers (application_knockout_answers) instead of remaining as pending
-- suggestions that require recruiter review.
--
-- Criteria for auto-save:
--   suggested_source     = 'candidate_email'
--   answer_method        = 'direct_statement'
--   ai_model             = 'deterministic_extractor'
--   verification_status  = 'verified'
--   confidence           >= 0.95
--
-- Schema change:
--   Add evidence_text and confidence to application_knockout_answers so the
--   answer provenance can be shown inline in the UI without joining to the
--   suggestions table.
--
-- Backfill:
--   Promote existing pending deterministic suggestions that meet the criteria
--   to final answers and mark the suggestion as accepted.
--
-- Safe to re-run: all steps are guarded with IF NOT EXISTS / ON CONFLICT DO NOTHING.

BEGIN;
SET search_path TO cv_analyzer;

-- ── Step 1: Add provenance columns to final answers table ─────────────────────
ALTER TABLE application_knockout_answers
    ADD COLUMN IF NOT EXISTS evidence_text TEXT,
    ADD COLUMN IF NOT EXISTS confidence    NUMERIC(4,3);

-- ── Step 2: Backfill — create final answers from pending high-confidence ──────
--   deterministic suggestions where no final answer already exists.
INSERT INTO application_knockout_answers (
    application_id,
    tenant_id,
    question_id,
    answer_value,
    is_disqualifying,
    answer_source,
    answer_method,
    evidence_text,
    confidence,
    updated_at,
    updated_by
)
SELECT
    s.application_id,
    app.tenant_id,
    s.question_id,
    s.suggested_answer,
    FALSE,
    'candidate_email',
    'direct_statement',
    s.evidence_text,
    s.confidence,
    now(),
    NULL
FROM application_knockout_answer_suggestions s
JOIN applications app ON app.application_id = s.application_id
LEFT JOIN application_knockout_answers existing
       ON existing.application_id = s.application_id
      AND existing.question_id    = s.question_id
WHERE s.suggested_source    = 'candidate_email'
  AND s.answer_method       = 'direct_statement'
  AND s.ai_model            = 'deterministic_extractor'
  AND s.verification_status = 'verified'
  AND s.confidence          >= 0.95
  AND s.status              = 'pending'
  AND s.suggested_answer    IS NOT NULL
  AND existing.answer_id    IS NULL    -- no final answer already exists
ON CONFLICT (application_id, question_id) DO NOTHING;

-- ── Step 3: Mark promoted suggestions as accepted ─────────────────────────────
UPDATE application_knockout_answer_suggestions s
SET    status      = 'accepted',
       accepted_at = now()
FROM   application_knockout_answers a
WHERE  s.application_id    = a.application_id
  AND  s.question_id       = a.question_id
  AND  s.suggested_source  = 'candidate_email'
  AND  s.answer_method     = 'direct_statement'
  AND  s.ai_model          = 'deterministic_extractor'
  AND  s.verification_status = 'verified'
  AND  s.confidence        >= 0.95
  AND  s.status            = 'pending'
  AND  a.answer_source     = 'candidate_email';

COMMIT;
