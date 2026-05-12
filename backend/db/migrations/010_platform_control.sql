-- 010_platform_control.sql
-- Adds Platform Control module: enhanced system_config, subscription_plans,
-- and tenant subscription tracking fields.
-- Safe to run on existing data: all new columns use IF NOT EXISTS with defaults.

SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1.  Enhance system_config with metadata columns
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE system_config
    ADD COLUMN IF NOT EXISTS type        VARCHAR(20)  NOT NULL DEFAULT 'string'
        CHECK (type IN ('string', 'number', 'boolean', 'json')),
    ADD COLUMN IF NOT EXISTS category    VARCHAR(50)  NOT NULL DEFAULT 'general'
        CHECK (category IN ('scoring', 'ai', 'email', 'queue', 'subscription', 'security', 'general')),
    ADD COLUMN IF NOT EXISTS editable    BOOLEAN      NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS updated_by  UUID         REFERENCES users (user_id) ON DELETE SET NULL;

-- Grant write access to cv_app (was SELECT-only)
GRANT INSERT, UPDATE, DELETE ON system_config TO cv_app;

-- Back-fill type/category for rows seeded by previous migrations
UPDATE system_config SET type = 'number', category = 'scoring'
WHERE key IN ('qualified_threshold', 'partial_threshold');

UPDATE system_config SET type = 'number', category = 'ai'
WHERE key IN ('gatekeeper_semantic_threshold', 'gatekeeper_skill_fuzzy_threshold');

-- Seed additional platform config entries
INSERT INTO system_config (key, value, type, category, editable, description) VALUES
    ('default_ai_model',        'gpt-4o-mini',        'string',  'ai',           true,
        'OpenAI model used for CV scoring and criteria extraction'),
    ('output_language',         'ar',                 'string',  'ai',           true,
        'Default language for AI-generated text outputs: ar or en'),
    ('max_file_size_mb',        '10',                 'number',  'general',      true,
        'Maximum CV file size allowed for upload, in megabytes'),
    ('imap_poll_interval',      '120',                'number',  'email',        true,
        'Seconds between IMAP inbox polling cycles for email CV ingestion'),
    ('queue_stuck_timeout_min', '10',                 'number',  'queue',        true,
        'Minutes before a queued scoring job is considered stuck and eligible for reset'),
    ('default_trial_days',      '14',                 'number',  'subscription', true,
        'Default free trial period in days assigned to newly created tenants'),
    ('default_tenant_status',   'trial',              'string',  'subscription', true,
        'Default subscription_status assigned to newly created tenants (trial/active)'),
    ('email_sender_name',       'CV Analyzer',        'string',  'email',        true,
        'Display name used as the email sender for all outbound messages'),
    ('email_sender_address',    'noreply@ai970.cloud','string',  'email',        true,
        'From/reply-to email address for all outbound system messages')
ON CONFLICT (key) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2.  Create subscription_plans (platform-level, no RLS)
-- ─────────────────────────────────────────────────────────────────────────────
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

GRANT SELECT, INSERT, UPDATE, DELETE ON subscription_plans TO cv_app;

CREATE TRIGGER trg_subscription_plans_updated_at
    BEFORE UPDATE ON subscription_plans
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Seed the three default plans
INSERT INTO subscription_plans (
    plan_code, plan_name, description,
    monthly_price, yearly_price, currency, trial_days,
    max_campaigns, max_processed_cvs_per_month, max_users,
    api_access, advanced_analytics, priority_support, custom_ai_prompts,
    status, display_order
) VALUES
(
    'starter', 'Starter',
    'Perfect for small teams starting their recruitment automation journey',
    49, 490, 'USD', 14,
    5, 500, 3,
    false, false, false, false,
    'active', 1
),
(
    'professional', 'Professional',
    'Advanced features for growing teams and high-volume recruitment operations',
    149, 1490, 'USD', 14,
    25, 5000, 15,
    false, true, false, false,
    'active', 2
),
(
    'enterprise', 'Enterprise',
    'Full-featured platform built for enterprise-scale recruitment with API access',
    499, 4990, 'USD', 14,
    500, 100000, 100,
    true, true, true, true,
    'active', 3
)
ON CONFLICT (plan_code) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3.  Add subscription tracking columns to tenants
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS subscription_status  VARCHAR(20) DEFAULT 'trial'
        CHECK (subscription_status IN ('trial', 'active', 'suspended', 'expired')),
    ADD COLUMN IF NOT EXISTS trial_start_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS trial_end_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS subscription_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS subscription_ends_at    TIMESTAMPTZ;

-- Back-fill existing tenants: treat them as active (they predate the trial system)
UPDATE tenants
SET subscription_status = 'active',
    subscription_started_at = created_at
WHERE subscription_status IS NULL OR subscription_status = 'trial';

CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status
    ON tenants (subscription_status);
