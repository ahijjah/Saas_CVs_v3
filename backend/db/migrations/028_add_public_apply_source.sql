-- Migration 028: Add 'public_apply' to applications submission_source constraint
--
-- Migration 012 defined the constraint with only three values.
-- Public apply submissions (POST /applications/public) use source='public_apply'
-- which was rejected by the existing CHECK. This migration widens the constraint.

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_submission_source_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_submission_source_check
    CHECK (submission_source IN (
        'manual_upload',
        'email_forwarding',
        'platform_email',
        'public_apply'
    ));

COMMIT;
