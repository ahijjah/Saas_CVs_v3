"""The privilege manifest (backend/db/privilege_manifest.json) is the justification for every runtime grant.

Three things must agree: the matrix (db/runtime_grants.py, the source of truth), the manifest (each grant with its production consumers),
and the live catalogue after the full chain and the operator scripts. Any undeclared live grant, any declared grant that is not live,
any excessive grant, and any consumer that is missing or no longer reachable fails the suite.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from db import privilege_consumers as C  # noqa: E402
from db import runtime_grants as G  # noqa: E402

MANIFEST = BACKEND / "db" / "privilege_manifest.json"
RUNTIME = G.RUNTIME_ROLES


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def expected_tuples(manifest: dict) -> set[tuple[str, str, str]]:
    """(object, role, privilege) for every grant the manifest declares. Column grants name the object as table.column."""
    out: set[tuple[str, str, str]] = set()
    for cell in manifest["cells"]:
        for privilege in cell["privileges"]:
            out.add((cell["object"], cell["role"], privilege))
    for column in manifest["columns"]:
        for name in column["columns"]:
            out.add((f"{column['object']}.{name}", column["role"], "SELECT"))
    for function in manifest["functions"]:
        out.add((function["object"], function["role"], "EXECUTE"))
    for sequence in manifest["sequences"]:
        out.add((sequence["object"], sequence["role"], "USAGE"))
    for schema in manifest["schemas"]:
        out.add((schema["object"], schema["role"], "USAGE"))
    return out


def live_tuples(conn) -> set[tuple[str, str, str]]:
    """(object, role, privilege) for every runtime-role grant in the catalogue, read from the ACLs. PUBLIC grants are included, so a grant
    to PUBLIC shows up as an undeclared tuple."""
    cur = conn.cursor()
    runtime = list(RUNTIME)
    out: set[tuple[str, str, str]] = set()
    cur.execute("""
        SELECT c.relname, r.rolname, a.privilege_type
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r', 'p') AND (r.rolname = ANY(%s) OR a.grantee = 0)""", (runtime,))
    for name, role, privilege in cur.fetchall():
        out.add((name, role or "PUBLIC", privilege))
    cur.execute("""
        SELECT c.relname, r.rolname, a.privilege_type
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
        LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'S' AND (r.rolname = ANY(%s) OR a.grantee = 0)""", (runtime,))
    for name, role, privilege in cur.fetchall():
        out.add((name, role or "PUBLIC", privilege))
    cur.execute("""
        SELECT a.attname, r.rolname, p.privilege_type
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace,
             aclexplode(a.attacl) p LEFT JOIN pg_roles r ON r.oid = p.grantee
        WHERE n.nspname = 'cv_analyzer' AND c.relname = 'platform_secrets' AND a.attacl IS NOT NULL
          AND (r.rolname = ANY(%s) OR p.grantee = 0)""", (runtime,))
    for column, role, privilege in cur.fetchall():
        out.add((f"platform_secrets.{column}", role or "PUBLIC", privilege))
    cur.execute("""
        SELECT p.proname || '(' || oidvectortypes(p.proargtypes) || ')' AS sig, r.rolname, a.privilege_type
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) a
        LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname = 'cv_analyzer' AND (r.rolname = ANY(%s) OR a.grantee = 0)""", (runtime,))
    for signature, role, privilege in cur.fetchall():
        out.add((signature, role or "PUBLIC", privilege))
    cur.execute("""
        SELECT n.nspname, r.rolname, a.privilege_type
        FROM pg_namespace n, aclexplode(n.nspacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname = 'cv_analyzer' AND (r.rolname = ANY(%s) OR a.grantee = 0)""", (runtime,))
    for name, role, privilege in cur.fetchall():
        out.add((name, role or "PUBLIC", privilege))
    return out


def _shape(tuples: set[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    """Normalise function signatures: the manifest writes `name(args)` as the matrix does."""
    return {(obj.replace("public.", "").replace("cv_analyzer.", ""), role, priv) for obj, role, priv in tuples}


def diff(declared: set, live: set) -> tuple[set, set]:
    """(undeclared, missing): live grants the manifest does not justify, and declared grants that are not live."""
    return _shape(live) - _shape(declared), _shape(declared) - _shape(live)


# ---- the manifest, against the matrix and the code ----

def test_the_manifest_declares_exactly_the_matrix():
    manifest = _manifest()
    fresh = C.build()
    assert {(c["object"], c["role"], tuple(c["privileges"])) for c in manifest["cells"]} == \
        {(c["object"], c["role"], tuple(c["privileges"])) for c in fresh["cells"]}
    assert expected_tuples(manifest) == expected_tuples(fresh)


def test_every_grant_has_at_least_one_production_consumer_and_a_path():
    for section in ("cells", "columns", "functions", "sequences", "schemas"):
        for item in _manifest()[section]:
            assert item["consumers"], f"{section} {item.get('object')} {item.get('role')} has no consumer"
            assert item["path"], f"{section} {item.get('object')} {item.get('role')} has no path"


def test_each_declared_consumer_exists_and_is_reachable_from_its_login_and_refers_to_the_object():
    """The consumers are recomputed from the code. A declared consumer that is not in the recomputed set is stale or missing."""
    manifest = _manifest()
    fresh = C.build()
    known = C.modules()
    for section in ("cells", "columns", "functions", "sequences"):
        fresh_by_key = {(i["object"], i["role"]): set(i["consumers"]) for i in fresh[section]}
        for item in manifest[section]:
            key = (item["object"], item["role"])
            for consumer in item["consumers"]:
                if consumer == "database.py":
                    assert (BACKEND / consumer).exists()
                    continue
                assert consumer in known, f"{key}: consumer {consumer} does not exist"
                assert consumer in fresh_by_key[key], f"{key}: {consumer} is not reachable from the {item['role']} login"
    for item in manifest["schemas"]:
        assert (BACKEND / "database.py").exists()


def test_the_manifest_lists_no_consumer_that_has_stopped_referring_to_the_object():
    fresh = C.build()
    manifest = _manifest()
    for section in ("cells", "functions", "sequences"):
        fresh_map = {(i["object"], i["role"]): set(i["consumers"]) for i in fresh[section]}
        for item in manifest[section]:
            stale = set(item["consumers"]) - fresh_map[(item["object"], item["role"])] - {"database.py"}
            assert not stale, f"{item['object']} {item['role']}: stale consumers {sorted(stale)}"


def test_the_sequence_is_granted_to_the_job_path_only_and_no_sequence_is_granted_in_bulk():
    manifest = _manifest()
    assert [(s["object"], s["role"], s["consumers"]) for s in manifest["sequences"]] == [
        ("job_code_seq", G.APP, ["routers.jobs"])]
    script = G.generate_sql()
    for line in script.splitlines():
        if "ALL SEQUENCES" in line:
            assert line.startswith("REVOKE"), line


def test_the_secret_functions_are_declared_with_their_only_consumers():
    functions = {f["object"]: f for f in _manifest()["functions"]}
    assert functions["get_platform_secret(text)"]["role"] == G.SECRETS
    assert functions["get_platform_secret(text)"]["consumers"] == ["services.runtime_config"]
    assert functions["set_platform_secret(text, text, text, text, text)"]["role"] == G.ADMIN
    assert functions["set_platform_secret(text, text, text, text, text)"]["consumers"] == ["routers.platform_secrets"]


def test_the_append_only_logs_grant_no_read_or_change_to_the_tenant_or_worker_login():
    grants = expected_tuples(_manifest())
    for table in ("audit_logs", "ai_usage_log"):
        for role in (G.APP, G.WORKER):
            privileges = {p for obj, r, p in grants if obj == table and r == role}
            assert privileges <= {"INSERT"}, (table, role, privileges)
        assert {p for obj, r, p in grants if obj == table and r == G.ADMIN} == {"SELECT"}


def test_no_runtime_login_is_granted_the_platform_secrets_table_or_its_value_column():
    grants = expected_tuples(_manifest())
    assert not [g for g in grants if g[0] == "platform_secrets" and g[1] in RUNTIME]
    assert not [g for g in grants if g[0] == "platform_secrets.value"]


def test_the_generated_operator_script_is_the_matrix_output():
    script = (BACKEND / "db" / "ops" / "0005_runtime_privileges.sql.template").read_text(encoding="utf-8")
    assert script == G.generate_sql()


# ---- the live catalogue, after the full chain and the operator scripts ----

@pytest.fixture(scope="module")
def hardened_for_justifications():
    import realdb_helper as H
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


def _superuser(built):
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name)
    return conn


def test_the_live_catalogue_matches_the_manifest_exactly(hardened_for_justifications):
    conn = _superuser(hardened_for_justifications)
    try:
        undeclared, missing = diff(expected_tuples(_manifest()), live_tuples(conn))
    finally:
        conn.close()
    assert not undeclared, f"undeclared or excessive live grants: {sorted(undeclared)}"
    assert not missing, f"declared grants that are not live: {sorted(missing)}"


def test_the_detector_reports_an_undeclared_grant_and_is_clean_once_revoked(hardened_for_justifications):
    """Self-test: a grant that the manifest does not declare must be reported, then removed."""
    conn = _superuser(hardened_for_justifications)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("GRANT SELECT ON cv_analyzer.audit_logs TO cv_app")
        undeclared, _ = diff(expected_tuples(_manifest()), live_tuples(conn))
        assert ("audit_logs", "cv_app", "SELECT") in undeclared
    finally:
        cur.execute("REVOKE SELECT ON cv_analyzer.audit_logs FROM cv_app")
    undeclared, missing = diff(expected_tuples(_manifest()), live_tuples(conn))
    conn.close()
    assert not undeclared and not missing


def test_the_detector_reports_a_declared_grant_that_is_missing(hardened_for_justifications):
    conn = _superuser(hardened_for_justifications)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("REVOKE SELECT ON cv_analyzer.jobs FROM cv_app")
        _, missing = diff(expected_tuples(_manifest()), live_tuples(conn))
        assert ("jobs", "cv_app", "SELECT") in missing
    finally:
        cur.execute("GRANT SELECT ON cv_analyzer.jobs TO cv_app")
    conn.close()


def test_the_live_append_only_logs_hold_only_their_insert_grant(hardened_for_justifications):
    conn = _superuser(hardened_for_justifications)
    try:
        live = live_tuples(conn)
    finally:
        conn.close()
    for table in ("audit_logs", "ai_usage_log"):
        for role in (G.APP, G.WORKER):
            assert {p for obj, r, p in live if obj == table and r == role} <= {"INSERT"}


# ---- verb-level evidence: a privilege the code never exercises is excessive ----

_VERB_SQL = {
    "SELECT": r"\bFROM\s+(?:cv_analyzer\.)?{t}\b|\bJOIN\s+(?:cv_analyzer\.)?{t}\b",
    "INSERT": r"\bINSERT\s+INTO\s+(?:cv_analyzer\.)?{t}\b",
    "UPDATE": r"\bUPDATE\s+(?:cv_analyzer\.)?{t}\b|\bON\s+CONFLICT\b",
    "DELETE": r"\bDELETE\s+FROM\s+(?:cv_analyzer\.)?{t}\b",
}


def test_every_declared_table_privilege_is_exercised_by_sql_in_one_of_its_consumers():
    """A declared table privilege must be used by SQL that the consumer modules contain. The check is textual: it finds the statement
    kind for the table. ORM access would need an explicit entry here, and there is none."""
    import re
    known = C.modules()
    for cell in _manifest()["cells"]:
        if cell["kind"] != "table":
            continue
        texts = [known[m].read_text(encoding="utf-8") for m in cell["consumers"] if m in known]
        for privilege in cell["privileges"]:
            pattern = re.compile(_VERB_SQL[privilege].format(t=re.escape(cell["object"])), re.I)
            assert any(pattern.search(t) for t in texts), (
                f"{cell['role']} holds {privilege} on {cell['object']} but no declared consumer issues it")


def _dependency_calls(dependant) -> set:
    """Every callable in a route's dependency graph, through sub-dependencies."""
    calls = set()
    stack = list(dependant.dependencies)
    while stack:
        item = stack.pop()
        calls.add(item.call)
        stack.extend(item.dependencies)
    return calls


def _checks_super_admin(call) -> bool:
    """True for the checker require_role builds for super_admin (its closure holds the role tuple)."""
    return any(isinstance(cell.cell_contents, tuple) and "super_admin" in cell.cell_contents
               for cell in (getattr(call, "__closure__", None) or ()))


def test_every_super_admin_route_uses_the_administration_session():
    """Introspected from the mounted routes, not the source text: a route that requires super_admin must not take the tenant session
    (get_db, the cv_app login) as its own session. The tenant login holds no write on the platform tables, so such a route fails at run
    time."""
    import importlib
    from fastapi.routing import APIRoute
    from database import get_db
    from auth.dependencies import get_platform_db
    offenders = []
    checked = 0
    for path in sorted((BACKEND / "routers").glob("*.py")):
        module = importlib.import_module(f"routers.{path.stem}")
        for route in getattr(module, "router", None).routes if hasattr(module, "router") else []:
            if not isinstance(route, APIRoute):
                continue
            calls = _dependency_calls(route.dependant)
            if not any(_checks_super_admin(call) for call in calls):
                continue
            checked += 1
            # The route's own session parameter. get_current_user reads the caller's identity through the tenant login by design, so
            # sub-dependencies are not the session the route writes through: only the route's direct dependencies are.
            own = {item.call for item in route.dependant.dependencies}
            if get_db in own:
                offenders.append(f"{path.stem}:{route.name}")
    assert checked > 0, "no super-admin route was found; the scan is not looking at the routers"
    assert not offenders, offenders
