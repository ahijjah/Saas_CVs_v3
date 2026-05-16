-- Migration 027: Public apply intake + max applications intake control

BEGIN;
SET search_path = cv_analyzer;

-- ── Intake controls on jobs ────────────────────────────────────────────────
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS max_applications               INTEGER,          -- NULL = unlimited
    ADD COLUMN IF NOT EXISTS auto_close_when_limit_reached  BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN jobs.max_applications               IS 'Maximum number of valid (non-duplicate, non-failed) applications accepted. NULL = unlimited.';
COMMENT ON COLUMN jobs.auto_close_when_limit_reached  IS 'When max_applications is reached, automatically set job status to closed.';

-- ── Additional candidate fields for public/portal submissions ──────────────
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS candidate_phone  VARCHAR(50),
    ADD COLUMN IF NOT EXISTS cover_letter     TEXT;

COMMENT ON COLUMN applications.candidate_phone IS 'Phone number provided by candidate on public apply form.';
COMMENT ON COLUMN applications.cover_letter    IS 'Cover letter text provided by candidate on public apply form.';

COMMIT;
