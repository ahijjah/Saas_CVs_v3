-- Migration 038: Email verification and forced password change
-- Adds email verification workflow and must_change_password flag to users.
-- Extends the users.status CHECK to include 'pending_email_verification'.

SET search_path TO cv_analyzer;

-- ── 1. Extend status CHECK constraint ──────────────────────────────────────
-- Drop the inline, auto-named constraint first (name varies by PG version).
DO $$
DECLARE
  cname text;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid = 'users'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) LIKE '%status%';
  IF cname IS NOT NULL THEN
    EXECUTE 'ALTER TABLE users DROP CONSTRAINT ' || quote_ident(cname);
  END IF;
END
$$;

ALTER TABLE users
  ADD CONSTRAINT users_status_check
  CHECK (status IN ('active', 'disabled', 'pending_email_verification'));

-- ── 2. Add email verification fields ───────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verified_at             TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS email_verification_token_hash TEXT,
  ADD COLUMN IF NOT EXISTS email_verification_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS must_change_password          BOOLEAN NOT NULL DEFAULT false;

-- ── 3. Index for fast token lookups ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_email_verification_token
  ON users (email_verification_token_hash)
  WHERE email_verification_token_hash IS NOT NULL;

-- ── 4. Backfill: mark existing active users as already verified ─────────────
UPDATE users
  SET email_verified_at = COALESCE(created_at, now())
  WHERE status = 'active'
    AND email_verified_at IS NULL;
