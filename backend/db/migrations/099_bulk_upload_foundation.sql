-- Migration 099: E-01 Phase 1 — Bulk Excel Upload Foundation
--
-- Adds the database objects needed for the "Bulk Upload + Answers Sheet"
-- feature.  This migration is Phase 1 only: it creates the storage
-- foundation.  Excel parsing, ZIP processing, validation engine, import
-- logic, frontend, and report export are NOT part of this phase.
--
-- New tables:
--   bulk_upload_batches  — one row per uploaded Excel + ZIP pair
--   bulk_upload_rows     — one row per Excel data row in a batch
--
-- Constraint extension:
--   applications.submission_source CHECK is widened to include
--   'bulk_excel_upload'.
--
-- Both tables use tenant_id-scoped RLS, matching the rest of the schema.
-- Both tables are safe to re-run (CREATE TABLE IF NOT EXISTS + IF NOT EXISTS
-- guards on every index and policy).
--
-- Batch status lifecycle:
--   created → uploaded → validating → validated → importing →
--   imported → processing → completed | failed
--
-- Row validation status: pending | ready | warning | error
-- Row import   status:   pending | imported | skipped | failed
-- Row processing status: pending | queued | processing | completed | failed

BEGIN;
SET search_path = cv_analyzer;

-- ── 1. bulk_upload_batches ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bulk_upload_batches (
    batch_id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID          NOT NULL,
    job_id                  UUID          NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    created_by              UUID          REFERENCES users(user_id) ON DELETE SET NULL,

    -- Status lifecycle
    batch_status            VARCHAR(20)   NOT NULL DEFAULT 'created',
    --   created | uploaded | validating | validated | importing |
    --   imported | processing | completed | failed

    -- Source file metadata (populated after upload)
    original_filename       VARCHAR(500),
    source_file_path        VARCHAR(1000),   -- server-side path to the Excel file
    zip_file_path           VARCHAR(1000),   -- server-side path to the CV ZIP (optional)

    -- Row summary counters (maintained by update_bulk_upload_batch_summary)
    row_count               INTEGER       NOT NULL DEFAULT 0,
    valid_row_count         INTEGER       NOT NULL DEFAULT 0,
    warning_row_count       INTEGER       NOT NULL DEFAULT 0,
    error_row_count         INTEGER       NOT NULL DEFAULT 0,
    imported_row_count      INTEGER       NOT NULL DEFAULT 0,
    skipped_row_count       INTEGER       NOT NULL DEFAULT 0,
    failed_row_count        INTEGER       NOT NULL DEFAULT 0,
    completed_row_count     INTEGER       NOT NULL DEFAULT 0,

    -- Error details for batch-level failures
    error_message           TEXT,

    -- Audit timestamps
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    validated_at            TIMESTAMPTZ,
    imported_at             TIMESTAMPTZ,
    processing_started_at   TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,

    CONSTRAINT bulk_upload_batches_status_check
        CHECK (batch_status IN (
            'created', 'uploaded', 'validating', 'validated',
            'importing', 'imported', 'processing', 'completed', 'failed'
        ))
);

COMMENT ON TABLE  bulk_upload_batches                       IS 'One record per bulk upload session (Excel sheet + optional CV ZIP).';
COMMENT ON COLUMN bulk_upload_batches.batch_id              IS 'Primary key — shared with the caller as a handle for subsequent operations.';
COMMENT ON COLUMN bulk_upload_batches.batch_status          IS 'Lifecycle: created→uploaded→validating→validated→importing→imported→processing→completed|failed.';
COMMENT ON COLUMN bulk_upload_batches.row_count             IS 'Total rows parsed from the Excel sheet. 0 until parsing completes.';
COMMENT ON COLUMN bulk_upload_batches.valid_row_count       IS 'Rows with validation_status=ready. Maintained by update_bulk_upload_batch_summary.';
COMMENT ON COLUMN bulk_upload_batches.warning_row_count     IS 'Rows with validation_status=warning. Can still be imported.';
COMMENT ON COLUMN bulk_upload_batches.error_row_count       IS 'Rows with validation_status=error. Blocked from import.';
COMMENT ON COLUMN bulk_upload_batches.completed_row_count   IS 'Rows whose applications have reached processing_status=completed.';

CREATE INDEX IF NOT EXISTS idx_bulk_batches_tenant
    ON bulk_upload_batches (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bulk_batches_job
    ON bulk_upload_batches (job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bulk_batches_status
    ON bulk_upload_batches (tenant_id, batch_status)
    WHERE batch_status NOT IN ('completed', 'failed');

ALTER TABLE bulk_upload_batches ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bulk_batches_tenant_isolation ON bulk_upload_batches;
CREATE POLICY bulk_batches_tenant_isolation ON bulk_upload_batches
    USING (
        current_setting('app.current_role',        true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );


-- ── 2. bulk_upload_rows ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bulk_upload_rows (
    row_id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id                UUID          NOT NULL REFERENCES bulk_upload_batches(batch_id) ON DELETE CASCADE,
    tenant_id               UUID          NOT NULL,
    job_id                  UUID          NOT NULL,
    row_number              INTEGER       NOT NULL,  -- 1-indexed row in the Excel data sheet

    -- Validation lifecycle
    validation_status       VARCHAR(20)   NOT NULL DEFAULT 'pending',
    --   pending | ready | warning | error

    -- Import lifecycle
    import_status           VARCHAR(20)   NOT NULL DEFAULT 'pending',
    --   pending | imported | skipped | failed

    -- Processing lifecycle (after CV is scored)
    processing_status       VARCHAR(20)   NOT NULL DEFAULT 'pending',
    --   pending | queued | processing | completed | failed

    -- Candidate fields extracted from the Excel row
    -- Shape: { "first_name": "...", "last_name": "...", "email": "...",
    --          "phone": "...", "linkedin_url": "...", ... }
    candidate_data          JSONB,

    -- Knockout answers extracted from the Excel columns
    -- Shape: { "<question_id>": "<answer_value>", ... }  OR
    --        { "<question_text>": "<answer_value>", ... } before question matching
    knockout_answers        JSONB,

    -- CV file name from the companion ZIP archive that maps to this row
    matched_cv_filename     VARCHAR(500),

    -- Set after successful import
    application_id          UUID          REFERENCES applications(application_id) ON DELETE SET NULL,

    -- Validation detail (arrays of human-readable strings)
    validation_warnings     JSONB,   -- [ "Phone number missing", ... ]
    validation_errors       JSONB,   -- [ "Email address is required", ... ]

    -- Processing-level error detail
    error_message           TEXT,

    -- Audit timestamps
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    validated_at            TIMESTAMPTZ,
    imported_at             TIMESTAMPTZ,
    processing_started_at   TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,

    CONSTRAINT bulk_upload_rows_validation_status_check
        CHECK (validation_status IN ('pending', 'ready', 'warning', 'error')),
    CONSTRAINT bulk_upload_rows_import_status_check
        CHECK (import_status IN ('pending', 'imported', 'skipped', 'failed')),
    CONSTRAINT bulk_upload_rows_processing_status_check
        CHECK (processing_status IN ('pending', 'queued', 'processing', 'completed', 'failed')),
    CONSTRAINT bulk_upload_rows_row_number_positive
        CHECK (row_number >= 1)
);

COMMENT ON TABLE  bulk_upload_rows                          IS 'One record per data row in a bulk upload Excel sheet.';
COMMENT ON COLUMN bulk_upload_rows.row_number               IS '1-indexed position of this row in the Excel data sheet (row 1 = first data row after the header).';
COMMENT ON COLUMN bulk_upload_rows.candidate_data           IS 'JSONB: candidate fields parsed from the Excel row (name, email, phone, etc.).';
COMMENT ON COLUMN bulk_upload_rows.knockout_answers         IS 'JSONB: {question_id → answer_value} map extracted from the Excel answers columns.';
COMMENT ON COLUMN bulk_upload_rows.matched_cv_filename      IS 'File name inside the companion ZIP that was matched to this Excel row.';
COMMENT ON COLUMN bulk_upload_rows.application_id           IS 'Set after successful import. NULL until the row is imported.';
COMMENT ON COLUMN bulk_upload_rows.validation_warnings      IS 'JSONB array of human-readable warnings. Row can still be imported.';
COMMENT ON COLUMN bulk_upload_rows.validation_errors        IS 'JSONB array of human-readable errors. Row is blocked from import.';

CREATE INDEX IF NOT EXISTS idx_bulk_rows_batch
    ON bulk_upload_rows (batch_id, row_number);

CREATE INDEX IF NOT EXISTS idx_bulk_rows_tenant
    ON bulk_upload_rows (tenant_id);

CREATE INDEX IF NOT EXISTS idx_bulk_rows_application
    ON bulk_upload_rows (application_id)
    WHERE application_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bulk_rows_validation_status
    ON bulk_upload_rows (batch_id, validation_status);

CREATE INDEX IF NOT EXISTS idx_bulk_rows_import_status
    ON bulk_upload_rows (batch_id, import_status);

CREATE INDEX IF NOT EXISTS idx_bulk_rows_processing_status
    ON bulk_upload_rows (batch_id, processing_status)
    WHERE processing_status NOT IN ('completed', 'failed');

-- Unique: one row per (batch, row_number)
CREATE UNIQUE INDEX IF NOT EXISTS uq_bulk_rows_batch_row_number
    ON bulk_upload_rows (batch_id, row_number);

ALTER TABLE bulk_upload_rows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bulk_rows_tenant_isolation ON bulk_upload_rows;
CREATE POLICY bulk_rows_tenant_isolation ON bulk_upload_rows
    USING (
        current_setting('app.current_role',        true) = 'super_admin'
        OR tenant_id::text = current_setting('app.current_tenant_id', true)
    );


-- ── 3. Widen submission_source to include 'bulk_excel_upload' ─────────────

ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_submission_source_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_submission_source_check
    CHECK (submission_source IN (
        'manual_upload',
        'email_forwarding',
        'platform_email',
        'public_apply',
        'bulk_excel_upload'
    ));

COMMENT ON CONSTRAINT applications_submission_source_check ON applications
    IS 'Allowed intake sources. bulk_excel_upload added in migration 099 for E-01.';

COMMIT;
