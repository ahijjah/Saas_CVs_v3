# Runtime role (cv_runtime): rollout and rollback

**Not to be run from this repository's commit as it stands.** Two gates must be met first (section 0). Nothing here changes the enabled extraction
(`requirements_v2.enabled`), the active prompt, the re-analysis switch (`requirements_v2.recovery_enabled = 'false'`) or scoring (`False` in code).

## 0. Gates (all required before the first production step)

1. **The after-commit defect is handled in the repository** (`PLAN.md` section 4, item B): the helper is adopted in `database.py`
   (`_reapply_rls_context`) and its tests pass in both modes. Production adoption is unknown and is verified by the release, not assumed.
2. **The operator's inventory is taken** on production with `inventory.sql` (read-only). Compare it with `PLAN.md` section 1. Any table that has
   a grant or an owner not described there stops the rollout until it is explained.
3. **The rehearsal passes**: `tests/test_runtime_role_plan.py` and the runtime-mode suites (`PLAN.md` section 3) pass on the release commit.

## 1. Snapshot (read-only, before anything)

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /root/backups/runtime_role_${TS}
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/settings_snapshot.sql > /root/backups/runtime_role_${TS}/settings_before.txt
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/runtime_role/inventory.sql > /root/backups/runtime_role_${TS}/inventory.txt
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.job_criteria WHERE criteria_extraction_status IN ('pending','processing')" \
  > /root/backups/runtime_role_${TS}/extraction_in_progress.txt
cat /root/backups/runtime_role_${TS}/extraction_in_progress.txt   # must be 0 before the switch
```

If `extraction_in_progress.txt` is not `0`, wait. Do not switch during an extraction.

## 2. Create the role and its grants (additive; owner or admin login)

The grants change no existing role, owner, policy or setting. They only add `cv_runtime`.

```bash
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod -f - < deploy/runtime_role/grants.sql
# the password is set separately: it is typed here (not echoed), never written into the repository, and kept in this shell only until section 4
read -rs RUNTIME_PASSWORD
printf "ALTER ROLE cv_runtime PASSWORD :'pw';\n" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -v pw="$RUNTIME_PASSWORD" -U cv_app -d cv_analyzer_prod -f -
```

Check the role's shape (must print `f|f|f` for superuser, bypassrls and inherit, and no memberships):

```bash
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc \
  "SELECT rolsuper||'|'||rolbypassrls||'|'||rolinherit FROM pg_roles WHERE rolname = 'cv_runtime'"
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc \
  "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'cv_runtime'"   # 0
```

## 3. Verify the role can do what the application needs, and nothing it must not (read-only, as cv_runtime)

```bash
docker compose exec -T -e PGPASSWORD="$RUNTIME_PASSWORD" postgres psql -U cv_runtime -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.system_config"
docker compose exec -T -e PGPASSWORD="$RUNTIME_PASSWORD" postgres psql -U cv_runtime -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.requirements_analysis_log"   # reads work
docker compose exec -T -e PGPASSWORD="$RUNTIME_PASSWORD" postgres psql -U cv_runtime -d cv_analyzer_prod -tAc "UPDATE cv_analyzer.requirements_analysis_log SET event = event"  # must FAIL (permission denied)
# (the same shell still holds RUNTIME_PASSWORD; it is cleared at the end of section 4)
```

The last command must fail. Anything else is a stop condition.

## 4. Switch the connection (the only change the application sees)

The change is the database user in the connection string, and only that. The application code, the schema, the policies and the settings do not change.

1. Stop the worker and beat (no task runs during the change):
   `cd /opt/cv-analyzer/backend && docker compose stop worker beat`
2. In `backend/.env` on the server, set `DATABASE_URL` to the `cv_runtime` user (same host, database and port; the password from the secret store).
   Keep the old line as a comment so the rollback is a one-line edit.
3. Start the worker and beat, then the API:
   `docker compose up -d worker beat && docker compose up -d api`
4. Check the worker: `docker compose logs worker 2>&1 | tail -20` shows no permission error.
5. Clear the password from the shell: `unset RUNTIME_PASSWORD`. The `TS` value from section 1 is used again below; keep the same shell or set it again.

## 5. Verification after the switch

```bash
TS2=$(date -u +%Y%m%dT%H%M%SZ)
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/settings_snapshot.sql > /root/backups/runtime_role_${TS}/settings_after.txt
diff /root/backups/runtime_role_${TS}/settings_before.txt /root/backups/runtime_role_${TS}/settings_after.txt   # must print nothing
curl -fsS http://72.62.31.221:8000/health
```

- The settings diff must be empty: `requirements_v2.enabled` stays as it was (true on the evidence supplied), the active prompt row is unchanged,
  `requirements_v2.recovery_enabled` stays `false`.
- Log in as an existing user of one tenant. Open one of that tenant's requirements-v2 jobs. The readiness must match the value before the switch.
- Open a job of another tenant with that user: it must not be found.
- The API and worker must show no permission errors in `docker compose logs api worker`.
- Confirm the connected user: `SELECT usename FROM pg_stat_activity WHERE datname = 'cv_analyzer_prod' GROUP BY 1` shows `cv_runtime` for the API and worker,
  and `cv_app` only for the operator's own sessions.

Do not run a model call to test. An operator-approved extraction is a separate decision and not part of this rollout.

## 6. Rollback

**The switch is reversible in one edit and needs no database change.** The role and its grants can stay in place, unused.

Roll back the application to `cv_app`:

```bash
cd /opt/cv-analyzer/backend
docker compose stop worker beat api
# in backend/.env: restore the previous DATABASE_URL (the commented line from section 4)
docker compose up -d worker beat api
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/settings_snapshot.sql > /root/backups/runtime_role_${TS}/settings_rolled_back.txt
diff /root/backups/runtime_role_${TS}/settings_before.txt /root/backups/runtime_role_${TS}/settings_rolled_back.txt   # must print nothing
```

Remove the role only when it is unused. It must have no open connections, and the rollback script refuses otherwise:

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod -f - < deploy/runtime_role/rollback.sql
```

Rollback does not touch the recovery history (it is only ever read or appended by the application; the table and its rows stay), the policies,
the owners, the settings or `cv_app`. If the grants are removed while the application still connects as `cv_runtime`, its queries fail: so always
switch the application back first.

## 7. What is not part of this rollout

- Changing the owner of any object, or creating an owner role: separate decision (`PLAN.md` section 6, item 1).
- Changing `cv_app` from superuser: separate decision, after the owner split.
- Applying the history RLS policy (migration 110) or adopting the context helper (`database.py`): both are in the repository. Verify them in the release, not from this line. Recommended
  before the switch (gate 1), but each is its own reviewed change.
- Narrowing `platform_secrets` access: separate decision.
- Enabling re-analysis or any scoring: not part of this plan.
