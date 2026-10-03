-- Migration 105: P0-02a — 'needs_verification' recommendation
--
-- applications.decision gains 'needs_verification': one or more required
-- criteria are CANNOT_DETERMINE (not established either way by the CV) and
-- verifying them would change the recommendation to qualified. Written only by
-- the P0-02a deterministic engine (application_scores.scoring_method =
-- 'deterministic_v2').
--
-- The new constraint is a strict superset of migration 012's
-- ('qualified', 'partial', 'rejected'), so code without P0-02a keeps working.
-- No new columns, no data changes, no re-scoring.
--
-- Pre-check (read-only), must return only qualified / partial / rejected / NULL:
--   SELECT DISTINCT decision FROM cv_analyzer.applications;
--
-- Idempotent: safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_decision_check;
ALTER TABLE applications ADD CONSTRAINT applications_decision_check
    CHECK (decision IN ('qualified', 'partial', 'rejected', 'needs_verification'));

COMMIT;
