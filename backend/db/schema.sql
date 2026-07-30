-- CV Analyzer SaaS — Clean Schema Rebuild
-- Schema: cv_analyzer
-- Compatible with PostgreSQL 16

BEGIN;

CREATE SCHEMA IF NOT EXISTS cv_analyzer;
SET search_path = cv_analyzer;

-- Required extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- bcrypt + gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-------------------------------------------------------------------------------
-- system_config
-- Global key-value store for system-wide defaults
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT        NOT NULL,
    type        VARCHAR(20)  NOT NULL DEFAULT 'string'
                    CHECK (type IN ('string', 'number', 'boolean', 'json')),
    category    VARCHAR(50)  NOT NULL DEFAULT 'general'
                    CHECK (category IN ('scoring', 'ai', 'email', 'queue', 'subscription', 'security', 'general')),
    editable    BOOLEAN      NOT NULL DEFAULT true,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  UUID        REFERENCES users (user_id) ON DELETE SET NULL
);

-- Seed defaults (applied via seed.sql; schema only defines structure)

-------------------------------------------------------------------------------
-- subscription_plans
-- Platform-level plan catalogue (no RLS — visible to super_admin only via app)
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_plans (
    plan_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_code       VARCHAR(50)  NOT NULL,
    plan_name       VARCHAR(100) NOT NULL,
    description     TEXT,
    monthly_price   NUMERIC(10,2) NOT NULL DEFAULT 0,
    yearly_price    NUMERIC(10,2) NOT NULL DEFAULT 0,
    currency        VARCHAR(10)   NOT NULL DEFAULT 'USD',
    trial_days      INT           NOT NULL DEFAULT 14,
    max_campaigns   INT           NOT NULL DEFAULT 5,
    max_processed_cvs_per_month INT NOT NULL DEFAULT 500,
    max_users       INT           NOT NULL DEFAULT 3,
    api_access          BOOLEAN   NOT NULL DEFAULT false,
    advanced_analytics  BOOLEAN   NOT NULL DEFAULT false,
    priority_support    BOOLEAN   NOT NULL DEFAULT false,
    custom_ai_prompts   BOOLEAN   NOT NULL DEFAULT false,
    status          VARCHAR(20)   NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive')),
    display_order   INT           NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_by      UUID          REFERENCES users (user_id) ON DELETE SET NULL,
    CONSTRAINT uq_subscription_plan_code UNIQUE (plan_code)
);

-------------------------------------------------------------------------------
-- tenants
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    email_domain VARCHAR(253) NOT NULL,    -- e.g. "company.com"; platform_email = {job_id}@{email_domain}
    plan        VARCHAR(50)  NOT NULL DEFAULT 'starter',  -- references subscription_plans.plan_code
    max_users   INT          NOT NULL DEFAULT 3,
    max_jobs    INT          NOT NULL DEFAULT 10,
    cv_ingestion_mode VARCHAR(20) NOT NULL DEFAULT 'platform_email'
                    CHECK (cv_ingestion_mode IN ('platform_email', 'forwarding')),
    forwarding_email VARCHAR(255),          -- set when cv_ingestion_mode = 'forwarding'
    -- Threshold overrides — NULL means "use system default"
    qualified_threshold SMALLINT CHECK (qualified_threshold BETWEEN 0 AND 100),
    partial_threshold   SMALLINT CHECK (partial_threshold   BETWEEN 0 AND 100),
    status      VARCHAR(20)  NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended')),
    -- Subscription tracking
    subscription_status  VARCHAR(20) DEFAULT 'trial'
                    CHECK (subscription_status IN ('trial', 'active', 'suspended', 'expired')),
    trial_start_at        TIMESTAMPTZ,
    trial_end_at          TIMESTAMPTZ,
    subscription_started_at TIMESTAMPTZ,
    subscription_ends_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status);
CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status ON tenants (subscription_status);

-------------------------------------------------------------------------------
-- users
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL,
    password_hash   TEXT        NOT NULL,   -- bcrypt via pgcrypto crypt(pw, gen_salt('bf', 10))
    full_name       VARCHAR(255) NOT NULL,
    role            VARCHAR(30)  NOT NULL DEFAULT 'admin'
                        CHECK (role IN ('super_admin', 'admin', 'recruiter', 'viewer')),
    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'disabled')),
    reset_token_hash    TEXT,               -- sha256 hex of reset token
    reset_token_expires TIMESTAMPTZ,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email  ON users (email);

-------------------------------------------------------------------------------
-- user_invites
-- Pending invites for multi-user onboarding
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_invites (
    invite_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    invited_by      UUID        NOT NULL REFERENCES users (user_id),
    email           VARCHAR(255) NOT NULL,
    role            VARCHAR(30)  NOT NULL DEFAULT 'recruiter'
                        CHECK (role IN ('admin', 'recruiter', 'viewer')),
    token_hash      TEXT        NOT NULL,   -- sha256 hex
    expires_at      TIMESTAMPTZ  NOT NULL,
    accepted_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invites_tenant ON user_invites (tenant_id);
CREATE INDEX IF NOT EXISTS idx_invites_token  ON user_invites (token_hash);

-------------------------------------------------------------------------------
-- jobs
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    job_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    created_by      UUID        NOT NULL REFERENCES users (user_id),
    title           VARCHAR(255) NOT NULL,
    department      VARCHAR(255),
    location        VARCHAR(255),
    job_type        VARCHAR(100),
    duration        VARCHAR(100),
    description     TEXT        NOT NULL,   -- raw job description text
    platform_email  VARCHAR(255),           -- {job_id}@{tenant.email_domain}; set after insert
    status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'closed', 'draft')),
    -- Per-job threshold overrides — NULL means "use tenant or system default"
    qualified_threshold SMALLINT CHECK (qualified_threshold BETWEEN 0 AND 100),
    partial_threshold   SMALLINT CHECK (partial_threshold   BETWEEN 0 AND 100),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

-------------------------------------------------------------------------------
-- job_criteria
-- AI-generated scoring criteria, editable after generation
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_criteria (
    criteria_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID        NOT NULL UNIQUE REFERENCES jobs (job_id) ON DELETE CASCADE,
    -- 7 scoring dimensions (arrays of criteria strings, editable)
    skills                  TEXT[]  NOT NULL DEFAULT '{}',
    experience              TEXT[]  NOT NULL DEFAULT '{}',
    education               TEXT[]  NOT NULL DEFAULT '{}',
    certifications          TEXT[]  NOT NULL DEFAULT '{}',
    soft_skills             TEXT[]  NOT NULL DEFAULT '{}',
    domain_knowledge        TEXT[]  NOT NULL DEFAULT '{}',
    other_requirements      TEXT[]  NOT NULL DEFAULT '{}',
    -- Weights (integers, must sum to 100)
    weight_skills           SMALLINT NOT NULL DEFAULT 30,
    weight_experience       SMALLINT NOT NULL DEFAULT 25,
    weight_education        SMALLINT NOT NULL DEFAULT 15,
    weight_certifications   SMALLINT NOT NULL DEFAULT 10,
    weight_soft_skills      SMALLINT NOT NULL DEFAULT 10,
    weight_domain_knowledge SMALLINT NOT NULL DEFAULT 5,
    weight_other            SMALLINT NOT NULL DEFAULT 5,
    -- Async extraction tracking
    criteria_extraction_status VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (criteria_extraction_status IN ('pending', 'processing', 'completed', 'failed')),
    criteria_extraction_error  TEXT,
    criteria_extracted_at      TIMESTAMPTZ,
    -- Structured JSON matching the frontend AnalysisJson interface (populated by Celery task)
    analysis_json              JSONB,
    -- Audit
    ai_model        VARCHAR(100),           -- model used to generate (e.g. gpt-4o-mini)
    ai_generated_at TIMESTAMPTZ,
    last_edited_by  UUID        REFERENCES users (user_id),
    last_edited_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT weights_sum_100 CHECK (
        weight_skills + weight_experience + weight_education +
        weight_certifications + weight_soft_skills +
        weight_domain_knowledge + weight_other = 100
    )
);

-------------------------------------------------------------------------------
-- applications
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applications (
    application_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID        NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    candidate_name      VARCHAR(255) NOT NULL,
    candidate_email     VARCHAR(255),
    submission_source   VARCHAR(30)  NOT NULL DEFAULT 'email'
                            CHECK (submission_source IN ('email', 'manual_upload', 'api')),
    processing_status   VARCHAR(30)  NOT NULL DEFAULT 'pending'
                            CHECK (processing_status IN ('pending', 'processing', 'scored', 'failed')),
    decision            VARCHAR(20)
                            CHECK (decision IN ('qualified', 'partial', 'rejected')),
    -- Thresholds used at scoring time (audit snapshot — never changes after scoring)
    qualified_threshold_used SMALLINT,
    partial_threshold_used   SMALLINT,
    applied_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    scored_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_applications_job     ON applications (job_id);
CREATE INDEX IF NOT EXISTS idx_applications_tenant  ON applications (tenant_id);
CREATE INDEX IF NOT EXISTS idx_applications_decision ON applications (decision);
CREATE INDEX IF NOT EXISTS idx_applications_status  ON applications (processing_status);

-------------------------------------------------------------------------------
-- application_files
-- One row per uploaded/received file (PDF or DOCX)
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS application_files (
    file_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID        NOT NULL REFERENCES applications (application_id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL REFERENCES tenants (tenant_id),
    original_name       VARCHAR(255) NOT NULL,
    mime_type           VARCHAR(100) NOT NULL,
    file_path           TEXT        NOT NULL,   -- relative path under /files/
    file_size_bytes     BIGINT,
    extracted_text      TEXT,                   -- populated after PDF extraction
    extraction_status   VARCHAR(20)  NOT NULL DEFAULT 'pending'
                            CHECK (extraction_status IN ('pending', 'done', 'failed')),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_files_application ON application_files (application_id);

-------------------------------------------------------------------------------
-- application_scores
-- One row per scored application; holds all 7 dimension scores + final
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS application_scores (
    score_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID        NOT NULL UNIQUE REFERENCES applications (application_id) ON DELETE CASCADE,
    -- Raw scores 0-100 per dimension
    score_skills            SMALLINT NOT NULL CHECK (score_skills        BETWEEN 0 AND 100),
    score_experience        SMALLINT NOT NULL CHECK (score_experience    BETWEEN 0 AND 100),
    score_education         SMALLINT NOT NULL CHECK (score_education     BETWEEN 0 AND 100),
    score_certifications    SMALLINT NOT NULL CHECK (score_certifications BETWEEN 0 AND 100),
    score_soft_skills       SMALLINT NOT NULL CHECK (score_soft_skills   BETWEEN 0 AND 100),
    score_domain_knowledge  SMALLINT NOT NULL CHECK (score_domain_knowledge BETWEEN 0 AND 100),
    score_other             SMALLINT NOT NULL CHECK (score_other         BETWEEN 0 AND 100),
    -- Weighted final score (0.00 - 100.00)
    final_score         NUMERIC(5,2) NOT NULL,
    -- Weights snapshot at time of scoring (criteria may have been edited later)
    weights_snapshot    JSONB       NOT NULL,
    -- AI output
    ai_model            VARCHAR(100) NOT NULL,
    strengths           TEXT[]      NOT NULL DEFAULT '{}',
    gaps_identified     TEXT[]      NOT NULL DEFAULT '{}',
    evaluation_notes    TEXT,
    interview_questions TEXT[]      NOT NULL DEFAULT '{}',
    raw_ai_response     JSONB,                  -- full AI response for audit
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-------------------------------------------------------------------------------
-- email_ingest_log
-- Deduplication + audit trail for all IMAP-ingested emails
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_ingest_log (
    log_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        REFERENCES tenants (tenant_id),
    job_id              UUID        REFERENCES jobs (job_id),
    application_id      UUID        REFERENCES applications (application_id),
    message_id          TEXT        UNIQUE,     -- IMAP Message-ID header (deduplication)
    sender_email        VARCHAR(255),
    recipient_email     VARCHAR(255),
    subject             TEXT,
    ingestion_mode      VARCHAR(30)  CHECK (ingestion_mode IN ('platform_email', 'forwarding')),
    status              VARCHAR(30)  NOT NULL DEFAULT 'received'
                            CHECK (status IN ('received', 'processing', 'scored', 'failed', 'skipped')),
    error_message       TEXT,
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingest_log_tenant   ON email_ingest_log (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ingest_log_job      ON email_ingest_log (job_id);
CREATE INDEX IF NOT EXISTS idx_ingest_log_msg_id   ON email_ingest_log (message_id);

-------------------------------------------------------------------------------
-- Row Level Security
-- Enforces tenant isolation at the DB layer.
-- FastAPI sets app.current_tenant_id before each query.
-------------------------------------------------------------------------------

ALTER TABLE tenants        ENABLE ROW LEVEL SECURITY;
ALTER TABLE users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_invites   ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_criteria   ENABLE ROW LEVEL SECURITY;
ALTER TABLE applications   ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_ingest_log ENABLE ROW LEVEL SECURITY;

-- App role (used by FastAPI connection pool — NOT superuser)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cv_app') THEN
        CREATE ROLE cv_app LOGIN PASSWORD 'CHANGE_ME_IN_ENV';
    END IF;
END$$;

GRANT USAGE ON SCHEMA cv_analyzer TO cv_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cv_analyzer TO cv_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA cv_analyzer TO cv_app;

-- Super-admin bypass: when current_setting returns 'super_admin', skip tenant filter
-- Tenant-scoped tables: filter by current_setting('app.current_tenant_id')

CREATE POLICY tenant_isolation_tenants ON tenants
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_users ON users
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_invites ON user_invites
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_jobs ON jobs
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_job_criteria ON job_criteria
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR job_id IN (
            SELECT job_id FROM jobs
            WHERE tenant_id::text = current_setting('app.current_tenant_id', true)
        )
    );

CREATE POLICY tenant_isolation_applications ON applications
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_app_files ON application_files
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

CREATE POLICY tenant_isolation_app_scores ON application_scores
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR application_id IN (
            SELECT application_id FROM applications
            WHERE tenant_id::text = current_setting('app.current_tenant_id', true)
        )
    );

CREATE POLICY tenant_isolation_ingest_log ON email_ingest_log
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

-- system_config: readable by all, writable by super_admin through app
GRANT SELECT, INSERT, UPDATE, DELETE ON system_config TO cv_app;
-- subscription_plans: platform-level, managed only by super_admin
GRANT SELECT, INSERT, UPDATE, DELETE ON subscription_plans TO cv_app;

-------------------------------------------------------------------------------
-- updated_at triggers
-------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_subscription_plans_updated_at BEFORE UPDATE ON subscription_plans FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_tenants_updated_at   BEFORE UPDATE ON tenants        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at     BEFORE UPDATE ON users          FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_jobs_updated_at      BEFORE UPDATE ON jobs           FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_criteria_updated_at  BEFORE UPDATE ON job_criteria   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_apps_updated_at      BEFORE UPDATE ON applications   FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
