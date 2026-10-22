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

Nothing in this document is run against production or staging by the change that added it. Run it first on the restored backup (section 5).

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
  the lineage does not know**, stops the conversion and is listed. Seeded configuration rows that an operator added are ignored.
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

### What it changes, in order

1. Creates `cv_worker`, `cv_admin`, `cv_secrets` as `NOLOGIN NOSUPERUSER NOBYPASSRLS` with no privilege (only those that are absent).
   `cv_migrator` and `cv_migration_operator` must already exist (`db/ops/0000`).
2. Moves ownership of every object in `cv_analyzer` to `cv_migrator` with explicit `ALTER ... OWNER TO` statements from
   `scripts/generate_ownership_transfer.py` (never `REASSIGN OWNED`). If a runtime role owns the database or the `public` schema, ownership of
   those goes to the bootstrap login.
3. Creates the ledger (`cv_analyzer.schema_migrations`) and the evidence table (`cv_analyzer.schema_migration_legacy_evidence`), both owned by the migrator
   and granted to no runtime role.
4. Switches to `cv_migrator`. Runs `096` if needed. Runs main's `093` (idempotent; legacy 106 and 107 added the same columns).
5. Reconciles the three places where legacy and main disagree (each is a documented, tested step in `db/legacy/lineage.py`):
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

   Exit `0` is required. `REPAIR` lines name a file that will be run (only `096` is possible). `ATTEST` lines are the files accepted without a
   proof. `FAIL` lines are blocking and the output names the object; do not work around them (section 8).
4. **Sign-off.** Confirm `005_job_code_alias.sql` is the only `ATTEST`, and that you accept it: it set `jobs.platform_email = job_code@email_domain`
   once, and the application has written that column since. Confirm any `ACK` data lines were intended.
5. **Migration roles** (once; the passwords come from the secret store):

   ```bash
   psql -X -v ON_ERROR_STOP=1 -v migration_operator_password="$MIGRATION_OPERATOR_PASSWORD" "$BOOTSTRAP_URL" \
        -f backend/db/ops/0000_migration_roles.sql.template
   ```
6. **Conversion: dry run, then real.**

   ```bash
   cd backend
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade prepare --dry-run     # rolls back
   LEGACY_UPGRADE_DATABASE_URL="$BOOTSTRAP_URL" python -m scripts.legacy_upgrade prepare | tee /var/backups/legacy-prepare.txt
   ```

   Expect `ledger rows: 94` and `pending for scripts.migrate:` followed by exactly `098`, `099`, `100`, `101`.
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
# 3. check (read-only). Exit 0 is required.
tool python -m scripts.legacy_upgrade check | tee "$EVIDENCE/01-check-before.txt"
tool python -m scripts.legacy_upgrade check --json > "$EVIDENCE/01-check-before.json"
docker exec -e PGPASSWORD "$DB" psql -X -h localhost -U "$PGUSER" -d cv_analyzer_prod -At -c \
  "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit FROM pg_roles WHERE rolname LIKE 'cv\_%' ORDER BY 1" \
  > "$EVIDENCE/00-roles-before.txt"

# 5. migration roles
psql_file "$SRC/backend/db/ops/0000_migration_roles.sql.template" -v migration_operator_password="$MIGRATION_OPERATOR_PASSWORD"

# 6. conversion: dry run, then real
tool python -m scripts.legacy_upgrade prepare --dry-run | tee "$EVIDENCE/02-prepare-dry-run.txt"
tool python -m scripts.legacy_upgrade prepare            | tee "$EVIDENCE/03-prepare.txt"

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

**5.7 What a good rehearsal looks like.** `check` exits 0 with only the expected `REPAIR` and `ATTEST` lines; `prepare --dry-run` and `prepare` exit 0
with 94 ledger rows and the four pending files; `migrate` applies exactly those four; the preflight is all `PASS`; the ledger, evidence and role
queries match section 2; the counts match before and after; the API answers `/health`. If `check` or `prepare` stops, it prints what it found and
**changed nothing**; send the output (it contains object names only) and do not retry with a changed database.

## 6. Rollback

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

* **The rehearsal decides.** This was built and proved against the lineage reconstructed from the committed files. The real database can differ
  (a hand-applied fix, an extra table, a drifted constraint). `check` is built to find exactly that and to stop; the first run on the restored
  backup is the real test. The expected result is `REPAIR 096` plus `ATTEST 005`.
* **The release does not carry the legacy features' code.** Main (and so the converted database's application) does not reference the scoring-V2
  columns, `bulk_upload_*`, `tenant_features`, the gender columns, `scoring_method`, `needs_verification` or the Requirements V2 extraction
  token and configuration keys. Their schema and data are preserved and sealed, but deploying this release stops the application from using them.
  If production relies on any of those features, that is a code decision to make before deploying, not something a database conversion can supply.
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

| Output | Meaning | What to do |
|---|---|---|
| `FAIL unknown object ...` | A table, column, constraint, index, trigger, function or policy that is not part of the legacy lineage | Find out what created it. Do not drop it to make the check pass. If it is a legitimate production addition, extend `db/legacy/lineage.py` in a reviewed change and regenerate the reference. |
| `FAIL expected object missing/different ...` | The database differs from the lineage | Same. If the object belongs to 010, 024 or 087, the message says its effect was never applied; apply it in a rehearsal, review it, then re-run `check`. |
| `FAIL data check <file>` | Rows still show the problem the migration corrected | Inspect (counts first). Correct them, or accept with `--acknowledge-data <file>`. |
| `FAIL runtime sessions` | A runtime role is connected | Stop the API, workers and beat. |
| `FAIL ledger` | A ledger or evidence table already exists | The conversion has already run, or the database is not the legacy lineage. Use the standard procedure. |
| `FAIL role cv_migrator` | `db/ops/0000` has not run | Run it (step 5). |
