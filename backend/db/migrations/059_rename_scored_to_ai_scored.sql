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
--   1. Update the applications_processing_status_check constraint to include both
--      'scored' and 'ai_scored' (transition period allows both values)
--   2. Rename processing_status = 'scored' → 'ai_scored' for all existing records
--   3. Going forward the worker now writes 'ai_scored' directly (cv_score.py updated)
--
-- Safety:
--   - Idempotent: running twice is harmless (no rows with 'scored' after first run)
--   - Non-destructive: only renames a processing_status value, no data deleted
--   - Does NOT touch workflow_status — recruiter-reviewed apps (under_review,
--     interviewing, hired, etc.) keep their workflow_status unchanged
--   - Failed/blocked apps (processing_status = 'failed') are completely unaffected
--   - Constraint expanded to allow both values during transition
--
-- Run this migration ONCE. Safe to run multiple times (idempotent).

BEGIN;
SET search_path = cv_analyzer;

-- Step 0: Audit — show current distribution before migration
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

-- Step 1: Drop and recreate the CHECK constraint to allow both 'scored' and 'ai_scored'
--         This permits the UPDATE below to succeed even if the constraint exists.
--         The constraint will be further relaxed if future processing_status values
--         are introduced (e.g., in later migrations).
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_processing_status_check;
ALTER TABLE applications
    ADD CONSTRAINT applications_processing_status_check
    CHECK (processing_status IN (
        'pending',      -- Initial state when application is created
        'queued',       -- Application queued for AI scoring
        'processing',   -- Currently being scored by the AI pipeline
        'scored',       -- LEGACY: old name for successful scoring (being renamed to ai_scored)
        'ai_scored',    -- NEW: successfully processed by AI (can be gatekeeper-rejected or L3-scored)
        'failed'        -- Failed/blocked: security_blocked, duplicate_blocked, extraction_failed, etc.
    ));

-- Step 2: Rename 'scored' → 'ai_scored' for all existing applications
UPDATE applications
SET processing_status = 'ai_scored'
WHERE processing_status = 'scored';

-- Step 3: Verify result and fail the migration if any 'scored' rows remain
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

-- Step 4: Optional cleanup — remove the legacy 'scored' value from constraint
--         Comment this out if rolling back and needing to re-run the migration.
--         Uncomment after confirming successful deployment.
-- ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_processing_status_check;
-- ALTER TABLE applications
--     ADD CONSTRAINT applications_processing_status_check
--     CHECK (processing_status IN (
--         'pending', 'queued', 'processing', 'ai_scored', 'failed'
--     ));

COMMIT;

-- ── Post-migration verification (run manually after deployment) ────────────────

-- 1. Verify constraint allows ai_scored:
--    SELECT constraint_name, constraint_definition
--    FROM information_schema.check_constraints
--    WHERE constraint_name = 'applications_processing_status_check';

-- 2. Dashboard awaiting_review count should now be > 0 (if any ai_scored apps exist):
--    SELECT COUNT(*) FROM applications
--    WHERE workflow_status = 'awaiting_review' AND processing_status = 'ai_scored';

-- 3. Confirm no 'scored' rows remain:
--    SELECT COUNT(*) FROM applications WHERE processing_status = 'scored';
--    -- Expected: 0

-- 4. Confirm 'ai_scored' rows present and correct:
--    SELECT processing_status, COUNT(*) FROM applications
--    GROUP BY processing_status ORDER BY COUNT(*) DESC;

