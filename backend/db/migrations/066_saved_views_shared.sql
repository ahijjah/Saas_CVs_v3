-- Migration 066: Shared Saved Views
-- Adds visibility_type and created_by to support team-shared views alongside private ones.
-- Admins can create tenant_shared views visible to all users in the tenant.

ALTER TABLE candidate_saved_views
    ADD COLUMN IF NOT EXISTS visibility_type VARCHAR(20) NOT NULL DEFAULT 'private'
        CHECK (visibility_type IN ('private', 'tenant_shared')),
    ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(user_id) ON DELETE SET NULL;

-- Backfill created_by from user_id for all existing views
UPDATE candidate_saved_views
   SET created_by = user_id
 WHERE created_by IS NULL;

-- Index for listing tenant-shared views (admin creates, all see)
CREATE INDEX IF NOT EXISTS idx_saved_views_tenant_shared
    ON candidate_saved_views(tenant_id, visibility_type)
    WHERE visibility_type = 'tenant_shared';
