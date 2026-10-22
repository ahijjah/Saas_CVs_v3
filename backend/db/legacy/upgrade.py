"""Convert the legacy production database (source commit 63f3bd7, no migration ledger) into the state the current release expects.

Two operations, both fail-closed:

  assess(conn)   read-only. Compares the live catalogue with the exact legacy lineage, runs the data checks, and reports what the
                 conversion would do. Nothing is written.
  prepare(conn)  one transaction, as the bootstrap superuser: creates the missing runtime roles, moves ownership to cv_migrator,
                 creates the ledger and the evidence table, runs or reconciles the few files where legacy and main disagree, records
                 every migration up to 094 in the ledger only after its effect is proved, and verifies that the catalogue changed in
                 exactly the declared identities and that the runner now sees exactly the pending files 098 to 101. Any failure rolls
                 the whole transaction back and the database is as it was.

It never marks a file applied on the strength of the lineage alone: a structural file is recorded when every identity it produces is
present with the expected definition; a data-correction file when its data check is zero; a file that neither can prove is recorded as
`attested` in the evidence table and listed for the operator. Passwords are never read or written here.

After prepare, the normal procedure continues (docs/LEGACY_PRODUCTION_UPGRADE.md): scripts.migrate for 098 to 101, db/ops/0004 and
0005, then the preflight with --phase after."""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field

import psycopg2
from psycopg2.extras import Json

from db.runner import runner
from db.runner.ledger import LEDGER_DDL
from db.runner.manifest import MANIFEST
from db.runner.sqltext import migration_body
from . import fingerprint as F
from . import lineage as L

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations"
MIGRATOR = "cv_migrator"
OPERATOR = "cv_migration_operator"
RUNTIME = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")
EVIDENCE_TABLE = "cv_analyzer.schema_migration_legacy_evidence"
EXPECTED_PENDING_AFTER_PREPARE = [
    "098_application_scores_requirements_revision.sql",
    "099_platform_secret_accessors.sql",
    "100_runtime_rls_identity.sql",
    "101_job_code_sequence.sql",
]

EVIDENCE_DDL = """
CREATE TABLE cv_analyzer.schema_migration_legacy_evidence (
    evidence_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lineage      text NOT NULL,
    item_kind    text NOT NULL CHECK (item_kind IN ('legacy_migration', 'baselined_migration', 'repair_run', 'reconciliation',
                                                    'role_state', 'conversion')),
    item         text NOT NULL,
    legacy_sha256 text CHECK (legacy_sha256 IS NULL OR legacy_sha256 ~ '^[0-9a-f]{64}$'),
    main_sha256   text CHECK (main_sha256 IS NULL OR main_sha256 ~ '^[0-9a-f]{64}$'),
    proof        text NOT NULL CHECK (proof IN ('catalog', 'data', 'catalog+data', 'ran', 'no_effect', 'attested', 'superseded')),
    detail       jsonb NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    recorded_by  text NOT NULL DEFAULT session_user,
    UNIQUE (lineage, item_kind, item)
)
"""


class ConversionError(RuntimeError):
    pass


@dataclass
class Result:
    assessment: L.Assessment
    ledger_rows: int = 0
    evidence_rows: int = 0
    pending: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- read-only assessment --------------------------------------------------------------------------------------------------------
def assess(conn, *, acknowledged: frozenset[str] = frozenset(), for_prepare: bool = False) -> L.Assessment:
    """Read-only. The connection is put in a read-only transaction for the duration."""
    previous = conn.autocommit
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        return assess_cursor(cur, acknowledged=acknowledged, for_prepare=for_prepare)
    finally:
        conn.rollback()
        conn.autocommit = previous


def assess_cursor(cur, *, acknowledged: frozenset[str] = frozenset(), for_prepare: bool = False) -> L.Assessment:
    out = L.Assessment()
    cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user), current_database(), "
                "current_setting('server_version')")
    user, is_super, database, version = cur.fetchone()
    out.add("INFO", "connection", f"database {database}, PostgreSQL {version}")
    if for_prepare:
        if not is_super:
            out.add("FAIL", "bootstrap login", "the conversion needs a superuser login (not a runtime role)")
        elif user in RUNTIME:
            out.add("FAIL", "bootstrap login", f"{user} is a runtime role; use a separate bootstrap superuser")
        else:
            out.add("PASS", "bootstrap login", "superuser, not a runtime role")

    cur.execute("SELECT to_regnamespace('cv_analyzer') IS NOT NULL")
    if not cur.fetchone()[0]:
        out.add("FAIL", "schema", "schema cv_analyzer does not exist")
        return out
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations') IS NOT NULL, "
                "to_regclass(%s) IS NOT NULL", (EVIDENCE_TABLE,))
    ledger, evidence = cur.fetchone()
    if ledger or evidence:
        out.add("FAIL", "ledger", "a migration ledger or legacy evidence table already exists: this database is not in the "
                                  "pre-ledger legacy state, and the conversion does not apply")
        return out
    out.add("PASS", "ledger", "absent, as expected for the legacy lineage")

    out.live = F.snapshot(cur)
    L.assess_structure(out.live, out)
    L.assess_data(cur, out, acknowledged=acknowledged)
    _assess_roles(cur, out, for_prepare=for_prepare)
    return out


def _assess_roles(cur, out: L.Assessment, *, for_prepare: bool) -> None:
    cur.execute("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
                (list(RUNTIME) + [MIGRATOR, OPERATOR],))
    roles = {r[0]: r for r in cur.fetchall()}
    for name in RUNTIME:
        r = roles.get(name)
        if r is None:
            out.add("INFO", f"role {name}", "absent: will be created NOLOGIN")
        else:
            out.add("INFO", f"role {name}", f"super={r[1]} bypassrls={r[2]} login={r[3]}: hardened later by db/ops/0004")
    for name in (MIGRATOR, OPERATOR):
        if name not in roles:
            out.add("FAIL" if for_prepare else "WARN", f"role {name}", "absent: run db/ops/0000_migration_roles.sql.template first")
    if MIGRATOR in roles and (roles[MIGRATOR][1] or roles[MIGRATOR][2] or roles[MIGRATOR][3]):
        out.add("FAIL", f"role {MIGRATOR}", "must be NOLOGIN, NOSUPERUSER and NOBYPASSRLS")
    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
                "AND usename = ANY(%s)", (list(RUNTIME),))
    sessions = cur.fetchone()[0]
    out.add("FAIL" if sessions and for_prepare else "INFO", "runtime sessions",
            f"{sessions} open session(s) of runtime roles" + ("; stop the API and workers first" if sessions else ""))
    cur.execute("SELECT pg_get_userbyid(c.relowner), count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','p','S','v','m') GROUP BY 1 ORDER BY 1")
    out.add("INFO", "relation owners", ", ".join(f"{o}={n}" for o, n in cur.fetchall()))
    cur.execute("SELECT pg_get_userbyid(datdba), (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public') "
                "FROM pg_database WHERE datname = current_database()")
    dba, public_owner = cur.fetchone()
    out.add("INFO", "database owner", f"{dba}; schema public owner {public_owner}")


# --- the conversion --------------------------------------------------------------------------------------------------------------
def _exec(cur, sql: str) -> None:
    cur.execute(sql)


def _changed(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def prepare(conn, *, acknowledged: frozenset[str] = frozenset(), dry_run: bool = False) -> Result:
    """Run the whole conversion in one transaction. With dry_run the transaction is rolled back at the end, so the result shows what
    would be recorded and the database is unchanged."""
    from scripts.generate_ownership_transfer import GenerationError, generate

    # The ownership statements are generated from the catalogue before the transaction starts: the generator manages its own
    # transaction and rolls it back, which would discard this one.
    conn.autocommit = True
    try:
        ownership = generate(conn, MIGRATOR)
    except GenerationError as exc:
        raise ConversionError(f"ownership transfer cannot be generated: {exc}") from exc

    conn.autocommit = False
    cur = conn.cursor()
    result: Result | None = None
    try:
        cur.execute("SET LOCAL lock_timeout = '15s'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (runner.LOCK_KEY,))
        assessment = assess_cursor(cur, acknowledged=acknowledged, for_prepare=True)
        result = Result(assessment=assessment)
        if assessment.blocking:
            raise ConversionError("the database is not in the exact legacy state; nothing was changed:\n" + "\n".join(
                f"  {name}: {detail}" for level, name, detail in assessment.findings if level == "FAIL"))
        before_all = dict(assessment.live)

        # 1. Roles. The runtime roles that the legacy schema never created are added NOLOGIN, with no privilege.
        for name in RUNTIME:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
            if cur.fetchone() is None:
                cur.execute(f"CREATE ROLE {name} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT")
                result.steps.append(f"created role {name}")
        cur.execute("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit FROM pg_roles "
                    "WHERE rolname = ANY(%s) ORDER BY rolname", (list(RUNTIME),))
        role_state_before = [list(r) for r in cur.fetchall()]

        # 2. Ownership: explicit statements, schema cv_analyzer only. The database and public schema go to the bootstrap login
        #    when a runtime role owns them (a database owner can create schemas and drop the database).
        cur.execute("\n".join(ownership))
        result.steps.append(f"moved ownership of {len(ownership) - 2} object(s) in cv_analyzer to {MIGRATOR}")
        cur.execute("SELECT current_user, pg_get_userbyid(datdba), (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE "
                    "nspname = 'public'), current_database() FROM pg_database WHERE datname = current_database()")
        bootstrap, dba, public_owner, database = cur.fetchone()
        if dba in RUNTIME:
            cur.execute(f'ALTER DATABASE "{database}" OWNER TO "{bootstrap}"')
            result.steps.append(f"database owner {dba} -> {bootstrap}")
        if public_owner in RUNTIME:
            cur.execute(f'ALTER SCHEMA public OWNER TO "{bootstrap}"')
            result.steps.append(f"schema public owner {public_owner} -> {bootstrap}")

        # 3. The ledger and the evidence table, owned by the migrator like everything else.
        cur.execute(LEDGER_DDL)
        cur.execute(f"ALTER TABLE cv_analyzer.schema_migrations OWNER TO {MIGRATOR}")
        cur.execute(EVIDENCE_DDL)
        cur.execute(f"ALTER TABLE {EVIDENCE_TABLE} OWNER TO {MIGRATOR}")
        cur.execute(f"SET LOCAL ROLE {MIGRATOR}")
        cur.execute("SET LOCAL search_path = cv_analyzer, public")

        evidence: list[tuple] = []     # (kind, item, legacy_sha, main_sha, proof, detail)

        # 4. Base files that did not apply on a clean chain: run only the ones whose objects are absent. Each validates what it finds.
        for file in assessment.repairs:
            body = migration_body((MIGRATIONS_DIR / file).read_text(encoding="utf-8"))
            cur.execute(body.sql)
            result.steps.append(f"ran {file}")
            evidence.append(("repair_run", file, None, _sha(MIGRATIONS_DIR / file), "ran",
                             {"reason": "objects it produces were absent or different in the legacy database"}))
        mid = F.snapshot(cur)
        still = [(k, "missing" if k not in mid else "different") for k in L.EXPECTED_LEGACY
                 if L.SETTER_LEGACY[k] in L.AUTO_REPAIRS and mid.get(k) != L.EXPECTED_LEGACY[k]]
        if still:
            raise ConversionError(f"after the repair files, {len(still)} object(s) are still not as expected: {still[:5]}")

        # 5. The two files where legacy has the same schema under another name (093) or a conflicting one (094).
        pre_bridge = F.snapshot(cur)
        body = migration_body((MIGRATIONS_DIR / L.BRIDGE_RUN).read_text(encoding="utf-8"))
        cur.execute(body.sql)
        bridge_changed = _changed(pre_bridge, F.snapshot(cur))
        allowed_bridge = {"constraint|job_criteria.weights_sum_100"}
        if not bridge_changed <= allowed_bridge:
            raise ConversionError(f"{L.BRIDGE_RUN} changed objects outside the declared set: {sorted(bridge_changed - allowed_bridge)}")
        result.steps.append(f"ran {L.BRIDGE_RUN} (idempotent; changed {sorted(bridge_changed)})")
        evidence.append(("baselined_migration", L.BRIDGE_RUN, None, _sha(MIGRATIONS_DIR / L.BRIDGE_RUN), "ran",
                         {"changed": sorted(bridge_changed), "note": "legacy 106 and 107 added the same columns; the file is idempotent"}))

        for step in L.RECONCILIATIONS:
            pre = F.snapshot(cur)
            cur.execute(step.sql)
            changed = _changed(pre, F.snapshot(cur))
            if not changed <= set(step.changes):
                raise ConversionError(f"reconciliation {step.name} changed objects outside its declared set: "
                                      f"{sorted(changed - set(step.changes))}")
            result.steps.append(f"reconciled {step.name}")
            evidence.append(("reconciliation", step.name, None, None, "ran", {"reason": step.reason, "changed": sorted(changed)}))

        _prove_094(cur)
        evidence.append(("baselined_migration", L.BRIDGE_RECONCILED, None, _sha(MIGRATIONS_DIR / L.BRIDGE_RECONCILED), "catalog",
                         {"proved": ["applications_submission_source_check allows 'candidate_reuse'",
                                     "applications.submission_source has no default"],
                          "note": "the check also keeps 'bulk_excel_upload'; see reconciliation submission_source_union"}))

        # 6. Ledger rows, one per manifest file before 098, each with the sha256 of the file in this repository.
        files = [e.file for e in MANIFEST]
        first_pending = files.index(EXPECTED_PENDING_AFTER_PREPARE[0])
        ledger_rows = 0
        for entry in MANIFEST[:first_pending]:
            sha = _sha(MIGRATIONS_DIR / entry.file)
            if entry.superseded_by:
                cur.execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status, replaced_by) "
                            "VALUES (%s, %s, 'superseded', %s)", (entry.file, sha, entry.superseded_by))
                evidence.append(("baselined_migration", entry.file, None, sha, "superseded",
                                 {"replaced_by": entry.superseded_by, "note": L.NO_EFFECT.get(entry.file, "")}))
            else:
                cur.execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status) VALUES (%s, %s, 'applied')",
                            (entry.file, sha))
                if entry.file not in (L.BRIDGE_RUN, L.BRIDGE_RECONCILED) and entry.file not in assessment.repairs:
                    evidence.append(_baseline_evidence(entry.file, sha, assessment))
                elif entry.file in assessment.repairs:
                    pass     # recorded above as a repair run
            ledger_rows += 1
        result.ledger_rows = ledger_rows

        # 7. The legacy files: their checksums and the identities proved present.
        for item in L.legacy_files():
            evidence.append(("legacy_migration", item["file"], item["sha256"], None, "catalog",
                             {"identities_proved": assessment.verified_keys.get(item["file"], 0),
                              "introduced": len(item["set"])}))
        cur.execute("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
                    (list(RUNTIME),))
        evidence.append(("role_state", "runtime roles before hardening", None, None, "catalog",
                         {"roles": [list(r) for r in cur.fetchall()], "created_this_run": [s for s in result.steps if s.startswith("created role")],
                          "before": role_state_before}))
        evidence.append(("conversion", "prepare", None, None, "catalog",
                         {"lineage_commit": L.LEGACY_COMMIT, "steps": result.steps, "attested": sorted(L.ATTESTED),
                          "data_checks": assessment.data, "acknowledged": sorted(acknowledged)}))
        for kind, item, legacy_sha, main_sha, proof, detail in evidence:
            cur.execute(f"INSERT INTO {EVIDENCE_TABLE} (lineage, item_kind, item, legacy_sha256, main_sha256, proof, detail) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)", (L.LINEAGE_NAME, kind, item, legacy_sha, main_sha, proof, Json(detail)))
        result.evidence_rows = len(evidence)

        # 8. Postconditions: the catalogue differs from the legacy reference only in declared identities, and the runner agrees.
        after = F.snapshot(cur)
        declared = set(allowed_bridge)
        for step in L.RECONCILIATIONS:
            declared |= set(step.changes)
        missing, different, extra = F.diff(L.EXPECTED_LEGACY, after)
        bad_diff = [k for k in different if k not in declared] + missing
        new_objects = [k for k in extra if not k.startswith("seed|") and k not in declared]
        if bad_diff or new_objects:
            raise ConversionError(f"the catalogue differs from the legacy reference outside the declared identities: "
                                  f"{(bad_diff + new_objects)[:8]}")
        blobs = runner._read_blobs(MIGRATIONS_DIR, MANIFEST)
        shas = {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}
        _, _, actions = runner._plan_run(cur, MANIFEST, MIGRATIONS_DIR, shas, False)
        pending = [a.file for a in actions if a.kind == "apply"]
        if pending != EXPECTED_PENDING_AFTER_PREPARE:
            raise ConversionError(f"the runner would apply {pending}, expected {EXPECTED_PENDING_AFTER_PREPARE}")
        result.pending = pending
        cur.execute("RESET ROLE")
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def _prove_094(cur) -> None:
    """The effect of main migration 094, proved from the catalogue: 'candidate_reuse' is allowed and the default is gone."""
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'cv_analyzer.applications'::regclass "
                "AND conname = 'applications_submission_source_check'")
    row = cur.fetchone()
    if row is None or "candidate_reuse" not in row[0]:
        raise ConversionError("094 not proved: applications_submission_source_check does not allow 'candidate_reuse'")
    for value in ("manual_upload", "email_forwarding", "platform_email", "public_apply"):
        if value not in row[0]:
            raise ConversionError(f"094 not proved: applications_submission_source_check lost '{value}'")
    cur.execute("SELECT count(*) FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                "WHERE d.adrelid = 'cv_analyzer.applications'::regclass AND a.attname = 'submission_source'")
    if cur.fetchone()[0] != 0:
        raise ConversionError("094 not proved: applications.submission_source still has a default")


def _baseline_evidence(file: str, sha: str, assessment: L.Assessment) -> tuple:
    verified = assessment.verified_keys.get(file, 0)
    if file in L.DATA_CHECK_FILES:
        return ("baselined_migration", file, None, sha, "catalog+data" if verified else "data",
                {"identities_proved": verified, "data_check": L.DATA_CHECK_FILES[file].description,
                 "violating_rows": assessment.data.get(file)})
    if file in L.ATTESTED:
        return ("baselined_migration", file, None, sha, "attested", {"reason": L.ATTESTED[file], "identities_proved": verified})
    if verified:
        return ("baselined_migration", file, None, sha, "catalog", {"identities_proved": verified})
    return ("baselined_migration", file, None, sha, "no_effect", {"reason": L.NO_EFFECT.get(file, "produces no catalogue identity")})


def report_lines(a: L.Assessment) -> list[str]:
    order = {"FAIL": 0, "REPAIR": 1, "ACK": 2, "ATTEST": 3, "WARN": 4, "PASS": 5, "INFO": 6}
    return [f"{level:<7} {name}  {detail}".rstrip() for level, name, detail in sorted(a.findings, key=lambda f: order.get(f[0], 9))]


def to_json(a: L.Assessment) -> str:
    return json.dumps({"blocking": a.blocking, "repairs": a.repairs, "data": a.data,
                       "findings": [{"level": l, "name": n, "detail": d} for l, n, d in a.findings]}, indent=2)
