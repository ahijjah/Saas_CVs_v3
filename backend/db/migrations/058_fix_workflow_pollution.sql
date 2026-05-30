-- Migration 058: Verify and document workflow status pollution
--
-- Problem:
--   Applications that failed AI processing (security_blocked, duplicate_blocked,
--   extraction_failed, processing_failed, failed, stopped) were assigned
--   workflow_status = 'awaiting_review' by the column's DEFAULT value.
--   This caused them to appear in recruiter-operational queues alongside
--   genuine awaiting-review candidates.
--
-- Fix deployed (query-level, no schema change required):
--   1. dashboard.py: awaiting_review KPI now adds AND processing_status = 'ai_scored'
--   2. applications.py: processing_status = 'failed_or_blocked' alias added
--   3. CandidatesWorkspace: Awaiting Review quick view scopes to ai_scored only
--
-- This script is an AUDIT and VERIFICATION tool.
-- Run it before and after deploying the application fix to confirm counts align.
-- It makes NO changes to the schema or data.
--
-- Safe to run at any time. Idempotent.

-- ── Section 1: Audit — current pollution counts ────────────────────────────────

SELECT
    'POLLUTED: failed/blocked apps in awaiting_review' AS description,
    COUNT(*)                                           AS count
FROM applications
WHERE workflow_status = 'awaiting_review'
  AND processing_status IN (
      'failed', 'security_blocked', 'duplicate_blocked',
      'extraction_failed', 'processing_failed', 'stopped'
  )

UNION ALL

SELECT
    'CLEAN: recruiter-actionable awaiting_review (ai_scored)' AS description,
    COUNT(*)                                                   AS count
FROM applications
WHERE workflow_status = 'awaiting_review'
  AND processing_status = 'ai_scored'

UNION ALL

SELECT
    'TOTAL awaiting_review (old count, includes pollution)' AS description,
    COUNT(*)                                                 AS count
FROM applications
WHERE workflow_status = 'awaiting_review'

UNION ALL

SELECT
    'TOTAL failed_or_blocked (new dashboard count)' AS description,
    COUNT(*)                                         AS count
FROM applications
WHERE processing_status IN (
    'failed', 'security_blocked', 'duplicate_blocked',
    'extraction_failed', 'processing_failed', 'stopped'
);

-- ── Section 2: Breakdown by processing_status ──────────────────────────────────

SELECT
    processing_status,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE workflow_status = 'awaiting_review') AS in_awaiting_review
FROM applications
GROUP BY processing_status
ORDER BY total DESC;

-- ── Section 3: Validation — recruiter-reviewed applications are untouched ──────
-- Applications that a recruiter manually advanced from awaiting_review
-- will have a non-'awaiting_review' workflow_status. These must never be touched.

SELECT
    workflow_status,
    COUNT(*) AS count
FROM applications
WHERE workflow_status != 'awaiting_review'
GROUP BY workflow_status
ORDER BY count DESC;

-- ── Section 4: Optional data cleanse (NOT executed automatically) ──────────────
-- If you want the DB to reflect the query-level fix at the data level, you can
-- run the UPDATE below manually after verifying the audit counts above.
--
-- PREREQUISITE: First relax the NOT NULL constraint so NULL is allowed:
--   ALTER TABLE applications ALTER COLUMN workflow_status DROP NOT NULL;
--   ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_workflow_status_check;
--   ALTER TABLE applications ADD CONSTRAINT applications_workflow_status_check
--     CHECK (workflow_status IS NULL OR workflow_status IN (
--       'awaiting_review', 'under_review', 'shortlisted', 'interviewing',
--       'offer_made', 'hired', 'rejected', 'withdrawn', 'on_hold'
--     ));
--
-- THEN run the backfill:
--   UPDATE applications
--   SET workflow_status = NULL
--   WHERE workflow_status = 'awaiting_review'
--     AND processing_status IN (
--         'failed', 'security_blocked', 'duplicate_blocked',
--         'extraction_failed', 'processing_failed', 'stopped'
--     );
--
-- IMPORTANT: The application backend returns r["workflow_status"] or "awaiting_review"
-- which means NULL values would fall back to "awaiting_review" in API responses.
-- If you run the optional cleanse, also remove the `or "awaiting_review"` fallback
-- in backend/routers/applications.py (the row serialization loop).
--
-- The query-level fix in the application code is SUFFICIENT without this step.
