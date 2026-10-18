# Release runbook

This runbook covers one release of the CV Analyzer backend, workers and frontend. It is written for the operator who deploys the release. Commands are run from the repository root unless a step says otherwise. Credentials are never written in this file: set them in the environment or in the secret store.

## 1. Required environment variables

Set these for the API, the Celery worker and the Celery beat process. The names are the `Settings` field names in `backend/config.py` in upper case.

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | API, workers | Application login (`postgresql+asyncpg://…`). Least-privilege runtime role. |
| `MIGRATION_DATABASE_URL` | migration step only | Migration operator login. Never set this in the application environment. |
| `MIGRATION_OWNER_ROLE` | migration step only | Optional; defaults to `cv_migrator`. |
| `JWT_SECRET` | API | Replace the placeholder. Required. |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | API, workers | Redis instance. |
| `OPENAI_API_KEY` | workers | Required when a stage model uses the OpenAI provider. |
| `SMTP_*`, `EMAIL_FROM_ADDRESS`, `IMAP_*` | API, workers | Email and CV intake. |
| `FILES_BASE_PATH` | API, workers | Shared storage for uploaded CVs (default `/files`). |
| `CORS_ORIGINS` | API | JSON list of frontend origins. |
| `EMBEDDING_MODEL_PATH` | API, workers | Local directory holding the sentence-transformer model. Required (section 5). |
| `EMBEDDING_MODEL_LOCAL_ONLY` | API, workers | Keep `true` in production. `false` lets the library download the model: development only. |
| `HF_HUB_OFFLINE` | API, workers | Set to `1` in production so no library call reaches the network. |

Frontend: `WEBHOOK_CONFIG` in `config.ts` is compiled into the build. Set the API base URL before `npm run build`.

## 2. Database migrations

Run migrations before starting the new API or workers, and after the old processes have stopped or are idle on the old schema.

Dry run (prints the pending files and writes nothing):

```bash
cd backend
MIGRATION_DATABASE_URL='postgresql://…operator login…' python -m scripts.migrate --dry-run
```

Apply:

```bash
cd backend
MIGRATION_DATABASE_URL='postgresql://…operator login…' python -m scripts.migrate
```

Exit status is `0` on success. On failure the runner prints `STOPPED: <reason>` to stderr and writes nothing for the failing file, because each file runs in its own transaction. Do not edit applied migration files: the ledger binds each file to its checksum.

## 3. Startup order

1. Migration dry run, then migration apply (section 2). Both must exit `0`.
2. Redis and PostgreSQL reachable.
3. Embedding model present (section 5). The API and each worker process load it at startup and refuse to start without it.
4. API: `uvicorn main:app --host 0.0.0.0 --port 8000`. The lifespan loads the embedding model before the server accepts requests.
5. Celery worker: `celery -A workers.celery_app worker --loglevel=info --concurrency=4`. Each prefork child loads the model at process start.
6. Celery beat: `celery -A workers.celery_app beat --loglevel=info`.
7. Frontend: build with `npm run build` and serve `dist/` behind the web server.
8. Stage models (section 6), then the smoke test (section 9).

## 4. Stage-model configuration (required before creating jobs)

Job creation runs the V2 requirements extraction, which resolves the `cv_analyzer` stage. Without an enabled model for that stage the job stays in `pending` with the message that no model is configured. Configure these stages in the AI model registry before opening job creation to users:

| Stage | Needed for |
|---|---|
| `cv_analyzer` | Requirements extraction for new jobs (required before creating jobs). |
| `cv_scoring` | CV scoring. |
| `cv_comparison` | Candidate comparison. |
| `knockout_analysis` | Knockout questions and screening validation. |

Each stage needs an enabled model, a provider secret stored through the platform secrets screen, and the stage listed in the model's supported stages.

## 5. Sentence-transformer model preparation

The API and workers load `paraphrase-multilingual-MiniLM-L12-v2` from a local directory and never download it while serving a request. Prepare it once per host or once per image.

Prepare the directory (requires network access once, on a build host):

```bash
python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2').save('/opt/models/paraphrase-multilingual-MiniLM-L12-v2')"
```

Set `EMBEDDING_MODEL_PATH=/opt/models/paraphrase-multilingual-MiniLM-L12-v2`, `EMBEDDING_MODEL_LOCAL_ONLY=true` and `HF_HUB_OFFLINE=1`. Mount the directory read-only into the API and worker containers, or copy it onto the host.

The provided `backend/Dockerfile` does this at image build time and sets these variables in the image. A missing or unprepared directory produces these startup errors, which name the path and the fix:

- `EMBEDDING_MODEL_PATH is not set` (local-only mode without a path)
- `EMBEDDING_MODEL_PATH '…' is not a directory` (not prepared on this host)
- `sentence-transformers is not installed` (dependency missing)
- `Embedding model could not be loaded from '…'` (corrupt or incomplete directory)

PyMuPDF, sentence-transformers, torch, transformers and the other embedding-stack packages are pinned with `==` in `backend/requirements.txt`. Install with `pip install -r requirements.txt` in the image build. Do not upgrade them independently.

## 6. Health and readiness

| Check | Expected | Meaning |
|---|---|---|
| `GET /health` | `200 {"status":"ok"}` | Process is up. Does not check dependencies. |
| `GET /health/ready` | `200 {"status":"ready",…}` | Embedding model is loaded and usable. |
| `GET /health/ready` | `503 {"status":"unavailable",…}` | Embedding model is unavailable. The reason is in the API log, not in the response. |

Use `/health/ready` as the load-balancer readiness probe. Use `/health` for liveness. Check the worker log for `Sentence-transformer model loaded.` after each worker start.

## 7. Backup and rollback

Backup before every migration apply. Use the migration operator login from the secret store; do not paste it into shell history:

```bash
pg_dump --format=custom --file "/var/backups/cv-analyzer-$(date +%Y%m%dT%H%M%S).dump" "$MIGRATION_DATABASE_URL"
```

Store the dump outside the application host and confirm it restores before relying on it:

```bash
pg_restore --list "/var/backups/cv-analyzer-<timestamp>.dump" > /dev/null && echo "dump readable"
```

Rollback:

1. Stop the new API, workers and beat.
2. Migrations are forward-only. Do not reverse them by hand and do not use `REASSIGN OWNED`. If the database must return to the pre-release state, restore the backup into a new database and point `DATABASE_URL` at it.
3. Redeploy the previous backend and frontend builds against the restored database.
4. Confirm `/health/ready` and run the smoke test.

Losing writes made after the backup is possible. Record the backup time in the release notes before applying migrations.

## 8. Operator checklist before the release

- Migration dry run lists only the expected files, then the apply exits `0`.
- Backup taken and listed.
- Embedding model directory present on every API and worker host; `/health/ready` returns `200`.
- Stage models configured (section 4).
- Frontend built from the release commit with the correct API base URL.

## 9. Smoke test

From the repository root, with Chromium available at `/opt/pw-browsers` (or Playwright's default browser path):

```bash
./e2e/run.sh
```

The command starts its own disposable PostgreSQL cluster, migrations, API and frontend on free ports, and seeded test users. It uses scripted AI and email adapters and never contacts production or staging. It removes its processes and temporary data on exit. Failure screenshots and traces are written to `e2e/artifacts/`. This command must not be run against a live environment.

## 10. Accepted limitations

These are known and accepted for this release:

- Reordering of requirement items is limited to the category. Items cannot move across categories.
- Structural expectations are not derived for independent items. No heuristic is applied to them.
- Production database role, row-level security and secret hardening are a separate controlled deployment activity. This release does not change them.
- `/grants.py` is not changed by this release.
- Commits and pull requests carry no model identifiers or attribution lines.
