-- Migration 079: AI-suggested answers for knockout questions
--
-- Stores AI-generated answer suggestions separately from final answers.
-- Final answers in application_knockout_answers are NEVER overwritten automatically.
-- Users review suggestions and choose to accept, edit, or ignore them.

CREATE TABLE IF NOT EXISTS application_knockout_answer_suggestions (
    suggestion_id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID         NOT NULL REFERENCES applications(application_id)  ON DELETE CASCADE,
    tenant_id           UUID         NOT NULL,
    question_id         UUID         NOT NULL REFERENCES job_knockout_questions(question_id) ON DELETE CASCADE,

    -- AI-generated suggestion (null when suggested_source='not_found')
    suggested_answer    TEXT,

    -- Where the evidence was found
    suggested_source    VARCHAR(30)  NOT NULL DEFAULT 'not_found',
    -- 'cv' | 'email_body' | 'cv_and_email' | 'not_found'

    -- AI confidence score 0.00–1.00
    confidence          NUMERIC(3,2) NOT NULL DEFAULT 0.00,

    -- Quoted evidence text from the source document (max ~300 chars)
    evidence_text       TEXT,

    -- How strongly the evidence supports the answer
    verification_status VARCHAR(30)  NOT NULL DEFAULT 'not_found',
    -- 'verified' | 'inferred' | 'no_evidence' | 'contradiction' | 'not_found'

    -- AI provenance
    ai_model            VARCHAR(100),
    prompt_code         VARCHAR(100),
    prompt_version      INTEGER,

    -- User action on this suggestion
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
    -- 'pending' | 'accepted' | 'ignored'
    accepted_by         UUID         REFERENCES users(user_id) ON DELETE SET NULL,
    accepted_at         TIMESTAMPTZ,

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- One active suggestion per question per application.
    -- Re-running knockout analysis replaces the previous suggestion via ON CONFLICT.
    CONSTRAINT uq_ko_suggestion UNIQUE (application_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_ko_suggestions_app
    ON application_knockout_answer_suggestions (application_id);

CREATE INDEX IF NOT EXISTS idx_ko_suggestions_tenant
    ON application_knockout_answer_suggestions (tenant_id);

-- Row-Level Security (mirrors the pattern from application_knockout_answers)
ALTER TABLE application_knockout_answer_suggestions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ko_suggestions_tenant_isolation ON application_knockout_answer_suggestions;
CREATE POLICY ko_suggestions_tenant_isolation ON application_knockout_answer_suggestions
    USING (
        current_setting('app.current_role',      true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );
