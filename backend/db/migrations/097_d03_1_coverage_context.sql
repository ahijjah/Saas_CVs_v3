-- Migration 097: D-03.1 Required/Preferred Coverage Awareness for LLM Scoring
--
-- Feature:
--   D-03.1 injects a "coverage context" block into the scoring LLM prompt,
--   derived from the evidence-analysis LLM result (F-01 / LLMCriteriaMapper).
--   The block summarises how many required/preferred criteria were MATCHED,
--   PARTIAL, or ABSENT, allowing the scoring LLM to calibrate dimension scores
--   against the evidence rather than relying solely on the CV text.
--
-- This migration adds a JSONB column to cv_score_events (if it exists) to
-- store the coverage context metadata that was used during each scoring run,
-- enabling retrospective analysis and prompt-quality auditing.
--
-- The column is nullable with no default — rows scored before this migration
-- or scored without a coverage context will have NULL.

BEGIN;

DO $$
BEGIN
    -- Only add the column if the table exists (it may not exist in all envs)
    IF EXISTS (
        SELECT 1
        FROM   information_schema.tables
        WHERE  table_schema = 'cv_analyzer'
          AND  table_name   = 'cv_score_events'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM   information_schema.columns
            WHERE  table_schema = 'cv_analyzer'
              AND  table_name   = 'cv_score_events'
              AND  column_name  = 'coverage_context_meta'
        ) THEN
            ALTER TABLE cv_analyzer.cv_score_events
                ADD COLUMN coverage_context_meta JSONB DEFAULT NULL;

            COMMENT ON COLUMN cv_analyzer.cv_score_events.coverage_context_meta IS
                'D-03.1: Coverage context metadata injected into the scoring LLM prompt. '
                'Stores required/preferred criteria counts (matched/partial/absent) from '
                'the evidence-analysis pass (F-01 LLMCriteriaMapper). NULL when coverage '
                'context was unavailable or not applicable for this scoring run.';
        END IF;
    END IF;
END;
$$;

COMMIT;
