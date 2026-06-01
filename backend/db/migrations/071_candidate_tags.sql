-- 071_candidate_tags.sql
-- Add candidate tags system for better candidate organization and rediscovery

BEGIN;

SET search_path = cv_analyzer;

-- ── candidate_tags: normalized tag lookup table ────────────────────────────
CREATE TABLE IF NOT EXISTS candidate_tags (
  tag_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  tag_name VARCHAR(100) NOT NULL,
  color VARCHAR(10),                    -- hex color like #3b82f6 or predefined
  created_by UUID NOT NULL REFERENCES users(user_id) ON DELETE SET NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, tag_name),
  CHECK (tag_name ~ '^\S+.*\S+$' OR tag_name ~ '^\S+$')  -- no leading/trailing spaces
);

CREATE INDEX IF NOT EXISTS idx_candidate_tags_tenant
  ON candidate_tags(tenant_id);

CREATE INDEX IF NOT EXISTS idx_candidate_tags_name
  ON candidate_tags(tenant_id, tag_name);

-- ── application_candidate_tags: junction table ─────────────────────────────
CREATE TABLE IF NOT EXISTS application_candidate_tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id UUID NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES candidate_tags(tag_id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
  UNIQUE(application_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_app_candidate_tags_app
  ON application_candidate_tags(application_id);

CREATE INDEX IF NOT EXISTS idx_app_candidate_tags_tag
  ON application_candidate_tags(tag_id);

COMMIT;
