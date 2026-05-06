-- Add location, job_type, duration columns to jobs table
ALTER TABLE cv_analyzer.jobs
    ADD COLUMN IF NOT EXISTS location  VARCHAR(255),
    ADD COLUMN IF NOT EXISTS job_type  VARCHAR(100),
    ADD COLUMN IF NOT EXISTS duration  VARCHAR(100);
