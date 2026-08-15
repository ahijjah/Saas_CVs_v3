-- Migration 102: E-02 Phase 1 — Product Modules & Feature Flag Foundation
--
-- Creates the tenant_features table that stores per-tenant feature flag state.
-- All existing tenants are seeded with every known feature enabled (TRUE) so
-- no production behaviour changes.
--
-- New table:
--   tenant_features — one row per (tenant_id, feature_code) pair
--
-- Design notes:
--   * DEFAULT is_enabled = TRUE  — missing rows always evaluate as enabled
--   * feature_code is an unconstrained VARCHAR so new codes require no DDL
--   * RLS mirrors the rest of the schema (app.current_tenant_id session var)
--   * Seed INSERT uses ON CONFLICT DO NOTHING — fully idempotent
--
-- Feature codes seeded here must match constants in backend/constants/features.py.

BEGIN;
SET search_path = cv_analyzer;

-- ── 1. Table ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_features (
    tenant_feature_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID         NOT NULL,
    feature_code        VARCHAR(64)  NOT NULL,
    is_enabled          BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── 2. Unique constraint ──────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_tenant_features_tenant_feature'
           AND conrelid = 'cv_analyzer.tenant_features'::regclass
    ) THEN
        ALTER TABLE cv_analyzer.tenant_features
            ADD CONSTRAINT uq_tenant_features_tenant_feature
            UNIQUE (tenant_id, feature_code);
    END IF;
END $$;

-- ── 3. Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_tenant_features_tenant_id
    ON tenant_features (tenant_id);

CREATE INDEX IF NOT EXISTS idx_tenant_features_feature_code
    ON tenant_features (feature_code);

-- Covering index for the hot path: is_feature_enabled(tenant_id, feature_code)
CREATE INDEX IF NOT EXISTS idx_tenant_features_lookup
    ON tenant_features (tenant_id, feature_code)
    INCLUDE (is_enabled);

-- ── 4. Row Level Security ─────────────────────────────────────────────────────

ALTER TABLE tenant_features ENABLE ROW LEVEL SECURITY;

-- Tenant isolation: each tenant sees only its own rows
DROP POLICY IF EXISTS tenant_features_tenant_isolation ON tenant_features;
CREATE POLICY tenant_features_tenant_isolation ON tenant_features
    USING (
        tenant_id = (current_setting('app.current_tenant_id', true))::uuid
    )
    WITH CHECK (
        tenant_id = (current_setting('app.current_tenant_id', true))::uuid
    );

-- Platform admin bypass: unrestricted read/write for platform_admin role
DROP POLICY IF EXISTS tenant_features_admin_bypass ON tenant_features;
CREATE POLICY tenant_features_admin_bypass ON tenant_features
    USING (
        current_setting('app.current_role', true) IN ('platform_admin', 'super_admin')
    );

-- ── 5. updated_at auto-update trigger ────────────────────────────────────────

CREATE OR REPLACE FUNCTION cv_analyzer.tenant_features_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_tenant_features_updated_at ON tenant_features;
CREATE TRIGGER trg_tenant_features_updated_at
    BEFORE UPDATE ON tenant_features
    FOR EACH ROW EXECUTE FUNCTION cv_analyzer.tenant_features_set_updated_at();

-- ── 6. Seed: enable all features for all existing tenants ─────────────────────
-- ON CONFLICT DO NOTHING makes this safe to re-run on existing installations.

INSERT INTO tenant_features (tenant_id, feature_code, is_enabled)
SELECT
    t.tenant_id,
    fc.feature_code,
    TRUE
FROM tenants t
CROSS JOIN (VALUES
    ('AI_RECRUITMENT'),
    ('AI_JOB_ANALYSIS'),
    ('AI_SCORING'),
    ('AI_KNOCKOUTS'),
    ('AI_EXPLAINABILITY'),
    ('AI_COMPARISON'),
    ('AI_BULK_UPLOAD'),
    ('AI_INTAKE_EMAIL'),
    ('AI_INTAKE_PUBLIC'),
    ('AI_INTAKE_MANUAL'),
    ('ATS_MANAGEMENT'),
    ('ATS_WORKFLOWS'),
    ('ATS_NOTES'),
    ('ATS_COMMUNICATIONS'),
    ('ATS_ASSIGNMENTS'),
    ('ATS_EMAIL_TEMPLATES'),
    ('ATS_REPORTING')
) AS fc(feature_code)
ON CONFLICT (tenant_id, feature_code) DO NOTHING;

COMMIT;
