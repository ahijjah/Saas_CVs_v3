-- 067_candidate_interviews.sql
-- Structured interview scheduling and feedback scorecards

CREATE TABLE IF NOT EXISTS candidate_interviews (
    interview_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    job_id          UUID NOT NULL,
    interview_type  VARCHAR(30) NOT NULL
        CHECK (interview_type IN ('phone_screen','technical','hr','panel','final','other')),
    scheduled_at    TIMESTAMPTZ,
    duration_min    INT,
    location        TEXT,
    interviewers    TEXT[] DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled','completed','cancelled','no_show')),
    notes           TEXT,
    created_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interviews_application
    ON candidate_interviews(application_id, scheduled_at ASC);
CREATE INDEX IF NOT EXISTS idx_interviews_tenant
    ON candidate_interviews(tenant_id, scheduled_at ASC);

-- Feedback / scorecard per reviewer per interview
CREATE TABLE IF NOT EXISTS candidate_interview_feedback (
    feedback_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id     UUID NOT NULL REFERENCES candidate_interviews(interview_id) ON DELETE CASCADE,
    application_id   UUID NOT NULL,
    tenant_id        UUID NOT NULL,
    reviewer_id      UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    overall_rating   SMALLINT CHECK (overall_rating BETWEEN 1 AND 5),
    recommendation   VARCHAR(20)
        CHECK (recommendation IN ('strong_yes','yes','neutral','no','strong_no')),
    scorecard        JSONB NOT NULL DEFAULT '{}',
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One feedback row per reviewer per interview
CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_feedback_unique
    ON candidate_interview_feedback(interview_id, reviewer_id);
CREATE INDEX IF NOT EXISTS idx_interview_feedback_interview
    ON candidate_interview_feedback(interview_id);
