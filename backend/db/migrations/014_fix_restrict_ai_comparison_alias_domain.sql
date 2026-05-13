-- 014_fix_restrict_ai_comparison_alias_domain.sql
-- Fix three issues:
--   1. Ensure restrict_forwarding_sender_to_tenant_email has correct default / no NULLs.
--   2. Add platform_email_domain system_config key (platform alias must use platform domain).
--   3. Fix existing platform_email aliases generated with tenant domain.
SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Ensure restrict_forwarding_sender_to_tenant_email is never NULL.
--    Migration 012 added the column but some rows may still be NULL if the
--    column was added to an existing DB without backfill.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE jobs
    ALTER COLUMN restrict_forwarding_sender_to_tenant_email SET DEFAULT FALSE;

UPDATE jobs
SET restrict_forwarding_sender_to_tenant_email = FALSE
WHERE restrict_forwarding_sender_to_tenant_email IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Register platform_email_domain in system_config.
--    Per-job alias emails MUST use this domain, NOT tenant.email_domain.
--    Super Admin can change this via the Platform Config UI without a deploy.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO system_config (key, value, description) VALUES
    ('platform_email_domain', 'ai970.cloud',
     'Domain used for per-job dedicated email aliases (platform_email). Must be the platform domain, never tenant domain.')
ON CONFLICT (key) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Fix existing platform_email aliases that were generated using tenant domain
--    instead of the platform domain.
--    Safe: only updates rows where platform_email does not already end with
--    @ai970.cloud, leaving any manually customised addresses untouched.
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE jobs
SET platform_email = LOWER(job_code) || '@ai970.cloud'
WHERE platform_email IS NOT NULL
  AND platform_email NOT LIKE '%@ai970.cloud';
