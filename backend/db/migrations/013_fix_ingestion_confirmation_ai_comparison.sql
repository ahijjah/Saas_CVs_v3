-- 013_fix_ingestion_confirmation_ai_comparison.sql
-- Fixes for ingestion toggles, confirmation email redesign,
-- AI comparison system config, and processing_status constraint.
SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Restore 'queued' to processing_status CHECK constraint
--    Migration 012 accidentally dropped 'queued'.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_processing_status_check;
ALTER TABLE applications
    ADD CONSTRAINT applications_processing_status_check
    CHECK (processing_status IN ('pending', 'queued', 'processing', 'scored', 'failed'));

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Fix receive_cv_via_forwarding_email default: should be TRUE
--    Existing rows that have false (the wrong default) are set to true.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE jobs
    ALTER COLUMN receive_cv_via_forwarding_email SET DEFAULT TRUE;

UPDATE jobs
SET receive_cv_via_forwarding_email = TRUE
WHERE receive_cv_via_forwarding_email IS NULL
   OR receive_cv_via_forwarding_email = FALSE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Redesign confirmation email toggles
--    Old 4 toggles (on_receipt / on_qualified / on_rejected / on_partial) are
--    replaced with 4 source-aware toggles.
--
--    New defaults (business rules):
--      - upload:          cv_email = false (internal upload, usually no need)
--      - forwarding:      cv_email = false, sender_email = true (notify recruiter)
--      - platform_email:  cv_email = true  (applicant submitted directly)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS send_confirmation_to_cv_email_for_upload         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS send_confirmation_to_cv_email_for_forwarding     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS send_confirmation_to_sender_for_forwarding       BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS send_confirmation_to_cv_email_for_platform_email BOOLEAN NOT NULL DEFAULT TRUE;

-- Drop old confirmation columns (no longer used)
ALTER TABLE jobs
    DROP COLUMN IF EXISTS send_confirmation_on_receipt,
    DROP COLUMN IF EXISTS send_confirmation_on_qualified,
    DROP COLUMN IF EXISTS send_confirmation_on_rejected,
    DROP COLUMN IF EXISTS send_confirmation_on_partial;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Add system_config entries for secondary (comparison) LLM
--    These control whether AI comparison mode is available at all, and
--    which provider/model to use. The API key goes in platform_secrets.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO system_config (key, value, description) VALUES
    ('ai_comparison_mode_enabled', 'false',                    'Master switch: enable secondary LLM comparison scoring'),
    ('secondary_llm_provider',     'deepseek',                 'Secondary LLM provider identifier (deepseek / openai / etc.)'),
    ('secondary_llm_base_url',     'https://api.deepseek.com/v1', 'Secondary LLM OpenAI-compatible API base URL'),
    ('secondary_llm_model',        'deepseek-chat',            'Secondary LLM model name'),
    ('secondary_llm_temperature',  '0.2',                      'Secondary LLM sampling temperature (0.0–2.0)'),
    ('secondary_llm_max_tokens',   '2048',                     'Secondary LLM max output tokens')
ON CONFLICT (key) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Register SECONDARY_LLM_API_KEY in platform_secrets
--    No value stored yet — super admin sets it via the Platform Secrets UI.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO platform_secrets (key, description, category, is_critical) VALUES
    ('SECONDARY_LLM_API_KEY',
     'API key for the secondary/comparison LLM (e.g. DeepSeek). Used when AI comparison mode is enabled.',
     'ai', FALSE)
ON CONFLICT (key) DO NOTHING;
