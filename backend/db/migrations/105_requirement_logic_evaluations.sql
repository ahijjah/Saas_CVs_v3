-- 105_requirement_logic_evaluations.sql
--
-- Requirement logic (options, parts that must all be met, review-only alternatives, examples, flagged ambiguity): what an item evaluation
-- records about WHICH alternative was met and WHY a case needs review.
--
--   * application_item_evaluations.matched_option: the wording of the option that was met (a review-only option that was met is recorded
--     too; the result is then unclear, never an automatic pass).
--   * application_item_evaluations.option_results: every option's result as assessed (with the part results), for display in the scoring
--     view and the matrix. It holds no CV text beyond the evidence quotations the evaluation already stores.
--   * review_reason gains two labels: conditional_alternative (an alternative that is accepted only after review was met) and
--     ambiguous_requirement (the requirement was flagged ambiguous and the evidence does not establish it).
--
-- Additive and idempotent. No existing row changes: every new column is NULL for evaluations written before this migration. A criterion
-- without options is stored exactly as before. Rollback: db/rollback/105_requirement_logic_evaluations.down.sql (not a migration; run on a
-- restored copy first, see docs/REQUIREMENT_SEMANTICS.md).

BEGIN;

ALTER TABLE cv_analyzer.application_item_evaluations
    ADD COLUMN IF NOT EXISTS matched_option text,
    ADD COLUMN IF NOT EXISTS option_results jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'application_item_evaluations_option_results_check'
                   AND conrelid = 'cv_analyzer.application_item_evaluations'::regclass) THEN
        ALTER TABLE cv_analyzer.application_item_evaluations
            ADD CONSTRAINT application_item_evaluations_option_results_check
            CHECK (option_results IS NULL OR (jsonb_typeof(option_results) = 'array' AND pg_column_size(option_results) <= 65536));
    END IF;
END
$$;

ALTER TABLE cv_analyzer.application_item_evaluations DROP CONSTRAINT IF EXISTS application_item_evaluations_review_reason_check;
ALTER TABLE cv_analyzer.application_item_evaluations
    ADD CONSTRAINT application_item_evaluations_review_reason_check
    CHECK (review_reason IS NULL OR review_reason IN ('no_verified_evidence', 'low_confidence', 'conflicting_evidence',
                                                      'conditional_alternative', 'ambiguous_requirement'));

COMMIT;
