-- Migration 057: Application workflow status, recruiter notes, and workflow history

-- Add workflow_status to applications
ALTER TABLE cv_analyzer.applications
    ADD COLUMN IF NOT EXISTS workflow_status VARCHAR(50) NOT NULL DEFAULT 'new',
    ADD COLUMN IF NOT EXISTS recruiter_notes TEXT;

-- CHECK constraint for valid workflow statuses
ALTER TABLE cv_analyzer.applications
    DROP CONSTRAINT IF EXISTS applications_workflow_status_check;
ALTER TABLE cv_analyzer.applications
    ADD CONSTRAINT applications_workflow_status_check
        CHECK (workflow_status IN ('new', 'ai_processed', 'under_review', 'shortlisted', 'interviewing', 'offer_made', 'hired', 'rejected', 'withdrawn', 'on_hold'));

-- Backfill: scored applications that were qualified/partial go to 'ai_processed',
-- others stay 'new'
UPDATE cv_analyzer.applications
SET workflow_status = 'ai_processed'
WHERE processing_status = 'scored'
  AND decision IN ('qualified', 'partial')
  AND workflow_status = 'new';

-- Index for workflow status queries (e.g. filter by status per job)
CREATE INDEX IF NOT EXISTS idx_applications_workflow_status
    ON cv_analyzer.applications (job_id, workflow_status)
    WHERE workflow_status != 'new';

-- Workflow history table
CREATE TABLE IF NOT EXISTS cv_analyzer.application_workflow_history (
    history_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    changed_by      UUID REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    changed_by_name VARCHAR(255),
    from_status     VARCHAR(50),
    to_status       VARCHAR(50) NOT NULL,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_workflow_history_app_id
    ON cv_analyzer.application_workflow_history (application_id, created_at DESC);

-- RLS for workflow history
ALTER TABLE cv_analyzer.application_workflow_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON cv_analyzer.application_workflow_history;
CREATE POLICY tenant_isolation ON cv_analyzer.application_workflow_history
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);
