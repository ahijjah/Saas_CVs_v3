# Job-code allocator (migration 112): order of operations, rollback and the owner question

Status: prepared for review. Nothing here has been applied to production.

## Order (forward)

1. **Migration 112, as the migration owner, with `ON_ERROR_STOP`.** Creates `cv_analyzer.job_code_counters` (RLS enabled, no policy, no grant),
   backfills each year's counter from the highest existing sequence, and creates `cv_analyzer.allocate_job_code(integer)` as
   `SECURITY DEFINER` with `search_path = pg_catalog, pg_temp`, `EXECUTE` revoked from `PUBLIC`.
2. **Verify** (read only): the function exists; `has_function_privilege('public', ...)` is false; each year's counter is at least the highest
   existing code for that year.
3. **Role grants (`deploy/runtime_role/grants.sql`)** when the restricted role is being introduced. The file includes `GRANT EXECUTE ON FUNCTION
   allocate_job_code(integer) TO cv_runtime`. It must run after step 1: applied earlier, it fails with `UndefinedFunction` (verified).
4. **Application release** that calls `allocate_job_code` (`routers/jobs.py`). It must not run before step 1, or job creation fails.

Old application releases do not call the function, so they run correctly after step 1, before or after it. Steps 1 and 4 can therefore be
deployed in either order only if the new release is not yet serving traffic.

## Rollback (safe, counter state preserved by default)

1. Revert the application to the release without the allocator. Wait until no `cv_runtime` connection remains.
2. Run `deploy/runtime_role/rollback_112.sql`. By default it revokes `EXECUTE` from `cv_runtime` and keeps the function and the counters.
3. Do not drop the counter table as part of a routine rollback. Dropping it is a separate, explicit decision (see the commented section).

Rollback of the role grants themselves is in `deploy/runtime_role/rollback.sql`, which now also revokes the allocator's `EXECUTE` before the
role is dropped.

## Re-deployment after a rollback

The counters are kept, so a re-deployment continues from them. Codes allocated by the old release (count-based) are not in the counter, but
the allocator skips every code that already exists, so no code is reissued (tested in `tests/test_job_code_allocation.py`).

## Race with legacy inserts

A legacy writer computes a code and inserts it. The allocator's existence check cannot see an uncommitted row, so it can return a code that
the legacy writer then commits. The insert then fails on the unique index and the savepoint retry allocates again (three attempts). Tested in
`tests/test_job_code_allocation.py::test_a_legacy_insert_that_commits_after_allocation_is_caught_by_the_unique_index`.

## Owner (open decision)

The function is owned by the migration role. In production that is `cv_app`, a superuser, so the function runs with `BYPASSRLS`. See
`deploy/runtime_role/prototypes/job_code_owner_prototype.sql` for the proposed narrower owner, `cv_code_owner`. It is not applied.
