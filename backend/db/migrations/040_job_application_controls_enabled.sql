BEGIN;
SET search_path TO cv_analyzer;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS job_application_controls_enabled BOOLEAN NOT NULL DEFAULT false;

COMMIT;
