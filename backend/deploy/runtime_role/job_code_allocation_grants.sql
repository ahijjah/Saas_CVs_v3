-- For REVIEW (not applied). The minimal grant the restricted runtime role needs for migration 112.
-- It is EXECUTE on the allocator only: no table grant, no read of jobs, no change to any other privilege.
-- Apply after migration 112, in the same window as deploy/runtime_role/grants.sql.
SET search_path = cv_analyzer;
GRANT EXECUTE ON FUNCTION allocate_job_code(integer) TO cv_runtime;
