BEGIN;
SET search_path = cv_analyzer;

-- ── AI model pricing table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_model_pricing (
    pricing_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider                    TEXT NOT NULL,
    model                       TEXT NOT NULL,
    input_price_per_1m_tokens   NUMERIC(12, 6) NOT NULL DEFAULT 0,
    output_price_per_1m_tokens  NUMERIC(12, 6) NOT NULL DEFAULT 0,
    active                      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one active pricing row per (provider, model)
CREATE UNIQUE INDEX IF NOT EXISTS uix_ai_model_pricing_active
    ON ai_model_pricing (provider, model)
    WHERE active = TRUE;

-- ── Seed known OpenAI model pricing (USD per 1M tokens) ───────────────────
INSERT INTO ai_model_pricing (provider, model, input_price_per_1m_tokens, output_price_per_1m_tokens) VALUES
    ('openai', 'gpt-4o',        2.50,  10.00),
    ('openai', 'gpt-4o-mini',   0.15,   0.60),
    ('openai', 'gpt-4-turbo',  10.00,  30.00),
    ('openai', 'gpt-4',        30.00,  60.00),
    ('openai', 'gpt-3.5-turbo', 0.50,   1.50),
    ('openai', 'o1-preview',   15.00,  60.00),
    ('openai', 'o1-mini',       3.00,  12.00)
ON CONFLICT DO NOTHING;

-- ── AI usage log table ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_usage_log (
    usage_id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   UUID,
    job_id                      UUID,
    application_id              UUID,
    stage                       TEXT NOT NULL,         -- cv_scoring, cv_comparison, criteria_extraction, etc.
    provider                    TEXT NOT NULL DEFAULT 'openai',
    model                       TEXT NOT NULL,
    prompt_key                  TEXT,
    prompt_version_id           UUID,
    prompt_tokens               INT NOT NULL DEFAULT 0,
    completion_tokens           INT NOT NULL DEFAULT 0,
    total_tokens                INT NOT NULL DEFAULT 0,
    input_price_per_1m_tokens   NUMERIC(12, 6),
    output_price_per_1m_tokens  NUMERIC(12, 6),
    estimated_cost_usd          NUMERIC(14, 8),
    request_status              TEXT NOT NULL DEFAULT 'success'
                                CHECK (request_status IN ('success', 'failed', 'timeout', 'cancelled')),
    latency_ms                  INT,
    retry_count                 INT NOT NULL DEFAULT 0,
    error_type                  TEXT,
    error_message               TEXT,
    metadata                    JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_ai_usage_log_tenant
    ON ai_usage_log (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_job
    ON ai_usage_log (job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_application
    ON ai_usage_log (application_id);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_created
    ON ai_usage_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_stage
    ON ai_usage_log (stage, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_usage_log_model
    ON ai_usage_log (model, created_at DESC);

COMMIT;
