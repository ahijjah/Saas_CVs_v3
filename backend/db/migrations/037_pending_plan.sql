SET search_path TO cv_analyzer;

BEGIN;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS pending_plan VARCHAR(30) NULL;

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_pending_plan_check;
ALTER TABLE tenants ADD CONSTRAINT tenants_pending_plan_check
    CHECK (pending_plan IN ('trial', 'starter', 'professional', 'enterprise'));

COMMIT;
