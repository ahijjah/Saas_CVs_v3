-- Migration 064: Recruiter Assignment & Ownership
-- Adds assignment tracking to applications: who is responsible, when assigned, by whom.

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS assigned_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS assigned_by      UUID REFERENCES users(user_id) ON DELETE SET NULL;

-- Fast lookup for "assigned to me" and "unassigned" filters
CREATE INDEX IF NOT EXISTS idx_applications_assigned_user
    ON applications(tenant_id, assigned_user_id)
    WHERE assigned_user_id IS NOT NULL;
