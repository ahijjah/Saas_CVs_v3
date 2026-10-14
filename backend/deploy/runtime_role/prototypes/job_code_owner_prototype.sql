-- PROTOTYPE. NOT APPLIED ANYWHERE. For review and for tests/test_job_code_owner_prototype.py (disposable database only).
--
-- Purpose: move the allocator's owner from the migration role (a superuser with BYPASSRLS in production) to a dedicated NOLOGIN role that
-- can read ONLY the job codes, and only because a narrow policy says so. The restricted runtime role gets nothing new.
--
-- Why a column grant alone is not enough: jobs has row-level security. The tenant policy admits a row only when the session's tenant matches,
-- and the allocator's session has no tenant. Without a policy for this owner, row-level security returns no rows, so the existence check
-- cannot see existing codes and the allocator would hand out a code that is already taken (the unique index then refuses it).
-- The policy below admits the owner's SELECT on every row, and the column grant limits what that SELECT can read.
--
-- Scope: the policy names cv_code_owner only (not PUBLIC, not cv_runtime, not any tenant role). The owner has no INSERT, UPDATE or DELETE on
-- jobs and no read of any other column.

-- 1. the owner: cannot log in, cannot bypass row-level security, cannot create roles or databases, no membership
CREATE ROLE cv_code_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;

GRANT USAGE ON SCHEMA cv_analyzer TO cv_code_owner;

-- 2. the only job data it reads: the code, plus the policy that lets it see all rows for that column
GRANT SELECT (job_code) ON cv_analyzer.jobs TO cv_code_owner;
CREATE POLICY job_code_allocator_read ON cv_analyzer.jobs
    FOR SELECT TO cv_code_owner
    USING (true);

-- 3. the function and the counters belong to the owner (the owner of a table bypasses its RLS unless FORCE is set, and the counter table has
--    no policy, which is intended: only the owner can touch it)
ALTER FUNCTION cv_analyzer.allocate_job_code(integer) OWNER TO cv_code_owner;
ALTER TABLE cv_analyzer.job_code_counters OWNER TO cv_code_owner;

-- The runtime role's EXECUTE is unchanged (granted in deploy/runtime_role/grants.sql). It gains no table privilege and no policy.
