BEGIN;
SET search_path = cv_analyzer;

-- Knockout questions defined per job
CREATE TABLE IF NOT EXISTS job_knockout_questions (
    question_id     UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    job_id          UUID        NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL,
    question_text   TEXT        NOT NULL,
    question_type   VARCHAR(20) NOT NULL DEFAULT 'yes_no',   -- yes_no | single_choice | number
    is_required     BOOLEAN     NOT NULL DEFAULT TRUE,
    disqualifying_answer TEXT,          -- legacy column, superseded by passing_criteria
    options         JSONB,              -- for single_choice: ["Option A","Option B",...]
    display_order   INT         NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knockout_questions_job
    ON job_knockout_questions (job_id, display_order);

-- Candidate answers to knockout questions, linked to an application
CREATE TABLE IF NOT EXISTS application_knockout_answers (
    answer_id       UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    application_id  UUID        NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    question_id     UUID        NOT NULL REFERENCES job_knockout_questions(question_id) ON DELETE CASCADE,
    answer_value    TEXT        NOT NULL,
    is_disqualifying BOOLEAN    NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (application_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_knockout_answers_application
    ON application_knockout_answers (application_id);

-- Platform config: max questions allowed per job
-- Column names: type (not value_type), editable (not is_editable)
-- Valid categories: scoring|ai|email|queue|subscription|security|general
INSERT INTO system_config (key, value, type, category, description, editable)
VALUES (
    'max_knockout_questions_per_job',
    '5',
    'number',
    'general',
    'Maximum number of knockout questions allowed per job posting.',
    TRUE
)
ON CONFLICT (key) DO NOTHING;

COMMIT;

