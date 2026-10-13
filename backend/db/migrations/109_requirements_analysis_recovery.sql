-- Migration 109: versioned recovery for a requirements-v2 analysis that is stale or unverified (PREPARED; applied with the rest of the migrations).
--
-- ONE append-only table, requirements_analysis_log, holds every event of every analysis version of a job: a re-analysis request, its completion or
-- rejection, a keep decision, the application of a candidate (take or merge), the discard of a candidate, and the supersession of a previous active
-- draft. The payload of a completed candidate (its original snapshot, its pipeline record and its document) and of a superseded draft is stored here,
-- so the history is complete without touching job_criteria's original snapshot, which is never rewritten.
--
-- Immutability is enforced by the database: UPDATE, DELETE and TRUNCATE on the table raise an error (triggers below). Nothing in this migration
-- deletes or rewrites rows, and no retention policy is applied (none has been approved).
--
-- The feature is OFF by default: system_config 'requirements_v2.recovery_enabled' = 'false'. With it off, no request can be created and no
-- candidate is applied. Requests also require requirements-v2 extraction to be available (the v2 switch and a valid prompt/model).
--
-- Application role (cv_app): it may read and append the history, and nothing else. The privilege grants below are not durable on their own (a later
-- GRANT ... ON ALL TABLES restores them), which is why the trigger is the real guarantee: it refuses UPDATE, DELETE and TRUNCATE for every role that
-- is not a superuser. The application role must not be a superuser or the table's owner (see the deployment notes).
--
-- Rollback: while NO event exists, DROP the two triggers, DROP TABLE requirements_analysis_log and DELETE the system_config row. Once any event
-- exists the history is not dropped by rollback: rollback is the switch set to 'false' (the default), and any removal of the history is a separate,
-- approved archival decision (export with COPY first).
-- Additive only. Idempotent (IF NOT EXISTS; ON CONFLICT DO NOTHING; DROP TRIGGER IF EXISTS before CREATE TRIGGER; GRANT is repeatable).

BEGIN;
SET search_path = cv_analyzer;

CREATE TABLE IF NOT EXISTS requirements_analysis_log (
    log_id          BIGSERIAL    PRIMARY KEY,
    -- no foreign key to jobs: history must outlive the job row's own changes, and rows are never deleted
    job_id          UUID         NOT NULL,
    tenant_id       UUID         NOT NULL,
    version_id      UUID         NOT NULL,
    version_no      INTEGER      NOT NULL CHECK (version_no >= 1),
    kind            VARCHAR(24)  NOT NULL CHECK (kind IN ('candidate', 'superseded_draft')),
    event           VARCHAR(24)  NOT NULL CHECK (event IN ('requested', 'completed', 'rejected', 'kept', 'applied', 'discarded', 'superseded')),
    request_key     UUID         NULL,            -- idempotency key of a request (set on 'requested' only)
    attempt_token   UUID         NULL,
    actor_user_id   UUID         NULL,
    job_revision    INTEGER      NULL,            -- the job's requirements revision at this event
    input_sha256    VARCHAR(64)  NULL,            -- the model-input binding this event concerns
    payload         JSONB        NULL,            -- completed: original, record, document, model_inputs, context_review; superseded: the draft
    details         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- an idempotency key names at most one request per job
CREATE UNIQUE INDEX IF NOT EXISTS ux_req_log_request_key ON requirements_analysis_log (job_id, request_key) WHERE event = 'requested';
-- a version number is used at most once per job
CREATE UNIQUE INDEX IF NOT EXISTS ux_req_log_version_no ON requirements_analysis_log (job_id, version_no) WHERE event = 'requested';
-- each single-occurrence event happens at most once per version
CREATE UNIQUE INDEX IF NOT EXISTS ux_req_log_once ON requirements_analysis_log (version_id, event)
    WHERE event IN ('requested', 'completed', 'rejected', 'applied', 'discarded', 'superseded');
-- a candidate has at most one terminal decision: no double application, no apply after discard
CREATE UNIQUE INDEX IF NOT EXISTS ux_req_log_terminal ON requirements_analysis_log (version_id) WHERE event IN ('applied', 'discarded');
CREATE INDEX IF NOT EXISTS ix_req_log_job ON requirements_analysis_log (job_id, log_id);

CREATE OR REPLACE FUNCTION requirements_analysis_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'requirements_analysis_log is append-only: % is refused', TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$;

DROP TRIGGER IF EXISTS requirements_analysis_log_no_change ON requirements_analysis_log;
CREATE TRIGGER requirements_analysis_log_no_change BEFORE UPDATE OR DELETE ON requirements_analysis_log
    FOR EACH ROW EXECUTE FUNCTION requirements_analysis_log_is_append_only();

DROP TRIGGER IF EXISTS requirements_analysis_log_no_truncate ON requirements_analysis_log;
CREATE TRIGGER requirements_analysis_log_no_truncate BEFORE TRUNCATE ON requirements_analysis_log
    FOR EACH STATEMENT EXECUTE FUNCTION requirements_analysis_log_is_append_only();

-- The application role: append and read only. Granted here because this table is created after schema.sql's grants on ALL TABLES.
GRANT USAGE ON SCHEMA cv_analyzer TO cv_app;
GRANT SELECT, INSERT ON requirements_analysis_log TO cv_app;
GRANT USAGE, SELECT ON SEQUENCE requirements_analysis_log_log_id_seq TO cv_app;
REVOKE UPDATE, DELETE, TRUNCATE ON requirements_analysis_log FROM cv_app;

INSERT INTO system_config (key, value, type, category, editable, description)
VALUES ('requirements_v2.recovery_enabled', 'false', 'boolean', 'ai', true,
        'Feature switch for re-analysis of a stale or unverified requirements-v2 analysis. false (default) = no re-analysis can be requested or applied.')
ON CONFLICT (key) DO NOTHING;

COMMIT;
