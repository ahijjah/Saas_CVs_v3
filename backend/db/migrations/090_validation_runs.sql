-- 090_validation_runs.sql
--
-- Introduces application_knockout_validation_runs to track each execution of
-- Screening Answer Validation. Used to:
--   1. Prevent duplicate/unnecessary AI re-runs (if fresh, return already_completed)
--   2. Display validation summary (last run, counts, prompt version, model)
--   3. Store audit history of all validation passes
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS + idempotent index creation.

BEGIN;
SET search_path = cv_analyzer;

CREATE TABLE IF NOT EXISTS application_knockout_validation_runs (
    run_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID         NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    tenant_id           UUID         NOT NULL,
    validation_status   VARCHAR(20)  NOT NULL DEFAULT 'completed',
    -- completed | partial | failed
    questions_count     INTEGER      NOT NULL DEFAULT 0,
    validated_count     INTEGER      NOT NULL DEFAULT 0,     -- supported + contradicted + not_supported
    suggested_count     INTEGER      NOT NULL DEFAULT 0,
    cannot_count        INTEGER      NOT NULL DEFAULT 0,     -- cannot_validate + cannot_determine
    not_supported_count INTEGER      NOT NULL DEFAULT 0,
    prompt_version      INTEGER,
    model               VARCHAR(100),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ko_val_runs_app
    ON application_knockout_validation_runs (application_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ko_val_runs_tenant
    ON application_knockout_validation_runs (tenant_id);

ALTER TABLE application_knockout_validation_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ko_val_runs_tenant_isolation ON application_knockout_validation_runs;
CREATE POLICY ko_val_runs_tenant_isolation ON application_knockout_validation_runs
    USING (
        current_setting('app.current_role',      true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );

COMMIT;
