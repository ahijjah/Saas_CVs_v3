-- 078_knockout_answer_audit.sql
-- Add audit/source tracking columns to application_knockout_answers.
-- answer_source distinguishes who/how the answer was recorded.
-- updated_by and updated_at track manual recruiter edits.

ALTER TABLE application_knockout_answers
    ADD COLUMN IF NOT EXISTS answer_source VARCHAR(30) NOT NULL DEFAULT 'candidate_provided',
    ADD COLUMN IF NOT EXISTS updated_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ;
