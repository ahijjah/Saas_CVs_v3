-- 072_talent_pool_foundation.sql
-- Add talent pool foundation to allow candidates to exist beyond a single workflow

BEGIN;

SET search_path = cv_analyzer;

-- ── applications: add is_talent_pool flag ───────────────────────────────────
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS is_talent_pool BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_applications_talent_pool
  ON applications(job_id, is_talent_pool)
  WHERE is_talent_pool = TRUE;

-- Index for talent pool queries scoped by tenant
CREATE INDEX IF NOT EXISTS idx_applications_talent_pool_tenant
  ON applications(is_talent_pool)
  WHERE is_talent_pool = TRUE;

COMMIT;
