# Runbook: requirements-v2 re-analysis (migration 109) — deploy, verify, roll back

**Status of this release: re-analysis stays DISABLED (`requirements_v2.recovery_enabled = 'false'`) and v2 scoring stays disabled in code
(`REQUIREMENTS_V2_SCORING_SUPPORTED = False`).** This runbook changes no live setting, role or applied migration. Enabling re-analysis is a separate,
later decision (section 7).

Nothing here runs automatically. An operator runs each command on the server, in order, and keeps the output. Paths assume the server layout of
`deploy/setup.sh`: application at `/opt/cv-analyzer`, frontend at `/var/www/cv-analyzer/dist`.

## 1. Pinned versions

| Component | Version | Source of truth |
|---|---|---|
| Release commit | the full hash reported with this runbook (`RELEASE_COMMIT` below) | `git rev-parse HEAD` on the server must equal it |
| Branch | `feature/scoring-v2-evidence-engine` | `deploy/setup.sh` `REPO_BRANCH` |
| Python (API, worker, beat) | 3.11 (`python:3.11-slim`) | `backend/Dockerfile` |
| PostgreSQL | 16 (`postgres:16-alpine`) | `backend/docker-compose.yml` |
| Redis | 7 (`redis:7-alpine`) | `backend/docker-compose.yml` |
| Node.js (frontend build only) | 22 (verified 22.22.0) | operator machine or server |
| Python packages | `backend/requirements.txt` exactly (verified with `pip check` on 3.11) | `backend/requirements.txt` |

Unpinned packages, recorded and not changed here: `torch` (2.14.1 when verified; pulled in by sentence-transformers), `email-validator`,
`snowballstemmer`. A rebuild may resolve a different torch.

## 2. Settings: seed defaults versus live values

Migrations 108 and 109 insert their seed rows with `ON CONFLICT ... DO NOTHING`. **A seed default never overwrites a value that already exists.**
The live values are whatever the database holds on the day of the deployment. The table below separates the two. The "live" column is
operator-supplied evidence from the VPS, not something verified by this runbook: the snapshot in section 3 records the actual values.

| Setting | Seed default (migration) | Live value (VPS evidence supplied) | Rule for this deployment |
|---|---|---|---|
| `requirements_v2.enabled` | `'false'` (108, `ON CONFLICT DO NOTHING`) | `'true'` | **Leave as found.** No step here writes it. v2 extraction is live, so section 4 checks for work in progress. |
| `requirements_v2.recovery_enabled` | `'false'` (109, `ON CONFLICT DO NOTHING`) | not present before 109 | Created `'false'` by 109. Must be `'false'` afterwards. If it already exists with another value, **stop** and report; do not change it. |
| `criteria_extraction_v2` prompt, version 3 row | inactive (108 seed) | an active prompt labelled `v2-3` (the live row may be another version) | **Leave as found.** The activation `UPDATE` documented in migration 108's header must **not** be run here. The snapshot records the exact active row. |
| `job_analysis.require_classification_acknowledgment` | existing policy | not in the evidence | Leave as found; recorded in the snapshot. |
| `ai_stage_defaults` for `requirements_v2_extraction` | `gpt-4o-mini-2024-07-18` (108 seed) | not in the evidence | Leave as found; recorded in the snapshot. |
| `REQUIREMENTS_V2_SCORING_SUPPORTED` | `False` (code, `backend/services/requirements_guard.py`) | not a database setting | Stays `False` in this release. |

Environment variables: **none added or changed.** Verified by diff against the parent commit: `config.py`, `.env.example`, compose, Dockerfile,
requirements, the Celery app and the package files are unchanged.

Frontend: the API origin is compiled in. Build with `VITE_API_BASE` set explicitly (default `http://72.62.31.221:8000`).

## 3. Backup and settings snapshot (before any change)

```bash
sudo mkdir -p /root/backups
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f /opt/cv-analyzer/backend/docker-compose.yml exec -T postgres pg_dump -U cv_app -d cv_analyzer_prod -n cv_analyzer -Fc > /root/backups/cv_analyzer_${TS}.dump
docker compose -f /opt/cv-analyzer/backend/docker-compose.yml exec -T postgres pg_dump -U cv_app -d cv_analyzer_prod -Fc > /root/backups/cv_analyzer_full_${TS}.dump
cp -a /var/www/cv-analyzer/dist /root/backups/frontend_dist_${TS}
git -C /opt/cv-analyzer rev-parse HEAD > /root/backups/code_commit_${TS}.txt
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/settings_snapshot.sql > /root/backups/settings_before_${TS}.txt
```

Check the dump is readable without touching production:

```bash
pg_restore -l /root/backups/cv_analyzer_${TS}.dump | tail -3
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.jobs"
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.job_criteria"
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.audit_logs"
```

Record the three counts with the backup name. Keep the backup until section 6 has passed.

## 4. Preflight (read-only)

```bash
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_preflight.sql
```

It must end with `preflight ok`. It stops with an error if 106, 107 or 108 is missing, or if 109 is already applied. Its output also prints the
live switch values and the live prompt rows. **Record them; they must match the snapshot.**

Then check the release conditions:

```bash
git -C /opt/cv-analyzer rev-parse HEAD | tee /root/backups/rollback_to_commit.txt        # the commit to roll back to (section 8)
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT criteria_extraction_status, count(*) FROM cv_analyzer.job_criteria WHERE criteria_extraction_status IN ('pending','processing') GROUP BY 1"
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.requirements_analysis_log" 2>/dev/null || echo "109 not applied (expected)"
```

- Work in progress: because `requirements_v2.enabled` is `true`, extraction may be running. If any row is `pending` or `processing`, wait for it or stop. Do not deploy during an extraction.
- Do not run any statement that sets `requirements_v2.enabled`, `requirements_v2.recovery_enabled`, or the prompt's `is_active`.

## 5. Deploy (in this order)

```bash
cd /opt/cv-analyzer
git fetch origin
RELEASE_COMMIT=<full hash reported with this runbook>
git checkout --detach "$RELEASE_COMMIT"
test "$(git rev-parse HEAD)" = "$RELEASE_COMMIT" || exit 1

cd backend
docker compose stop worker beat                  # no task runs while the schema changes

# 5a. migration 109 (additive; one transaction; the file contains BEGIN and COMMIT). Piped from the host so the file is read here.
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < db/migrations/109_requirements_analysis_recovery.sql

# 5b. verification of the schema (read-only)
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_verify.sql

# 5c. code: API, worker and beat from the release commit
docker compose up -d --build api worker beat

# 5d. frontend
cd /opt/cv-analyzer && npm ci
VITE_API_BASE=http://72.62.31.221:8000 npm run build
rsync -a --delete dist/ /var/www/cv-analyzer/dist/
```

Do not run migration 108's activation statements, and do not run any `UPDATE` on `system_config` or `ai_prompts`.

`npm run lint` (`tsc --noEmit`) reports 16 errors that predate this release, in `pages/ApplicationsList.tsx`, `pages/CandidatesWorkspace.tsx` and
`pages/JobDetails.tsx`. The production build (`vite build`) does not type-check and succeeds. Do not fix them inside this deployment.

## 6. Verification after deployment

```bash
curl -fsS http://72.62.31.221:8000/health                    # expect {"status":"ok"}
curl -fsSI http://72.62.31.221/ | head -1                    # expect HTTP/1.1 200
docker compose ps                                            # api, worker, beat up; postgres and redis healthy
docker compose logs worker 2>&1 | grep -c "requirements_v2_extraction_worker.extract_candidate_task"   # expect 1 or more (task registered)
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f - < deploy/settings_snapshot.sql > /root/backups/settings_after_${TS}.txt
diff /root/backups/settings_before_${TS}.txt /root/backups/settings_after_${TS}.txt
```

The diff must show **only** the new `requirements_v2.recovery_enabled = false` row. Any other difference, including a changed
`requirements_v2.enabled` or a changed active prompt row, is a stop condition: roll back code (section 8) and report.

Then:
- `deploy/recovery_109_verify.sql` again (section 5b).
- `SELECT count(*) FROM cv_analyzer.requirements_analysis_log` must be `0`.
- The three counts from section 3 must not shrink (`jobs` and `audit_logs` may grow through normal use).
- In the browser, open one existing requirements-v2 job as an administrator. It must load, its readiness must match the value before the deployment, and no re-analysis control may appear while the switch is `false`.

## 7. Enabling re-analysis (NOT part of this deployment)

Do not enable it in this deployment. Enabling requires all of the following, recorded:
1. A decision on the database role model (section 10, item 1). A superuser application role can disable the immutability triggers.
2. Product-owner approval of the recovery feature in production.
3. The test evidence for the release commit (section 9) and a fresh run of the browser checks against the production configuration.

## 8. Rollback

Rollback has four parts. Choose by what has happened on the server: **before** or **after** a recovered (applied) version.

### 8.1 Code rollback before any recovered version has been applied

Check first:

```bash
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.requirements_analysis_log WHERE event = 'applied'"
```

- Must print `0`. If not, follow 8.2.

Check for an open candidate (requested and not yet applied or discarded):

```bash
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "
SELECT r.version_id, r.version_no FROM cv_analyzer.requirements_analysis_log r
WHERE r.event = 'requested'
  AND NOT EXISTS (SELECT 1 FROM cv_analyzer.requirements_analysis_log t WHERE t.version_id = r.version_id AND t.event IN ('applied','discarded'))"
```

- If any row is printed: do not roll back yet. An administrator discards each open candidate through the release that is running
  (`POST /jobs/{job_id}/requirements/recovery/versions/{version_id}/discard`), so the history records the decision. Then repeat the check.

Roll back the code to the commit recorded before the deployment:

```bash
cd /opt/cv-analyzer/backend
docker compose stop api worker beat
cd /opt/cv-analyzer && git checkout --detach "$(cat /root/backups/rollback_to_commit.txt)"
cd backend && docker compose up -d --build api worker beat
rsync -a --delete /root/backups/frontend_dist_<TS>/ /var/www/cv-analyzer/dist/
```

The database is unchanged by this path. The history table stays (it is empty of applied versions and holds only the decisions recorded so
far), and the switch stays `false`. Repeat the section 6 health checks, without the 109 steps.

### 8.2 Code rollback after a recovered version has been applied

Applying a candidate (take or merge) changed the job's own content: the job's requirements document, its stored record, and the pointer to the
active version. **Rolling back code does not undo that.** The applied content stays. Recovering the previous draft is not a rollback: it is a new
recovery action under the current release, or a separately approved data procedure using the superseded payload in the history.

Requirements for the target commit:
- It must be **at or after `2952855`**, the commit that introduced the input binding that marks an unverified analysis. A merged analysis may
  store a record without that binding; an older commit would not show such a job as unverified. Check with
  `git merge-base --is-ancestor 2952855 <target>`.
- It must read the job's stored analysis without losing the pointer `requirements_active_version`. The parent commit's save path copies the
  stored analysis and keeps unknown keys, which is why it is a valid target; this is read from the code, so the drill below must confirm it.

Before rolling back in production, run a **drill** on a copy: restore the backup from section 3 into a scratch database, run the target commit
against it, and for every applied job check: it loads; its readiness is the same or stricter than before; a save keeps `requirements_active_version`.
Only then roll back production, with the same commands as 8.1 but with the drilled target commit.

Keep the history table and the switch exactly as they are.

### 8.3 Dropping migration 109 (schema rollback)

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_rollback_empty.sql
```

- While the history is empty, the script drops the two triggers, the table and the switch row, and restores the 108 state.
- Once any event exists, the script refuses. The table stays. The correct rollback is the switch, already `false`, together with 8.1 or 8.2.
- Removing a non-empty history is an archival decision, not a rollback. It needs an approved export first:
  `\copy (SELECT * FROM cv_analyzer.requirements_analysis_log ORDER BY log_id) TO '/root/backups/recovery_history.csv' CSV HEADER`
  followed by an owner-level change, approved separately.

### 8.4 Restoring the database from the backup

Only for catastrophic loss, with approval. A restore rewinds all data to the backup time, including history, audit rows and job edits made after
it. History cannot be rolled back row by row, so this is the only route that removes events. It is destructive.

## 9. Test evidence for the release commit

Recorded in the deployment report: the full backend suite on Python 3.11 with the pinned requirements; the migration-109 deployment tests (19)
on an isolated PostgreSQL 16 migrated through 108; the requirements-v2 frontend tests (186); the production frontend build; the browser harness
against a disposable database (73/73).

## 10. Open items (decide, do not assume)

1. **The application role's privileges (corrected).** `docker-compose.yml` bootstraps `cv_app` as the PostgreSQL superuser (`POSTGRES_USER: cv_app`).
   Triggers fire for superusers too: a superuser's `UPDATE` on the history is refused. But a superuser, and the table's owner, can still:
   `ALTER TABLE ... DISABLE TRIGGER`, `SET session_replication_role = replica` (superuser only), drop the table, or change the role. Both
   checks are in `backend/tests/test_requirements_v2_migration_109_deploy.py`. So the append-only guarantee against the application role is
   administrative while `cv_app` is a superuser or owner. Changing the role model affects row-level security (a superuser bypasses it, and
   many tables enable it), so it needs its own tested migration. Recovery stays off until this is decided.
2. **Migration 070 does not apply on a clean database** (`CREATE INDEX ... USING GIN (policies->'sla_thresholds')` needs parentheses around the
   expression). Existing databases already past it are unaffected. Do not edit an applied migration; a rebuild from scratch must use a corrected
   copy. Recorded for a decision.
3. **The image build is not verified in this environment** (no Docker daemon was available to the verifier). The Python packages were verified
   on 3.11 with `pip check`; the image adds apt packages and a pre-downloaded embedding model that were not built here. Build it on the server
   before the deployment.
4. **`torch` is unpinned** (section 1).
