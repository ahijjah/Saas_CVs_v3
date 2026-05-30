-- Migration 059: Rename processing_status 'scored' → 'ai_scored'
--
-- Background:
--   The cv_score worker historically wrote processing_status = 'scored' for all
--   successfully processed applications (both gatekeeper-rejected and L3 AI-scored).
--   The dashboard and CandidatesWorkspace were written to filter on 'ai_scored'.
--   This mismatch caused:
--     - Dashboard "Awaiting Review" KPI always showing 0
--     - Recruiter workspace showing 0 actionable candidates
--
-- Fix:
--   Rename 'scored' → 'ai_scored' for all existing applications.
--   Going forward the worker now writes 'ai_scored' directly (cv_score.py updated).
--
-- Safety:
--   - Idempotent: running twice is harmless (no rows with 'scored' after first run)
--   - Non-destructive: only renames a processing_status value, no data deleted
--   - Does NOT touch workflow_status — recruiter-reviewed apps (under_review,
--     interviewing, hired, etc.) keep their workflow_status unchanged
--   - Failed/blocked apps (processing_status = 'failed') are completely unaffected
--
-- Prerequisite: ensure applications_workflow_status_check or any processing_status
-- check constraint allows 'ai_scored'. If a check constraint exists, update it first.
--
-- Run this migration ONCE before or after deploying the application code update.

BEGIN;

-- Step 1: Verify current distribution before migration
-- (informational — does not block migration)
DO $$
DECLARE
    scored_count INTEGER;
    ai_scored_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO scored_count
    FROM applications WHERE processing_status = 'scored';

    SELECT COUNT(*) INTO ai_scored_count
    FROM applications WHERE processing_status = 'ai_scored';

    RAISE NOTICE 'Before migration: scored=%, ai_scored=%', scored_count, ai_scored_count;
END $$;

-- Step 2: Rename 'scored' → 'ai_scored'
UPDATE applications
SET processing_status = 'ai_scored'
WHERE processing_status = 'scored';

-- Step 3: Verify result
DO $$
DECLARE
    remaining_scored INTEGER;
    new_ai_scored INTEGER;
BEGIN
    SELECT COUNT(*) INTO remaining_scored
    FROM applications WHERE processing_status = 'scored';

    SELECT COUNT(*) INTO new_ai_scored
    FROM applications WHERE processing_status = 'ai_scored';

    RAISE NOTICE 'After migration: scored=% (should be 0), ai_scored=%',
        remaining_scored, new_ai_scored;

    IF remaining_scored > 0 THEN
        RAISE EXCEPTION 'Migration failed: % rows still have processing_status = scored',
            remaining_scored;
    END IF;
END $$;

COMMIT;

-- ── Post-migration verification queries (run manually to confirm) ──────────────

-- 1. Dashboard awaiting_review count should now be > 0 (if any scored apps exist):
--    SELECT COUNT(*) FROM applications
--    WHERE workflow_status = 'awaiting_review' AND processing_status = 'ai_scored';

-- 2. Confirm no 'scored' rows remain:
--    SELECT COUNT(*) FROM applications WHERE processing_status = 'scored';
--    -- Expected: 0

-- 3. Confirm 'ai_scored' rows present:
--    SELECT processing_status, COUNT(*) FROM applications
--    GROUP BY processing_status ORDER BY COUNT(*) DESC;
