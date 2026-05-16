BEGIN;
SET search_path = cv_analyzer;

-- Add extended metadata columns to jobs table (all nullable for safe migration)
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS experience_level      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS work_mode             VARCHAR(50),
    ADD COLUMN IF NOT EXISTS application_deadline  DATE,
    ADD COLUMN IF NOT EXISTS vacancies_count       INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS updated_by            UUID REFERENCES users(user_id) ON DELETE SET NULL;

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION cv_analyzer.set_jobs_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_jobs_updated_at ON jobs;
CREATE TRIGGER trg_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_jobs_updated_at();

COMMIT;
