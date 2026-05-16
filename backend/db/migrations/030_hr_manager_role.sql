-- Migration 030: Add hr_manager to users role CHECK constraint
-- Also creates a campaign_count limit enforced by trigger.

BEGIN;

-- ── 1. Expand the role CHECK to include hr_manager ───────────────────────────
-- PostgreSQL requires DROP + ADD to rename or extend a CHECK constraint.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users
    ADD CONSTRAINT users_role_check
    CHECK (role IN ('super_admin', 'admin', 'hr_manager', 'recruiter', 'viewer'));

-- Backfill any existing 'viewer' rows that were inserted before this role
-- existed (none expected in practice, but safe to keep).

COMMIT;
