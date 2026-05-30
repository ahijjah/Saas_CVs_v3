-- Migration 056: Campaign Maturity — lifecycle, operational fields
--
-- Evolves campaigns from a simple grouping tag into a proper recruitment
-- operational object. Key changes:
--
--   1. Status enum: replaces the coarse active/archived pair with a real
--      business lifecycle: draft → active → on_hold → closed | cancelled.
--      "Archived" is removed as a status concept. Existing archived rows are
--      migrated to 'active' (safe fallback; likely zero such rows in prod).
--
--   2. Operational fields: start_date, end_date, target_hire_count,
--      campaign_owner_id, notes, public_title, is_publicly_listed.
--      All nullable with safe defaults — zero risk to existing rows.
--
-- Design principles (unchanged from migration 055):
--   • Campaigns are NEVER a permission or security boundary.
--   • ON DELETE SET NULL: deleting a campaign detaches jobs, never deletes them.
--   • All new columns are nullable / have defaults → fully backward compatible.
--
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS throughout.

BEGIN;
SET search_path = cv_analyzer;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Status constraint: drop old, migrate data, add new
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE job_campaigns
    DROP CONSTRAINT IF EXISTS job_campaigns_status_check;

-- Any existing 'archived' rows become 'active' (fallback; avoids constraint
-- violation on re-run and preserves data integrity on first run).
UPDATE job_campaigns
SET    status = 'active'
WHERE  status = 'archived';

ALTER TABLE job_campaigns
    ADD CONSTRAINT job_campaigns_status_check
    CHECK (status IN ('draft', 'active', 'on_hold', 'closed', 'cancelled'));

COMMENT ON COLUMN job_campaigns.status IS
    'Business lifecycle: draft (setup), active (running), on_hold (paused), '
    'closed (completed normally), cancelled (stopped before completion). '
    'Status NEVER cascades to linked jobs — jobs remain independently operable.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Operational fields (all nullable / with safe defaults)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE job_campaigns
    ADD COLUMN IF NOT EXISTS start_date          DATE,
    ADD COLUMN IF NOT EXISTS end_date            DATE,
    ADD COLUMN IF NOT EXISTS target_hire_count   INTEGER
        CONSTRAINT job_campaigns_target_hire_check CHECK (target_hire_count IS NULL OR target_hire_count > 0),
    ADD COLUMN IF NOT EXISTS campaign_owner_id   UUID
        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS notes               TEXT,
    ADD COLUMN IF NOT EXISTS public_title        VARCHAR(255),
    ADD COLUMN IF NOT EXISTS is_publicly_listed  BOOLEAN NOT NULL DEFAULT FALSE;

-- Index for owner lookups
CREATE INDEX IF NOT EXISTS idx_job_campaigns_owner
    ON job_campaigns(campaign_owner_id)
    WHERE campaign_owner_id IS NOT NULL;

COMMENT ON COLUMN job_campaigns.start_date IS
    'Campaign window start — informational, system never auto-activates on this date.';
COMMENT ON COLUMN job_campaigns.end_date IS
    'Campaign window end — informational, system never auto-closes on this date.';
COMMENT ON COLUMN job_campaigns.target_hire_count IS
    'Total headcount goal across all campaign jobs. Drives fill-rate reporting. '
    'System never auto-closes campaign when this target is reached.';
COMMENT ON COLUMN job_campaigns.campaign_owner_id IS
    'The recruiter / manager responsible for this campaign. May differ from created_by.';
COMMENT ON COLUMN job_campaigns.public_title IS
    'External-facing title for future public campaign listing page. '
    'NULL = use internal name field.';
COMMENT ON COLUMN job_campaigns.is_publicly_listed IS
    'Architecture placeholder for future public campaign listing feature. '
    'Has no effect on current access control.';

COMMIT;
