-- 036_billing_cycle.sql
-- Add billing_cycle field to tenants to track monthly vs yearly subscription cadence.

SET search_path TO cv_analyzer;

BEGIN;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS billing_cycle VARCHAR(20) NULL;

ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_billing_cycle_check;
ALTER TABLE tenants
    ADD CONSTRAINT tenants_billing_cycle_check
    CHECK (billing_cycle IN ('monthly', 'yearly'));

COMMIT;
