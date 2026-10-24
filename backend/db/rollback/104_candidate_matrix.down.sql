-- Rollback of 104_candidate_matrix.sql. NOT a migration: the runner never reads this directory and the ledger row for 104 is not
-- removed by it. Use it only on a restored COPY first (docs/FINAL_CANDIDATE_MATRIX.md), and only together with deploying the code that
-- predates 104. Migrations are otherwise forward-only; the normal rollback is restoring the pre-upgrade backup.
--
-- It removes exactly what 104 added: every saved matrix layout (tenant, job and personal) and the review_reason label on item evaluations.
-- Applications, scores, scoring runs, item evaluations (their results, evidence and resolutions) and gate results are untouched.

BEGIN;
DROP INDEX IF EXISTS cv_analyzer.idx_item_evaluations_run_item;
ALTER TABLE cv_analyzer.application_item_evaluations DROP CONSTRAINT IF EXISTS application_item_evaluations_review_reason_check;
ALTER TABLE cv_analyzer.application_item_evaluations DROP COLUMN IF EXISTS review_reason;
DROP TABLE IF EXISTS cv_analyzer.candidate_matrix_configs;
COMMIT;
