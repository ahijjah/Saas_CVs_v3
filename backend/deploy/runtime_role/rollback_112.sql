-- Rollback of migration 112 (job-code allocator and its owner role). Run ONLY after the application has been reverted to a release that does
-- not call cv_analyzer.allocate_job_code. The previous release counts visible jobs and needs nothing from 112.
--
-- DEFAULT (preserves all counter state):
--   1. revoke the restricted runtime role's EXECUTE on the allocator;
--   2. remove the owner's one read path (the policy and the SELECT(job_code) column grant), so cv_code_owner can no longer read jobs.
-- The allocator, the counter table and their owner (cv_code_owner) are KEPT. Keeping them is harmless: an unused allocator changes nothing,
-- and a later re-deployment resumes from the same counters without reissuing any code.
--
-- OPTIONAL, commented out on purpose: dropping the allocator, the counter table, or the owner role. Each is a separate, explicit decision.
-- Run:  psql -v ON_ERROR_STOP=1 -U <admin> -d cv_analyzer_prod -f rollback_112.sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = 'cv_runtime') THEN
        RAISE EXCEPTION 'cv_runtime still has open connections: revert the application first and wait for them to close';
    END IF;
END $$;

REVOKE EXECUTE ON FUNCTION cv_analyzer.allocate_job_code(integer) FROM cv_runtime;
DROP POLICY IF EXISTS job_code_allocator_read ON cv_analyzer.jobs;
REVOKE SELECT (job_code) ON cv_analyzer.jobs FROM cv_code_owner;

-- OPTIONAL (not run by default). Dropping the owner role requires first reassigning what it owns; the counter table would be lost with it.
-- REASSIGN OWNED BY cv_code_owner TO cv_app;     -- or the migration owner
-- DROP ROLE cv_code_owner;
-- DROP FUNCTION cv_analyzer.allocate_job_code(integer);
-- DROP TABLE cv_analyzer.job_code_counters;      -- destroys the per-year counters
