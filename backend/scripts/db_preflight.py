"""Read-only preflight for an existing cv_analyzer database, before and after the hardening procedure (docs/DATABASE_ROLES.md).

It inspects the catalogue and the migration ledger. It never writes: the connection is read-only, and every check runs inside a
read-only transaction.

Two phases:

  before  The database as it is before the operator steps. Anything the procedure creates or changes (roles, ownership, grants,
          policies, secret functions, pending migrations) is reported as WARN, because the procedure is expected to fix it.
  after   The database after the procedure. Every check must be PASS.

Results:

  PASS      the state matches what this release expects.
  WARN      not yet in the expected state, and the procedure will change it (only in phase "before"). Not blocking.
  FAIL      an unsafe or incompatible state. Blocking.
  UNKNOWN   the state could not be established. Blocking.

Exit status: 0 when nothing is FAIL or UNKNOWN (WARN is printed and allowed); 1 when a check FAILs; 2 when a check is UNKNOWN or the
database cannot be read. Any non-zero status means the operator procedure must stop.

Credentials are never printed: a password in the connection URL, or in a driver message, is replaced by ***.

    PREFLIGHT_DATABASE_URL=postgresql://<inspection login>@host/db python -m scripts.db_preflight --phase before|after [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import psycopg2

from db import runtime_grants as G

PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
PHASES = ("before", "after")
MIGRATOR = "cv_migrator"
OPERATOR = "cv_migration_operator"
RUNTIME = G.RUNTIME_ROLES
TENANT_TABLES_REQUIRING_RLS = (
    "applications", "application_files", "application_scores", "audit_logs", "email_ingest_log", "job_criteria", "jobs",
    "users", "tenants", "user_invites", "client_organizations", "agency_user_clients", "tenant_usage_monthly", "job_campaigns",
)
MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def redact(message: str, url: str = "") -> str:
    """Remove every credential from a message: the password of the connection URL and any password key/value a driver prints."""
    text = str(message)
    if url:
        password = urlsplit(url).password
        if password:
            text = text.replace(password, "***")
    return re.sub(r"(password|passwd|pwd)=\S+", r"\1=***", text, flags=re.IGNORECASE)


def _status(ok: bool, phase: str, fixable_by_procedure: bool) -> str:
    """PASS when ok. Otherwise WARN in the before phase when the procedure will fix it, and FAIL in every other case."""
    if ok:
        return PASS
    return WARN if phase == "before" and fixable_by_procedure else FAIL


def _one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def _rows(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def run(conn, phase: str = "after") -> list[Check]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    checks: list[Check] = []
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")

    schema = _one(cur, "SELECT count(*) FROM pg_namespace WHERE nspname = 'cv_analyzer'")[0]
    if schema != 1:
        checks.append(Check("schema cv_analyzer exists", UNKNOWN, f"found {schema}"))
        return checks
    checks.append(Check("schema cv_analyzer exists", PASS))
    if not _one(cur, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NOT NULL")[0]:
        checks.append(Check("migration ledger exists", UNKNOWN, "no ledger: an existing database must have one"))
        return checks
    checks.append(Check("migration ledger exists", PASS))

    # Ledger: applied files must be known and must match the file they were applied from. Pending files are the upgrade itself.
    from db.runner.manifest import MANIFEST
    applied = {r[0]: r[1] for r in _rows(cur, "SELECT filename, sha256 FROM cv_analyzer.schema_migrations WHERE status = 'applied'")}
    known = {e.file for e in MANIFEST}
    unknown = sorted(set(applied) - known)
    checks.append(Check("every applied migration is known to this release", FAIL if unknown else PASS, f"unknown={unknown[:5]}"))
    mismatched = []
    for entry in MANIFEST:
        if entry.file in applied and entry.superseded_by is None:
            path = MIGRATIONS / entry.file
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != applied[entry.file]:
                mismatched.append(entry.file)
    checks.append(Check("applied migrations match their checksums", FAIL if mismatched else PASS, f"changed={mismatched[:5]}"))
    pending = sorted(e.file for e in MANIFEST if e.superseded_by is None and e.file not in applied)
    checks.append(Check("pending migrations of this release", _status(not pending, phase, True) if pending else PASS,
                        f"{len(pending)} pending: {pending[:3]}" if pending else ""))

    # Roles. A runtime role that is a superuser or bypasses row-level security is unsafe in every phase.
    roles = {r[0]: r for r in _rows(cur, "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit, rolcanlogin "
                                         "FROM pg_roles WHERE rolname = ANY(%s)", (list(RUNTIME) + [MIGRATOR, OPERATOR],))}
    for name in RUNTIME:
        r = roles.get(name)
        if r is None:
            checks.append(Check(f"runtime role {name} is hardened", _status(False, phase, True), "missing: created by db/ops/0004"))
            continue
        ok = not (r[1] or r[2] or r[3] or r[4] or r[5])
        unsafe = r[1] or r[2]
        if unsafe:
            checks.append(Check(f"runtime role {name} is hardened", FAIL, f"super={r[1]} bypassrls={r[2]}"))
        else:
            checks.append(Check(f"runtime role {name} is hardened", _status(ok, phase, True),
                                f"createdb={r[3]} createrole={r[4]} inherit={r[5]}"))
    m = roles.get(MIGRATOR)
    checks.append(Check("migrator is NOLOGIN and not a superuser",
                        _status(bool(m and not m[6] and not m[1] and not m[2]), phase, True) if m else _status(False, phase, True),
                        "missing: created by db/ops/0000" if m is None else ""))
    op = roles.get(OPERATOR)
    checks.append(Check("operator is a login that is not a superuser",
                        _status(bool(op and op[6] and not op[1] and not op[2]), phase, True) if op else _status(False, phase, True),
                        "missing: created by db/ops/0000" if op is None else ""))
    memberships = _rows(cur, """
        SELECT r.rolname, m.roleid::regrole::text, m.set_option, m.inherit_option
        FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
        WHERE r.rolname = ANY(%s) OR m.roleid::regrole::text = ANY(%s)""", (list(RUNTIME) + [OPERATOR], list(RUNTIME) + [OPERATOR]))
    unexpected = [x for x in memberships if not (x[0] == OPERATOR and x[1] == MIGRATOR and x[2] and not x[3])]
    checks.append(Check("no runtime role is related to another role", FAIL if unexpected else PASS, f"unexpected={unexpected[:5]}"))
    operator_link = any(x[0] == OPERATOR and x[1] == MIGRATOR and x[2] and not x[3] for x in memberships)
    checks.append(Check("operator may only SET ROLE to the migrator", _status(operator_link, phase, True), ""))

    # Ownership: nothing in the schema is owned by a runtime role; the migrator owns everything else (before the transfer, not yet).
    owned_by_runtime = _rows(cur, """
        SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'cv_analyzer' AND pg_get_userbyid(c.relowner) = ANY(%s)""", (list(RUNTIME),))
    checks.append(Check("no runtime role owns a relation", FAIL if owned_by_runtime else PASS, str(owned_by_runtime[:5])))
    foreign = _rows(cur, """
        SELECT c.relkind, c.relname, pg_get_userbyid(c.relowner) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','S','v','m','p')
          AND pg_get_userbyid(c.relowner) <> %s""", (MIGRATOR,))
    checks.append(Check("every relation is owned by the migrator", _status(not foreign, phase, True),
                        f"{len(foreign)} not owned by {MIGRATOR}: {foreign[:3]}" if foreign else ""))
    routines_foreign = _rows(cur, """
        SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'cv_analyzer' AND pg_get_userbyid(p.proowner) <> %s""", (MIGRATOR,))
    checks.append(Check("every routine is owned by the migrator", _status(not routines_foreign, phase, True),
                        f"not owned by {MIGRATOR}: {routines_foreign[:3]}" if routines_foreign else ""))

    converted = bool(_one(cur, "SELECT to_regclass('cv_analyzer.schema_migration_legacy_evidence') IS NOT NULL")[0])
    if converted:
        checks.extend(_legacy_lineage_checks(cur, phase))

    # Table privileges equal the matrix. Only meaningful once the runtime roles exist; before that they are all missing.
    if all(name in roles for name in RUNTIME):
        extra, missing = [], []
        for (table,) in _rows(cur, "SELECT tablename FROM pg_tables WHERE schemaname = 'cv_analyzer'"):
            sealed_legacy = table in G.LEGACY_PRESERVED_TABLES and converted
            if table not in G.MATRIX and table not in ("platform_secrets", "platform_secret_readable") + G.OPERATOR_TABLES \
                    and not sealed_legacy:
                checks.append(Check(f"table {table} has a matrix entry", FAIL, "add it to db/runtime_grants.py after review"))
                continue
            for role in RUNTIME:
                for verb, privilege in G.VERB_PRIVILEGES.items():
                    granted = _one(cur, "SELECT has_table_privilege(%s, %s, %s)", (role, f"cv_analyzer.{table}", privilege))[0]
                    wanted = privilege in G.expected(table, role)
                    if granted and not wanted:
                        extra.append(f"{role} {privilege} {table}")
                    if wanted and not granted:
                        missing.append(f"{role} {privilege} {table}")
        checks.append(Check("table privileges hold no grant beyond the matrix", _status(not extra, phase, True), str(extra[:8])))
        checks.append(Check("table privileges include every matrix grant", _status(not missing, phase, True), str(missing[:8])))
    else:
        checks.append(Check("table privileges equal the matrix", _status(False, phase, True), "runtime roles not yet created"))

    public_grants = _one(cur, """
        SELECT (SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema = 'cv_analyzer' AND grantee = 'PUBLIC')
             + (SELECT count(*) FROM information_schema.routine_privileges WHERE routine_schema = 'cv_analyzer' AND grantee = 'PUBLIC')""")[0]
    checks.append(Check("PUBLIC holds no table or routine privilege", _status(public_grants == 0, phase, True), f"{public_grants} grants"))

    rls_off = [r[0] for r in _rows(cur, """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'cv_analyzer' AND c.relname = ANY(%s) AND NOT c.relrowsecurity""", (list(TENANT_TABLES_REQUIRING_RLS),))]
    checks.append(Check("row-level security enabled on tenant tables", _status(not rls_off, phase, True), str(rls_off)))
    guc = _one(cur, "SELECT count(*) FROM pg_policies WHERE schemaname = 'cv_analyzer' AND (qual LIKE '%app.current_role%' "
                    "OR with_check LIKE '%app.current_role%')")[0]
    checks.append(Check("no policy reads app.current_role", _status(guc == 0, phase, True), f"{guc} policies"))

    secret_functions = dict(_rows(cur, "SELECT proname, pg_get_function_identity_arguments(oid) FROM pg_proc "
                                       "WHERE pronamespace = 'cv_analyzer'::regnamespace"))
    for signature, role in G.FUNCTION_EXECUTE.items():
        name = signature.split("(")[0]
        if name not in secret_functions:
            checks.append(Check(f"secret accessor {name} exists", _status(False, phase, True), "created by migration 099"))
            continue
        bad = [r for r in RUNTIME if _one(cur, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (r, f"cv_analyzer.{signature}"))[0]
               != (r == role)]
        checks.append(Check(f"secret accessor {name} is executable only by {role}", _status(not bad, phase, True), str(bad)))

    defaults = {r[0]: r[1] for r in _rows(cur, """
        SELECT r.rolname, s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole WHERE s.setdatabase = 0""")}
    no_path = [name for name in RUNTIME if not any(x.replace(" ", "").startswith("search_path=cv_analyzer,public")
                                                   for x in (defaults.get(name) or []))]
    checks.append(Check("runtime roles default their search_path", _status(not no_path, phase, True), str(no_path)))
    return checks


def _legacy_lineage_checks(cur, phase: str) -> list[Check]:
    """For a database converted from the legacy production lineage (db/legacy/upgrade.py): the evidence table is consistent with the
    committed reference, the ledger rows it describes exist with the committed checksums, and the legacy-only tables are sealed."""
    from db.legacy import lineage as L
    out: list[Check] = []
    rows = _rows(cur, "SELECT item_kind, item, legacy_sha256, main_sha256, proof FROM cv_analyzer.schema_migration_legacy_evidence "
                      "WHERE lineage = %s", (L.LINEAGE_NAME,))
    legacy = {r[1]: r[2] for r in rows if r[0] == "legacy_migration"}
    expected = {f["file"]: f["sha256"] for f in L.legacy_files()}
    out.append(Check("legacy lineage: every legacy migration is recorded with its source checksum",
                     PASS if legacy == expected else FAIL, f"recorded {len(legacy)} of {len(expected)}"))
    out.append(Check("legacy lineage: the conversion is recorded",
                     PASS if any(r[0] == "conversion" for r in rows) else FAIL, ""))
    ledger = {r[0]: r[1] for r in _rows(cur, "SELECT filename, sha256 FROM cv_analyzer.schema_migrations")}
    baselined = [(r[1], r[3]) for r in rows if r[0] == "baselined_migration"]
    wrong = [name for name, sha in baselined if ledger.get(name) != sha]
    out.append(Check("legacy lineage: baselined migrations match their ledger rows", FAIL if wrong else PASS, str(wrong[:5])))
    attested = sorted(r[1] for r in rows if r[4] == "attested")
    out.append(Check("legacy lineage: files accepted without a structural or data proof", PASS if attested == sorted(L.ATTESTED) else FAIL,
                     f"attested={attested}"))
    relations = {r[0]: r[1] for r in _rows(cur, "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                                "WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r'")}
    unsealed = [t for t in G.LEGACY_PRESERVED_TABLES if t in relations and not relations[t]]
    out.append(Check("legacy-only tables keep row-level security enabled", FAIL if unsealed else PASS, str(unsealed)))
    return out


def _exit_code(checks: list[Check]) -> int:
    if any(c.status == FAIL for c in checks):
        return 1
    if any(c.status == UNKNOWN for c in checks):
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight for an existing cv_analyzer database.")
    parser.add_argument("--phase", choices=PHASES, default="after",
                        help="before: the database before the procedure (procedure-owned gaps are WARN). after: every check must pass.")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    url = os.environ.get("PREFLIGHT_DATABASE_URL", "")
    if not url:
        print("PREFLIGHT_DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    except psycopg2.Error as exc:
        print(f"UNKNOWN: cannot connect: {redact(exc, url)}", file=sys.stderr)
        return 2
    conn.autocommit = False
    try:
        conn.set_session(readonly=True)
        checks = run(conn, phase=args.phase)
    except psycopg2.Error as exc:
        print(f"UNKNOWN: preflight could not complete: {redact(exc, url)}", file=sys.stderr)
        return 2
    finally:
        conn.rollback()
        conn.close()
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for c in checks:
            print(f"{c.status:<8} {c.name}  {c.detail}".rstrip())
    return _exit_code(checks)


if __name__ == "__main__":
    raise SystemExit(main())
