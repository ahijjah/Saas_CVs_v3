-- 012_scoring_ingestion_audit_enhancements.sql
--
-- Implements 11 platform enhancements:
--  1. Remove low_match — replaced by evaluation_stage=1 + gatekeeper_passed=false
--  2. audit_logs table for Super Admin audit trail
--  3. Ingestion mode as booleans (per-job + per-tenant)
--  4. Candidate contact fields on applications
--  5. Per-job confirmation email toggle settings
--  6. Standardise submission_source values
--  8. score_details JSONB on application_scores
--  9. final_score promoted to INTEGER (ceiling rounding)
-- 10. AI comparison mode: application_score_comparisons + job toggle
-- 11. Prompt version references on application_scores
--
-- Run: psql -h localhost -U cv_app cv_analyzer_prod -c "SET search_path = cv_analyzer" < 012_scoring_ingestion_audit_enhancements.sql

BEGIN;

SET search_path = cv_analyzer;

-- ════════════════════════════════════════════════════════════════════
-- 1. Remove low_match from processing_status and decision
-- ════════════════════════════════════════════════════════════════════

-- Migrate existing low_match rows before constraint change
UPDATE applications
SET processing_status = 'scored',
    decision          = 'rejected'
WHERE processing_status = 'low_match';

ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_processing_status_check;
ALTER TABLE applications ADD CONSTRAINT applications_processing_status_check
    CHECK (processing_status IN ('pending', 'processing', 'scored', 'failed'));

ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_decision_check;
ALTER TABLE applications ADD CONSTRAINT applications_decision_check
    CHECK (decision IN ('qualified', 'partial', 'rejected'));

-- ════════════════════════════════════════════════════════════════════
-- 2. audit_logs table
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID         REFERENCES tenants (tenant_id) ON DELETE SET NULL,
    user_id       UUID         REFERENCES users (user_id) ON DELETE SET NULL,
    user_email    VARCHAR(255),
    action        VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id   TEXT,
    details       JSONB,
    ip_address    VARCHAR(50),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant  ON audit_logs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user    ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action  ON audit_logs (action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at DESC);

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT ON audit_logs TO cv_app;

CREATE POLICY audit_logs_isolation ON audit_logs
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

-- ════════════════════════════════════════════════════════════════════
-- 3. Ingestion mode booleans
-- ════════════════════════════════════════════════════════════════════

-- Per-job ingestion booleans (replace forwarding_enabled / alias_enabled semantics)
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS receive_cv_via_forwarding_email            BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS receive_cv_via_platform_email              BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS restrict_forwarding_sender_to_tenant_email BOOLEAN NOT NULL DEFAULT false;

-- Populate from the existing forwarding_enabled / alias_enabled columns
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'cv_analyzer'
          AND table_name   = 'jobs'
          AND column_name  = 'forwarding_enabled'
    ) THEN
        UPDATE jobs SET
            receive_cv_via_forwarding_email = COALESCE(forwarding_enabled, false),
            receive_cv_via_platform_email   = COALESCE(alias_enabled, true);
    END IF;
END$$;

-- Per-tenant ingestion booleans
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS receive_cv_via_forwarding_email            BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS receive_cv_via_platform_email              BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS restrict_forwarding_sender_to_tenant_email BOOLEAN NOT NULL DEFAULT false;

UPDATE tenants SET
    receive_cv_via_forwarding_email = (cv_ingestion_mode = 'forwarding'),
    receive_cv_via_platform_email   = (cv_ingestion_mode = 'platform_email');

-- ════════════════════════════════════════════════════════════════════
-- 4. Candidate contact fields on applications
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS candidate_email_from_cv      VARCHAR(255),
    ADD COLUMN IF NOT EXISTS candidate_phone_from_cv      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS email_sender_address         VARCHAR(255),
    ADD COLUMN IF NOT EXISTS confirmation_email_recipient VARCHAR(255);

-- ════════════════════════════════════════════════════════════════════
-- 5. Per-job confirmation email toggle settings
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS send_confirmation_on_receipt   BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS send_confirmation_on_qualified BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS send_confirmation_on_rejected  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS send_confirmation_on_partial   BOOLEAN NOT NULL DEFAULT false;

-- ════════════════════════════════════════════════════════════════════
-- 6. Standardise submission_source values
-- ════════════════════════════════════════════════════════════════════

UPDATE applications SET submission_source = 'email_forwarding' WHERE submission_source = 'email';
UPDATE applications SET submission_source = 'manual_upload'    WHERE submission_source = 'api';

ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_submission_source_check;
ALTER TABLE applications ADD CONSTRAINT applications_submission_source_check
    CHECK (submission_source IN ('manual_upload', 'email_forwarding', 'platform_email'));

-- ════════════════════════════════════════════════════════════════════
-- 8. score_details JSONB on application_scores
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS score_details JSONB;

-- ════════════════════════════════════════════════════════════════════
-- 9. final_score → INTEGER (ceiling rounding applied on existing rows)
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE application_scores
    ALTER COLUMN final_score TYPE SMALLINT USING CEIL(final_score)::SMALLINT;

-- ════════════════════════════════════════════════════════════════════
-- 10. AI comparison mode
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS enable_ai_comparison BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS application_score_comparisons (
    comparison_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id       UUID        NOT NULL REFERENCES applications (application_id) ON DELETE CASCADE,
    provider             VARCHAR(50)  NOT NULL,
    model                VARCHAR(100) NOT NULL,
    final_score          SMALLINT    NOT NULL,
    score_skills         SMALLINT,
    score_experience     SMALLINT,
    score_education      SMALLINT,
    score_certifications SMALLINT,
    score_soft_skills    SMALLINT,
    score_domain_knowledge SMALLINT,
    score_other          SMALLINT,
    score_details        JSONB,
    weights_snapshot     JSONB        NOT NULL,
    evaluation_notes     TEXT,
    strengths            TEXT[]       NOT NULL DEFAULT '{}',
    gaps_identified      TEXT[]       NOT NULL DEFAULT '{}',
    scoring_prompt_code  VARCHAR(100),
    scoring_prompt_version INT,
    raw_response         JSONB,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_score_comparisons_app ON application_score_comparisons (application_id);

ALTER TABLE application_score_comparisons ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT ON application_score_comparisons TO cv_app;

CREATE POLICY score_comparisons_isolation ON application_score_comparisons
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR application_id IN (
            SELECT application_id FROM applications
            WHERE tenant_id::text = current_setting('app.current_tenant_id', true)
        )
    );

-- ════════════════════════════════════════════════════════════════════
-- 11. Prompt version references on application_scores
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS criteria_prompt_code    VARCHAR(100),
    ADD COLUMN IF NOT EXISTS criteria_prompt_version INTEGER,
    ADD COLUMN IF NOT EXISTS level2_prompt_code      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS level2_prompt_version   INTEGER,
    ADD COLUMN IF NOT EXISTS scoring_prompt_code     VARCHAR(100),
    ADD COLUMN IF NOT EXISTS scoring_prompt_version  INTEGER,
    ADD COLUMN IF NOT EXISTS scoring_provider        VARCHAR(50) NOT NULL DEFAULT 'openai';

COMMIT;
