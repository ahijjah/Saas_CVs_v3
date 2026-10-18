# CI workflows

One workflow, `ci.yml`, runs on pull requests, on pushes to `main`, and on manual dispatch. It needs no secrets. Superseded runs for
the same branch are cancelled. The workflow token has `contents: read`.

Everything runs against throw-away resources:

- PostgreSQL 16 clusters that each job creates and destroys (the tests build their own; the migration job starts one on port 55432).
- External AI, e-mail and the embedding encoder are scripted test doubles. They exist only in `backend/tests` and `e2e/support`.
  No job sets an environment variable that enables them, and `backend/tests/test_embedding_readiness_offline.py` fails if a
  production module references one.
- The migration job sets `MIGRATION_OWNER_ROLE=postgres` and a `MIGRATION_DATABASE_URL` for its disposable cluster only. That cluster
  is not production or staging.

## Jobs

| Job | Purpose | Command (run from repository root unless noted) | Budget |
|---|---|---|---|
| `backend-tests` | Full backend suite, with a guard that fails when zero tests are collected | `cd backend && python -m pytest tests -q` | 45 min |
| `backend-security` | Security tests alone, and after the test that leaks the event loop (issue #20 order) | `cd backend && python -m pytest tests/test_security_detection.py -q` then `python -m pytest tests/test_criteria_status_postgres.py tests/test_security_detection.py -q` | 30 min |
| `migration-lint-dry-run` | Migration lint tests, then apply the chain to a disposable database and dry-run it; fails unless the dry run reports 0 pending | `cd backend && python -m pytest tests/test_migration_lint_new_files.py tests/test_db0_migration_runner.py tests/test_db0_audit_regressions.py tests/test_db0_operator_audit.py -q`, then `python -m scripts.migrate --init-fresh` and `--dry-run` | 30 min |
| `frontend` | TypeScript (zero errors), Vitest (fails unless tests pass and the count is non-zero), production build | `npm run typecheck`, `npm test`, `npm run build` | 20 min |
| `e2e` | Deterministic Playwright suite against a disposable stack; uploads `e2e/artifacts/` (screenshots, traces, logs) when it fails | `./e2e/run.sh` | 45 min |
| `mutation-checks` | Applies each release mutant to a copy of the repository and requires the checking test to fail | `python3 scripts/release_mutation_checks.py` | 40 min (expected 10–20) |

## Pins

- Actions are pinned to full commit SHAs with the release in a comment: `actions/checkout` v4.2.2, `actions/setup-python` v5.6.0,
  `actions/setup-node` v4.4.0, `actions/upload-artifact` v4.6.2. The SHAs were read from GitHub with `git ls-remote`.
- Python 3.11 (matches `backend/Dockerfile`), Node 22, pytest 9.1.1, pytest-asyncio 1.4.0, Playwright 1.63.0.
- Backend dependencies come from `backend/requirements.txt`. The job installs PyTorch from its CPU index
  (`--extra-index-url https://download.pytorch.org/whl/cpu`) so the CUDA wheels are not pulled in. The pins still apply.
- PostgreSQL 16 comes from the Ubuntu 24.04 repository (`postgresql-16`); `PG16_BIN` points at its binaries.

## Caches

Only dependency caches: pip keyed on `backend/requirements.txt`, npm keyed on `package-lock.json`. Databases and build outputs are
never cached.

## Reproducing locally

1. Install PostgreSQL 16 binaries. Set `PG16_BIN` to their directory (default `/usr/lib/postgresql/16/bin`).
2. Backend: `python -m pip install -r backend/requirements.txt pytest==9.1.1 pytest-asyncio==1.4.0`, then run the commands above.
   Run as a non-root user, because PostgreSQL refuses to start as root.
3. Frontend: `npm ci`, then the three npm commands.
4. E2E: also `python -m pip install playwright==1.63.0` and `python -m playwright install --with-deps chromium`
   (set `PLAYWRIGHT_BROWSERS_PATH` first if you want the browser somewhere specific), then `./e2e/run.sh`.
5. Mutation checks: as in step 4, then `python3 scripts/release_mutation_checks.py`.

## Known limits

- The migration job applies `db/schema.sql` with the same single forward-reference patch the test helper uses
  (`realdb_helper.schema_sql`). Unpatched, `schema.sql` fails on an empty database at its first reference to `users`.
- The migration commands need a UTF-8 locale (`LC_ALL=C.UTF-8`), set for that step.
- The first run of each job has no cache, so budgets assume a cold install; PyTorch is the largest download.
