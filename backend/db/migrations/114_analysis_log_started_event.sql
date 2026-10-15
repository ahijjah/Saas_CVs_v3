-- 114: the analysis log gains the 'started' event: a worker's claim on a re-analysis candidate, recorded before its model call.
--
-- Why: a candidate stays 'requested' until a decision, so a duplicate delivery of its task could run a second model call. The worker now
-- appends 'started' under the job's lock, and a second claim finds it and does nothing. 'started' is not a decision: version_status
-- ignores it, so a started candidate still reads as requested and the existing decisions and their uniqueness are unchanged.
--
-- Additive only. The history is append-only (migration 109 triggers); nothing is rewritten or deleted.
-- Rollback (only while no 'started' row exists): ALTER TABLE requirements_analysis_log DROP CONSTRAINT requirements_analysis_log_event_check;
-- and re-add the check with migration 109's list. Once a 'started' row exists, rollback is the code change, not the constraint.
BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE requirements_analysis_log DROP CONSTRAINT IF EXISTS requirements_analysis_log_event_check;
ALTER TABLE requirements_analysis_log
    ADD CONSTRAINT requirements_analysis_log_event_check
    CHECK (event IN ('requested', 'started', 'completed', 'rejected', 'kept', 'applied', 'discarded', 'superseded'));

COMMIT;
