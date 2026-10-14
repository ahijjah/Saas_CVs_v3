-- 112: atomic, global allocation of job codes (JOB-YYYY-NNNN), with a dedicated owner role.
--
-- Why: routers/jobs.py counted the jobs the requester could SEE in the current year. The job_code index is global, so under a restricted
-- role (row-level security applied) a second tenant's first job of a year collided with the first tenant's JOB-YYYY-0001 (HTTP 500).
--
-- What:
--   * a per-year counter (job_code_counters), incremented inside the caller's transaction by a SECURITY DEFINER allocator
--     cv_analyzer.allocate_job_code(year). Concurrent allocators wait on the counter row; a rollback returns the number; the allocator skips
--     any code that already exists. Existing codes are not changed.
--   * the allocator and the counter are OWNED by cv_code_owner: NOLOGIN, NOSUPERUSER, NOBYPASSRLS, no memberships. The owner may read
--     jobs.job_code only, through one policy that names it alone. It cannot read other job columns, write jobs, or log in.
--   * the restricted runtime role (cv_runtime) receives EXECUTE on the allocator only (deploy/runtime_role/grants.sql). It gains no table
--     privilege, no policy, no membership, and no way to assume cv_code_owner.
--
-- Owner role: created here if absent. If it already exists it is ACCEPTED ONLY in the exact safe shape below. Anything else stops the
-- migration before any change (the transaction is rolled back). Nothing is silently accepted or repaired.
--
-- Existing data: each year's counter starts at the highest sequence already used for that year (backfill). Codes are never reused.
-- Year transition: a new year gets its own counter on first use and starts at 0001. Years accepted: 2000 to 9999.
-- Re-running is safe (every step is idempotent). Requires a role with CREATEROLE (a superuser in production).
BEGIN;
SET search_path = cv_analyzer;

-- 1. The owner role: create, or verify that an existing one is exactly the safe shape.
DO $$
DECLARE
    r        record;
    members  integer;
    owned    integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cv_code_owner') THEN
        CREATE ROLE cv_code_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
    END IF;

    SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolinherit
      INTO r FROM pg_roles WHERE rolname = 'cv_code_owner';
    IF r.rolcanlogin OR r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolinherit THEN
        RAISE EXCEPTION 'cv_code_owner exists with unsafe attributes; refusing to continue. Required: NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- no membership in either direction: it must not be a member of any role, and no role may be a member of it
    SELECT count(*) INTO members
    FROM pg_auth_members m
    JOIN pg_roles o ON o.oid IN (m.roleid, m.member)
    WHERE o.rolname = 'cv_code_owner';
    IF members > 0 THEN
        RAISE EXCEPTION 'cv_code_owner has role memberships; refusing to continue' USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- it may own only the objects this migration gives it
    SELECT count(*) INTO owned FROM pg_proc p
    JOIN pg_roles o ON o.oid = p.proowner
    WHERE o.rolname = 'cv_code_owner' AND p.proname <> 'allocate_job_code';
    SELECT owned + count(*) INTO owned FROM pg_class c
    JOIN pg_roles o ON o.oid = c.relowner
    WHERE o.rolname = 'cv_code_owner' AND c.relname NOT IN ('job_code_counters', 'job_code_counters_pkey');
    IF owned > 0 THEN
        RAISE EXCEPTION 'cv_code_owner owns objects other than the allocator and its counter; refusing to continue' USING ERRCODE = 'insufficient_privilege';
    END IF;
END $$;

-- 2. The counter table: owned by the owner, no policy, no grant to anyone else (RLS enabled, so the owner's own access is the only access).
CREATE TABLE IF NOT EXISTS job_code_counters (
    year       integer PRIMARY KEY CHECK (year BETWEEN 2000 AND 9999),
    last_seq   integer NOT NULL CHECK (last_seq >= 0)
);
ALTER TABLE job_code_counters ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON job_code_counters FROM PUBLIC;

-- 3. Backfill: the highest sequence already used for each year (codes are JOB-YYYY-NNNN; anything else is ignored).
INSERT INTO job_code_counters (year, last_seq)
SELECT CAST(substring(job_code FROM '^JOB-([0-9]{4})-[0-9]+$') AS integer),
       MAX(CAST(substring(job_code FROM '^JOB-[0-9]{4}-([0-9]+)$') AS integer))
FROM jobs
WHERE job_code ~ '^JOB-[0-9]{4}-[0-9]+$'
GROUP BY 1
ON CONFLICT (year) DO NOTHING;

-- 4. The allocator. Every name is schema-qualified and the search path is fixed.
CREATE OR REPLACE FUNCTION cv_analyzer.allocate_job_code(p_year integer)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_seq  integer;
    v_code text;
BEGIN
    IF p_year IS NULL OR p_year < 2000 OR p_year > 9999 THEN
        RAISE EXCEPTION 'job code year out of range' USING ERRCODE = '22023';
    END IF;

    -- First use of a year: start from what already exists for it (normally nothing; the backfill covers existing years).
    IF NOT EXISTS (SELECT 1 FROM cv_analyzer.job_code_counters WHERE year = p_year) THEN
        INSERT INTO cv_analyzer.job_code_counters (year, last_seq)
        SELECT p_year, COALESCE(MAX(CAST(substring(job_code FROM '^JOB-[0-9]{4}-([0-9]+)$') AS integer)), 0)
        FROM cv_analyzer.jobs
        WHERE job_code LIKE 'JOB-' || p_year || '-%'
        ON CONFLICT (year) DO NOTHING;
    END IF;

    -- The increment is the lock: a concurrent allocator for the same year waits here until the caller's transaction ends.
    UPDATE cv_analyzer.job_code_counters SET last_seq = last_seq + 1 WHERE year = p_year RETURNING last_seq INTO v_seq;
    LOOP
        v_code := 'JOB-' || p_year || '-' || lpad(v_seq::text, 4, '0');
        EXIT WHEN NOT EXISTS (SELECT 1 FROM cv_analyzer.jobs WHERE job_code = v_code);
        UPDATE cv_analyzer.job_code_counters SET last_seq = last_seq + 1 WHERE year = p_year RETURNING last_seq INTO v_seq;
    END LOOP;
    RETURN v_code;
END;
$$;

-- 5. Ownership, privileges and the owner's one read path.
ALTER FUNCTION cv_analyzer.allocate_job_code(integer) OWNER TO cv_code_owner;
ALTER TABLE cv_analyzer.job_code_counters OWNER TO cv_code_owner;
REVOKE ALL ON FUNCTION cv_analyzer.allocate_job_code(integer) FROM PUBLIC;

GRANT USAGE ON SCHEMA cv_analyzer TO cv_code_owner;
GRANT SELECT (job_code) ON cv_analyzer.jobs TO cv_code_owner;
DROP POLICY IF EXISTS job_code_allocator_read ON cv_analyzer.jobs;
CREATE POLICY job_code_allocator_read ON cv_analyzer.jobs
    FOR SELECT TO cv_code_owner
    USING (true);
COMMIT;
