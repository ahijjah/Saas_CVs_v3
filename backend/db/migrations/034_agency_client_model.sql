-- Migration 034: Agency & client organisation model
--
-- Adds:
--   1. tenant_type to tenants (organization | agency | individual_recruiter)
--   2. Fair-usage limit fields on tenants (soft/hard monthly CV caps)
--   3. client_organizations table (for agency/individual_recruiter tenants)
--   4. client_organization_id FK on jobs
--   5. agency_user_clients table (user↔client assignment with per-client role)
--   6. tenant_usage_monthly table (monthly CV/report usage tracking)
--   7. RLS policies for new tables
--   8. Backfill: existing tenants → 'organization'; existing jobs → NULL client_org
--
-- Note on job-level applicant caps:
--   max_applications (added in migration 027) already serves as the per-job
--   applicant limit (NULL = unlimited). This migration extends its enforcement
--   to ALL intake paths (previously only public apply). No new column is needed.
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / IF EXISTS.

BEGIN;
SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tenant type
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS tenant_type VARCHAR(30) NOT NULL DEFAULT 'organization';

ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_tenant_type_check;

ALTER TABLE tenants
    ADD CONSTRAINT tenants_tenant_type_check
        CHECK (tenant_type IN ('organization', 'agency', 'individual_recruiter'));

COMMENT ON COLUMN tenants.tenant_type IS
    'organization = direct employer; agency = recruitment agency; individual_recruiter = solo recruiter working for multiple clients.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Fair-usage limit fields on tenants (internal — not exposed in plan UI)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS monthly_cv_processing_soft_limit INTEGER,
    ADD COLUMN IF NOT EXISTS monthly_cv_processing_hard_limit INTEGER;

COMMENT ON COLUMN tenants.monthly_cv_processing_soft_limit IS
    'Fair Usage Policy: admin monitoring/alert threshold (CVs per calendar month). Does not block processing. NULL = no soft cap.';
COMMENT ON COLUMN tenants.monthly_cv_processing_hard_limit IS
    'Fair Usage Policy: hard block threshold (CVs per calendar month). AI processing is paused when reached. NULL = no hard cap.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Client organisations (agency/individual_recruiter tenants only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS client_organizations (
    client_organization_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    organization_name       VARCHAR(255) NOT NULL,
    industry                VARCHAR(100),
    contact_person          VARCHAR(255),
    email                   VARCHAR(255),
    phone                   VARCHAR(50),
    logo_url                TEXT,
    notes                   TEXT,
    status                  VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT client_organizations_status_check
        CHECK (status IN ('active', 'inactive'))
);

CREATE INDEX IF NOT EXISTS idx_client_organizations_tenant_id
    ON client_organizations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_client_organizations_tenant_status
    ON client_organizations(tenant_id, status);

COMMENT ON TABLE client_organizations IS
    'Client organisations managed by agency/individual_recruiter tenants. Direct organisation tenants do not use this table.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Link jobs to client organisations
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS client_organization_id UUID
        REFERENCES client_organizations(client_organization_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_client_organization_id
    ON jobs(client_organization_id)
    WHERE client_organization_id IS NOT NULL;

COMMENT ON COLUMN jobs.client_organization_id IS
    'Mandatory for agency/individual_recruiter tenants; NULL for direct organisation tenants.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Agency user → client assignment table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agency_user_clients (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    user_id                 UUID        NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    client_organization_id  UUID        NOT NULL REFERENCES client_organizations(client_organization_id) ON DELETE CASCADE,
    role_for_client         VARCHAR(30) NOT NULL DEFAULT 'recruiter',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT agency_user_clients_unique
        UNIQUE (user_id, client_organization_id),
    CONSTRAINT agency_user_clients_role_check
        CHECK (role_for_client IN ('account_manager', 'recruiter', 'viewer'))
);

CREATE INDEX IF NOT EXISTS idx_agency_user_clients_tenant
    ON agency_user_clients(tenant_id);
CREATE INDEX IF NOT EXISTS idx_agency_user_clients_user
    ON agency_user_clients(user_id);
CREATE INDEX IF NOT EXISTS idx_agency_user_clients_client
    ON agency_user_clients(client_organization_id);

COMMENT ON TABLE agency_user_clients IS
    'Maps agency/recruiter users to specific client organisations with a per-client role. '
    'Agency admins see all clients; other roles see only their assigned clients.';
COMMENT ON COLUMN agency_user_clients.role_for_client IS
    'account_manager | recruiter | viewer — access level for this specific client.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Monthly usage tracking table
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenant_usage_monthly (
    id                    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID    NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    usage_month           DATE    NOT NULL,  -- first day of month, e.g. 2025-05-01
    cvs_processed         INTEGER NOT NULL DEFAULT 0,
    ai_reports_generated  INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenant_usage_monthly_unique
        UNIQUE (tenant_id, usage_month)
);

CREATE INDEX IF NOT EXISTS idx_tenant_usage_monthly_tenant
    ON tenant_usage_monthly(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_usage_monthly_month
    ON tenant_usage_monthly(usage_month DESC);

COMMENT ON TABLE tenant_usage_monthly IS
    'Calendar-month CV processing and AI report usage per tenant. '
    'Used for Fair Usage Policy (soft/hard limit) enforcement and admin reporting. '
    'usage_month is always the first day of the month (DATE_TRUNC(''month'', current_date)).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Row Level Security for new tables
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE client_organizations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE agency_user_clients   ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_usage_monthly  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_client_organizations ON client_organizations;
CREATE POLICY tenant_isolation_client_organizations ON client_organizations
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

DROP POLICY IF EXISTS tenant_isolation_agency_user_clients ON agency_user_clients;
CREATE POLICY tenant_isolation_agency_user_clients ON agency_user_clients
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

DROP POLICY IF EXISTS tenant_isolation_tenant_usage_monthly ON tenant_usage_monthly;
CREATE POLICY tenant_isolation_tenant_usage_monthly ON tenant_usage_monthly
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Backfill
-- ─────────────────────────────────────────────────────────────────────────────

-- All existing tenants are direct organisations (tenant_type default already set)
UPDATE tenants
SET tenant_type = 'organization'
WHERE tenant_type IS DISTINCT FROM 'organization'
  AND tenant_type NOT IN ('agency', 'individual_recruiter');

-- Existing jobs have no client organisation — client_organization_id defaults to NULL, correct.

-- Update max_applications column comment to reflect universal enforcement
COMMENT ON COLUMN jobs.max_applications IS
    'Maximum number of valid (non-duplicate, non-failed) applications accepted for this job. '
    'NULL = unlimited. Enforced across all intake paths: manual upload, email forwarding, '
    'platform email alias, public apply form. Added in migration 027; universal enforcement '
    'added in migration 034.';

COMMIT;
