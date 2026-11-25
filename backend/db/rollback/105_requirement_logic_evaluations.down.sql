-- Rollback of 105_requirement_logic_evaluations.sql. NOT a migration: the runner never reads this directory and the ledger row for 105 is
-- not removed by it. Use it only on a restored COPY first (docs/REQUIREMENT_SEMANTICS.md), and only together with deploying the code that
-- predates 105. Migrations are otherwise forward-only; the normal rollback is restoring the pre-upgrade backup.
--
-- It removes exactly what 105 added: matched_option, option_results, and the two review_reason labels (rows carrying them lose the label,
-- not the result). Results, evidence, resolutions, scores, runs and gate results are untouched.

BEGIN;
UPDATE cv_analyzer.application_item_evaluations SET review_reason = NULL WHERE review_reason IN ('conditional_alternative', 'ambiguous_requirement');
ALTER TABLE cv_analyzer.application_item_evaluations DROP CONSTRAINT IF EXISTS application_item_evaluations_review_reason_check;
ALTER TABLE cv_analyzer.application_item_evaluations
    ADD CONSTRAINT application_item_evaluations_review_reason_check
    CHECK (review_reason IS NULL OR review_reason IN ('no_verified_evidence', 'low_confidence', 'conflicting_evidence'));
ALTER TABLE cv_analyzer.application_item_evaluations DROP CONSTRAINT IF EXISTS application_item_evaluations_option_results_check;
ALTER TABLE cv_analyzer.application_item_evaluations DROP COLUMN IF EXISTS option_results, DROP COLUMN IF EXISTS matched_option;
COMMIT;
