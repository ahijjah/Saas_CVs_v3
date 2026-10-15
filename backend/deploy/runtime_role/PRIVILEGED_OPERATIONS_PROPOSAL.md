# Privileged admin and system operations, and platform_secrets: proposal

Status: PROPOSAL. Nothing here is applied to production, and no role, policy or connection has been changed. It is a separate decision from the
restricted runtime role (PLAN.md, ROLLOUT_AND_ROLLBACK.md). Read it with `context_audit.py` and `inventory.sql`.

## 1. The problem, stated from the code

- **The super_admin branch is self-asserted.** Every tenant policy has `OR current_setting('app.current_role', true) = 'super_admin'`.
  The application sets that value from the verified JWT role (`auth/dependencies.py` via `set_rls_context`). But `set_config` is available to
  any role, so the runtime role can set it too. Anything that runs as `cv_runtime` and executes SQL can therefore read or write every tenant.
  A session-set super_admin flag is **not** trusted database authority, and this proposal does not treat it as one.
- **The runtime role can write platform secrets.** `grants.sql` grants `cv_runtime` `SELECT, UPDATE` on `platform_secrets`. The reads are
  needed (`services/llm_provider.py`, `services/runtime_config.py`, `services/ai_model_registry_service.py`). The `UPDATE` is needed only by the
  super-admin route `PUT /platform-secrets/{key}` (`routers/platform_secrets.py`), which should not run on the request pool at all.
- **Platform configuration is writable by the request pool.** `routers/platform_config.py` writes `system_config` (feature switches, including
  `requirements_v2.enabled`); `routers/ai_usage.py` writes `ai_model_pricing`; `routers/subscription_plans.py` writes `plan_features` and deletes
  `subscription_plans`; `routers/ai_models.py` writes `ai_model_registry`, `ai_prompts` and `ai_stage_defaults`. All of these are behind
  `RequireSuperAdmin` at the route, and the grants let the runtime role perform them too.
- **Operations that depend on the current bypass.** Today the API and the worker connect as `cv_app`, a superuser with BYPASSRLS. Every operation
  above works because of that, not because of a grant. Under the restricted role they fail closed (the platform reads return nothing) or are
  refused, which is why they must be listed before rollout.

## 2. Proposed model

Three database identities, each with one purpose:

| Identity | Used by | Attributes | May do |
|---|---|---|---|
| `cv_runtime` (exists) | API tenant requests, background tenant work | NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owns nothing | Tenant-scoped reads and writes; no `platform_secrets`, no platform configuration |
| `cv_admin` (new) | Super-admin routes only (`RequireSuperAdmin`), on a separate connection pool | NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owns nothing | The platform tables above, by explicit grant; cross-tenant reads only through named functions (below), not through a policy |
| owner role (`cv_app` or a dedicated owner) | Migrations only, run by an operator | Owns the schema; not used by any running service | DDL |

Changes that follow from it:

1. **Drop the super_admin branch from tenant policies** for the runtime identity. Tenant policies check only `app.current_tenant_id`. Super-admin
   cross-tenant reads move to `cv_admin` (below). This is a policy migration (new number, no edit of applied migrations).
2. **platform_secrets through a function, not a grant.** A `SECURITY DEFINER` function owned by the owner role, `cv_analyzer.get_platform_secret(
   p_key text) RETURNS text`, returns one value for one key, with `REVOKE ALL ON FUNCTION ... FROM PUBLIC` and `GRANT EXECUTE ... TO cv_runtime`.
   `cv_runtime` loses `SELECT` and `UPDATE` on the table. The write path (`PUT`) moves to `cv_admin` with `UPDATE` on the table only. The function
   must use `SET search_path = cv_analyzer, pg_temp` and must not accept a table name or a free-form query.
3. **Platform configuration on the admin identity.** `system_config`, `ai_model_*`, `ai_prompts`, `ai_stage_defaults`, `ai_model_pricing`,
   `plan_features` and `subscription_plans` move to `cv_admin` writes. `cv_runtime` keeps the reads it needs (the feature switch read, the active
   prompt read, the model registry read for the stage lookup) and nothing else on those tables.
4. **Operations that stay on the owner role.** Migrations, `DROP`, `TRUNCATE`, trigger changes and any `ALTER ... DISABLE TRIGGER`. None of them is
   reachable from a request. A superuser can do all of this regardless of grants; that is why `cv_app` must not be the running identity.
5. **Audit.** Every `cv_admin` write records the acting user and the statement in `audit_logs`, inside the same transaction, so a rollback removes
   both.

## 3. What is not proposed

- No change to the append-only triggers (migrations 109 and 110 keep them). They refuse UPDATE, DELETE and TRUNCATE for every role except
  through owner-level operations.
- No change to tenant data policies other than removing the super_admin branch for `cv_runtime`.
- No change to production roles or passwords in this step.

## 4. Test plan (disposable PostgreSQL, before any production step)

- `cv_runtime` cannot read `platform_secrets` directly; it can read a key only through `get_platform_secret`; the function refuses a missing key.
- `cv_runtime` cannot update `system_config`, `plan_features` or `ai_model_registry`.
- `cv_admin` can perform each platform write listed in section 2.3 and nothing outside that list.
- A `cv_runtime` session that sets `app.current_role = 'super_admin'` still sees only its own tenant after the change (today it sees every tenant;
  `tests/test_runtime_role_plan.py` records that gap as an explicit, currently-passing assertion so it is noticed when it closes).
- The application's own admin routes pass under `cv_admin` in the HTTP harness (route-level, real database), and the request-pool routes pass under
  `cv_runtime`.

## 5. Decisions the owner must make

1. Whether `cv_admin` is a separate login with its own secret and pool, or the same process with a second `DATABASE_URL`. Recommendation: separate
   login and pool, so that a bug in a tenant route cannot pick up the admin identity.
2. Whether `get_platform_secret` is acceptable for the three read sites, or the secrets are injected as environment variables at start (no DB read).
   Recommendation: function for the DB-held keys that rotate from the admin UI; environment for anything needed before the database is up.
3. Whether the super_admin cross-tenant reads (support screens, the platform tenant list) are enumerated as functions or served by `cv_admin` with
   explicit grants. Recommendation: `cv_admin` grants, because the set of screens is finite and reviewed.
4. Timing relative to rollout: this proposal should be implemented and tested before the restricted runtime role is applied to production, because
   the restricted role without it either breaks platform configuration or keeps the platform secrets writable by the request pool.

## 6. What the current code and tests already show

- `inventory.sql` lists the grants that matter here (`platform_secrets`, `system_config`, the `ai_*` tables, `plan_features`, `subscription_plans`).
- `tests/test_runtime_affected_paths.py` exercises `subscription_plans` delete (no RLS) under `cv_runtime`; it passes today because the grant exists.
  Once section 2 lands, this control must move to `cv_admin` and the test must follow it.
