BEGIN;
SET search_path = cv_analyzer;

-- Add passing_criteria JSONB to job_knockout_questions.
-- Replaces the disqualifying_answer approach with a qualification-focused model.
--
-- Structure by question_type:
--   yes_no:        {"passing_answers": ["yes"]}  or  ["no"]  or  ["yes","no"]
--   single_choice: {"passing_answers": ["Option A", "Option B"]}
--   number:        {"operator": ">=", "value": 3}
--
-- disqualifying_answer column is kept for backward compatibility; new rows
-- use passing_criteria only.

ALTER TABLE job_knockout_questions
    ADD COLUMN IF NOT EXISTS passing_criteria JSONB;

COMMIT;
