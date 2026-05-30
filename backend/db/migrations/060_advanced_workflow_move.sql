-- Migration 060: Advanced workflow move — add is_advanced_move audit column
-- Safe to run multiple times (IF NOT EXISTS).

SET search_path = cv_analyzer;

-- Track whether a workflow transition was a privileged stage-jump bypass.
-- Default FALSE preserves all existing history rows as normal transitions.
ALTER TABLE cv_analyzer.application_workflow_history
    ADD COLUMN IF NOT EXISTS is_advanced_move BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index: fast lookup of advanced-move history entries per application.
CREATE INDEX IF NOT EXISTS idx_app_workflow_history_advanced
    ON cv_analyzer.application_workflow_history (application_id)
    WHERE is_advanced_move = TRUE;
