-- Migration 020: Intake metadata — uploader identity for manual uploads

BEGIN;

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS submitted_by_user_id  UUID,
    ADD COLUMN IF NOT EXISTS submitted_by_name     TEXT,
    ADD COLUMN IF NOT EXISTS submitted_by_email    TEXT;

COMMENT ON COLUMN applications.submitted_by_user_id IS
    'User who manually uploaded this CV. NULL for email/API intake.';
COMMENT ON COLUMN applications.submitted_by_name IS
    'Display name of the uploading user (denormalised for display without JOIN).';
COMMENT ON COLUMN applications.submitted_by_email IS
    'Email of the uploading user (denormalised for display).';

COMMIT;
