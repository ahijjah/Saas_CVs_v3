-- Add per-job advanced workflow move setting
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS allow_advanced_workflow_move BOOLEAN NOT NULL DEFAULT FALSE;
