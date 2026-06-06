-- 089_recruiter_approved_method.sql
--
-- Corrects provenance for knockout answers that were accepted from AI suggestions
-- but stored with answer_method = 'ai_inference' instead of 'recruiter_approved'.
--
-- Rules applied:
--   • answer_source IN ('ai_cv', 'ai_email', 'ai_cv_email')
--   • answer_method = 'ai_inference'
--   • a matching accepted suggestion exists for the same application + question
--   • answer_value equals the suggestion's suggested_answer (value was not edited)
--   → update answer_method to 'recruiter_approved'
--
-- Records where the recruiter edited the value (answer_value differs from
-- suggested_answer) are left unchanged as 'manual_entry' (already correct).
--
-- Idempotent: safe to re-run; the WHERE clause only targets rows that still
-- have the old value.

BEGIN;
SET search_path = cv_analyzer;

UPDATE application_knockout_answers a
SET    answer_method = 'recruiter_approved',
       updated_at   = now()
FROM   application_knockout_answer_suggestions s
WHERE  s.application_id = a.application_id
  AND  s.question_id    = a.question_id
  AND  s.status         = 'accepted'
  AND  a.answer_source  IN ('ai_cv', 'ai_email', 'ai_cv_email')
  AND  a.answer_method  = 'ai_inference'
  AND  a.answer_value   = s.suggested_answer;

COMMIT;
