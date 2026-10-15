# Subscription activation and access: billing rules and runbook

Production payment integration does not exist, so activation is a platform provisioning step. This runbook states the approved rules as
implemented, and the steps for deployment and rollback. No production SQL is part of this change.

## Who can do what

| Action | Who | Effect |
|---|---|---|
| Start the trial (`POST /tenant/subscription/activate-trial`) | tenant admin, once | 14 days from its start, status `trial`. Once per email domain. |
| Request a plan (`POST /tenant/subscription/select-plan`, `POST /subscription/subscribe`) | tenant admin | Status `pending_payment` (paid) or `pending_sales_contact` (enterprise), with `pending_plan`. **Never activates.** `subscribe` refuses the trial plan (409). |
| Provision (`PATCH /admin/tenants/{id}/subscription`) | platform super_admin | `assign_plan` and `reactivate` set status `active` with `subscription_ends_at` (a future date, required). `suspend` sets `suspended`. Every change needs a reason and is audited in the same transaction. |
| Simulated payment (`POST /tenant/subscription/simulate-payment`) | tenant admin | **Development and test only** (`ENVIRONMENT` plus `ALLOW_SIMULATED_PAYMENT`). Refused in production. |

## The trial

The trial is 14 days from its start and is not extended. There is **no extension action**: the platform cannot grant a longer trial, and
`PATCH .../subscription` with `extend_trial` returns 422 without changing anything. Domain eligibility is unchanged (one trial per email
domain, checked by `activate-trial`). Subscribing cannot start, extend or reset the trial, and a provisioning change leaves
`trial_start_at` as it was. A tenant that wants a longer evaluation goes through sales, which is a commercial decision outside this change.

## What a tenant may do (server-side, per request and per task)

`services/entitlement_service.py` classifies the tenant against the database clock (`now()`) at each request or task. No scheduler is needed.

| State | Reads and billing | Writes, AI processing, public and email intake |
|---|---|---|
| `full` (active with end > now, or trial with end > now) | yes | yes |
| `suspended` | yes | no |
| `expired` (active or trial past its end, or an expired status) | yes | no |
| `not_activated` (pending_*) | yes | no |
| `validity_missing` (active or trial with NO end date) | yes | no |
| `blocked` (any other or NULL status, or no tenant) | yes | no |

The tenant's state is in `GET /tenant/usage` under `access`.

## Queued work: paused, not failed, not retried

Each kind of queued, unstarted AI work checks the tenant before any model call, and leaves itself exactly as it was when the tenant has no
access:

- **CV scoring** (`score_cv_task`): the application stays `queued` with `stopped_reason = 'access_paused'`. The task returns without raising,
  so Celery does not retry, and the watchdog skips paused rows.
- **Requirements-v2 extraction** (`extract_requirements_v2_task`): the attempt stays `pending` with its token. It is not claimed, so it
  has no failure, no usage row and no audit row.
- **Legacy criteria extraction** (`extract_criteria_task`): the job stays `pending`.
- **Re-analysis candidates** (`extract_candidate_task`): the candidate stays `requested`, with no `started` event and no decision.

**Resume** happens in the provisioning request, once the tenant is entitled. It re-dispatches the still-pending work of that tenant only.
Each kind is claimed once, so a repeated resume or a duplicate delivery does not make a second model call:

- Paused applications are claimed by one atomic UPDATE that clears the marker. A repeated resume dispatches nothing for them.
- A v2 attempt is claimed only with its **current** token from `pending`. A superseded delivery (an older token) cannot claim it.
- A legacy job is claimed only from `pending`, or for a Celery retry of the run that already holds it.
- A candidate is claimed by appending a `started` event under the job's lock. A second claim finds it.

**Known limits:**
- A repeated resume re-sends the pending v2 and legacy tasks, because they carry no dispatch marker. The duplicates are refused at the
  claim: they make no model call and change no state, but they are queue messages. A dispatch marker would remove them, and was not added.
- Work that started **before** a suspension runs to completion. Only queued, unstarted work is paused. A model call in progress is not
  interrupted, and its result is stored under the normal rules.
- A processing claim older than 30 minutes (a crashed worker) can be taken over by a later delivery with a different token. This is the
  existing recovery rule and is tested.

## Legacy records with no validity date

A tenant that is `active` or `trial` with no end date is **not extended** and **not reactivated**. It keeps its data and read access, and it
is `validity_missing`: writes, AI and intake are refused until a platform provisioning records a future expiry. This is fail-closed by design.

**Inventory and review before deploying.** Run `deploy/billing/inventory_legacy_validity.sql` on a restored or read-only copy (it is
SELECT only), and review every row with the platform team. For each tenant, one of these is decided, and recorded through provisioning:
a future expiry (the tenant resumes its paused work), or a deliberate suspension. Nothing in this change decides for them.

## Migrations and deployment order

1. **Migration 113** (`db/migrations/113_access_paused_stopped_reason.sql`): adds `access_paused` to `applications.stopped_reason`. Apply before
   the release that pauses scoring, or the pause write is refused.
2. **Migration 114** (`db/migrations/114_analysis_log_started_event.sql`): adds the `started` event to the analysis log, used by the
   re-analysis claim. Apply before the release that claims candidates. Additive: no row is changed.
3. **Release** of the application.
4. **Legacy inventory review** (above). Records that need a decision are decided through provisioning. Until then, those tenants stay
   read-only.

No subscription-status schema change is needed: the existing statuses are reused.

## Rollback

Rolling back the **billing change** is not a configuration switch. The old release has **no** access gate, so rolling the application back
re-opens recruitment writes and AI processing for suspended and expired tenants. Prefer a roll-forward fix. If a rollback is unavoidable,
it must be a build that keeps the entitlement gate, or the gate must be restored first.

Whatever the build, the **paused work** must be preserved:

1. **Disable the watchdog** (the scheduled `reset_stuck_scoring_tasks`) for the whole rollback window. The current watchdog skips paused rows,
   but the **old** one does not, and would fail them after 15 minutes. Disabling it is the only protection against that.
2. **Export the paused-work inventory** before any change, with `psql` on the database (a client-side copy, not run by the guard):
   `\copy (SELECT application_id, job_id, tenant_id, queued_at FROM cv_analyzer.applications WHERE stopped_reason = 'access_paused') TO 'paused_applications.csv' CSV HEADER`
   Keep the file with the change record.
3. **Run the guard**, `deploy/billing/rollback_access_pause_guard.sql`. It verifies that the constraint still allows `access_paused`, that
   every paused row is still `queued`, and reports how many are older than the watchdog window. It changes nothing.
4. **Do not clear `access_paused`.** Clearing it drops the tenant's paused intent: the work would never be resumed and would look like
   ordinary queued work to the old watchdog. Do not drop migration 113's constraint while paused rows exist.
5. **Do not drop the analysis-log constraint** (migration 114) once any `started` row exists: it is part of the history.
6. Roll the application back only as described above. Keep the watchdog disabled until the marker-aware build is back.

**Roll-forward.** Re-enable the watchdog only with a build that excludes `access_paused` (the current one). Provisioning a tenant resumes its
paused work, once per item, as described above.

**Drill.** `tests/test_billing_rollback_drill.py` runs this procedure on a **restored copy** of a disposable database (pg_dump and pg_restore),
with dispatch stubbed and no model. It checks that the guard passes and changes nothing, that the guard refuses when the constraint cannot hold
the marker, that the inventory lists the dateless tenant, that the current watchdog leaves paused work queued, and that roll-forward resumes
each item once. Run it in both database modes before any production rollback decision.

## Development and test only

`ENVIRONMENT=development` or `test` and `ALLOW_SIMULATED_PAYMENT=true` enable simulated payment. Production never reaches it. The frontend
simulation UI is built only with `VITE_ALLOW_SIMULATED_PAYMENT=true`.

## Remaining gaps (not in this change)

- A dispatch marker for pending v2 and legacy extraction (to stop repeated resumes re-sending queue messages).
- Interrupting a model call that is already running when a tenant is suspended.
- Work queued before this deployment runs the new code when it executes, so it is gated the same way; nothing needs to be drained first.
- The production-side decision on legacy tenants with no end date, and on any tenant that needs a longer trial.
