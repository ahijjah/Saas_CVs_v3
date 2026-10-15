-- Rollback of migration 109. ONLY while the history is EMPTY. The history is append-only and is never deleted by rollback: once any row exists this
-- script refuses, and the correct rollback is the switch (already 'false'). Removing a non-empty history is an archival decision, not a rollback:
-- export it first with COPY (see the runbook).
-- Run:  psql -v ON_ERROR_STOP=1 -U <owner> -d cv_analyzer_prod -f deploy/recovery_109_rollback_empty.sql
SET search_path = cv_analyzer;
BEGIN;
DO $$
BEGIN
    IF to_regclass('cv_analyzer.requirements_analysis_log') IS NULL THEN
        RAISE EXCEPTION 'requirements_analysis_log does not exist: nothing to roll back';
    END IF;
    IF EXISTS (SELECT 1 FROM requirements_analysis_log) THEN
        RAISE EXCEPTION 'the history has events and will not be dropped by rollback; keep the table and the switch false, or archive first';
    END IF;
END $$;
DROP TRIGGER IF EXISTS requirements_analysis_log_no_change ON requirements_analysis_log;
DROP TRIGGER IF EXISTS requirements_analysis_log_no_truncate ON requirements_analysis_log;
DROP TABLE requirements_analysis_log;
DELETE FROM system_config WHERE key = 'requirements_v2.recovery_enabled';
COMMIT;
