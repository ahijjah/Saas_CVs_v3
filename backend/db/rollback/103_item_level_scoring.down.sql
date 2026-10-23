-- Rollback of 103_item_level_scoring.sql. NOT a migration: the runner never reads this directory and the ledger row for 103 is not
-- removed by it. Use it only on a restored COPY first (docs/ITEM_LEVEL_SCORING.md), and only together with deploying the code that predates
-- 103. Migrations are otherwise forward-only; the normal rollback is restoring the pre-upgrade backup.
--
-- It removes exactly what 103 added and loses every item-level run, item evaluation, resolution and its history, plus
-- applications.scoring_status and item_final_score. Applications, their legacy scores in application_scores, their stored decisions,
-- eligibility results and the requirements documents are untouched. An application scored at item level has no application_scores row, so
-- after the rollback it shows as scored without a numeric score until it is scored again.

BEGIN;
DROP TABLE IF EXISTS cv_analyzer.application_item_evaluation_events;
DROP TABLE IF EXISTS cv_analyzer.application_item_evaluations;
DROP TABLE IF EXISTS cv_analyzer.application_scoring_runs;
ALTER TABLE cv_analyzer.applications DROP CONSTRAINT IF EXISTS applications_item_final_score_check;
ALTER TABLE cv_analyzer.applications DROP CONSTRAINT IF EXISTS applications_scoring_status_check;
ALTER TABLE cv_analyzer.applications DROP COLUMN IF EXISTS item_final_score, DROP COLUMN IF EXISTS scoring_status;
COMMIT;
