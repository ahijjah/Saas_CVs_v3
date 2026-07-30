-- 010_platform_secrets_ai_prompts.sql
-- Secrets & Credentials management and AI Prompt versioning tables.
--
-- SECURITY NOTE: platform_secrets stores actual secret values in the DB.
-- The column `value` is never returned by any API endpoint — only `masked_value`
-- (last 4 chars visible) is returned. Runtime components currently read secrets
-- from environment variables; DB-stored values serve as an auditable management
-- layer. Future integration can load from DB at startup with a service restart.
SET search_path = cv_analyzer;

-- ── Platform Secrets ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_secrets (
    key              VARCHAR(100) PRIMARY KEY,
    value            TEXT,                          -- NEVER returned by API
    masked_value     VARCHAR(60),                   -- ••••••••abcd (last 4 visible)
    description      TEXT,
    category         VARCHAR(50) NOT NULL DEFAULT 'general',
    is_critical      BOOLEAN NOT NULL DEFAULT FALSE, -- show warning before changing
    has_value        BOOLEAN NOT NULL DEFAULT FALSE,  -- set to TRUE when value is first set
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_by       VARCHAR(255),
    updated_by_email VARCHAR(255)
);

-- Seed known secret keys with descriptions; no values yet
INSERT INTO platform_secrets (key, description, category, is_critical) VALUES
    ('OPENAI_API_KEY',   'OpenAI API key for CV scoring and criteria extraction. Used by all AI pipeline stages.',                  'ai',       TRUE),
    ('JWT_SECRET',       'JWT signing secret. CRITICAL: changing this invalidates ALL active user sessions immediately.',           'security', TRUE),
    ('SMTP_PASSWORD',    'SMTP server password for outgoing email (invitations, notifications, candidate confirmations).',          'email',    FALSE),
    ('IMAP_PASSWORD',    'IMAP server password for CV email ingestion worker.',                                                     'email',    FALSE),
    ('REDIS_PASSWORD',   'Redis broker/cache password. Leave blank if Redis authentication is disabled.',                           'queue',    FALSE),
    ('DB_PASSWORD',      'PostgreSQL database password. CRITICAL: changing requires immediate service restart.',                    'database', TRUE)
ON CONFLICT (key) DO NOTHING;

-- ── AI Prompts ────────────────────────────────────────────────────────────────
-- Stores versioned AI prompts used throughout the CV scoring pipeline.
-- Multiple versions per prompt_code are kept; only one version can be active.
-- Workers load the active prompt at task execution time, falling back to the
-- hardcoded defaults in ai_service.py if no active DB prompt is found.
CREATE TABLE IF NOT EXISTS ai_prompts (
    prompt_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_code          VARCHAR(100) NOT NULL,
    prompt_name          VARCHAR(255) NOT NULL,
    prompt_category      VARCHAR(50)  NOT NULL CHECK (prompt_category IN ('criteria','scoring','screening','summary','interview')),
    system_prompt        TEXT         NOT NULL,
    user_prompt_template TEXT,
    model                VARCHAR(100) NOT NULL DEFAULT 'gpt-4o-mini',
    temperature          DECIMAL(3,2) NOT NULL DEFAULT 0.20 CHECK (temperature >= 0 AND temperature <= 2),
    max_tokens           INTEGER      NOT NULL DEFAULT 2000,
    output_language      VARCHAR(10)  NOT NULL DEFAULT 'ar',
    is_active            BOOLEAN      NOT NULL DEFAULT FALSE,
    version              INTEGER      NOT NULL DEFAULT 1,
    notes                TEXT,
    created_at           TIMESTAMPTZ  DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  DEFAULT NOW(),
    updated_by           VARCHAR(255),
    updated_by_email     VARCHAR(255),
    UNIQUE (prompt_code, version)
);

CREATE INDEX IF NOT EXISTS idx_ai_prompts_code   ON ai_prompts (prompt_code);
CREATE INDEX IF NOT EXISTS idx_ai_prompts_active ON ai_prompts (prompt_code, is_active) WHERE is_active = TRUE;

-- updated_at trigger
CREATE OR REPLACE FUNCTION update_ai_prompts_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS trg_ai_prompts_updated_at ON ai_prompts;
CREATE TRIGGER trg_ai_prompts_updated_at
    BEFORE UPDATE ON ai_prompts
    FOR EACH ROW EXECUTE FUNCTION update_ai_prompts_updated_at();
