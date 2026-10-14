# Subscription activation and access: billing rules

Approved rules, as implemented. Production payment integration does not exist yet, so activation is a platform provisioning step.

## Who can do what

| Action | Who | Effect |
|---|---|---|
| Start the trial (`POST /tenant/subscription/activate-trial`) | tenant admin, once | 14 days, status `trial`. Once per email domain. |
| Request a plan (`POST /tenant/subscription/select-plan`, `POST /subscription/subscribe`) | tenant admin | Status `pending_payment` (paid) or `pending_sales_contact` (enterprise), with `pending_plan` set. **Never activates.** `subscribe` refuses the trial plan (409). |
| Provision (`PATCH /admin/tenants/{id}/subscription`) | platform super_admin | `assign_plan`, `reactivate`: status `active` with `subscription_ends_at`. `extend_trial`: status `trial` with `trial_end_at`. `suspend`: status `suspended`. Needs a reason; `expires_at` must be in the future for the first three. Audited in the same transaction. |
| Simulated payment (`POST /tenant/subscription/simulate-payment`) | tenant admin | **Development and test only** (`ENVIRONMENT` plus `ALLOW_SIMULATED_PAYMENT`). Refused in production. |

A tenant cannot change its plan from `trial`, `active` or `suspended`; that goes through support.

## What a tenant may do (server-side, per request)

`services/entitlement_service.py` classifies the tenant against the database clock (`now()`) at each request or task. No scheduler is needed.

| State | Reads | Writes, AI, public intake |
|---|---|---|
| `full` (active with end > now, or trial with end > now) | yes | yes |
| `suspended` | yes | no |
| `expired` (active or trial past its end, or an expired status) | yes | no |
| `not_activated` (pending_*) | yes | no |
| `validity_missing` (active or trial with NO end date) | yes | no |
| `blocked` (any other or NULL status) | yes | no |

Billing routes (plans, usage, plan requests) stay available in every state. The tenant's current state is in `GET /tenant/usage` under `access`.

## Legacy records with no validity date

A tenant that is `active` or `trial` with no end date is **not extended** and is **not reactivated**. It keeps its data and read access and
gets `validity_missing`: recruitment writes, AI processing and public intake are refused until platform provisioning records a future
expiry. This is fail-closed by design. Migration 113 does not backfill dates.

**Before deploying, list the affected tenants (read-only, do not run as a change):**

```sql
SELECT tenant_id, name, subscription_status, plan
FROM cv_analyzer.tenants
WHERE subscription_status IN ('active', 'trial')
  AND (subscription_ends_at IS NULL AND subscription_status = 'active'
       OR trial_end_at IS NULL AND subscription_status = 'trial');
```

Each listed tenant needs a platform decision (a provisioned expiry, or a deliberate suspension) before it can write again.

## Intake and queued work

- **Public and email intake** are refused while the tenant is not `full`. A refused public submission returns the generic 422 message, stores no application, and writes an `INTAKE_BLOCKED` intake-log row. The public response does not disclose billing state.
- **Queued CV scoring** is **paused, not failed**. The scoring task checks the tenant before any AI call. If the tenant is not entitled, the application stays `queued` with `stopped_reason = 'access_paused'`, and the task returns without raising, so Celery does not retry. The watchdog ignores paused rows.
- **Resume** happens when platform provisioning makes the tenant entitled. It claims the paused rows in one atomic UPDATE, clears the marker, and dispatches each row once. A repeated provisioning dispatches nothing more. A failed dispatch re-pauses the row.
- **Not yet paused:** requirements-v2 and criteria extraction tasks already queued before a suspension run to completion. New extraction requests are refused at enqueue time by the recruitment routers. Gating in-flight extraction tasks needs its own design (their retry and attempt-token state machines), and is recorded as a follow-up.

## Migration and rollout requirements

1. **Migration 113** (`db/migrations/113_access_paused_stopped_reason.sql`) extends `applications.stopped_reason` with `access_paused`. It must be applied before the release that pauses scoring, or the pause write is refused by the constraint.
2. **No subscription-status schema change.** The existing statuses (`pending_payment`, `pending_sales_contact`, `expired`, `suspended`) are reused.
3. **Deploy order:** migration 113, then the application release, then the legacy-validity review above.
4. **Rollback:** clear `access_paused` markers first (`UPDATE applications SET stopped_reason = NULL WHERE stopped_reason = 'access_paused';`), then restore migration 106's constraint list. Do not roll back the application while paused rows exist without clearing them, or the old code will treat them as ordinary queued work.
5. **Recovery of production tenants** stays with platform provisioning. No production SQL is part of this change.

## Development and test only

`ENVIRONMENT=development` or `test` and `ALLOW_SIMULATED_PAYMENT=true` enable simulated payment. Production never reaches it.
