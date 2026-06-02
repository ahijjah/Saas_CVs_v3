-- Migration 055: Campaigns (optional job grouping layer)
--
-- Introduces a lightweight, OPTIONAL grouping layer over jobs.
--
-- Design principles (Phase A — backend only):
--   1. Campaign is an organizational / filtering / reporting layer ONLY.
--      It is NEVER a permission or security boundary. Access continues to be
--      governed by tenant RLS + jobs.client_organization_id, unchanged.
--   2. A campaign groups zero or more jobs. A job may belong to at most one
--      campaign, or remain standalone (campaign_id = NULL).
--   3. A campaign belongs to exactly one tenant, and is linked to at most one
--      client organisation. client_organization_id = NULL means a public/shared
--      campaign (which may only contain jobs that are themselves public).
--   4. Existing jobs are untouched: jobs.campaign_id defaults to NULL, so every
--      current job remains standalone and every current flow is unaffected.
--   5. ON DELETE SET NULL on jobs.campaign_id guarantees that deleting/archiving
--      a campaign NEVER deletes jobs or applications — the jobs simply detach
--      and become standalone again.
--
-- The "one client per campaign / no mixing clients" invariant is enforced at the
-- application layer (v1), not via DB constraints. See services/campaign_service.py.
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / IF EXISTS.

BEGIN;
SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Campaign table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS job_campaigns (
    campaign_id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID         NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    client_organization_id  UUID         REFERENCES client_organizations(client_organization_id) ON DELETE SET NULL,
    name                    VARCHAR(255) NOT NULL,
    description             TEXT,
    status                  VARCHAR(20)  NOT NULL DEFAULT 'active',
    created_by              UUID         REFERENCES users(user_id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT job_campaigns_status_check
        CHECK (status IN ('active', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_job_campaigns_tenant
    ON job_campaigns(tenant_id);
CREATE INDEX IF NOT EXISTS idx_job_campaigns_tenant_client
    ON job_campaigns(tenant_id, client_organization_id);

COMMENT ON TABLE job_campaigns IS
    'Optional grouping layer over jobs (filtering/reporting only — never a permission boundary). '
    'A campaign belongs to one tenant and at most one client organisation '
    '(NULL client_organization_id = public/shared campaign).';
COMMENT ON COLUMN job_campaigns.client_organization_id IS
    'The single client this campaign belongs to. NULL = public/shared campaign '
    '(may contain only jobs that are themselves public, i.e. jobs.client_organization_id IS NULL).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Link jobs to a campaign (optional)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS campaign_id UUID
        REFERENCES job_campaigns(campaign_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_campaign_id
    ON jobs(campaign_id)
    WHERE campaign_id IS NOT NULL;

COMMENT ON COLUMN jobs.campaign_id IS
    'Optional campaign grouping. NULL = standalone job. '
    'ON DELETE SET NULL: removing a campaign detaches its jobs, never deletes them. '
    'Must align with the campaign client (enforced in the application layer).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Row-Level Security (tenant isolation — identical to all other tenant tables)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE job_campaigns ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_job_campaigns ON job_campaigns;
CREATE POLICY tenant_isolation_job_campaigns ON job_campaigns
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Backfill
-- ─────────────────────────────────────────────────────────────────────────────

-- No backfill required: existing jobs keep campaign_id = NULL (standalone), which
-- is the correct and intended default. No existing rows are modified.

COMMIT;
