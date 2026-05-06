-- Migration 003: CV receiving options per job
-- Adds job_code, per-job ingestion toggles, forwarding_email system config,
-- and attachment hash deduplication on email_ingest_log.

-- ── job_code — human-readable code e.g. JOB-2026-0001 ─────────────────────
ALTER TABLE cv_analyzer.jobs
    ADD COLUMN IF NOT EXISTS job_code VARCHAR(50);

-- Backfill existing rows: JOB-YYYY-NNNN sequential within each year
WITH ranked AS (
    SELECT job_id,
           'JOB-' || TO_CHAR(created_at, 'YYYY') || '-' ||
           LPAD(ROW_NUMBER() OVER (
               PARTITION BY EXTRACT(YEAR FROM created_at)
               ORDER BY created_at
           )::text, 4, '0') AS code
    FROM cv_analyzer.jobs
)
UPDATE cv_analyzer.jobs
SET    job_code = ranked.code
FROM   ranked
WHERE  cv_analyzer.jobs.job_id = ranked.job_id
  AND  cv_analyzer.jobs.job_code IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_code ON cv_analyzer.jobs (job_code);

-- ── Per-job ingestion toggles ──────────────────────────────────────────────
ALTER TABLE cv_analyzer.jobs
    ADD COLUMN IF NOT EXISTS forwarding_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS alias_enabled      BOOLEAN NOT NULL DEFAULT TRUE;

-- ── System config: central forwarding email ────────────────────────────────
INSERT INTO cv_analyzer.system_config (key, value, description)
VALUES (
    'forwarding_email',
    'jobs@ai970.cloud',
    'Central email address for CV forwarding (Option 1 — configurable here, not in code)'
)
ON CONFLICT (key) DO NOTHING;

-- ── email_ingest_log: attachment dedup + metadata ─────────────────────────
ALTER TABLE cv_analyzer.email_ingest_log
    ADD COLUMN IF NOT EXISTS attachment_hash VARCHAR(64),   -- SHA-256 of file bytes
    ADD COLUMN IF NOT EXISTS attachment_name VARCHAR(255);  -- original filename

CREATE INDEX IF NOT EXISTS idx_ingest_log_hash
    ON cv_analyzer.email_ingest_log (attachment_hash)
    WHERE attachment_hash IS NOT NULL;
