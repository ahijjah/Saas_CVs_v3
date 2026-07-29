-- Migration 097: Add Qualitative Summary Columns to application_scores
--
-- Issue #10: qualitative_summary was being parsed correctly from LLM D-01 output
-- and passed through F-01 deterministic scoring into det_score_json, but the
-- database columns to store the extracted qualitative fields did not exist on
-- application_scores, causing INSERT statements to fail silently.
--
-- This migration adds four columns to application_scores to store qualitative
-- summary data extracted from the LLM response:
-- - evaluation_notes: overall assessment summarizing the assessments
-- - strengths: list of strengths derived from MATCHED/PARTIAL assessments
-- - gaps_identified: list of gaps derived from ABSENT/PARTIAL assessments
-- - interview_questions: suggested questions targeting gaps/uncertain areas
--
-- These columns are nullable/defaulted to empty for backward compatibility.
-- Existing rows will have NULL/empty values. New rows scored after this
-- migration will be populated from det_score_json.qualitative_summary.
--
-- Idempotent:
--   ALTER TABLE … ADD COLUMN IF NOT EXISTS (PostgreSQL 9.6+)
--
-- Apply:
--   psql -h localhost -U cv_app cv_analyzer_prod \
--       -c "SET search_path = cv_analyzer" \
--       < 097_qualitative_summary_columns.sql
-- Or via Docker:
--   docker compose exec -T postgres psql -U cv_app cv_analyzer_prod \
--       -f /migrations/097_qualitative_summary_columns.sql

BEGIN;

SET search_path = cv_analyzer;

-- ── Add qualitative summary columns to application_scores ──────────────────────

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS evaluation_notes TEXT,
    ADD COLUMN IF NOT EXISTS strengths TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS gaps_identified TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS interview_questions TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN application_scores.evaluation_notes IS
    'D-01/F-01: Qualitative evaluation summary (2-4 sentences overall assessment). '
    'Extracted from det_score_json.qualitative_summary.evaluation_notes if available.';

COMMENT ON COLUMN application_scores.strengths IS
    'D-01/F-01: List of strengths identified from MATCHED/PARTIAL criteria in LLM assessment. '
    'Extracted from det_score_json.qualitative_summary.strengths.';

COMMENT ON COLUMN application_scores.gaps_identified IS
    'D-01/F-01: List of gaps identified from ABSENT/PARTIAL criteria in LLM assessment. '
    'Extracted from det_score_json.qualitative_summary.gaps_identified.';

COMMENT ON COLUMN application_scores.interview_questions IS
    'D-01/F-01: Suggested interview questions targeting gaps or uncertain assessment areas. '
    'Extracted from det_score_json.qualitative_summary.suggested_interview_questions.';

COMMIT;
