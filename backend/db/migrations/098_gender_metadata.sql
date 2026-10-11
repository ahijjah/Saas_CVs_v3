-- Migration 098: Gender metadata columns on applications table
--
-- Stores optional applicant gender metadata inferred from CV text only.
-- These fields are metadata for filtering/reporting — never used in scoring.
--
-- Inference sources allowed: title (Mr./Mrs.), pronouns, explicit self-statement.
-- Inference sources forbidden: photo, image analysis, nationality, religion.
-- Default: unknown / 0.0 / unknown when no clear signal is present.

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS gender_value              VARCHAR(10)    DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS gender_confidence         NUMERIC(3, 2)  DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS gender_basis              VARCHAR(30)    DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS gender_source             VARCHAR(20)    DEFAULT 'cv_text',
    ADD COLUMN IF NOT EXISTS gender_used_for_scoring   BOOLEAN        DEFAULT FALSE;

-- Index for the gender filter on the candidate list endpoint
CREATE INDEX IF NOT EXISTS idx_applications_gender_value
    ON applications (tenant_id, gender_value)
    WHERE gender_value IS NOT NULL;

COMMENT ON COLUMN applications.gender_value             IS 'Inferred gender: male | female | unknown. Metadata only — never influences scoring.';
COMMENT ON COLUMN applications.gender_confidence        IS '0.0–1.0 confidence in the inference. 0.0 = no signal found.';
COMMENT ON COLUMN applications.gender_basis             IS 'Signal used: title | pronoun | explicit_cv_text | unknown.';
COMMENT ON COLUMN applications.gender_source            IS 'Always cv_text — images and external sources are forbidden.';
COMMENT ON COLUMN applications.gender_used_for_scoring  IS 'Always FALSE. Enforced in application code and documented here.';
