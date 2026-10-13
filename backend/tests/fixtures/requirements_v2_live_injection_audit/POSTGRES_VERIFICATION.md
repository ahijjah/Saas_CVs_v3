# Verification of 2952855 on a real PostgreSQL 16 (isolated, local)

Scope: the model-input binding, the in-flight check, the entry guard and the persistence of the requirements-v2 extraction, verified against a real
PostgreSQL 16 server with fake model responses. No production database, no model call, no deployment, no activation. Scoring stays disabled
(`REQUIREMENTS_V2_SCORING_SUPPORTED = False`).

## Environment

- PostgreSQL 16 server binaries from the system package (`/usr/lib/postgresql/16/bin`), started by `tests/realdb_helper.py` as a disposable cluster in
  a temporary directory, with the project's `db/schema.sql`, every migration and `seed.sql`. Every test database is created inside that cluster.
- Pinned drivers, installed from the project's own pins: `psycopg2-binary==2.9.10`, `asyncpg==0.30.0`, `sqlalchemy==2.0.36`.
- `pgserver` (the optional package the API and migration suites previously required) is not available from the package index here. The API and
  migration suites now use `tests/realdb_pgserver_shim.py`, which provides the same two calls (`get_server`, `get_uri`, `cleanup`) on the same
  real cluster. Nothing is mocked. If no PostgreSQL 16 can start, the shim raises ImportError and the suites skip, as before.
- Because the cluster runs locally, no VPS setup command was needed.

## Results

| Suite | Result |
|---|---|
| `tests/isolated_requirements_v2_extraction_postgres.py` (worker, persistence, transitions, audit, rollback, superseded attempts, legacy jobs, guard) | 43 passed (before: 14 failed, 29 passed; see defects) |
| `tests/test_requirements_v2_binding_postgres.py` (new: edits during the call, edits after completion, unchanged inputs, context directives, older records, lock ordering in both orders, locked save, audit) | 13 passed |
| `tests/test_requirements_v2_postgres.py` (API on real SQL, previously skipped) | passed with the adapter |
| `tests/test_requirements_v2_migration.py` (migrations 106/107 on real SQL, previously skipped) | passed with the adapter |
| `tests/test_requirements_pipeline_postgres.py` (previously skipped) | 45 passed |
| Full related set, including the above and the offline suites | 1263 passed, 0 skipped |

## Defects found by the real database and fixed (within the authorised scope)

1. **The editor view compared against no job context.** `requirements_api._load` computed the six context fields but did not pass them into the
   loaded record, so every view of a job with context reported `inputs_changed` falsely. Fixed: the field is passed through. Only the real database
   showed this; the offline tests passed the context in directly.
2. **Completion audit did not name the binding it wrote.** It now records `model_inputs_sha256` and `context_findings` alongside the existing fields.
3. **Test-harness mismatches** (not product defects, corrected so the harness matches production): the worker harness sent no job context while the
   job row held a title, although creation and retry both send the stored context; the synthetic `jobs` table lacked the six context columns, now
   added with the real types; the pipeline storage test seeded a version-1 record against a job with no description, now seeds the version-2 record
   bound to the job's description and asserts the version-2 key set (stricter than before, no assertion removed).

## What the database tests establish

- An edit made while the model answers is detected by the locked read. The attempt fails with `inputs_changed_during_call`, nothing is stored, the
  rejection is audited, and a new attempt, after a retry request, completes bound to the current inputs.
- An edit committed after completion makes the analysis stale: the view says `inputs_changed`, the entry guard refuses, and the stored draft, the
  original snapshot and the revision are unchanged, including after repeated reads.
- Unchanged inputs complete, show no input blocker and pass the entry guard. A directive in the job title is stored as an open context issue and
  blocks progression without becoming a failure.
- Lock ordering, both orders, with the edit held open by a second connection:
  - an edit waits behind a result that holds the share lock (checked with a 500 ms lock timeout, which raises `LockNotAvailable`), and the edit goes
    through only after the result commits;
  - a result waits for an uncommitted edit, reads the committed value and rejects itself;
  - a locked save on the criteria row waits for an in-flight result, and nothing is reported as a deadlock.
- An older (version-1) record on real rows is reported `inputs_unverified`, its context is never claimed as verified, the guard refuses it, and
  reading it does not rebind it.
- The completion and rejection audit rows are committed and visible from another connection.

## Not verified here

- Rollback of a write after the check (an audit failure rolls the whole write back): covered by an existing isolated test that passes on the real
  database, not re-designed here.
- Superseded attempts mid-call: covered by an existing isolated test that passes on the real database.
- The production database, its migrations, and the deployed VPS code. Nothing here touches them.
- Timing: the lock tests use fixed waits (1 s before the lock check; 500 ms lock timeouts). They are deterministic in their assertions but depend on
  the thread having reached the waiting point; on a much slower machine a wait could be too short, which would show up as a failure, not a pass.
- Concurrency beyond two connections, and PostgreSQL versions other than 16.

## Frontend

- The readiness and reason labels in `components/requirementsV2/i18n.ts` have no entries for `inputs_changed`, `inputs_unverified`, or the reason codes
  `description_changed`, `context_changed`, `context_unverified`, `context_instruction`, `binding_invalid`, `inputs_unavailable`. `Panels.tsx` falls
  back to the raw state key, and `PipelinePanels.tsx` shows the backend's English message in both locales.
- `services/requirementsV2Api.ts` does not include the new basis `pipeline_inputs` in its union, and the reason type does not include `field`.
- Context-finding snippets are not in the injection issues list (`unresolved_issues`), so the injection panel cannot show a context-only block, and
  no resolution option exists for it. A stale analysis has no recruiter action at all, which is the recovery gap, deliberately left for a decision.
- The existing frontend readiness test file (`tests/requirementsV2/pipeline.test.tsx`) fails in this environment with a DOM-environment error
  (22 failed, 3 passed). It predates this work (last changed in `5cc8c22`) and no frontend file changed here. Not verified.
