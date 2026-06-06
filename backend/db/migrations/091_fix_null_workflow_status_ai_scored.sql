-- Migration 091: Repair null workflow_status for ai_scored applications
--
-- Migration 077 made workflow_status nullable and set it to NULL for stopped-before-AI
-- candidates. However, some ai_scored candidates were also left with NULL workflow_status
-- (records that existed before the workflow column was introduced).
-- The recruiter PATCH endpoint now explicitly handles NULL as 'awaiting_review' for
-- ai_scored candidates, but this repair migration makes the DB state consistent
-- so audit queries and reports reflect the correct status directly.

UPDATE cv_analyzer.applications
SET    workflow_status = 'awaiting_review',
       updated_at      = now()
WHERE  processing_status = 'ai_scored'
  AND  workflow_status IS NULL;
