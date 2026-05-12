-- 009_evaluation_stages.sql
-- Track which evaluation level each CV reached and why it was rejected early.
-- evaluation_stage: 1 = local pre-screen, 2 = lightweight AI screen, 3 = full AI scoring
-- evaluation_exit_reason: human-readable reason for Level 1 or Level 2 rejection
SET search_path = cv_analyzer;

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS evaluation_stage       SMALLINT,
    ADD COLUMN IF NOT EXISTS evaluation_exit_reason TEXT;
