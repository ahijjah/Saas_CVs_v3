-- 035_subscription_mvp_fields.sql
-- Add max_clients, api_access_enabled, branding_level to tenants.
-- Expand subscription_status CHECK constraint with all SaaS lifecycle values.
-- Make plan nullable so pending_plan_selection tenants can have NULL plan.

BEGIN;

-- 1. Make plan nullable (no plan yet for pending tenants)
ALTER TABLE tenants ALTER COLUMN plan DROP NOT NULL;
ALTER TABLE tenants ALTER COLUMN plan DROP DEFAULT;

-- 2. Add new limit/feature columns
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS max_clients        INTEGER,           -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS api_access_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS branding_level     VARCHAR(20) NOT NULL DEFAULT 'none';

-- 3. branding_level check constraint
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_branding_level_check;
ALTER TABLE tenants
    ADD CONSTRAINT tenants_branding_level_check
    CHECK (branding_level IN ('none', 'basic', 'white_label'));

-- 4. Expand subscription_status check constraint
ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_subscription_status_check;
ALTER TABLE tenants
    ADD CONSTRAINT tenants_subscription_status_check
    CHECK (subscription_status IN (
        'pending_plan_selection',
        'pending_payment',
        'pending_sales_contact',
        'trial',
        'active',
        'grace',
        'trial_expired',
        'suspended',
        'cancelled',
        'cancelled_pending_expiry',
        'expired'
    ));

-- 5. Backfill existing tenants (max_clients only; other columns default on ADD COLUMN)
UPDATE tenants SET max_clients = 1 WHERE max_clients IS NULL;

-- 6. Ensure existing tenants without a plan_code retain 'starter' so queries don't break
UPDATE tenants SET plan = 'starter' WHERE plan IS NULL;

COMMIT;
