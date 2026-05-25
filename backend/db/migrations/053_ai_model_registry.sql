BEGIN;
SET search_path = cv_analyzer;

CREATE TABLE IF NOT EXISTS ai_model_registry (
    model_id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider                    TEXT NOT NULL,
    model_name                  TEXT NOT NULL,
    display_name                TEXT NOT NULL,
    provider_secret_key         TEXT,           -- references platform_secrets.key, never stores value
    base_url                    TEXT,           -- NULL = use provider default endpoint
    supported_stages            TEXT[] NOT NULL DEFAULT '{}',
    enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
    max_context_tokens          INT,
    input_price_per_1m_tokens   NUMERIC(12,6),
    output_price_per_1m_tokens  NUMERIC(12,6),
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, model_name)
);

CREATE TABLE IF NOT EXISTS ai_stage_defaults (
    stage                       TEXT PRIMARY KEY,
    primary_model_id            UUID REFERENCES ai_model_registry(model_id) ON DELETE SET NULL,
    fallback_model_id           UUID REFERENCES ai_model_registry(model_id) ON DELETE SET NULL,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed OpenAI models
INSERT INTO ai_model_registry (provider, model_name, display_name, provider_secret_key, supported_stages, enabled, max_context_tokens, input_price_per_1m_tokens, output_price_per_1m_tokens, notes)
VALUES
    ('openai', 'gpt-4o',          'GPT-4o',           'OPENAI_API_KEY', ARRAY['cv_scoring','cv_comparison','criteria_extraction','level2_screening','translation_explanation','fallback'], TRUE, 128000, 2.50, 10.00, 'Most capable OpenAI model'),
    ('openai', 'gpt-4o-mini',     'GPT-4o Mini',      'OPENAI_API_KEY', ARRAY['cv_scoring','cv_comparison','criteria_extraction','level2_screening','translation_explanation','fallback'], TRUE, 128000, 0.15,  0.60, 'Fast and cost-efficient OpenAI model'),
    ('openai', 'gpt-4-turbo',     'GPT-4 Turbo',      'OPENAI_API_KEY', ARRAY['cv_scoring','cv_comparison','criteria_extraction'], TRUE, 128000, 10.00, 30.00, NULL),
    ('openai', 'gpt-4',           'GPT-4',             'OPENAI_API_KEY', ARRAY['cv_scoring','cv_comparison','criteria_extraction'], TRUE, 8192,   30.00, 60.00, NULL),
    ('openai', 'gpt-3.5-turbo',   'GPT-3.5 Turbo',    'OPENAI_API_KEY', ARRAY['level2_screening','translation_explanation','fallback'], TRUE, 16385, 0.50, 1.50, 'Lightweight tasks only'),
    ('openai', 'o1-preview',      'o1 Preview',        'OPENAI_API_KEY', ARRAY['cv_scoring'], FALSE, 128000, 15.00, 60.00, 'Reasoning model — high latency, high cost'),
    ('openai', 'o1-mini',         'o1 Mini',           'OPENAI_API_KEY', ARRAY['cv_scoring','criteria_extraction'], FALSE, 128000, 3.00, 12.00, 'Compact reasoning model')
ON CONFLICT (provider, model_name) DO NOTHING;

-- Seed stage defaults pointing to gpt-4o-mini (current production default)
INSERT INTO ai_stage_defaults (stage, primary_model_id, fallback_model_id)
SELECT
    stage,
    (SELECT model_id FROM ai_model_registry WHERE provider='openai' AND model_name='gpt-4o-mini') AS primary_model_id,
    (SELECT model_id FROM ai_model_registry WHERE provider='openai' AND model_name='gpt-4o-mini') AS fallback_model_id
FROM (VALUES
    ('cv_scoring'),
    ('cv_comparison'),
    ('criteria_extraction'),
    ('level2_screening'),
    ('translation_explanation'),
    ('fallback')
) AS stages(stage)
ON CONFLICT (stage) DO NOTHING;

COMMIT;
