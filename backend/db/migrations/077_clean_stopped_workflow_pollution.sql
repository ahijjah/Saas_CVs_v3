-- Migration 077: Clean workflow_status pollution from stopped-before-AI applications
--
-- Applications that were stopped before reaching AI scoring (processing_status = 'failed'
-- with a stopped_reason) must never appear in recruiter workflow queues.
-- This migration nullifies their workflow_status so they are excluded from all
-- recruiter pipeline views (Awaiting Review, Under Review, etc.).
--
-- This is a safe, idempotent cleanup: it only touches rows where the contradiction
-- is unambiguous (failed processing AND a non-null stopped_reason AND a non-null
-- workflow_status that was incorrectly set on ingestion).

UPDATE applications
SET workflow_status = NULL
WHERE processing_status = 'failed'
  AND stopped_reason IS NOT NULL
  AND workflow_status IS NOT NULL;
