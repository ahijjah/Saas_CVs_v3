-- Migration 092: Repair legacy 'new' workflow_status values for ai_scored applications
--
-- Root cause:
--   An early draft of migration 057 created the workflow_status column with
--   DEFAULT 'new' before the column's default was corrected to 'awaiting_review'.
--   Migration 057's own cleanup UPDATE (`WHERE workflow_status = 'new'`) did not
--   catch every row — some ai_scored applications still carry the legacy literal
--   value 'new', which is NOT a member of VALID_WORKFLOW_TRANSITIONS and is also
--   rejected by the applications_workflow_status_check CHECK constraint for any
--   future write, causing PATCH /workflow-status to return 422
--   ("Cannot transition workflow from 'new' to '<target>'").
--
-- This migration normalises any remaining legacy 'new' (and NULL) values for
-- ai_scored applications to 'awaiting_review', matching the same effective-status
-- rule already enforced at runtime (see migration 091 and applications.py
-- effective_current_status logic). Stopped-before-AI applications (security_blocked,
-- duplicate_blocked, extraction_failed, processing_failed, etc.) are NOT touched —
-- they remain system-managed and excluded from recruiter workflow queues.

BEGIN;

UPDATE cv_analyzer.applications
SET    workflow_status = 'awaiting_review',
       updated_at      = now()
WHERE  processing_status = 'ai_scored'
  AND  (workflow_status IS NULL OR workflow_status = 'new');

COMMIT;

-- ── Audit: report any workflow_status values outside the valid set ────────────
-- Run this separately to confirm no other invalid values remain anywhere
-- (including non-ai_scored rows, which are intentionally left as-is but should
-- still only ever be NULL or a valid status — never a stray legacy value).
--
-- SELECT workflow_status, processing_status, COUNT(*) AS count
-- FROM cv_analyzer.applications
-- WHERE workflow_status IS NOT NULL
--   AND workflow_status NOT IN (
--       'awaiting_review', 'under_review', 'shortlisted', 'interviewing',
--       'offer_made', 'hired', 'rejected', 'withdrawn', 'on_hold'
--   )
-- GROUP BY workflow_status, processing_status
-- ORDER BY count DESC;
