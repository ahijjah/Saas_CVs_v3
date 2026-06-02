-- Migration 061: Add tenant-level Advanced Workflow Move setting
-- Safe to run multiple times (IF NOT EXISTS, DEFAULT).

SET search_path = cv_analyzer;

-- Add allow_advanced_workflow_move setting to tenants table
-- Default FALSE: disabled by default for safety/strict workflow control
ALTER TABLE cv_analyzer.tenants
    ADD COLUMN IF NOT EXISTS allow_advanced_workflow_move BOOLEAN NOT NULL DEFAULT FALSE;

-- Index for queries filtering by this setting (optional but useful)
CREATE INDEX IF NOT EXISTS idx_tenants_allow_advanced_move
    ON cv_analyzer.tenants (allow_advanced_workflow_move);
