-- 022_notification_log.sql
-- Intake-phase email notification log + recruiter alert configuration.
--
-- Changes:
--   1. Fix email_ingest_log CHECK constraints (add 'unknown'/'duplicate'/'unassigned')
--   2. Create notification_log table (delivery tracking + audit)
--   3. Add per-job notification columns to jobs
--   4. Add system_config keys for unmatched-email recruiter alert

BEGIN;

SET search_path = cv_analyzer;

-- ════════════════════════════════════════════════════════════════════
-- 1. Fix email_ingest_log CHECK constraints
--    The intake worker uses 'unknown' (ingestion_mode) and
--    'duplicate' / 'unassigned' (status) which the original schema
--    did not include.
-- ════════════════════════════════════════════════════════════════════

ALTER TABLE email_ingest_log
    DROP CONSTRAINT IF EXISTS email_ingest_log_status_check,
    DROP CONSTRAINT IF EXISTS email_ingest_log_ingestion_mode_check;

ALTER TABLE email_ingest_log
    ADD CONSTRAINT email_ingest_log_status_check
        CHECK (status IN (
            'received', 'processing', 'scored',
            'duplicate', 'failed', 'skipped', 'unassigned'
        )),
    ADD CONSTRAINT email_ingest_log_ingestion_mode_check
        CHECK (
            ingestion_mode IN ('platform_email', 'forwarding', 'unknown')
            OR ingestion_mode IS NULL
        );

-- ════════════════════════════════════════════════════════════════════
-- 2. notification_log
--    Audit trail + delivery status for all outbound notifications
--    triggered during CV intake (candidate error/receipt emails and
--    recruiter alerts).
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS notification_log (
    notification_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Classification
    notification_type VARCHAR(60)  NOT NULL,
        -- 'CANDIDATE_INTAKE'         — email to candidate
        -- 'RECRUITER_UNMATCHED_ALERT'— alert when too many emails fail routing
        -- 'RECRUITER_INACTIVE_ALERT' — alert when email sent to inactive job
    intake_status     VARCHAR(60)  NOT NULL,
        -- IntakeStatus enum value (see intake_notification_service.py)

    -- Recipient
    recipient_email   VARCHAR(255) NOT NULL,
    recipient_type    VARCHAR(20)  NOT NULL DEFAULT 'candidate'
                          CHECK (recipient_type IN ('candidate', 'recruiter')),

    -- Context references (all nullable — not every event has all of these)
    tenant_id         UUID         REFERENCES tenants (tenant_id)            ON DELETE SET NULL,
    job_id            UUID         REFERENCES jobs    (job_id)                ON DELETE SET NULL,
    application_id    UUID         REFERENCES applications (application_id)  ON DELETE SET NULL,
    intake_log_id     UUID         REFERENCES email_ingest_log (log_id)      ON DELETE SET NULL,
    duplicate_log_id  UUID         REFERENCES duplicate_application_logs (log_id) ON DELETE SET NULL,

    -- Payload for email rendering (job title, per-attachment results, counts, etc.)
    details           JSONB,

    -- Delivery state
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'sent', 'failed')),
    error_message     TEXT,
    sent_at           TIMESTAMPTZ,
    retry_count       SMALLINT     NOT NULL DEFAULT 0,

    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Idempotency: one candidate notification per (intake log event × status × recipient).
-- Recruiter alerts are not subject to this constraint (they have no intake_log_id
-- dependency and are suppressed via time-window logic in the service layer instead).
CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_intake_candidate
    ON notification_log (intake_log_id, intake_status, recipient_email)
    WHERE intake_log_id IS NOT NULL AND recipient_type = 'candidate';

CREATE INDEX IF NOT EXISTS idx_notification_log_tenant  ON notification_log (tenant_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_job     ON notification_log (job_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_status  ON notification_log (status);
CREATE INDEX IF NOT EXISTS idx_notification_log_type    ON notification_log (notification_type);
CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log (created_at DESC);

-- RLS: super_admin sees all; tenants see their own.
-- notification_log rows with tenant_id IS NULL (e.g. global unmatched alerts)
-- are visible only to super_admin.
ALTER TABLE notification_log ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE ON notification_log TO cv_app;

CREATE POLICY notification_log_isolation ON notification_log
    USING (
        current_setting('app.current_role', true) = 'super_admin'
        OR (
            tenant_id IS NOT NULL
            AND tenant_id::text = current_setting('app.current_tenant_id', true)
        )
    );

-- ════════════════════════════════════════════════════════════════════
-- 3. Per-job notification columns
-- ════════════════════════════════════════════════════════════════════

-- Controls all candidate intake notifications for this job
-- (RECEIVED_SUCCESSFULLY, error types, DUPLICATE_APPLICATION)
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS send_intake_notifications        BOOLEAN      NOT NULL DEFAULT TRUE;

-- Recruiter inactive-job alert
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS notify_recruiter_on_inactive     BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Override recipient; NULL = use job creator's email
    ADD COLUMN IF NOT EXISTS recruiter_alert_email            VARCHAR(255);

-- ════════════════════════════════════════════════════════════════════
-- 4. System-config keys for unmatched-email recruiter alert
-- ════════════════════════════════════════════════════════════════════

INSERT INTO system_config (key, value, type, category, description, editable)
VALUES
    (
        'unmatched_alert_threshold',
        '50',
        'number',
        'email',
        'Number of unidentified inbound emails (status=''unassigned'') required to '
        'trigger a recruiter alert. Counted since the last alert was sent.',
        true
    ),
    (
        'unmatched_alert_email',
        '',
        'string',
        'email',
        'Recipient address for JOB_NOT_IDENTIFIED recruiter alerts. '
        'Leave empty to disable this alert.',
        true
    )
ON CONFLICT (key) DO NOTHING;

COMMIT;
