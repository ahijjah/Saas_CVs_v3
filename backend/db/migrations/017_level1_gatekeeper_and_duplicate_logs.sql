-- Migration 017: Duplicate application logs + level1_gatekeeper_enabled system param

BEGIN;

-- ── Duplicate application logs ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS duplicate_application_logs (
    log_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    job_id              UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    duplicate_email     TEXT NOT NULL,
    duplicate_name      TEXT,
    attachment_hash     TEXT NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    original_application_id UUID REFERENCES applications(application_id) ON DELETE SET NULL,
    email_message_id    TEXT,
    raw_filename        TEXT,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_dup_logs_tenant ON duplicate_application_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dup_logs_job    ON duplicate_application_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_dup_logs_hash   ON duplicate_application_logs(attachment_hash);
CREATE INDEX IF NOT EXISTS idx_dup_logs_email  ON duplicate_application_logs(duplicate_email);

ALTER TABLE duplicate_application_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY dup_logs_tenant_isolation ON duplicate_application_logs
    USING (tenant_id::text = current_setting('app.current_tenant_id', true));

-- ── Level 1 gatekeeper system parameter ──────────────────────────────────────
INSERT INTO system_config (key, value, type, category, description, editable)
VALUES (
    'level1_gatekeeper_enabled',
    'true',
    'boolean',
    'scoring',
    'When enabled, CVs below the semantic similarity threshold are rejected locally before reaching the AI scorer. Disable to pass all CVs to AI scoring (higher cost, higher recall).',
    true
)
ON CONFLICT (key) DO NOTHING;

COMMIT;
