# Converting the legacy production database

This runbook takes the current production database to the state the current release expects. It is the controlled compatibility path for one
exact starting point; for any other database use `docs/DATABASE_ROLES.md`.

**Starting point (production today).** Application source commit `63f3bd7a3932d3c335d679cecf615ad592bd8423`. Database `cv_analyzer_prod`,
schema `cv_analyzer`. Migrations 002 to 092, then the legacy 093 to 108 (`backend/db/legacy/lineage_63f3bd7/migrations`, byte-identical to
the files at that commit). **No `cv_analyzer.schema_migrations` ledger.** `cv_app` is `LOGIN SUPERUSER BYPASSRLS` and owns every object.
`cv_worker`, `cv_admin`, `cv_secrets` and the migration roles do not exist.

**Target.** The release whose migration manifest ends at `101_job_code_sequence.sql`, the runtime roles `cv_app`, `cv_worker`, `cv_admin`
and `cv_secrets` as `NOSUPERUSER NOBYPASSRLS` with the grants of `backend/db/runtime_grants.py`, owner `cv_migrator`, operator
`cv_migration_operator`, and a valid ledger.

**The real database is not the reconstructed one.** The first rehearsal on the restored backup found 13 expected objects missing, 50 different and 23
unknown. Section 2a classifies every one of those 86 identities individually (`backend/db/legacy/production_lineage.json`, proved against the schema of the
restored backup, committed as `backend/tests/fixtures/restored_production/schema.sql`). The conversion accepts exactly that recorded state, reconciles the
part main needs, and stops on anything else.

Nothing in this document is run against production or staging by the change that added it. Run it first on a freshly restored copy of the backup (section 5).

## 1. Why the standard procedure does not apply

The standard procedure (`docs/DATABASE_ROLES.md`) assumes a ledger and the main chain. Four facts about the legacy lineage break it:

1. **Different numbering from 093.** Main's 093 to 101 are not the legacy 093 to 108. A ledger that simply listed main's files as applied would
   be a claim about the database that nothing had checked, and the legacy files have no entry in the manifest at all.
2. **Main's migration 094 contradicts legacy 099.** 094 replaces `applications_submission_source_check` with a set that has no
   `bulk_excel_upload`. Legacy 099 added that value. Run as written, 094 fails as soon as one bulk application exists, and would make the
   database unable to hold them again.
3. **Main's migration 100 cannot rewrite one legacy policy.** It rewrites `app.current_role = 'super_admin'` and raises on any other use of
   the forgeable `app.current_role`. Legacy 102 created `tenant_features_admin_bypass` as `app.current_role = ANY (ARRAY['platform_admin',
   'super_admin'])`, so 100 stops with "policy still reads app.current_role" on this database. (A test proves it.)
4. **The roles.** `cv_worker`, `cv_admin` and `cv_secrets` do not exist, so `db/ops/0004` fails; `cv_app` is a superuser that owns the
   schema; and the legacy tables (`bulk_upload_*`, `tenant_features`, `tenant_feature_audit`) have no entry in the privilege matrix, so the
   preflight rejects them.

## 2. What the conversion does

`python -m scripts.legacy_upgrade` (library: `backend/db/legacy/`). Two commands:

* `check` is read-only. It compares the live catalogue with `backend/db/legacy/reference.json`: for every migration of the base chain and of the
  legacy chain, the objects it produces (relations, columns with type, nullability and default, constraints, indexes, triggers, functions,
  policies, row-level-security flags, sequences, and the presence of seeded configuration rows). The reference is generated from clean
  builds by `scripts/generate_legacy_reference.py`, and a test fails when the committed file is stale.
* `prepare` does the conversion in **one transaction**, as the bootstrap superuser. Any failure rolls everything back; the database is then
  exactly as it was. `--dry-run` runs all of it and rolls back at the end.

### Fail-closed rules

* **Exact lineage.** Every identity in the reference must be present with the same definition. A missing or different object, **or any object
  the lineage does not know**, stops the conversion and is listed. Seeded configuration rows that an operator added are ignored. A seeded row that
  an operator *deleted* (a plan, a configuration key, a prompt version, a model) stops it too, until you name it with
  `--acknowledge-missing-seed 'seed|table|key'` (repeatable); the acceptance is recorded in the evidence table.
* **Never blind.** A migration is recorded in the ledger only after its effect is proved: a structural file when every identity it produces
  is present; a data-correction file (059, 077, 081, 084, 085, 086, 089, 091, 092) when its data check finds zero rows that still show the
  problem. A data check that finds rows stops the conversion unless the operator names that file with `--acknowledge-data FILE`, which is
  recorded. One file, `005_job_code_alias.sql`, cannot be proved (it back-fills a column the application keeps writing); it is recorded as
  `attested` in the evidence table, and **you sign it off** (section 4, step 4).
* **Four files that do not apply on a clean chain** (010, 024, 070, 087). A working production database must have the effects of 010, 024 and
  087, because the application depends on them (plans, configuration keys, `application_knockout_answers.tenant_id`); 070 has a syntax error
  and cannot have applied anywhere. So: if the effects of 010, 024 or 087 are missing, the conversion **stops** and names the objects (their
  repair files validate against the state at their own position in the chain and are not run out of order). If only 070's effect is missing
  (the SLA index and default thresholds), `096_repair_070_sla_policy_index.sql` is run, because it is idempotent and order-independent.
* **No active sessions.** `prepare` refuses to run while any runtime role has a session on the database.
* **The login.** It must be a superuser and must not be a runtime role.
* **Exactly the declared changes.** After each reconciliation step, and at the end, the catalogue is compared again: it may differ from
  the reference only in identities the step declares. A surprise rolls everything back.

### 2a. The production lineage: what the restored backup showed, identity by identity

`backend/db/legacy/production_lineage.json` has one entry per identity that differs from the reference: its disposition, its root cause, and the exact
catalogue text found on production. Nothing is matched by pattern at run time: a live definition that is neither the reference's nor the recorded one is
unexplained and stops the conversion. `backend/tests/test_legacy_production_variations.py` proves the file equals the difference between the restored
schema and the reference (no identity more, none fewer), and that each disposition means what it says.

| Disposition | Count | Meaning | What the conversion does |
|---|---|---|---|
| `equivalent` | 41 | Same behaviour, printed differently | Accepts the recorded text. Proved in tests: same allowed values in the same order, same expression apart from casts. |
| `extended` | 1 | Allows a superset of the reference's values | Accepts it (`job_criteria.criteria_extraction_status` also allows `insufficient` and `blocked`). |
| `wider` | 1 | A wider column, otherwise equal | Accepts it (`email_ingest_log.job_code_source` is `varchar(50)`, the reference `varchar(20)`). |
| `production_only` | 23 | An object the reference does not have | Preserves it, never drops it. Tests prove none can make a statement of this release fail (every added column is nullable or defaulted). |
| `substituted` | 1 | An expected index that exists under another name | Accepts its absence while the production index of the same definition exists as recorded. |
| `reconcile` | 18 | Missing or weaker than main needs | Creates or tightens it, after its prechecks are zero, in the same transaction. |
| `repair` | 1 | Created by repair migration 096 | 096 runs (as before). |

Root cause of each category:

| Cause | Identities | Why production differs |
|---|---|---|
| `check_rebuilt_by_type_change` | 39 | These CHECK constraints and two partial-index predicates print as `ANY (ARRAY[('a'::character varying)::text, ...])` instead of `ANY ((ARRAY['a'::character varying, ...])::text[])`. PostgreSQL stores that form when a column was retyped after the constraint existed. The allowed values are identical. |
| `criteria_status_superset` | 1 | Production's extraction code writes two more statuses than `schema.sql` allows. |
| `set_updated_at_in_public` | 2 | Migration 032 was applied with `search_path = public`, so `CREATE OR REPLACE FUNCTION set_updated_at()` created `public.set_updated_at()`; `trg_ail_updated_at` calls that. `cv_analyzer.set_updated_at()` kept `schema.sql`'s multi-line body (same statements). |
| `intake_031_partial` | 4 | Migration 031 applied partially: `processing_duration_ms`, `notification_queued_at` and the stuck-detection index are absent (this release's intake worker writes the first two), and `job_code_source` is wider. |
| `knockout_early_044` | 17 | The knockout tables come from an earlier draft of migration 044: no foreign keys, no unique pair, nullable timestamp and flag columns, `question_type varchar(50)` without a default, extra columns and indexes. 044 is `IF NOT EXISTS`, so it never added what was missing; 097's tenant foreign key was never applied. |
| `workflow_057_early` | 3 | `applications.workflow_status` was created with default `'new'`, a partial index excluding `'new'` and no CHECK. The committed 057 uses `'awaiting_review'`, a full index and a CHECK. |
| `early_schema_columns` | 19 | Columns and constraints from the first production schema on `applications` (15 columns, 2 constraints) and `jobs` (`alias_status` and its CHECK). Populated or defaulted; nothing in this release reads them; removing them would destroy data. |
| `migration_096_not_applied` | 1 | Migration 070 has a syntax error, so `idx_workflow_policies_sla` never existed. |

**Reconcile steps** (`backend/db/legacy/production.py`; each is one transaction step with prechecks, an exact set of identities it may change, and a check that
each ends in the reference's definition):

| Step | Creates or tightens | Prechecks (all must be 0; the tool changes none of these rows) |
|---|---|---|
| `email_ingest_log_operational_columns` | `processing_duration_ms`, `notification_queued_at`, `idx_email_ingest_log_stuck` | none (additive) |
| `knockout_answers_integrity` | NOT NULL on `created_at` and `is_disqualifying`; foreign keys to applications, questions and tenants; the unique pair | NULL timestamps or flags; answers whose application, question or tenant does not exist; duplicate pairs |
| `knockout_questions_integrity` | NOT NULL on `created_at`, `is_required`; default `'yes_no'` on `question_type` (width 50 is kept); foreign key to jobs; `idx_knockout_questions_job` | NULL timestamps or flags; questions whose job does not exist |
| `workflow_status_model` | default `'awaiting_review'`; the CHECK; the full `(job_id, workflow_status)` index replacing the partial one | rows with a status outside the valid set (after the data corrections) |

Production already has a unique index on `(application_id, question_id)`, so duplicate answers cannot exist there; the precheck is kept because it is what protects the
new constraint on any database. The duplicate index `idx_application_knockout_answers_app` is kept (it is the `substituted` entry's substitute); `idx_job_knockout_questions_job (job_id)` is kept beside the
new composite index. Dropping redundant indexes is an operator decision, not part of this procedure.

**External dependency.** `trg_ail_updated_at` calls `public.set_updated_at()`, which a schema-only dump does not contain. `check` and the preflight verify, every time, that it exists,
is a plain (not `SECURITY DEFINER`, no settings) `plpgsql` trigger function with a body equal to `cv_analyzer.set_updated_at()`, and is not owned by a runtime role. Any other
trigger that calls a function outside `cv_analyzer` stops the conversion.

**Data corrections are explicit.** `check` reports the counts first (`check --json` adds a breakdown by status columns only, never a name, address or answer).
Nothing is changed until the operator confirms the exact count per file:

| File | Count in the real report | What the declared migration does |
|---|---|---|
| `077_clean_stopped_workflow_pollution.sql` | 86 | Sets `workflow_status` to NULL on failed applications that carry a `stopped_reason`. |
| `081_fix_knockout_answer_value_encoding.sql` | 2 | Strips JSON quoting from knockout answers and **deletes** answers that are empty strings. |
| `092_repair_legacy_new_workflow_status.sql` | 524 | Sets `workflow_status` to `'awaiting_review'` on scored applications holding `'new'` or NULL. |
| `057_application_workflow.sql` (backfill only) | 0 expected | 057 ends with a `'new'` to `'awaiting_review'` backfill that production's early 057 never ran. Applies the same statement to the applications 077 and 092 do not own. Needed only if rows exist. |

`--apply-data-correction FILE=COUNT` runs that migration's body inside the conversion transaction, but only when the live count equals `COUNT`; afterwards the same check must show 0, no
application may have been deleted, and only 081 may delete knockout answers (and no more than it reported). Each correction is recorded in the evidence table with the reported
count, the count after, the totals before and after, and what it does. Migration 005 stays an explicit operator attestation.

### What it changes, in order

1. Creates `cv_worker`, `cv_admin`, `cv_secrets` as `NOLOGIN NOSUPERUSER NOBYPASSRLS` with no privilege (only those that are absent).
   `cv_migrator` and `cv_migration_operator` must already exist (`db/ops/0000`).
2. Moves ownership of every object in `cv_analyzer` to `cv_migrator` with explicit `ALTER ... OWNER TO` statements from
   `scripts/generate_ownership_transfer.py` (never `REASSIGN OWNED`). If a runtime role owns the database or the `public` schema, ownership of
   those goes to the bootstrap login.
3. Creates the ledger (`cv_analyzer.schema_migrations`) and the evidence table (`cv_analyzer.schema_migration_legacy_evidence`), both owned by the migrator
   and granted to no runtime role.
4. Switches to `cv_migrator`. Runs `096` if needed. Runs main's `093` (idempotent; legacy 106 and 107 added the same columns).
5. Applies the data corrections the operator confirmed (077, 081, 092, and the 057 backfill if rows exist), then the reconcile steps of section 2a, each after its prechecks. Reconciles the three places where legacy and main disagree (each is a documented, tested step in `db/legacy/lineage.py`):
   * `submission_source_union`: `applications_submission_source_check` allows both `candidate_reuse` and `bulk_excel_upload`, and the column default
     is dropped. This is the effect main's 094 requires, proved from the catalogue, without making the database refuse its bulk rows.
   * `tenant_features_admin_policy`: the legacy `app.current_role` policy becomes `current_user = 'cv_admin'`, the form main uses.
   * `tenant_feature_audit_rls`: row-level security is enabled on `tenant_feature_audit` with main's tenant policy shape (the legacy table had none).
6. Writes one ledger row per manifest file before 098 with the SHA-256 of **the file in this repository**: `applied`, or `superseded` for the
   originals 010, 070 and 087 with their replacements 095, 096 and 097 `applied`. It writes the evidence rows: the sixteen legacy files with
   their source checksums, how each base file was proved, the repair and reconciliation steps, and the conversion record.
7. Verifies that the runner would now apply exactly `098`, `099`, `100`, `101`.

Everything legacy 093 to 108 created stays: scoring columns and configuration, prompts, gender metadata, bulk upload tables, tenant features and
their audit, the scoring-method column and checks, `needs_verification`, and the Requirements V2 columns, configuration, model and prompt rows. The
legacy-only tables (`bulk_upload_batches`, `bulk_upload_rows`, `tenant_features`, `tenant_feature_audit`) are kept with their data, owned by the
migrator, with row-level security on, and **sealed**: no runtime role has any privilege on them, because this release's code does not use them
(`LEGACY_PRESERVED_TABLES` in `db/runtime_grants.py`; the preflight accepts them only when the evidence table shows the conversion ran).

### What it does not do

* It does not make the *legacy application code* work against the new schema, and it does not carry the legacy features' code. See the risks in
  section 7: the release being deployed does not read the legacy-only tables or columns.
* It does not run `098` to `101`, the operator scripts `0004` and `0005`, or the preflight. Those follow, in section 4.
* It does not read, print or store a password.

## 3. Tests

`backend/tests/test_legacy_production_upgrade_postgres.py` (disposable PostgreSQL 16 clusters, never a shared database) rebuilds the legacy
chain through 108 as `cv_app`, inserts rows across every table family (tenants, users, jobs with their codes, legacy and Requirements V2 criteria,
applications including a bulk application, scores with the scoring columns, bulk batches and rows, tenant features and audit, knockout data,
comments, workflow policies, tuned configuration), runs the whole procedure, and checks: every row survives; the ledger is accepted by the runner;
the preflight is all `PASS`; the grants equal the manifest and the legacy tables hold none; row-level security holds and a forged role variable grants
nothing; the real API starts on the runtime logins and serves the legacy data and Requirements V2 documents, and creates a job whose code is above
every code issued before; a worker path runs on the worker login; the converted catalogue equals a fresh install of this release plus exactly the
legacy additions; and the refusals (a clean-chain remnant, an unknown table, a missing column, a changed constraint, a failed data correction, open
sessions, a runtime role as the login, a second conversion) change nothing.

`backend/tests/test_legacy_production_variations.py` and `backend/tests/test_legacy_production_restored_postgres.py` do the same for the **real** schema: the
fixture is the restored backup's schema-only dump; rows are generated with the real counts (86, 2, 524); seeded configuration rows are copied from a legacy build. They
prove the report's counts and breakdowns, that corrections change only the rows reported (and delete only the declared answer), that the converted catalogue equals the
reference apart from the recorded production state, that the preflight is all `PASS`, that the database now refuses the retired status, orphans and duplicates, that the
new API and a worker path run on it, and that each of these stops the conversion without changing a catalogue entry or a row: an orphaned answer or question, a NULL
timestamp or flag, residual `'new'` rows, an unrecorded or altered object, a half-applied step, a changed superset constraint, a wrong or unconfirmed count, a correction that
leaves rows, deletes applications or removes answers it is not declared to remove, and an external trigger function that differs, is `SECURITY DEFINER` or is owned by a runtime role.
`scripts/release_mutation_checks.py` carries a mutant for each rule.

## 4. Procedure

Do these in order. Stop at the first non-zero exit or unexpected output. Every command that needs a credential reads it from the environment
or the secret store; none is written here.

**Variables used below** (set in the shell; do not echo them):

| Variable | Meaning |
|---|---|
| `BOOTSTRAP_URL` | `postgresql://<bootstrap superuser>@<host>/cv_analyzer_prod`. A superuser that is **not** `cv_app`. |
| `MIGRATION_OPERATOR_PASSWORD`, `CV_APP_PASSWORD`, `CV_WORKER_PASSWORD`, `CV_ADMIN_PASSWORD`, `CV_SECRETS_PASSWORD` | New passwords from the secret store. `cv_app`'s old password is replaced. |
| `DATABASE` | `cv_analyzer_prod` |

1. **Maintenance window; stop the old stack.** Stop the API, the workers and beat. `prepare` refuses to run while a runtime role has a session.
2. **Backup, and record the roles.** The roles are cluster-wide and are part of rollback (section 6):

   ```bash
   pg_dump --format=custom --file "/var/backups/cv-analyzer-prod-pre-conversion-$(date +%Y%m%dT%H%M%S).dump" "$BOOTSTRAP_URL"
   pg_restore --list /var/backups/cv-analyzer-prod-pre-conversion-<timestamp>.dump > /dev/null && echo "dump readable"
   psql -X -At "$BOOTSTRAP_URL" -c "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit FROM pg_roles WHERE rolname LIKE 'cv\\_%' ORDER BY 1" \
     > /var/backups/cv-analyzer-prod-roles-before.txt
   ```

   Record the backup time in the release notes. Keep both files off the database host.
3. **Check (read-only).**

   ```bash
   cd backend
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade check | tee /var/backups/legacy-check-before.txt
   ```

   On the real database the first `check` exits `1` until the data corrections are confirmed (below); it must show **no** `FAIL expected object`, `FAIL unknown object`,
   `FAIL production state` or `FAIL precheck` line. `ACCEPT` and `RECONCILE` lines name each root cause of section 2a with counts. `FAIL data check` lines give the counts to confirm.
   `REPAIR` names a file that will be run (only `096`). `ATTEST` lines are the files accepted without a proof. Any other `FAIL` is blocking: do not work around it (section 8).
4. **Sign-off.** Decide, from the `check` output, each of these (all are operator decisions; the tool decides none of them):
   * `005_job_code_alias.sql` is the only `ATTEST`, and you accept it: it set `jobs.platform_email = job_code@email_domain` once, and the application has written that column since.
   * The data corrections: read the counts and breakdowns (expected 077: 86, 081: 2, 092: 524, 057: 0). A different count means the data changed since the rehearsal; read it again and decide.
     Note that 081 **deletes** the answers that are empty strings (the count includes them).
   * `public.set_updated_at()` is the function the trigger should call (it is verified equal to `cv_analyzer.set_updated_at()`); re-pointing the trigger is optional and not done here.
   * Whether to keep production-only columns and duplicate indexes (they are preserved; dropping them is a separate change).
   * Any `ACK` data lines were intended.
5. **Migration roles** (once; the passwords come from the secret store):

   ```bash
   psql -X -v ON_ERROR_STOP=1 -v migration_operator_password="$MIGRATION_OPERATOR_PASSWORD" "$BOOTSTRAP_URL" \
        -f backend/db/ops/0000_migration_roles.sql.template
   ```
6. **Conversion: dry run, then real.**

   ```bash
   cd backend
   CONFIRM="--apply-data-correction 077_clean_stopped_workflow_pollution.sql=86 \
            --apply-data-correction 081_fix_knockout_answer_value_encoding.sql=2 \
            --apply-data-correction 092_repair_legacy_new_workflow_status.sql=524"   # the counts YOU read in step 3
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade check $CONFIRM        # exit 0 now; CORRECT lines show what will be done
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade prepare --dry-run $CONFIRM     # rolls back
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade prepare $CONFIRM | tee /var/backups/legacy-prepare.txt
   ```

   Expect `done: corrected ...: 86 -> 0`, `2 -> 0 (rows deleted: 1)` or as the report said, `524 -> 0`, four `reconciled ...` steps, `ledger rows: 94` and
   `pending for scripts.migrate:` followed by exactly `098`, `099`, `100`, `101`. If a precheck stops it, nothing was changed; section 8.
7. **Migrations 098 to 101** (dry run first):

   ```bash
   cd backend
   MIGRATION_DATABASE_URL='postgresql://cv_migration_operator:…@host/cv_analyzer_prod' python -m scripts.migrate --dry-run
   MIGRATION_DATABASE_URL='postgresql://cv_migration_operator:…@host/cv_analyzer_prod' python -m scripts.migrate
   ```
8. **Runtime logins and grants** (this is where `cv_app` stops being a superuser):

   ```bash
   psql -X -v ON_ERROR_STOP=1 \
     -v cv_app_password="$CV_APP_PASSWORD" -v cv_worker_password="$CV_WORKER_PASSWORD" \
     -v cv_admin_password="$CV_ADMIN_PASSWORD" -v cv_secrets_password="$CV_SECRETS_PASSWORD" \
     "$BOOTSTRAP_URL" -f backend/db/ops/0004_runtime_roles.sql.template
   (cd backend && python -m db.runtime_grants | diff - db/ops/0005_runtime_privileges.sql.template)       # no output
   psql -X -v ON_ERROR_STOP=1 "$BOOTSTRAP_URL" -f backend/db/ops/0005_runtime_privileges.sql.template
   ```
9. **Preflight, after.** Every line must be `PASS`, exit `0`:

   ```bash
   cd backend
   PREFLIGHT_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.db_preflight --phase after
   ```
10. **Start the new stack** with the runtime URLs (`DATABASE_URL` = cv_app, `ADMIN_DATABASE_URL` = cv_admin, `WORKER_DATABASE_URL` = cv_worker,
    `SECRETS_DATABASE_URL` = cv_secrets), then `GET /health`, `GET /health/ready`, a tenant login, the jobs list and one job's requirements.
    The API refuses to start with a superuser or RLS-bypassing login.

## 5. Rehearsal on the restored backup

The backup is restored into the isolated container `cvprod-upgrade-rehearsal` (network `cvprod-upgrade-rehearsal-net`, database
`cv_analyzer_prod`). **Nothing here touches production or staging, and the rehearsal environment must not be given production service
addresses** (no production Redis, SMTP, IMAP or AI provider keys).

**Start each rehearsal from a freshly restored copy of the same backup.** A rehearsal that stopped partway through `0000`, `migrate` or `0004` has changed the cluster (roles) and
possibly the database; `prepare` itself never leaves a partial change, but the steps after it do. Drop the rehearsal database and restore the backup again before repeating
(`pg_restore` into a new `cv_analyzer_prod`), and drop the rehearsal's `cv_*` roles, so the starting point is identical. Do not rehearse twice on the same copy.

Run on the VPS, from a checkout of the pull request head, as the operator who can read the credentials file. Set `REHEARSAL_ENV` to the
credentials file and `SRC` to the checkout. The commands never print a value: the file is only sourced, and the variables are passed to
containers by name.

```bash
set -euo pipefail
REHEARSAL_ENV=/opt/cv-analyzer-production-backups/pre-bae8bce-20261021T191202Z/rehearsal.env
SRC=/opt/cv-analyzer-rehearsal-src            # git checkout of the pull request head (not the working tree of production or staging)
NET=cvprod-upgrade-rehearsal-net
DB=cvprod-upgrade-rehearsal
EVIDENCE=/opt/cv-analyzer-production-backups/pre-bae8bce-20261021T191202Z/rehearsal-evidence
umask 077; mkdir -p "$EVIDENCE"
```

**5.1 Confirm the container, the network and the names in the credentials file (names only, no values).**

```bash
docker inspect -f '{{.Name}} {{.State.Status}} {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$DB"
sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$REHEARSAL_ENV"          # lists variable NAMES only
```

The rest assumes the file defines the bootstrap superuser and its password as `POSTGRES_USER` and `POSTGRES_PASSWORD`. If your names differ, edit
only the next two lines. They map the file's names to the libpq variables `PGUSER` and `PGPASSWORD`, which the containers read by name:

```bash
set -a; . "$REHEARSAL_ENV"; set +a
export PGUSER="${POSTGRES_USER:?name the bootstrap superuser variable}" PGPASSWORD="${POSTGRES_PASSWORD:?name its password variable}"
```

Check that the login is a superuser and not `cv_app` (prints one word):

```bash
docker exec -e PGPASSWORD "$DB" psql -h localhost -U "$PGUSER" -d cv_analyzer_prod -Atc \
  "SELECT CASE WHEN rolsuper AND rolname <> 'cv_app' THEN 'ok: superuser, not cv_app' ELSE 'STOP: not a usable bootstrap login' END FROM pg_roles WHERE rolname = current_user"
```

**5.2 A helper that runs the tooling from the pull request checkout, on the rehearsal network only.** It uses an image that already has the Python
dependencies (the staging API image, `cvstaging-api:latest`, is the one already on the host) with the checkout mounted read-only over the code, so
no image is built and no dependency is downloaded. The password is passed by name (`-e PGPASSWORD`), never on the command line.

```bash
tool() {
  docker run --rm --network "$NET" -e PGUSER -e PGPASSWORD -v "$SRC/backend:/work:ro" -w /work -e PYTHONPATH=/work \
    -e LEGACY_UPGRADE_DATABASE_URL="postgresql://$DB/cv_analyzer_prod" \
    -e PREFLIGHT_DATABASE_URL="postgresql://$DB/cv_analyzer_prod" \
    cvstaging-api:latest "$@"
}
psql_file() {   # psql_file <template> [-v name=value ...] : the file is read on the host and sent on stdin; values come from the environment
  local file="$1"; shift
  docker exec -i -e PGPASSWORD "$DB" psql -X -h localhost -U "$PGUSER" -d cv_analyzer_prod -v ON_ERROR_STOP=1 "$@" -f - < "$file"
}
```

**5.3 Rehearsal passwords.** Generated here, held in a root-only file, never printed, discarded with the rehearsal:

```bash
SECRETS_FILE="$EVIDENCE/rehearsal-runtime-passwords.env"
for name in MIGRATION_OPERATOR CV_APP CV_WORKER CV_ADMIN CV_SECRETS; do
  printf '%s_PASSWORD=%s\n' "$name" "$(openssl rand -base64 33 | tr -d '/+=\n')"
done > "$SECRETS_FILE"; chmod 600 "$SECRETS_FILE"
set -a; . "$SECRETS_FILE"; set +a
```

**5.4 Run the procedure** (section 4, steps 3 to 9) against the container, saving the outputs as evidence:

```bash
# 3. check (read-only). Expected to exit 1 only because of the three FAIL data check lines, which give the counts to confirm.
tool python -m scripts.legacy_upgrade check | tee "$EVIDENCE/01-check-before.txt" || true
tool python -m scripts.legacy_upgrade check --json > "$EVIDENCE/01-check-before.json" || true   # complete: every identity classified, both definitions, no row content
# Stop here and read it: no "FAIL expected object", "FAIL unknown object", "FAIL production state" or "FAIL precheck" line may be present, and the
# counts must be the ones you will confirm. Then set CONFIRM from what you read (the real report showed 86, 2 and 524):
CONFIRM="--apply-data-correction 077_clean_stopped_workflow_pollution.sql=86 --apply-data-correction 081_fix_knockout_answer_value_encoding.sql=2 --apply-data-correction 092_repair_legacy_new_workflow_status.sql=524"
tool python -m scripts.legacy_upgrade check $CONFIRM | tee "$EVIDENCE/01b-check-confirmed.txt"          # exit 0 now
docker exec -e PGPASSWORD "$DB" psql -X -h localhost -U "$PGUSER" -d cv_analyzer_prod -At -c \
  "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit FROM pg_roles WHERE rolname LIKE 'cv\_%' ORDER BY 1" \
  > "$EVIDENCE/00-roles-before.txt"

# 5. migration roles
psql_file "$SRC/backend/db/ops/0000_migration_roles.sql.template" -v migration_operator_password="$MIGRATION_OPERATOR_PASSWORD"

# 6. conversion: dry run, then real
tool python -m scripts.legacy_upgrade prepare --dry-run $CONFIRM | tee "$EVIDENCE/02-prepare-dry-run.txt"
tool python -m scripts.legacy_upgrade prepare $CONFIRM            | tee "$EVIDENCE/03-prepare.txt"

# 7. migrations 098 to 101 as the operator login (the helper passes the operator password by name)
migrate() {
  docker run --rm --network "$NET" -e MIGRATION_OPERATOR_PASSWORD -v "$SRC/backend:/work:ro" -w /work -e PYTHONPATH=/work \
    -e MIGRATION_OWNER_ROLE=cv_migrator -e MIGRATION_DATABASE_URL_HOST="$DB" cvstaging-api:latest \
    sh -c 'MIGRATION_DATABASE_URL="postgresql://cv_migration_operator:${MIGRATION_OPERATOR_PASSWORD}@${MIGRATION_DATABASE_URL_HOST}/cv_analyzer_prod" python -m scripts.migrate "$@"' sh "$@"
}
migrate --dry-run | tee "$EVIDENCE/04-migrate-dry-run.txt"
migrate           | tee "$EVIDENCE/05-migrate.txt"

# 8. runtime logins and privileges
psql_file "$SRC/backend/db/ops/0004_runtime_roles.sql.template" \
  -v cv_app_password="$CV_APP_PASSWORD" -v cv_worker_password="$CV_WORKER_PASSWORD" \
  -v cv_admin_password="$CV_ADMIN_PASSWORD" -v cv_secrets_password="$CV_SECRETS_PASSWORD"
tool sh -c 'python -m db.runtime_grants | diff - db/ops/0005_runtime_privileges.sql.template && echo "0005 matches the generator"'
psql_file "$SRC/backend/db/ops/0005_runtime_privileges.sql.template"

# 9. preflight, after: every line PASS, exit 0
tool python -m scripts.db_preflight --phase after | tee "$EVIDENCE/06-preflight-after.txt"
```

**5.5 Rehearsal checks beyond the preflight** (read-only; each prints counts or words, no row content):

```bash
q() { docker exec -e PGPASSWORD "$DB" psql -X -h localhost -U "$PGUSER" -d cv_analyzer_prod -At -c "$1"; }
q "SELECT 'ledger rows', count(*) FROM cv_analyzer.schema_migrations"
q "SELECT item_kind, proof, count(*) FROM cv_analyzer.schema_migration_legacy_evidence GROUP BY 1, 2 ORDER BY 1, 2"
q "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname LIKE 'cv\_%' ORDER BY 1"
q "SELECT 'next job code number', last_value + CASE WHEN is_called THEN 1 ELSE 0 END FROM cv_analyzer.job_code_seq"
q "SELECT 'highest issued job code number', max(substring(job_code FROM '([0-9]+)\$')::bigint) FROM cv_analyzer.jobs"
q "SELECT 'bulk applications kept', count(*) FROM cv_analyzer.applications WHERE submission_source = 'bulk_excel_upload'"
q "SELECT item_kind, item, detail->>'rows_reported' AS reported, detail->>'rows_after' AS after, detail->'rows_deleted' AS deleted FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'data_correction' ORDER BY item"
q "SELECT 'applications on the retired status', count(*) FROM cv_analyzer.applications WHERE workflow_status = 'new'"
q "SELECT 'production variations recorded', count(*) FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'production_variation'"
q "SELECT conname FROM pg_constraint WHERE conrelid = 'cv_analyzer.application_knockout_answers'::regclass AND contype IN ('f', 'u') ORDER BY 1"
```

Compare row counts of the data tables before and after (counts only). Run the same query on the restored database before step 5.4 and after step 9:

```bash
counts() { q "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'cv_analyzer' ORDER BY 1"; }
```

(`n_live_tup` is an estimate; for exact counts run `SELECT count(*)` on `applications`, `application_scores`, `jobs`, `job_criteria`, `bulk_upload_batches`,
`bulk_upload_rows`, `tenant_features`, `tenant_feature_audit`, `application_knockout_answers`.)

**5.6 Start the new API against the rehearsal database only** (optional but recommended). It needs the embedding model that the image carries, no Redis and no provider
key. It runs on the rehearsal network, bound to the host loopback only:

```bash
docker run -d --name cvrehearsal-api --network "$NET" -p 127.0.0.1:18000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://cv_app:${CV_APP_PASSWORD}@${DB}/cv_analyzer_prod" \
  -e ADMIN_DATABASE_URL="postgresql+asyncpg://cv_admin:${CV_ADMIN_PASSWORD}@${DB}/cv_analyzer_prod" \
  -e WORKER_DATABASE_URL="postgresql+asyncpg://cv_worker:${CV_WORKER_PASSWORD}@${DB}/cv_analyzer_prod" \
  -e SECRETS_DATABASE_URL="postgresql+asyncpg://cv_secrets:${CV_SECRETS_PASSWORD}@${DB}/cv_analyzer_prod" \
  -e JWT_SECRET="$(openssl rand -hex 32)" -v "$SRC/backend:/app:ro" -w /app cvstaging-api:latest \
  uvicorn main:app --host 0.0.0.0 --port 8000
sleep 20; curl -sS http://127.0.0.1:18000/health; echo; curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/health/ready
docker logs cvrehearsal-api 2>&1 | tail -n 5          # startup lines only; no credentials are logged
docker rm -f cvrehearsal-api
```

(The `-e` values above put the passwords on this container's environment, not in your shell history or on a command line you type; `docker inspect` of the
rehearsal container shows them, which is acceptable for a throw-away rehearsal and a reason not to reuse any of these passwords elsewhere.)

**5.7 What a good rehearsal looks like.** The first `check` shows `ACCEPT`/`RECONCILE` lines for the eight causes of section 2a, `REPAIR 096`, `ATTEST 005`, and
exactly three `FAIL data check` lines with the counts; `check $CONFIRM` exits 0; `prepare --dry-run` and `prepare` exit 0 with three `corrected` lines, four `reconciled` steps,
94 ledger rows and the four pending files; `migrate` applies exactly those four; the preflight is all `PASS`; the evidence queries show three `data_correction` rows ending at
0 and the production variations; no application is on the retired status; row counts of every table match before and after except `application_knockout_answers`
(minus the empty answers 081 deletes); the API answers `/health`. If `check` or `prepare` stops, it prints what it found and **changed nothing**; send the output (object
names and counts only) and do not retry with a changed database.

**Report back from the rehearsal:** the new head SHA you ran, `01-check-before.json` (if it differs from the committed fixture's classification, it will say which identity), the
`03-prepare.txt` output, the preflight output, the evidence queries above, and the row counts before and after.

## 6. Rollback

**Abort and roll back when any of these is true** (before the new stack takes traffic): `prepare` stopped (nothing to roll back; fix the cause in a rehearsal first);
`migrate` applied anything other than 098 to 101 or failed; the preflight shows any non-`PASS` line; a data-correction line in the evidence table does not end at 0; the row counts
of any table other than `application_knockout_answers` differ from before (that table is expected to lose exactly the empty answers 081 reports); `/health/ready` or the tenant login
fails; or the operator who signed off section 4 step 4 no longer accepts a decision it covered. **After the new stack has taken traffic**, a restore loses everything written since:
decide it as an incident, not as a procedure step.

Migrations are forward-only. Rollback is a **restore into a new database**, never a reversal by hand and never `REASSIGN OWNED`.

The conversion changes two kinds of thing: the database (objects, ownership, the ledger) and the cluster (roles). A restore of the dump brings back
the first. The second needs care:

1. Stop the new API, workers and beat.
2. Create a new, empty database and restore the pre-conversion dump (step 2 of section 4) into it as a superuser:

   ```bash
   createdb --encoding UTF8 cv_analyzer_prod_restored
   pg_restore --dbname cv_analyzer_prod_restored --exit-on-error /var/backups/cv-analyzer-prod-pre-conversion-<timestamp>.dump
   ```

   Ownership is restored as recorded (`cv_app` still exists after the conversion). Do not restore over the live database; keep it for inspection.
3. **Roles.** The conversion left `cv_app` a non-superuser and added roles. The old build (`63f3bd7`) needs `cv_app` as it was. Restore its
   attributes **exactly as recorded** in `cv-analyzer-prod-roles-before.txt` (here: `SUPERUSER BYPASSRLS`) and set the old password from the secret store:

   ```bash
   psql -X -v ON_ERROR_STOP=1 -v old_password="$OLD_CV_APP_PASSWORD" "$BOOTSTRAP_URL" \
     -c "ALTER ROLE cv_app SUPERUSER BYPASSRLS LOGIN PASSWORD :'old_password'"
   ```

   This is a rollback-only restoration of a recorded state, to be written up in the incident record. It is not a fix for a permission error in the new
   release: a runtime role that needs more privilege than the matrix gives it is a defect to correct, not a rollback step.
   The extra roles (`cv_worker`, `cv_admin`, `cv_secrets`, `cv_migrator`, `cv_migration_operator`) may stay: the old build does not use them and
   they hold no privilege on the restored database.
4. Point the previous application build at the restored database, redeploy the previous frontend, and run the smoke test.
5. Records written after the backup are lost by this procedure. The backup time is in the release notes.

**If `prepare` itself fails**, nothing needs rolling back: it ran in one transaction. If a failure happens *after* `prepare` (during 098 to 101, `0004`,
`0005`), restore as above; the ledger and evidence are part of the restored database, so a restore returns to the no-ledger starting point.

**Job codes.** Migration 101 starts `job_code_seq` above the highest code issued. After a restore, the sequence is restored with the data. Never run 101
by hand against a database that has issued codes outside the runner.

## 7. Remaining risks

* **The rehearsal still decides.** The first rehearsal found the real database differed from the reconstructed lineage in 86 identities; the procedure now encodes the real
  restored schema. The production database itself can still have drifted since the backup. `check` is built to find that and to stop; it changes nothing. The expected
  result is the eight causes of section 2a, `REPAIR 096`, `ATTEST 005`, and the three data counts.
* **The recorded production state is the backup's.** The classification was proved against the schema of one restored backup. If production changed after that backup
  (a hot fix, a new column), `check` reports it as unexplained and stops, which is the intended behaviour.
* **`public.set_updated_at()` was not in the schema-only dump.** The tests use a stand-in documented in the fixture loader; the conversion verifies the real function every
  time it runs (existence, plain `plpgsql`, body equal to `cv_analyzer.set_updated_at()`, not owned by a runtime role).
* **Data-correction 081 deletes rows** (answers that are empty strings), by design of that migration. The count to confirm includes them; the evidence records how many were deleted.
* **Rows that could block a new constraint** (orphaned or duplicate knockout answers, NULL timestamps or flags, residual `'new'` statuses) are reported by `check`, stop `prepare`, and are never changed
  by the tool. Production is expected to have none; the rehearsal shows it.
* **The release does not carry the legacy features' code.** Main (and so the converted database's application) does not reference the scoring-V2
  columns, `bulk_upload_*`, `tenant_features`, the gender columns, `scoring_method`, `needs_verification` or the Requirements V2 extraction
  token and configuration keys. Their schema and data are preserved and sealed, but deploying this release stops the application from using them.
  If production relies on any of those features, that is a code decision to make before deploying, not something a database conversion can supply.
* **The reference was generated on PostgreSQL 16.** `check` compares definitions as the catalogue prints them (constraint and index text, function
  bodies, column types). A different major version can print some of them differently and make `check` report a difference that is not real. `check` is
  read-only, so the rehearsal shows this at no cost; if it happens, regenerate the reference on the production major version
  (`python -m scripts.generate_legacy_reference`) in a reviewed change.
* **`005_job_code_alias.sql` is attested, not proved.** See step 4 of section 4.
* **Data checks can stop the run.** A data-correction migration whose problem has reappeared (for example JSON-quoted knockout answers written since)
  stops the conversion. Inspect the rows, and either correct them or acknowledge that file with `--acknowledge-data`.
* **The `tenant_features_tenant_isolation` policy casts `app.current_tenant_id` to `uuid`.** It is left as it is. No runtime role can read the table,
  so the cast cannot be reached; if a future release grants access, replace it with the text comparison main uses.
* **Rollback needs the recorded role attributes.** Without `cv-analyzer-prod-roles-before.txt`, the old `cv_app` attributes are unknown.
* **Cluster-wide roles survive a restore.** After a rollback, `cv_worker`, `cv_admin`, `cv_secrets`, `cv_migrator` and `cv_migration_operator` still
  exist. They hold nothing on the restored database; drop them only if you will not retry.
* **The rehearsal commands in section 5 were written without access to the host.** They use only names and flags verified against the repository
  scripts. Confirm the credential variable names (5.1) and the image tag on the host before relying on them.

## 8. When `check` or `prepare` stops

The text output prints at most 40 findings per category and says how many more there are. **`check --json` is never capped**: it lists every
missing, different and unknown identity with the expected and the live definition (DDL text only), the data-check counts and, for each data check
that finds rows, a breakdown by status-like columns. It contains no names, addresses, answers or documents. It is the document a production
difference is classified from.

| Output | Meaning | What to do |
|---|---|---|
| `FAIL unknown object ...` | A table, column, constraint, index, trigger, function or policy that is not part of the legacy lineage | Find out what created it. Do not drop it to make the check pass. If it is a legitimate production addition, extend `db/legacy/lineage.py` in a reviewed change and regenerate the reference. |
| `FAIL expected object missing/different ...` | The database differs from the lineage | Same. If the object belongs to 010, 024 or 087, the message says its effect was never applied; apply it in a rehearsal, review it, then re-run `check`. |
| `FAIL expected object missing seed\|...` | A seeded configuration row is absent | If an operator deleted it on purpose, accept it with `--acknowledge-missing-seed`; if not, restore the row. |
| `FAIL data check <file>` | Rows still show the problem the migration corrected | For 077, 081, 092 and 057: read the count, then confirm it with `--apply-data-correction <file>=<count>`. For the others: inspect (counts first), correct them, or accept with `--acknowledge-data <file>`. |
| `FAIL data correction <file>` | The confirmed count is not the live count, or the file has no declared correction | Read the report again; the data changed. Never raise or lower the number to make it pass. |
| `FAIL precheck <step>` | Rows that would make a new constraint fail (orphans, duplicates, NULLs, stray statuses) | Decide what those rows are (counts only are printed). The tool changes none of them. Correct them in a reviewed change, restore, rehearse again. |
| `FAIL production state ...` | An identity of the recorded production state is in a different form, or a reconcile step is half applied | The database is not the restored backup's. Classify the new difference in `production_lineage.json` through review; do not edit the database. |
| `FAIL external dependency ...` | `public.set_updated_at()` differs from `cv_analyzer.set_updated_at()`, is not a plain trigger function, or a runtime role owns it | Correct the function's owner or body in a reviewed change; re-run `check`. |
| `FAIL runtime sessions` | A runtime role is connected | Stop the API, workers and beat. |
| `FAIL ledger` | A ledger or evidence table already exists | The conversion has already run, or the database is not the legacy lineage. Use the standard procedure. |
| `FAIL role cv_migrator` | `db/ops/0000` has not run | Run it (step 5). |
