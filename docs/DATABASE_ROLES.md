# Database roles, grants and row-level security

This document describes the database access model of the CV Analyzer. The rules here are enforced by the operator scripts in
`backend/db/ops/`, the migrations `099` and `100`, the privilege matrix in `backend/db/runtime_grants.py`, and the tests in
`backend/tests/test_db_runtime_roles_postgres.py` and `backend/tests/test_runtime_search_path_postgres.py`.

## Logins and roles

| Role | Kind | Used by | Attributes | Owns objects | Notes |
|---|---|---|---|---|---|
| `cv_bootstrap` | superuser login | the database container's initialisation only | superuser | yes (initial) | Never used by the application or by workers. |
| `cv_migrator` | `NOLOGIN` owner | migrations, through `SET ROLE` | `NOSUPERUSER NOBYPASSRLS` | **all** objects in `cv_analyzer` | Created by `db/ops/0000`. |
| `cv_migration_operator` | login | deployment migration step | `NOSUPERUSER NOBYPASSRLS NOINHERIT` | no | Member of `cv_migrator` with `SET` only. |
| `cv_app` | login | tenant API requests | `NOSUPERUSER NOBYPASSRLS NOINHERIT` | no | Row-level security applies. |
| `cv_worker` | login | Celery workers | `NOSUPERUSER NOBYPASSRLS NOINHERIT` | no | Cross-tenant only through named policies. |
| `cv_admin` | login | verified platform administration; two unauthenticated platform flows (below) | `NOSUPERUSER NOBYPASSRLS NOINHERIT` | no | Cross-tenant on the tables it administers. |
| `cv_secrets` | login | secret reads only | `NOSUPERUSER NOBYPASSRLS NOINHERIT` | no | Execute on `get_platform_secret` only. No table access. |

Runtime roles are members of no role, and no role is a member of them. No runtime role may `SET ROLE` to another role. None owns an object,
and none has `CREATE` on a schema.

## Connection policy

Every connection names its role and resolves the application schema without qualification:

- **Role default.** `db/ops/0004` stores `search_path = cv_analyzer, public` on each runtime role, so every new session, pooled connection,
  and session after a transaction rollback resolves `jobs`, `job_criteria` and the rest.
- **Explicit engine setting.** The engines in `backend/database.py` and `backend/services/runtime_config.py` pass the same path. A test
  never depends on a `SET` issued by an earlier session.
- **Pools.** `engine` (cv_app), `admin_engine` (cv_admin) and `worker_engine` (cv_worker) are separate. A tenant request never reaches
  the administration or worker login by choosing it from request content.
- **Startup check.** The API refuses to start if its pool logins are superusers or bypass row-level security
  (`enforce_runtime_role_checks`, default on).

Which path uses which login:

| Path | Login | How it is selected |
|---|---|---|
| Tenant request | `cv_app` | default `get_db` |
| Verified platform administration (`super_admin` role) | `cv_admin` | `get_platform_db`, after the bearer token is verified and the user is `super_admin` |
| Unauthenticated platform flows: login, registration, email verification, password reset, public job and application submission | `cv_admin` | `get_admin_db`, declared on the handler |
| Workers and worker claims | `cv_worker` | `worker_database_url` in each worker |
| Secret reads (`runtime_config.get_secret`) | `cv_secrets` | `secrets_database_url` |
| Migrations | `cv_migration_operator`, then `SET ROLE cv_migrator` | `scripts.migrate` |
| Fixture seeding and inspection in tests | the bootstrap login or a test-provisioned role | tests only |

A bearer token's role claim is **never** used to choose a pool. The administration pool is reached only after the token is verified and the
user is confirmed as `super_admin`.

## Row-level security

Policies decide access by the **PostgreSQL role of the session**, not by a variable the application sets.

- **Tenant rows.** `tenant_id` must equal `app.current_tenant_id`. A session with no tenant context matches nothing (fails closed).
- **Administration.** `current_user = 'cv_admin'` grants cross-tenant access on the tables the administration login manages. The privilege
  itself comes from the grant matrix, so `cv_admin` cannot write what its grants do not allow.
- **Workers.** Named policies `cv_worker_access_<table>` grant cross-tenant access on the tables workers use. The verbs are limited by
  the grant matrix.
- **The role variable.** `app.current_role` is no longer read by any policy. Setting it grants nothing; it is kept only as an informational
  field.

## Privilege matrix

The matrix is generated from `backend/db/runtime_grants.py` into `backend/db/ops/0005_runtime_privileges.sql.template`. A test fails if the
two differ. Each entry is justified by a request path, a worker path, or an administration path, and the test suite checks the live grants
against the matrix in both directions.

Append-only protection: `audit_logs` and `ai_usage_log` grant `cv_app` and `cv_worker` `INSERT` only, with no `SELECT`, `UPDATE` or `DELETE`;
`cv_admin` reads them and may not change them. `application_workflow_history` grants the tenant login `INSERT` and `SELECT` only, with no
`UPDATE` or `DELETE`.

`platform_secrets` has **no table privilege** for any runtime role. Metadata columns (not `value`) are readable by `cv_app` (`key`,
`has_value`) and `cv_admin` (the metadata columns). Writes go through `set_platform_secret`, which only `cv_admin` may execute, and
reads of values go through `get_platform_secret`, which only `cv_secrets` may execute.

PostgreSQL requires `UPDATE` privilege for every row-level lock (`FOR UPDATE`, `FOR SHARE`, `FOR KEY SHARE`, `FOR NO KEY UPDATE`). Code
must use a lock that the role's grants support; the matrix records `UPDATE` where a lock is taken.

## Secrets

- `DB_PASSWORD` and `JWT_SECRET` live in the environment or the secret manager. `get_platform_secret` and `set_platform_secret` refuse
  both keys, and no runtime role can select them from the table.
- Other keys are readable only through `get_platform_secret`, and only for keys in `platform_secret_readable` (or AI provider keys named
  `*_API_KEY` in category `ai`). Secret values never enter logs, audit details or API responses.
- Both functions are `SECURITY DEFINER` with `SET search_path = pg_catalog, cv_analyzer`, schema-qualified objects, and `EXECUTE`
  revoked from `PUBLIC`.

## Accepted residual limitation

**A compromised `cv_app` SQL session can spoof the tenant variable.** `app.current_tenant_id` is set by the application, so an attacker who
can run arbitrary SQL through `cv_app` can set it to another tenant and read that tenant's rows. Row-level security here protects against
application mistakes and against a request that omits or mixes up the tenant. It does **not** provide complete isolation against arbitrary
SQL executed through a compromised tenant session. Stronger isolation would need per-tenant database roles or a tenant identity the database
itself verifies; that is outside this release.

## Operator procedure

Run from the repository root with the deployment secret store supplying every password. None is written in this repository.

1. **Preflight (read-only).** On the existing database, using a read-only inspection login:

   ```bash
   PREFLIGHT_DATABASE_URL='postgresql://<inspection login>@<host>/<database>' python -m scripts.db_preflight
   ```

   Every check must be `PASS`. `FAIL` means the state differs from what this release expects; `UNKNOWN` means the state could not be
   established. **Stop on either.** Do not proceed to step 2 until the cause is understood.

2. **Role bootstrap (once per environment, as the bootstrap superuser).**

   ```bash
   psql -X -v ON_ERROR_STOP=1 -v migration_operator_password="$MIGRATION_OPERATOR_PASSWORD" -d "$DATABASE" \
        -f backend/db/ops/0000_migration_roles.sql.template
   ```

3. **Ownership transfer (generated, reviewed, then run).** Generate the explicit statements with `python -m scripts.generate_ownership_transfer
   --target-owner cv_migrator`, review them, and run them. The generator emits one `ALTER ... OWNER TO` per object and never emits
   `REASSIGN OWNED`. It stops if it meets an object kind it does not handle.

4. **Ledger (only where the ledger does not yet exist).**

   ```bash
   psql -X -v ON_ERROR_STOP=1 -d "$DATABASE" -f backend/db/ops/0001_roles_and_ledger.sql.template
   ```

5. **Migrations (dry run first, then apply).** After a backup (step 8):

   ```bash
   MIGRATION_DATABASE_URL='postgresql://cv_migration_operator:…@host/db' python -m scripts.migrate --dry-run
   MIGRATION_DATABASE_URL='postgresql://cv_migration_operator:…@host/db' python -m scripts.migrate
   ```

6. **Runtime logins and attributes.**

   ```bash
   psql -X -v ON_ERROR_STOP=1 \
     -v cv_app_password="$CV_APP_PASSWORD" -v cv_worker_password="$CV_WORKER_PASSWORD" \
     -v cv_admin_password="$CV_ADMIN_PASSWORD" -v cv_secrets_password="$CV_SECRETS_PASSWORD" \
     -d "$DATABASE" -f backend/db/ops/0004_runtime_roles.sql.template
   ```

7. **Privileges (generated).** Confirm the file matches the generator (`python -m db.runtime_grants | diff - backend/db/ops/0005_runtime_privileges.sql.template`),
   then run it:

   ```bash
   psql -X -v ON_ERROR_STOP=1 -d "$DATABASE" -f backend/db/ops/0005_runtime_privileges.sql.template
   ```

8. **Backup** before steps 5 to 7 on an existing database: `pg_dump --format=custom --file <dump> "$DATABASE"` using the migration operator
   login. Keep the dump outside the database host, and verify that it lists (`pg_restore --list <dump>`).

9. **Verification.** Re-run the preflight. All checks must pass before the application starts. The API refuses to start with a superuser or
   RLS-bypassing login.

## Rollback

Migrations are forward-only. Rollback restores the database from the backup taken in step 8, not by reversing migrations by hand.

1. Stop the new API, workers and beat.
2. Restore the dump into a **new** database with `pg_restore --clean --if-exists --no-owner --dbname <new database> <dump>` run by the
   bootstrap login. Do not restore over the live database.
3. Point the previous application build at the restored database.
4. Restore runtime attributes exactly as captured in the preflight output, rather than making any runtime role a superuser. A runtime role
   that needs more privilege is a defect to fix, not a rollback step.
5. Run the smoke test. Do not use `REASSIGN OWNED`.

Records written after the backup are lost by this procedure. Record the backup time in the release notes before step 5.

## Bootstrap and fresh installation

The database container initialises as `cv_bootstrap` (superuser) with `db/schema.sql` and `db/seed.sql`. The runtime roles are created
`NOLOGIN` by `schema.sql` with no password. The seeded platform administrator is created **disabled** with an unrecorded password; an
operator enables it through the deployment secret store. Then the operator runs steps 2 to 9 above. The `migrate` service in
`backend/docker-compose.yml` runs the migrations as `cv_migration_operator` and must complete before the API and workers start.

No password is committed anywhere in this repository.
