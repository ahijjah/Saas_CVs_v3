-- 082_knockout_answer_provenance.sql
--
-- Adds granular provenance tracking to knockout answers and suggestions:
--
-- 1. answer_method column on application_knockout_answers:
--      direct_statement  — candidate explicitly stated the answer
--      ai_inference      — AI reasoned/inferred from available text
--      manual_entry      — recruiter typed it manually
--
-- 2. Migrate legacy answer_source values to the new scheme:
--      candidate_provided  → candidate_form  (direct_statement)
--      ai_extracted        → ai_cv           (ai_inference)
--      ai_suggested_accepted → derived from suggestions table, default ai_cv (ai_inference)
--      recruiter_entered   → recruiter_entered (manual_entry) — unchanged
--
-- 3. answer_method column on application_knockout_answer_suggestions.
--
-- 4. Rename suggested_source values in suggestions table:
--      cv            → ai_cv
--      email_body    → ai_email
--      cv_and_email  → ai_cv_email
--      not_found     — unchanged
--
-- Idempotent: safe to re-run; each step guards with WHERE filters.

BEGIN;
SET search_path = cv_analyzer;

-- ── Step 1: Add answer_method to application_knockout_answers ─────────────────
ALTER TABLE application_knockout_answers
    ADD COLUMN IF NOT EXISTS answer_method VARCHAR(30) NOT NULL DEFAULT 'direct_statement';

-- ── Step 2: Backfill answer_method for existing rows ──────────────────────────
-- recruiter_entered → manual_entry
UPDATE application_knockout_answers
SET answer_method = 'manual_entry'
WHERE answer_source = 'recruiter_entered'
  AND answer_method = 'direct_statement';

-- ai_extracted → ai_inference
UPDATE application_knockout_answers
SET answer_method = 'ai_inference'
WHERE answer_source IN ('ai_extracted', 'ai_suggested_accepted')
  AND answer_method = 'direct_statement';

-- ── Step 3: Migrate legacy answer_source values ───────────────────────────────
-- candidate_provided → candidate_form
UPDATE application_knockout_answers
SET answer_source = 'candidate_form'
WHERE answer_source = 'candidate_provided';

-- ai_extracted → ai_cv
UPDATE application_knockout_answers
SET answer_source = 'ai_cv'
WHERE answer_source = 'ai_extracted';

-- ai_suggested_accepted: derive new source from the suggestions table
-- Join to get the suggested_source, map old suggestion source names too.
UPDATE application_knockout_answers a
SET answer_source = CASE
    WHEN s.suggested_source IN ('cv', 'ai_cv')              THEN 'ai_cv'
    WHEN s.suggested_source IN ('email_body', 'ai_email')   THEN 'ai_email'
    WHEN s.suggested_source IN ('cv_and_email', 'ai_cv_email') THEN 'ai_cv_email'
    ELSE 'ai_cv'
END
FROM application_knockout_answer_suggestions s
WHERE a.answer_source = 'ai_suggested_accepted'
  AND s.application_id = a.application_id
  AND s.question_id    = a.question_id;

-- Rows where the suggestion was deleted or not found — default to ai_cv
UPDATE application_knockout_answers
SET answer_source = 'ai_cv'
WHERE answer_source = 'ai_suggested_accepted';

-- ── Step 4: Add answer_method to application_knockout_answer_suggestions ──────
ALTER TABLE application_knockout_answer_suggestions
    ADD COLUMN IF NOT EXISTS answer_method VARCHAR(30) NOT NULL DEFAULT 'ai_inference';

-- ── Step 5: Rename suggested_source values in suggestions table ───────────────
UPDATE application_knockout_answer_suggestions
SET suggested_source = 'ai_cv'
WHERE suggested_source = 'cv';

UPDATE application_knockout_answer_suggestions
SET suggested_source = 'ai_email'
WHERE suggested_source = 'email_body';

UPDATE application_knockout_answer_suggestions
SET suggested_source = 'ai_cv_email'
WHERE suggested_source = 'cv_and_email';

COMMIT;
