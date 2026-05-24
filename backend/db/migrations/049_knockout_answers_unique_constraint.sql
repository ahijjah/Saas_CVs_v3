BEGIN;
SET search_path = cv_analyzer;

-- Remove duplicate rows before adding the unique index, keeping the latest
-- answer for each (application_id, question_id) pair.
DELETE FROM application_knockout_answers
WHERE answer_id NOT IN (
    SELECT DISTINCT ON (application_id, question_id) answer_id
    FROM application_knockout_answers
    ORDER BY application_id, question_id, created_at DESC
);

-- Add unique index so ON CONFLICT (application_id, question_id) works.
CREATE UNIQUE INDEX IF NOT EXISTS idx_knockout_answers_app_question_unique
    ON application_knockout_answers (application_id, question_id);

COMMIT;
