# Browser E2E suite

One command, from the repository root:

```
./e2e/run.sh            # all journeys and the migration dry run
./e2e/run.sh -k retry   # any pytest selection
./e2e/mutation_cleanup_check.sh   # proves that teardown leaves no process behind
```

Prerequisites: `npm ci` (so `node_modules/.bin/vite` exists), PostgreSQL 16 binaries (`PG16_BIN`, default `/usr/lib/postgresql/16/bin`),
and Chromium at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (override with `E2E_CHROMIUM`). Playwright never downloads a browser
(`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`).

## What runs

- A disposable PostgreSQL 16 cluster in a temporary directory, built from `db/schema.sql` and the migration runner
  (`backend/tests/realdb_helper.py`). Nothing connects to a shared or production database.
- The backend (uvicorn, real application) on a free port, started with `e2e/support/backend_app.py`.
- The frontend (Vite dev server, real UI) on a free port with `--strictPort`. The UI is compiled with its configured API origin
  (`config.ts`); the browser's calls to that origin are routed to the live backend port. No frontend source or config is changed.
- Users: one tenant and an admin, recruiter and viewer per run. Passwords are generated at runtime, kept in memory, stored only as the
  application's hash, and never written to disk or committed.
- Test doubles, only in test code (`e2e/support/fakes.py`): the model call (scripted from a scenario marker in the job description), the
  sentence-embedding model (a deterministic encoder installed as `services.local_processor._MODEL`, so startup never loads or downloads
  weights), the AI scoring call and outbound e-mail.

## Journeys (`e2e/test_journeys.py`)

| Test | Journey | Checks |
|---|---|---|
| `test_english_creation_extraction_review_and_edit` | English creation, extraction, review and edit | 1–4 |
| `test_english_reorder_within_category` | Reorder within a category | 5 |
| `test_scoring_disabled_while_blocked_and_enabled_when_ready` | Scoring disabled while blocked, enabled when ready, score records revision | 10, 6, 7 |
| `test_arabic_creation_and_editing` | Arabic creation and editing | 8, 9 |
| `test_preferred_only_confirmation_enables_scoring` | Preferred-only confirmation | 10, 11 |
| `test_classification_acknowledgment_unblocks_readiness` | Classification acknowledgment | 12 |
| `test_structural_finding_resolution` | Structural-finding resolution | 13, 14 |
| `test_structure_confirmation_unblocks_readiness` | Structure confirmation | 15, 16 |
| `test_revision_conflict_reload_and_reapply` | Revision conflict: reload and reapply | 17–19 |
| `test_validation_error_preserves_local_edits` | Validation error keeps local edits | 20 |
| `test_viewer_is_refused_and_sees_no_editor` | Unauthorized viewer refusal | 21, 22 |
| `test_extraction_failure_and_retry` | Extraction failure and retry | 23, 24, R1–R3 |

The journey numbers are the 24 checks of the PR #33 browser acceptance. R1–R3 are retry checks added by this suite. Each journey
creates its own job(s), so journeys run alone and in any order. Job creation goes through the real `POST /jobs` route, not the UI form.
`test_migration_dry_run.py` runs the migration runner in dry-run mode on an empty database and asserts that nothing is written.

Each run writes `e2e/artifacts/checks.txt` (the names of the checks that passed).

## Failure output

A failed test leaves `e2e/artifacts/<test>-<n>.zip` (Playwright trace, open with `npx playwright show-trace`), a full-page screenshot
per page, and, once any test has failed, the backend and frontend logs in `e2e/artifacts/`. Artifacts are git-ignored.

## Cleanup

Teardown runs in `finally` blocks. Backend and frontend each run in their own process group and are stopped with SIGTERM, then SIGKILL
after 15 s. The cluster is stopped by the database helper. Teardown then verifies that no process is left, and fails the run if one is.
Temporary directories (cluster, files, logs) are removed. `E2E_KEEP_LOGS=1` keeps the run's temporary logs for debugging.

`mutation_cleanup_check.sh` proves this: an injected failure after start must leave no survivors, and a deliberately disabled teardown
must be detected.

## Limits

- Scoring is a fake: the scripted scoring call returns fixed scores. The check is that scoring is gated and records the revision used.
- The embedding model is a deterministic stand-in, so extraction matching is not measured here.
- The UI API origin is compiled into the frontend; requests are routed, not re-targeted at build time.
