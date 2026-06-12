-- 083_email_body_storage.sql
--
-- Adds email_body_plain to application_intake_log so the full plain-text
-- email body is preserved for AI analysis (knockout answer extraction, etc.).
--
-- email_body_plain is stored trimmed to a safe length (≤ 8,000 chars) by
-- the application layer — raw HTML and binary attachments are never stored.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS is safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE application_intake_log
    ADD COLUMN IF NOT EXISTS email_body_plain TEXT;

COMMIT;
