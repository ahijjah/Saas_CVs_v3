-- Migration 094: copy-to-job submissions, and removal of the unusable submission_source default.
--
-- schema.sql gives applications.submission_source DEFAULT 'email', but the CHECK (migrations 012 and 028) has never allowed 'email',
-- so any INSERT that omits the column is refused. The copy-to-job route (POST …/reuse-to-job) omits it and has never been able to
-- insert a row.
--
-- This migration:
--   1. adds 'candidate_reuse' to the allowed values (a copy of a candidate onto another job, never auto-scored);
--   2. drops the default, rather than replacing it with a misleading value. Every INSERT must now name submission_source.
-- No column is added. Existing rows are unaffected: every allowed value they hold stays allowed.
--
-- Before this migration the INSERT statements in the application were enumerated: the intake service (named), and the copy route
-- (named by this change). The migration chain contains no INSERT INTO applications, and the seed data contains none either.

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
        'public_apply',
        'candidate_reuse'
    ));

ALTER TABLE applications
    ALTER COLUMN submission_source DROP DEFAULT;

COMMIT;
