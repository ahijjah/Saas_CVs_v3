# Restricted runtime role for the API and the worker — plan, evidence and decisions

**Status (corrected).** A plan with tests. The repository has since adopted the after-commit helper (`database.py`, `_reapply_rls_context`)
and the history-table policy (migration 110). Nothing in this repository records a production change. Every production fact in this file is a
historical operator report that needs a fresh, read-only inventory before it is relied on (see the evidence block).

Settings that this plan must preserve, and their status in the repository:
- `requirements_v2.enabled`: extraction. Operator-reported **true** (14 October). The repository default in migration 108 is `false`; the live value
  is unknown until read. Nothing in this plan changes it, and the active prompt (`criteria_extraction_v2`, version 3) is not touched.
- `requirements_v2.recovery_enabled`: **off** (migration 109 default; stays off).
- `REQUIREMENTS_V2_SCORING_SUPPORTED`: a code constant, **False** (`services/requirements_guard.py`). It stays False.

**Historical operator evidence (reported for 14 October; requires refresh).** The API and the worker connect as `cv_app`. `pg_roles` showed
`rolsuper = true` and `rolbypassrls = true` for `cv_app`. This was not re-verified in the repository. The live state stays unknown until a
current read-only inventory is taken.

**Why it matters.** A superuser with BYPASSRLS bypasses privilege checks and row-level security. It can also disable the immutability
triggers, change any role, and drop objects.

## 1. Inventory

What was checked, and where.

| Area | How | Result |
|---|---|---|
| Roles, memberships, owners, RLS flags, policies, grants, functions, triggers | `inventory.sql` (read-only; run it on production with an admin login and keep the output) and the same queries run on a disposable database built from the full migration chain | Disposable build: 49 tables; 26 with RLS enabled, each with a policy (28 policies). All objects owned by the build's superuser. **Production ownership is not known from this repository: the compose bootstrap makes `cv_app` the owner, so `inventory.sql` on the server is the first step.** |
| Table privileges the code needs | `usage_inventory.py`: a static scan of `routers`, `services`, `workers`, `auth` for `FROM`/`JOIN` (SELECT), `INSERT INTO`, `UPDATE … SET`, `DELETE FROM` | 48 tables with statements; 0 tables used at run time by name (three hits were prose). Output: `grants.sql`, generated (`generate_grants.py`), checked by a drift test. |
| Tenant-context handling | `database.py` (`set_rls_context`, `get_db_with_rls`); `auth/dependencies.py` (`get_current_user` sets it per request); login, public intake and workers set `app.current_role = 'super_admin'`; `watchdog`, `cv_score`, `criteria_worker`, `backfill` set it inline; `services/requirements_api.py` `_load` sets it again | Context is transaction-local (`set_config(..., true)`). It lasts to the end of the transaction. |
| Routes that run SQL without an authenticated user | `context_audit.py` part 1 | 0 routes. Routes with `dependencies=[Require…]` resolve `get_current_user`, which sets the context. |
| Functions that commit and then run SQL without setting the context again | `context_audit.py` part 2 | 7 functions; 6 query RLS tables after the commit (`score_pending_uploads`, `bulk_update_workflow_status`, `retry_criteria_extraction`, `_handle_public_submission`, `create_tenant_user`, `_score_cv_async`). `deactivate_or_delete_subscription_plan` touches a table without RLS. |
| Runtime DDL, `SET ROLE`, role changes in the application | grep | None. |

## 2. Recommended runtime role

`cv_runtime`, created by `grants.sql`:

- `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT`. The password is set by the operator outside the repository.
- Owns nothing. Schema and tables stay owned by the migration owner. Ownership is the one thing that bypasses RLS unless forced, and the owner can alter and drop.
- Member of no role (`NOINHERIT`, no memberships). It cannot `SET ROLE` to `postgres` or `cv_app` (tested).
- Table privileges exactly as the source needs (`grants.sql`). `TRUNCATE`, `REFERENCES`, `TRIGGER`, `CREATE` are not granted. `CREATE` on the schema is revoked.
- The history table: `SELECT, INSERT` only; `UPDATE, DELETE, TRUNCATE` revoked (tested after a later `GRANT … ON ALL TABLES`, the trigger still refuses).
- Cannot disable triggers, drop tables, change grants, set `session_replication_role`, or alter the schema (all tested).

The migration owner remains `cv_app` until a separate owner role is decided (section 5). Migrations keep running as the owner.

## 3. Tests (disposable PostgreSQL 16, full migration chain, fake model responses)

`tests/test_runtime_role_plan.py` (26 tests): the role's shape; that it cannot assume a privileged role or change the schema; that it cannot disable
or bypass the history protections; tenant isolation with context, without context, across a pooled connection, and after a commit inside a request;
the super_admin exposure (asserted, documented); the history read/append and the refusals; the proposed context helper; the proposed history policy;
the grants file drift and idempotency; the rollback.

`tests/test_requirements_v2_migration_109_deploy.py` (20): migration 109 behaviour and the superuser facts (unchanged).

**Required operations run as the runtime role** (`CV_TEST_RUNTIME_ROLE=cv_runtime`). The shared harnesses connect as the role and set the
tenant context per session, as `get_current_user` does; the tests themselves are unchanged apart from passing the actor:

| Suite | Superuser (default) | Runtime role | What it exercises |
|---|---|---|---|
| `test_requirements_v2_recovery_postgres.py` | 38 pass | 38 pass | request, claim, worker completion, rejection, keep, take, merge, discard, concurrency, immutability |
| `isolated_requirements_v2_extraction_postgres.py` | 43 pass | 43 pass | extraction worker, prompt and model read, enabled-switch read, usage logging |
| `test_requirements_v2_binding_postgres.py` | 13 pass | 13 pass | input binding and context findings |
| `test_requirements_v2_postgres.py` | 33 pass | 33 pass | requirements API, migrations 106/107, tenant isolation on real SQL, concurrency |
| `test_requirements_pipeline_postgres.py` | 45 pass | 45 pass | pipeline record, guards, acknowledgments, rollback, concurrency |

Two harness defects were found and fixed on the way, and both changed the meaning of earlier results:

- `tests/conftest.py` replaced the `database` module with a **no-op** `set_rls_context` in every test session. Every test that went through the
  services or workers therefore ran with no tenant context, and the superuser bypass hid it. The stub is now only used when the real module cannot be
  imported. The superuser runs of the suites above pass with the real helper.
- `database` was not re-imported with the fresh SQLAlchemy in the synthetic-schema fixtures. It is now in the fresh-import list.

**Coverage as of commit 4ee2b36 (corrected).** 18 of the 138 test files read `CV_TEST_RUNTIME_ROLE`; those harnesses connect as the role and
set the tenant context per session. The full suite passes with the variable set. That pass covers the harnesses that honour the variable. It does
not show that every route runs as the role: the remaining harnesses still connect as the superuser.

## 4. Operations that depend on the current privilege bypass

| # | Operation | Under the superuser bypass | Under `cv_runtime` (today's code) | Evidence | Correction |
|---|---|---|---|---|---|
| A | A query on an RLS table with no tenant context (anywhere) | returns every row | returns none; writes are refused | `test_without_a_context_no_tenant_row_is_visible…` | fail-closed is correct; find and set the context for each such path (none found by the route audit) |
| B | Queries after a `commit()` in the same request (6 functions) | work: the superuser ignores the context | the context is gone: no rows, updates change nothing (silent) | `test_a_commit_inside_one_request_drops…`; `context_audit.py` part 2 | ADOPTED in `database.py` (`_reapply_rls_context`, after_begin); tested in both modes. Was: adopt `proposed_rls_context.py` (re-applies the context at every transaction start, tested with a pooled engine) or re-set the context after each commit, as `_load` does |
| C | Recovery history read across tenants | any tenant | **any tenant before migration 110**; tenant-scoped after it (repository) | `test_today_the_runtime_role_can_read_another_tenants_history` | migration 110 (tenant + super_admin, `WITH CHECK`; applied in the repository, production unknown). Was: `proposed_history_rls.sql` (not present); the isolated suites pass with it applied |
| D | Background and login paths | all tenants | all tenants, by **setting** `app.current_role = 'super_admin'` | `test_the_runtime_role_can_set_the_super_admin_context_itself` | not a privilege problem: the setting is application-controlled. See decision 2 |
| E | `platform_secrets` (no RLS) | readable | readable (as `cv_app` today) | `grants.sql` | decision 3 |
| F | Migrations and DDL | owner | owner (`cv_app`) — unchanged | none needed at runtime | a separate owner role, decision 1 |
| G | Enabled extraction and the active prompt | read as superuser | read as the runtime role (grants on `system_config`, `ai_prompts`, `ai_stage_defaults`, `ai_usage_log`) | isolated extraction suite in runtime mode | nothing to change; the grants cover them |

No operation in the source needs a privilege the role does not have, once the context is set per request. The one real break (B) is a
context-lifetime defect that the superuser hides. The other issues (C, D, E) are exposure, not breakage.

## 5. Proposed corrections (none applied)

1. `proposed_rls_context.py`: re-apply the tenant context at the start of every transaction (`after_begin`), from `session.info`. Fail-closed when
   nothing is set. Replaces the six after-commit defects without adding context calls to each function. Tested on a pooled async engine.
2. History RLS on `requirements_analysis_log` (implemented as migration 110): with the same tenant rule and the super_admin rule the other tables use, plus
   `WITH CHECK`. Tested: isolation, no-context refusal, background access, cross-tenant insert refused; the recovery suites pass with it applied.
3. The `super_admin` exposure (D): move the background and login paths onto a separate system role that holds the policy grant, or to a
   `SECURITY DEFINER` function that only those paths call. This changes which connection pool the background code uses, so it is a decision.

## 6. Decisions still open

1. **Owner role.** Migrations and DDL run as an owner that is not the runtime role (`cv_owner`, NOLOGIN, owning the schema and objects). Changing
   ownership of existing objects is a production change with its own migration and rehearsal. Until then `cv_app` remains the owner.
2. **The super_admin setting (D).** Keep the GUC design and accept that application code controls the exposure, or split the background and login
   paths onto a separate system role/function. Recommendation: split, because the exposure is a single setting.
3. **`platform_secrets` and other admin tables (E).** Narrow to an admin-only role, or keep as today. Recommendation: narrow.
4. **Adopting the helper (1) and the history policy (2).** Done in the repository: the helper is in `database.py`, the policy is migration 110.
   Production adoption is unknown and must be verified before any switch to the runtime role.
5. **The superuser flag on `cv_app`.** It can stay until the owner split (1). Once the runtime role serves the API and the worker, `cv_app` is used
   only by migrations and operators.

## 7. Rollout and rollback

See `ROLLOUT_AND_ROLLBACK.md`. In short: the grants are additive and change nothing that `cv_app` uses; the switch is a connection-string change
for the API and the worker; rollback is the same change in reverse. `rollback.sql` removes the role only when nothing connects as it.

## 8. Artefacts

- `inventory.sql` — read-only inventory (run on production).
- `usage_inventory.py`, `generate_grants.py`, `grants.sql` — the privilege matrix, generated and checked.
- `context_audit.py` — static audit of tenant-context handling (review aid).
- `proposed_rls_context.py` — the reference copy of the helper; the adopted version is `database.py` (`_reapply_rls_context`). The history policy is
  migration 110. (`proposed_history_rls.sql` was referenced here but never existed in this directory; the reference is corrected.)
- `rollback.sql` — removes the runtime role.
- `ROLLOUT_AND_ROLLBACK.md` — exact steps.

## 9. Changes since this plan was written (repository state, not production)

- Migration 110: RLS on `requirements_analysis_log` (tenant and super_admin rule, `WITH CHECK`).
- `database.py`: tenant context re-applied at every transaction start (`after_begin`), recorded on the session.
- Migration 111 (policy setting names, plan-features read, workflow-history tenant rule): **approval pending**, see the readiness sheet.
- Migration 112: the job-code allocator, owned by `cv_code_owner` (NOLOGIN, no memberships, owns only the allocator and its counter). `cv_runtime`
  holds EXECUTE on the allocator only.
- Migrations 113 and 114: `access_paused` stopped reason; `started` analysis-log event. Both additive.
- Billing: the access check reads `tenants` (`services/entitlement_service.py`). `grants.sql` gives `cv_runtime` SELECT, INSERT and **UPDATE** on
  `tenants`. The entitlement is only as strong as that write protection, so this grant needs a decision before billing relies on the role.
- `platform_secrets`: `cv_runtime` still holds SELECT and UPDATE (`grants.sql`). Unchanged by this work; see the readiness sheet.
