-- Migration 103: E-02 Phase 2 — Tenant Feature Audit Log
--
-- Creates tenant_feature_audit table that records every enable/disable
-- operation made by a platform administrator.
--
-- New table:
--   tenant_feature_audit — one row per feature flag change
--
-- Design:
--   * No RLS on this table — audit logs should be immutable and readable
--     by super_admin regardless of current_tenant_id context.
--     SECURITY INVOKER functions and super_admin-only endpoints protect it.
--   * No updated_at — audit rows are never modified.
--   * old_value / new_value stored as BOOLEAN for clarity.

BEGIN;
SET search_path = cv_analyzer;

-- ── 1. Table ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_feature_audit (
    audit_id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL,
    feature_code    VARCHAR(64)  NOT NULL,
    old_value       BOOLEAN,
    new_value       BOOLEAN      NOT NULL,
    changed_by      UUID         NOT NULL,
    changed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── 2. Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_tenant_feature_audit_tenant_id
    ON tenant_feature_audit (tenant_id);

CREATE INDEX IF NOT EXISTS idx_tenant_feature_audit_changed_by
    ON tenant_feature_audit (changed_by);

CREATE INDEX IF NOT EXISTS idx_tenant_feature_audit_changed_at
    ON tenant_feature_audit (changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_feature_audit_feature
    ON tenant_feature_audit (tenant_id, feature_code);

COMMIT;
