-- 101_job_code_sequence.sql
--
-- Job codes (JOB-YYYY-NNNN) must be unique across tenants, because jobs.job_code has a global unique index. The count of a tenant's
-- own jobs is not unique across tenants once row-level security scopes sessions to a tenant. A global sequence is.
--
-- The sequence starts above the largest existing code, so codes already issued are never reused. Its usage is granted to the runtime
-- roles by the operator grant script (db/ops/0005), like every other sequence.

BEGIN;

CREATE SEQUENCE IF NOT EXISTS cv_analyzer.job_code_seq AS bigint START WITH 1 MINVALUE 1;

SELECT setval(
    'cv_analyzer.job_code_seq',
    COALESCE((SELECT max(substring(j.job_code FROM '([0-9]+)$')::bigint) FROM cv_analyzer.jobs j
              WHERE j.job_code ~ '[0-9]+$'), 0) + 1,
    false
);

COMMIT;
