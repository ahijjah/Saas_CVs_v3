-- Migration 032: Unified application intake log
--
-- A single audit table covering all intake paths: email (imap), public_apply,
-- manual_upload, and future api_upload.  The existing email_ingest_log table
-- is kept intact for backward compatibility.

CREATE TABLE IF NOT EXISTS application_intake_log (
    intake_log_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                         UUID NOT NULL,
    job_id                            UUID,
    application_id                    UUID,

    -- How the CV arrived
    intake_method                     VARCHAR(30) NOT NULL,   -- email | public_apply | manual_upload | api_upload
    status                            VARCHAR(50) NOT NULL,   -- mirrors IntakeStatus values

    -- Candidate identity (as supplied at intake time)
    candidate_email                   VARCHAR(255),
    candidate_name                    VARCHAR(255),

    -- Source identifiers
    source_identifier                 TEXT,          -- IMAP uid, form session id, API request id, etc.
    source_message_id                 TEXT,          -- email Message-ID header
    sender_email                      VARCHAR(255),
    recipient_email                   VARCHAR(255),
    subject                           TEXT,

    -- File metadata
    original_filename                 TEXT,
    file_hash                         VARCHAR(64),   -- SHA-256 hex
    file_size_bytes                   INTEGER,
    mime_type                         VARCHAR(100),

    -- Outcome details
    error_message                     TEXT,
    duplicate_reference_application_id UUID,
    duplicate_log_id                  UUID,

    -- Operational timestamps
    received_at                       TIMESTAMPTZ,
    processing_started_at             TIMESTAMPTZ,
    processed_at                      TIMESTAMPTZ,
    processing_duration_ms            INTEGER,
    scoring_enqueued_at               TIMESTAMPTZ,
    notification_queued_at            TIMESTAMPTZ,

    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_ail_tenant_id      ON application_intake_log (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ail_job_id         ON application_intake_log (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ail_application_id ON application_intake_log (application_id) WHERE application_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ail_intake_method  ON application_intake_log (intake_method);
CREATE INDEX IF NOT EXISTS idx_ail_status         ON application_intake_log (status);
CREATE INDEX IF NOT EXISTS idx_ail_file_hash      ON application_intake_log (file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ail_message_id     ON application_intake_log (source_message_id) WHERE source_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ail_created_at     ON application_intake_log (created_at DESC);

-- Stuck-detection partial index (started but never completed)
CREATE INDEX IF NOT EXISTS idx_ail_stuck
    ON application_intake_log (processing_started_at)
    WHERE processed_at IS NULL AND processing_started_at IS NOT NULL;

-- updated_at auto-maintenance
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS trg_ail_updated_at ON application_intake_log;
CREATE TRIGGER trg_ail_updated_at
    BEFORE UPDATE ON application_intake_log
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Row-Level Security
ALTER TABLE application_intake_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY ail_tenant_isolation ON application_intake_log
    USING (
        tenant_id = CAST(current_setting('app.current_tenant_id', true) AS uuid)
        OR current_setting('app.current_role', true) = 'super_admin'
    );

COMMENT ON TABLE application_intake_log IS 'Unified intake audit log covering all CV submission paths';
COMMENT ON COLUMN application_intake_log.intake_method IS 'email | public_apply | manual_upload | api_upload';
COMMENT ON COLUMN application_intake_log.status        IS 'IntakeStatus enum value';
COMMENT ON COLUMN application_intake_log.file_hash     IS 'SHA-256 hex digest of raw file bytes';
