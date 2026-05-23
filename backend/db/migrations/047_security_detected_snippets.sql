BEGIN;
SET search_path = cv_analyzer;

-- Add safe human-readable snippets column to applications.
-- Stores up to 5 short (≤80 char) sanitized excerpts of suspicious text
-- detected during the security check — never raw CV text or full payloads.

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS security_detected_snippets TEXT[];

COMMIT;
