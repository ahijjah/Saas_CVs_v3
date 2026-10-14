-- 112: atomic, global allocation of job codes (JOB-YYYY-NNNN).
--
-- Why: routers/jobs.py counted the jobs the requester could SEE in the current year. The job_code index is global, so under a restricted
-- role (row-level security applied) a second tenant's first job of a year collided with the first tenant's JOB-YYYY-0001 (HTTP 500).
--
-- What: a per-year counter, allocated by a SECURITY DEFINER function that runs as the owner. The increment is an UPDATE inside the caller's
-- transaction: concurrent allocators wait on the row; a rollback returns the number, so a failed create leaves no gap. The function also skips
-- any code that already exists, so an allocation never hands out a taken code. Existing codes are not changed.
--
-- Existing data: each year's counter starts at the highest sequence already used for that year (backfill below). Codes are never reused.
-- Year transition: a new year gets its own counter row on first use and starts at 0001 (no reset of other years).
--
-- Grants: the counter table has no grant to any role other than its owner (RLS is enabled with no policy, so even a grant could not read it).
-- The restricted runtime role receives EXECUTE on the function only (deploy/runtime_role/job_code_allocation_grants.sql, for review).
-- Additive and idempotent.
BEGIN;
SET search_path = cv_analyzer;

CREATE TABLE IF NOT EXISTS job_code_counters (
    year       integer PRIMARY KEY CHECK (year BETWEEN 2000 AND 9999),
    last_seq   integer NOT NULL CHECK (last_seq >= 0)
);
ALTER TABLE job_code_counters ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON job_code_counters FROM PUBLIC;

-- Backfill: the highest sequence already used for each year (codes are JOB-YYYY-NNNN; anything else is ignored).
INSERT INTO job_code_counters (year, last_seq)
SELECT CAST(substring(job_code FROM '^JOB-([0-9]{4})-[0-9]+$') AS integer),
       MAX(CAST(substring(job_code FROM '^JOB-[0-9]{4}-([0-9]+)$') AS integer))
FROM jobs
WHERE job_code ~ '^JOB-[0-9]{4}-[0-9]+$'
GROUP BY 1
ON CONFLICT (year) DO NOTHING;

CREATE OR REPLACE FUNCTION allocate_job_code(p_year integer)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = cv_analyzer, pg_temp
AS $$
DECLARE
    v_seq  integer;
    v_code text;
BEGIN
    IF p_year IS NULL OR p_year < 2000 OR p_year > 9999 THEN
        RAISE EXCEPTION 'job code year out of range' USING ERRCODE = '22023';
    END IF;

    -- First use of a year: start from what already exists for it (normally nothing; the backfill covers existing years).
    IF NOT EXISTS (SELECT 1 FROM job_code_counters WHERE year = p_year) THEN
        INSERT INTO job_code_counters (year, last_seq)
        SELECT p_year, COALESCE(MAX(CAST(substring(job_code FROM '^JOB-[0-9]{4}-([0-9]+)$') AS integer)), 0)
        FROM jobs
        WHERE job_code LIKE 'JOB-' || p_year || '-%'
        ON CONFLICT (year) DO NOTHING;
    END IF;

    -- The increment is the lock: a concurrent allocator for the same year waits here until the caller's transaction ends.
    UPDATE job_code_counters SET last_seq = last_seq + 1 WHERE year = p_year RETURNING last_seq INTO v_seq;
    LOOP
        v_code := 'JOB-' || p_year || '-' || lpad(v_seq::text, 4, '0');
        EXIT WHEN NOT EXISTS (SELECT 1 FROM jobs WHERE job_code = v_code);
        UPDATE job_code_counters SET last_seq = last_seq + 1 WHERE year = p_year RETURNING last_seq INTO v_seq;
    END LOOP;
    RETURN v_code;
END;
$$;

REVOKE ALL ON FUNCTION allocate_job_code(integer) FROM PUBLIC;
COMMIT;
