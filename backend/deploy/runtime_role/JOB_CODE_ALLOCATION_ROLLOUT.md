# Job-code allocator (migration 112): order of operations, owner, rollback

Status: prepared for review. Nothing here has been applied to production. No role, grant, migration or connection has been changed on any
live database.

## The owner (adopted)

Migration 112 makes `cv_code_owner` the owner of the allocator and its counter table. The role is created by 112 when absent, and is accepted
only in the exact shape below. Anything else refuses the migration before any change, inside the migration's transaction.

| Attribute | Required value |
|---|---|
| LOGIN | no (NOLOGIN) |
| SUPERUSER, BYPASSRLS, CREATEDB, CREATEROLE, REPLICATION | no |
| INHERIT | no (NOINHERIT) |
| Memberships, either direction | none (it is not a member of any role, and no role is a member of it) |
| Owned objects | only `allocate_job_code(integer)`, `job_code_counters` and its primary key |

What the owner can do: execute the allocator's body as `SECURITY DEFINER` (`search_path = pg_catalog, pg_temp`), read and update
`job_code_counters`, and `SELECT (job_code)` on `jobs` through one policy, `job_code_allocator_read`, which names only this role and passes
every row. It cannot read any other `jobs` column, write `jobs`, or log in. Because the policy passes every row, the allocator's existence
check sees every tenant's codes, which is what prevents a collision across tenants.

What the restricted runtime role (`cv_runtime`) gets: `EXECUTE` on the allocator and nothing else. It gets no membership, cannot run
`SET ROLE cv_code_owner`, cannot read the counter, and sees only its own tenant's jobs. `PUBLIC` has no `EXECUTE`.

## Bootstrap (before 112, read-only)

1. Run `deploy/runtime_role/inventory.sql` and keep the output. Confirm that `cv_code_owner` either does not exist, or exists in the exact
   shape above. If it exists in any other shape, stop: 112 refuses it, and the cause must be removed by an operator decision, not by the
   migration.
2. The migration role must be able to create the owner and hand it the objects. In production that is a superuser (see prerequisites).

## Order (forward)

1. **Migration 112, as the migration role, with `ON_ERROR_STOP` and one transaction.** Creates the owner (if absent), the counter table
   (RLS enabled, no policy, no grant, owned by the owner), backfills each year's counter from the highest existing sequence, and creates
   `allocate_job_code(integer)` owned by the owner. It then revokes `PUBLIC`'s `EXECUTE` and grants the owner its one column and one policy.
2. **Verify** (read only): the owner's attributes and memberships are as in the table; the allocator and counter are owned by
   `cv_code_owner`; `has_function_privilege('public', ...)` is false; each year's counter is at least the highest existing code for that year.
3. **Role grants (`deploy/runtime_role/grants.sql`)**, when the restricted role is introduced. The file includes
   `GRANT EXECUTE ON FUNCTION allocate_job_code(integer) TO cv_runtime`. It must run after step 1: applied earlier, it fails with
   `UndefinedFunction`.
4. **Application release** that calls `allocate_job_code` (`routers/jobs.py`). It must not run before step 1, or job creation fails.

Old application releases do not call the function, so they run correctly after step 1, before or after it. Steps 1 and 4 can be deployed in
either order only while the new release is not yet serving traffic.

Re-running 112 is safe: every step is idempotent, and an existing owner in the exact shape is accepted.

## Rollback (safe, counter state preserved by default)

1. Revert the application to the release without the allocator. Wait until no `cv_runtime` connection remains (the script refuses to run
   while one is open).
2. Run `deploy/runtime_role/rollback_112.sql`. By default it:
   - revokes `EXECUTE` on the allocator from `cv_runtime`;
   - drops the owner's policy `job_code_allocator_read`;
   - revokes the owner's `SELECT (job_code)`.

   The allocator, the counter table, their owner and every counter value are kept. An unused allocator changes nothing.
3. Do not drop the counter table as part of a routine rollback. The file's commented section (`DROP FUNCTION`, `DROP TABLE`, `REASSIGN OWNED`,
   `DROP ROLE`) is a separate, explicit decision.

Rollback of the role grants themselves is in `deploy/runtime_role/rollback.sql`, which also revokes the allocator's `EXECUTE` before the
role is dropped.

## Re-deployment after a rollback

Re-apply 112, then `grants.sql`, then the application. The counters were kept, so the next code is the one after the last issued, and no code
is reissued. Codes allocated by the old count-based release are not in the counter, but the allocator skips every code that already exists.

## Race with legacy inserts

A legacy writer computes a code and inserts it. The allocator's existence check cannot see an uncommitted row, so it can return a code that
the legacy writer then commits. The insert then fails on the unique index and the savepoint retry allocates again (three attempts). Tested in
`tests/test_job_code_allocation.py::test_a_legacy_insert_that_commits_after_allocation_is_caught_by_the_unique_index`.

## Tests

- `tests/test_job_code_owner_adoption.py` (19): the owner's creation, acceptance, refusals (unsafe attributes, memberships in either
  direction, foreign owned objects) with no change; ownership and the pinned search path; the owner's single read path and no writes; the
  runtime role's limits; PUBLIC; skipping another tenant's code; rollback preserving counters and resuming on re-apply.
- `tests/test_job_code_allocation.py`, `tests/test_job_code_http.py`: allocation, concurrency, backfill, rollback and the HTTP path.

The owner role is cluster-wide, so the refusal tests each use their own fresh cluster.

## Prerequisites before production (not done here)

1. **Migration role.** The migration must run as a superuser, because creating the owner needs CREATEROLE and `ALTER ... OWNER TO` a role
   requires membership in it or superuser. The tests ran as superuser. A CREATEROLE-only migration role has not been tested.
   Do not solve this by granting the migration role membership in `cv_code_owner`: the membership check then refuses every later re-run.
2. **Order.** Migration 112 before `grants.sql`; the application release after 112.
3. **Verify the owner in production** after 112 with the read-only inventory (attributes, memberships, owned objects, the policy).
4. **No production action has been taken.** The owner has not been created on any live database.
