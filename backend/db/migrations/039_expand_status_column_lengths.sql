-- Migration 039: Expand status column lengths for production compatibility
-- tenants.subscription_status and users.status were VARCHAR(20) by default,
-- which caused truncation errors once longer values were introduced.
-- Both columns are widened to VARCHAR(50) to accommodate current and future values.

SET search_path TO cv_analyzer;

ALTER TABLE tenants
  ALTER COLUMN subscription_status TYPE VARCHAR(50);

ALTER TABLE users
  ALTER COLUMN status TYPE VARCHAR(50);
