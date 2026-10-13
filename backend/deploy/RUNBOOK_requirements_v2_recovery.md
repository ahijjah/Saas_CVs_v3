# Runbook: requirements-v2 re-analysis (migration 109) — deploy, verify, roll back

Status of this release: **recovery is OFF and must stay OFF; v2 scoring is disabled in code (`REQUIREMENTS_V2_SCORING_SUPPORTED = False`).**
This runbook does not enable anything. Enabling re-analysis is a separate, later decision (see section 7).

Nothing here runs automatically. Every command below is run by an operator on the server, in order, with the output recorded.
Paths assume the server layout of `deploy/setup.sh`: application at `/opt/cv-analyzer`, frontend at `/var/www/cv-analyzer/dist`.

## 1. Pinned versions

| Component | Version | Source of truth |
|---|---|---|
| Release commit | the commit that adds this runbook (reported with it) | `git rev-parse HEAD` must equal it on the server |
| Branch | `feature/scoring-v2-evidence-engine` | `deploy/setup.sh` `REPO_BRANCH` |
| Python (API, worker, beat image) | 3.11 (`python:3.11-slim`) | `backend/Dockerfile` |
| PostgreSQL | 16 (`postgres:16-alpine`) | `backend/docker-compose.yml` |
| Redis | 7 (`redis:7-alpine`) | `backend/docker-compose.yml` |
| Node.js (frontend build only) | 22 (verified 22.22.0) | operator machine or server |
| Python packages | exactly `backend/requirements.txt`; verified with `pip check` on 3.11 | see below |
| Key pins | fastapi 0.115.5, starlette 0.41.3 (resolved), sqlalchemy 2.0.36, asyncpg 0.30.0, psycopg2-binary 2.9.10, celery 5.4.0, redis 5.2.1, passlib 1.7.4, bcrypt 4.2.1, openai 1.57.0, sentence-transformers 3.3.1 | `backend/requirements.txt` |

Known unpinned packages (recorded, not changed here): `torch` (resolved 2.14.1 when verified; pulled in by sentence-transformers),
`email-validator`, `snowballstemmer`. A rebuild can therefore pull a different torch. Pin them before a rebuild you need to reproduce exactly.

## 2. Settings

| Setting | Value at deployment | Where | Notes |
|---|---|---|---|
| `requirements_v2.enabled` | `false` (unchanged) | `system_config` (migration 108) | v2 job creation and extraction stay off unless already enabled; do not change here |
| `requirements_v2.recovery_enabled` | `false` | `system_config` (migration 109) | re-analysis cannot be requested or applied while `false` |
| `job_analysis.require_classification_acknowledgment` | unchanged | `system_config` | existing policy, not touched |
| `REQUIREMENTS_V2_SCORING_SUPPORTED` | `False` | `backend/services/requirements_guard.py` (code) | scoring is not supported in this release |
| `prompt criteria_extraction_v2 version 3` | inactive (migration 108) | `ai_prompts` | must stay inactive until an administrator activates it |
| Environment variables | **no change** in this release | `.env` | verified: `git diff` of `config.py`, `.env.example`, compose, Dockerfile, requirements, celery app is empty since the parent commit |
| `VITE_API_BASE` | `http://72.62.31.221:8000` (set explicitly at build) | frontend build | `config.ts` defaults to the same value |

## 3. Backup (before any change)

Run on the server, in `/opt/cv-analyzer/backend`. Keep the backup until verification (section 6) has passed.

```bash
sudo mkdir -p /root/backups
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T postgres pg_dump -U cv_app -d cv_analyzer_prod -n cv_analyzer -Fc > /root/backups/cv_analyzer_${TS}.dump
docker compose exec -T postgres pg_dump -U cv_app -d cv_analyzer_prod -Fc > /root/backups/cv_analyzer_full_${TS}.dump
cp -a /var/www/cv-analyzer/dist /root/backups/frontend_dist_${TS}
echo "$(git rev-parse HEAD)" > /root/backups/code_commit_${TS}.txt
```

Verify the dump is readable (this does not touch production):

```bash
pg_restore -l /root/backups/cv_analyzer_${TS}.dump | tail -3   # must list tables without error
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.job_criteria"  # record this number
```

Record the row counts of `jobs`, `job_criteria` and `audit_logs` next to the backup name. They are compared in section 6.

## 4. Preflight (read-only)

```bash
cd /opt/cv-analyzer/backend
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_preflight.sql
```

It must end with `preflight ok`. It stops with an error if 106, 107 or 108 is missing, or if 109 is already applied.
Record the `rolsuper` value it prints for `cv_app`: this is an open item (section 8).

Also record the deployed code and the work in progress:

```bash
git -C /opt/cv-analyzer rev-parse HEAD | tee /root/backups/rollback_to_commit.txt   # the commit to roll back to
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc \
  "SELECT criteria_extraction_status, count(*) FROM cv_analyzer.job_criteria WHERE criteria_extraction_status IN ('pending','processing') GROUP BY 1"
```

If any v2 extraction is `pending` or `processing`, wait for it to finish or stop the deployment. Do not deploy during an extraction.

## 5. Deploy (in this order)

```bash
cd /opt/cv-analyzer
git fetch origin
git checkout --detach <RELEASE_COMMIT>            # the release commit reported with this runbook
test "$(git rev-parse HEAD)" = "<RELEASE_COMMIT>" # must succeed

cd backend
docker compose stop worker beat                   # no task runs while the schema changes

# 5a. migration 109 (additive; one transaction; the file contains BEGIN and COMMIT)
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < db/migrations/109_requirements_analysis_recovery.sql

# 5b. verification of the schema (read-only; fails if the switch is not false, if the triggers are missing, or if cv_app can change the history)
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_verify.sql

# 5c. code: API, worker and beat from the release commit
docker compose up -d --build api worker beat

# 5d. frontend (Node 22; the API origin is set explicitly)
cd /opt/cv-analyzer && npm ci
VITE_API_BASE=http://72.62.31.221:8000 npm run build
rsync -a --delete dist/ /var/www/cv-analyzer/dist/
```

Notes:
- Migration 109 must not be run through `psql -f` against the container file path; it is piped from the host (`<`) so the file is read on the server.
- `npm run lint` (`tsc --noEmit`) reports 16 errors that exist before this release (pages/ApplicationsList.tsx, pages/CandidatesWorkspace.tsx, pages/JobDetails.tsx). The production build (`vite build`) does not type-check and succeeds. Do not treat the lint failure as a deployment blocker, and do not fix it inside this deployment.

## 6. Verification after deployment (read-only unless stated)

```bash
curl -fsS http://72.62.31.221:8000/health                    # expect {"status":"ok"}
curl -fsSI http://72.62.31.221/ | head -1                    # expect HTTP/1.1 200
docker compose ps                                            # api, worker, beat up; postgres and redis healthy
docker compose logs worker 2>&1 | grep -c "requirements_v2_extraction_worker.extract_candidate_task"   # expect 1 or more (registered)
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc \
  "SELECT key||'='||value FROM cv_analyzer.system_config WHERE key IN ('requirements_v2.enabled','requirements_v2.recovery_enabled') ORDER BY key"
#   expect: requirements_v2.enabled=false and requirements_v2.recovery_enabled=false  (unless requirements_v2.enabled was already true before; then it is unchanged)
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.requirements_analysis_log"   # expect 0
docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -tAc "SELECT count(*) FROM cv_analyzer.jobs"  # same as section 3
```

Then run `deploy/recovery_109_verify.sql` again. In the browser, open one existing requirements-v2 job as an administrator: the page must load, the readiness must match its value before the deployment, and no re-analysis control may appear. Compare the job's `requirements_revision` before and after: it must not change by the deployment.

Recovery records are read only when the switch is on. With it off, the page shows no re-analysis control and no history.

Compare the three counts from section 3 with the current counts: `jobs` and `audit_logs` may grow through normal use, but must not shrink.

## 7. Enabling re-analysis (NOT part of this deployment)

Do not enable it in this deployment. Enabling requires all of these, recorded:
1. A decision on the database role model (section 8, item 1). Enabling with a superuser application role leaves the append-only guarantee trigger-level only.
2. Approval by the product owner of the recovery feature in production.
3. The approved test evidence for the release commit (section 9) and a fresh run of the browser checks against the production configuration.

## 8. Rollback

Rollback has three independent parts. Read the append-only point before choosing one.

**A. Code (the usual rollback).** Migration 109 is additive. The previous code never reads the new table or the new switch, so the schema can stay.

```bash
cd /opt/cv-analyzer/backend
docker compose stop api worker beat
cd /opt/cv-analyzer && git checkout --detach "$(cat /root/backups/rollback_to_commit.txt)"
cd backend && docker compose up -d --build api worker beat
# frontend: restore the saved build
rsync -a --delete /root/backups/frontend_dist_<TS>/ /var/www/cv-analyzer/dist/
```

Then repeat the health checks in section 6 (without the 109 steps).

**B. Schema (dropping migration 109).** The history is append-only: once any event exists, no rollback removes it, and the scripted rollback refuses:

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U cv_app -d cv_analyzer_prod < deploy/recovery_109_rollback_empty.sql
```

- While the history is empty, the script drops the two triggers, the table and the switch row, and restores the 108 state.
- Once any event exists, the script refuses. The correct rollback is the switch, which is already `false`. The table stays, and the code rollback in A is enough.
- Removing a non-empty history is an archival decision, not a rollback. It requires an approved export first:
  `\copy (SELECT * FROM cv_analyzer.requirements_analysis_log ORDER BY log_id) TO '/root/backups/recovery_history.csv' CSV HEADER`
  and then an owner-level change, approved separately.

**C. Data (restoring the backup).** Only for catastrophic loss, with approval. A restore rewinds all data to the backup time. It removes every change made after the backup, including history rows, audit rows and job edits. History cannot be rolled back row by row, so this is the only route that removes events, and it is destructive.

## 9. Test evidence for the release commit (run before deployment)

Recorded in the deployment report with this runbook: full backend suite on Python 3.11 with the pinned requirements; migration 109 tests on an isolated PostgreSQL 16 migrated through 108; requirements-v2 frontend tests; the production frontend build; the browser harness against a disposable database. The skip reasons are listed in the report.

## 10. Known open items (must be decided, not assumed)

1. **Application role is a superuser.** `docker-compose.yml` bootstraps `cv_app` as the PostgreSQL superuser (`POSTGRES_USER: cv_app`). A superuser bypasses privileges and triggers, so the append-only guarantee in migration 109 does not hold against that role. Changing it is a production change that affects row-level security (many tables enable RLS; a superuser bypasses it), so it needs its own tested migration and a decision. Until then recovery stays off.
2. **Migration 070 does not apply on a clean database** (`CREATE INDEX ... USING GIN (policies->'sla_thresholds')` needs parentheses around the expression). Existing databases already past it are unaffected. Do not edit an applied migration; a rebuild from scratch must use a corrected copy. Recorded for a decision.
3. **Image build not verified** in the release environment (no Docker daemon was available to the verifier). The Python packages were verified on 3.11 with `pip check`; the image adds apt packages (libreoffice, libgl1, libgomp1) and a pre-downloaded embedding model that were not built here.
