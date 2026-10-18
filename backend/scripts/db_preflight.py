"""Read-only preflight for an existing cv_analyzer database, before any role, ownership or grant change.

It inspects the catalogue and the migration ledger and reports PASS or FAIL for each check. It never writes:

  * the session is read-only (default_transaction_read_only=on and SET TRANSACTION READ ONLY on every statement);
  * the login used should be a read-only inspection login, never a runtime role and never a superuser when avoidable.

Exit status: 0 when every check passes; 1 when a check fails (incompatible or incomplete state); 2 when the state is unknown or the
connection is not read-only. Any non-zero status means the operator procedure must stop.

    PREFLIGHT_DATABASE_URL=postgresql://<inspection login>@host/db python -m scripts.db_preflight [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass

import psycopg2

from db import runtime_grants as G

RUNTIME = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")
MIGRATOR = "cv_migrator"
OPERATOR = "cv_migration_operator"
SECURITY_DEFINER = ("get_platform_secret(text)", "set_platform_secret(text, text, text, text, text)")
TENANT_TABLES_REQUIRING_RLS = (
    "applications", "application_files", "application_scores", "audit_logs", "email_ingest_log", "job_criteria", "jobs",
    "users", "tenants", "user_invites", "client_organizations", "agency_user_clients", "tenant_usage_monthly", "job_campaigns",
)


@dataclass
class Check:
    name: str
    status: str          # PASS, FAIL or UNKNOWN
    detail: str = ""


def _one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def _rows(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def run(conn) -> list[Check]:
    checks: list[Check] = []
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")

    # 1. The application schema and the ledger exist.
    schema = _one(cur, "SELECT count(*) FROM pg_namespace WHERE nspname = 'cv_analyzer'")[0]
    checks.append(Check("schema cv_analyzer exists", "PASS" if schema == 1 else "UNKNOWN", f"found {schema}"))
    if schema != 1:
        return checks
    ledger = _one(cur, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NOT NULL")[0]
    checks.append(Check("migration ledger exists", "PASS" if ledger else "UNKNOWN", ""))
    if ledger:
        applied = {r[0] for r in _rows(cur, "SELECT filename FROM cv_analyzer.schema_migrations WHERE status = 'applied'")}
        from db.runner.manifest import MANIFEST
        expected = {e.file for e in MANIFEST if e.superseded_by is None}
        missing = sorted(expected - applied)
        extra = sorted(applied - {e.file for e in MANIFEST})
        checks.append(Check("ledger matches the manifest", "PASS" if not missing and not extra else "FAIL",
                            f"missing={missing[:5]} unknown={extra[:5]}"))

    # 2. Roles: the migrator owns, the operator is only a member that can SET ROLE, runtime roles are hardened.
    roles = {r[0]: r for r in _rows(cur, "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit, rolcanlogin "
                                         "FROM pg_roles WHERE rolname = ANY(%s)", (list(RUNTIME) + [MIGRATOR, OPERATOR],))}
    for name in RUNTIME:
        r = roles.get(name)
        if r is None:
            checks.append(Check(f"runtime role {name} exists", "FAIL", "missing: run db/ops/0004 after review"))
            continue
        ok = (not r[1] and not r[2] and not r[3] and not r[4] and not r[5])
        checks.append(Check(f"runtime role {name} is hardened", "PASS" if ok else "FAIL",
                            f"super={r[1]} bypassrls={r[2]} createdb={r[3]} createrole={r[4]} inherit={r[5]}"))
    m = roles.get(MIGRATOR)
    checks.append(Check("migrator is NOLOGIN and not a superuser",
                        "PASS" if m and not m[6] and not m[1] and not m[2] else "FAIL", ""))
    op = roles.get(OPERATOR)
    checks.append(Check("operator is a login that is not a superuser",
                        "PASS" if op and op[6] and not op[1] and not op[2] else "FAIL", ""))
    memberships = _rows(cur, """
        SELECT r.rolname, m.roleid::regrole::text, m.set_option, m.inherit_option
        FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
        WHERE r.rolname = ANY(%s) OR m.roleid::regrole::text = ANY(%s)""", (list(RUNTIME) + [OPERATOR], list(RUNTIME) + [OPERATOR]))
    bad = [m_ for m_ in memberships if not (m_[0] == OPERATOR and m_[1] == MIGRATOR and m_[2] and not m_[3])]
    checks.append(Check("role memberships: only operator -> migrator (SET only, no INHERIT)", "PASS" if not bad else "FAIL",
                        f"unexpected={bad[:5]}"))

    # 3. Ownership: everything in the schema is owned by the migrator. Anything else stops the procedure.
    owners = _rows(cur, """
        SELECT c.relkind, c.relname, pg_get_userbyid(c.relowner) FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','S','v','m','p')""")
    foreign = [(k, n, o) for k, n, o in owners if o != MIGRATOR]
    checks.append(Check("every relation is owned by the migrator", "PASS" if not foreign else "FAIL",
                        f"{len(foreign)} not owned by {MIGRATOR}: {foreign[:5]}"))
    funcs = _rows(cur, """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid), pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'cv_analyzer'""")
    foreign_f = [f for f in funcs if f[2] != MIGRATOR]
    checks.append(Check("every routine is owned by the migrator", "PASS" if not foreign_f else "FAIL",
                        f"not owned by {MIGRATOR}: {foreign_f[:5]}"))

    # 4. Privileges: the live grants must equal the matrix exactly. An extra grant is a failure, not a warning.
    extra, missing = [], []
    for table in _rows(cur, "SELECT tablename FROM pg_tables WHERE schemaname = 'cv_analyzer'"):
        t = table[0]
        if t not in G.MATRIX and t not in ("platform_secrets", "platform_secret_readable", "schema_migrations"):
            checks.append(Check(f"table {t} has a matrix entry", "FAIL", "add it to db/runtime_grants.py after review"))
            continue
        for role in RUNTIME:
            for verb, privilege in G.VERB_PRIVILEGES.items():
                granted = _one(cur, "SELECT has_table_privilege(%s, %s, %s)", (role, f"cv_analyzer.{t}", privilege))[0]
                wanted = privilege in G.expected(t, role)
                if granted and not wanted:
                    extra.append(f"{role} {privilege} {t}")
                if wanted and not granted:
                    missing.append(f"{role} {privilege} {t}")
    checks.append(Check("table privileges equal the matrix (no extra)", "PASS" if not extra else "FAIL", str(extra[:8])))
    checks.append(Check("table privileges equal the matrix (none missing)", "PASS" if not missing else "FAIL", str(missing[:8])))

    # 5. PUBLIC holds nothing on the schema, its tables, or its routines.
    public_rows = _one(cur, """
        SELECT (SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema = 'cv_analyzer' AND grantee = 'PUBLIC')
             + (SELECT count(*) FROM information_schema.routine_privileges WHERE routine_schema = 'cv_analyzer' AND grantee = 'PUBLIC')""")[0]
    checks.append(Check("PUBLIC holds no table or routine privilege", "PASS" if public_rows == 0 else "FAIL", f"{public_rows} grants"))

    # 6. Row-level security: enabled on the tenant tables, and no policy reads the role variable any more.
    rls_off = [r[0] for r in _rows(cur, """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'cv_analyzer' AND c.relname = ANY(%s) AND NOT c.relrowsecurity""", (list(TENANT_TABLES_REQUIRING_RLS),))]
    checks.append(Check("row-level security enabled on tenant tables", "PASS" if not rls_off else "FAIL", str(rls_off)))
    guc = _one(cur, "SELECT count(*) FROM pg_policies WHERE schemaname = 'cv_analyzer' AND (qual LIKE '%app.current_role%' "
                    "OR with_check LIKE '%app.current_role%')")[0]
    checks.append(Check("no policy reads app.current_role", "PASS" if guc == 0 else "FAIL", f"{guc} policies"))

    # 7. Secret accessors: owned by the migrator, executable only as the matrix says.
    for signature, role in G.FUNCTION_EXECUTE.items():
        for r in RUNTIME:
            granted = _one(cur, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (r, f"cv_analyzer.{signature}"))[0]
            if granted != (r == role):
                checks.append(Check(f"{r} execute on {signature}", "FAIL", f"granted={granted}"))
    checks.append(Check("secret accessors executable only as the matrix states", "PASS" if not any(
        c.status == "FAIL" and " execute on " in c.name for c in checks) else "FAIL", ""))

    # 8. Role defaults: the schema path is stored on each runtime role.
    defaults = {r[0]: r[1] for r in _rows(cur, """
        SELECT r.rolname, s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole WHERE s.setdatabase = 0""")}
    no_path = [name for name in RUNTIME if not any(v and any(x.startswith("search_path=") for x in v) for k, v in defaults.items() if k == name)]
    checks.append(Check("runtime roles default their search_path", "PASS" if not no_path else "FAIL", str(no_path)))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight for an existing cv_analyzer database.")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    url = os.environ.get("PREFLIGHT_DATABASE_URL", "")
    if not url:
        print("PREFLIGHT_DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = psycopg2.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    conn.autocommit = False
    try:
        conn.set_session(readonly=True)
        checks = run(conn)
    except psycopg2.Error as exc:
        print(f"UNKNOWN: preflight could not complete: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.rollback()
        conn.close()
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for c in checks:
            print(f"{c.status:<8} {c.name}  {c.detail}")
    if any(c.status == "UNKNOWN" for c in checks):
        return 2
    return 0 if all(c.status == "PASS" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
