-- Rollback of migration 112 (job-code allocator). Run ONLY after the application has been reverted to a release that does not call
-- allocate_job_code (the previous release counts visible jobs and needs nothing from 112).
--
-- DEFAULT: revoke the runtime role's EXECUTE and stop there. The function and the counter table are kept, so the counter state
-- (job_code_counters) is preserved. Keeping them is harmless: an unused allocator changes nothing, and a later re-deployment resumes
-- from the same counters without reallocating any code.
--
-- Dropping is a separate, explicit decision. It is in the OPTIONAL section at the end, commented out on purpose.
-- Run:  psql -v ON_ERROR_STOP=1 -U <admin> -d cv_analyzer_prod -f rollback_112.sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = 'cv_runtime') THEN
        RAISE EXCEPTION 'cv_runtime still has open connections: revert the application first and wait for them to close';
    END IF;
END $$;

REVOKE EXECUTE ON FUNCTION cv_analyzer.allocate_job_code(integer) FROM cv_runtime;

-- OPTIONAL (not run by default). Only after the decision to lose the counter state:
-- DROP FUNCTION cv_analyzer.allocate_job_code(integer);
-- DROP TABLE cv_analyzer.job_code_counters;   -- destroys the per-year counters; a re-deployment would then restart each year from the
--                                            -- highest existing code (the allocator's backfill-on-first-use), not from the old counter.
