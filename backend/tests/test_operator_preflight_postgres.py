"""The read-only preflight (scripts/db_preflight.py): a safe state passes, each unsafe state is reported by the check that owns it, the
exit status follows the blocking results, and credentials never appear in its output.

Each unsafe state is created on the disposable database and restored in a finally block, so the checks that follow see the safe state.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import realdb_helper as H  # noqa: E402
from scripts import db_preflight as P  # noqa: E402
from security_fixtures import bootstrap  # noqa: E402


@pytest.fixture(scope="module")
def built():
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


def _inspect(built, phase: str) -> dict[str, P.Check]:
    """Run the preflight as the bootstrap login, the inspection login the procedure names for an existing database."""
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name)
    conn.autocommit = False
    try:
        conn.set_session(readonly=True)
        checks = P.run(conn, phase=phase)
    finally:
        conn.rollback()
        conn.close()
    return {c.name: c for c in checks}


def _statuses(checks) -> set[str]:
    return {c.status for c in checks.values()}


@contextlib.contextmanager
def changed(built, *statements: str):
    """Apply statements as the bootstrap superuser, then undo them: each undo is a fixed statement in the same order reversed."""
    conn = bootstrap(built)
    cur = conn.cursor()
    applied = []
    try:
        for statement, undo in statements:
            cur.execute(statement)
            applied.append(undo)
        yield
    finally:
        for undo in reversed(applied):
            cur.execute(undo)
        conn.close()


# ---- the safe state ----

def test_the_procedure_result_passes_every_check_in_the_after_phase(built):
    checks = _inspect(built, "after")
    failing = {n: c for n, c in checks.items() if c.status != P.PASS}
    assert not failing, failing
    assert P._exit_code(list(checks.values())) == 0


# ---- unsafe states: each is reported by the check that owns it ----

@pytest.mark.parametrize("statement, undo, check", [
    ("ALTER ROLE cv_app SUPERUSER", "ALTER ROLE cv_app NOSUPERUSER", "runtime role cv_app is hardened"),
    ("ALTER ROLE cv_worker BYPASSRLS", "ALTER ROLE cv_worker NOBYPASSRLS", "runtime role cv_worker is hardened"),
    ("GRANT SELECT ON cv_analyzer.audit_logs TO cv_app", "REVOKE SELECT ON cv_analyzer.audit_logs FROM cv_app",
     "table privileges hold no grant beyond the matrix"),
    ("REVOKE SELECT ON cv_analyzer.jobs FROM cv_app", "GRANT SELECT ON cv_analyzer.jobs TO cv_app",
     "table privileges include every matrix grant"),
    ("GRANT SELECT ON cv_analyzer.jobs TO PUBLIC", "REVOKE SELECT ON cv_analyzer.jobs FROM PUBLIC",
     "PUBLIC holds no table or routine privilege"),
    ("ALTER TABLE cv_analyzer.jobs OWNER TO postgres", "ALTER TABLE cv_analyzer.jobs OWNER TO cv_migrator",
     "every relation is owned by the migrator"),
    ("ALTER TABLE cv_analyzer.jobs DISABLE ROW LEVEL SECURITY", "ALTER TABLE cv_analyzer.jobs ENABLE ROW LEVEL SECURITY",
     "row-level security enabled on tenant tables"),
    ("GRANT EXECUTE ON FUNCTION cv_analyzer.get_platform_secret(text) TO PUBLIC",
     "REVOKE EXECUTE ON FUNCTION cv_analyzer.get_platform_secret(text) FROM PUBLIC",
     "secret accessor get_platform_secret is executable only by cv_secrets"),
    ("GRANT cv_app TO cv_worker", "REVOKE cv_app FROM cv_worker", "no runtime role is related to another role"),
    ("REVOKE cv_migrator FROM cv_migration_operator", "GRANT cv_migrator TO cv_migration_operator WITH INHERIT FALSE, SET TRUE",
     "operator may only SET ROLE to the migrator"),
    ("ALTER ROLE cv_app RESET search_path", "ALTER ROLE cv_app SET search_path = cv_analyzer, public",
     "runtime roles default their search_path"),
])
def test_an_unsafe_state_fails_the_check_that_owns_it_in_the_after_phase(built, statement, undo, check):
    with changed(built, (statement, undo)):
        checks = _inspect(built, "after")
        assert checks[check].status == P.FAIL, (check, checks[check])
        assert P._exit_code(list(checks.values())) == 1
    assert P._exit_code(list(_inspect(built, "after").values())) == 0, "the restored state must pass again"


@pytest.mark.parametrize("statement, undo, check", [
    ("GRANT SELECT ON cv_analyzer.audit_logs TO cv_app", "REVOKE SELECT ON cv_analyzer.audit_logs FROM cv_app",
     "table privileges hold no grant beyond the matrix"),
    ("ALTER TABLE cv_analyzer.jobs OWNER TO postgres", "ALTER TABLE cv_analyzer.jobs OWNER TO cv_migrator",
     "every relation is owned by the migrator"),
])
def test_a_procedure_owned_gap_is_a_warning_before_the_procedure(built, statement, undo, check):
    """Before the procedure these gaps are expected: the operator scripts revoke grants and transfer ownership. They are reported, not
    blocking."""
    with changed(built, (statement, undo)):
        checks = _inspect(built, "before")
        assert checks[check].status == P.WARN
        assert P._exit_code(list(checks.values())) == 0


def test_a_runtime_superuser_is_blocking_even_before_the_procedure(built):
    """No procedure-owned gap excuses a superuser runtime login: it is unsafe in every phase."""
    with changed(built, ("ALTER ROLE cv_app SUPERUSER", "ALTER ROLE cv_app NOSUPERUSER")):
        checks = _inspect(built, "before")
        assert checks["runtime role cv_app is hardened"].status == P.FAIL
        assert P._exit_code(list(checks.values())) == 1


def test_an_applied_migration_whose_file_changed_is_blocking(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT filename, sha256 FROM cv_analyzer.schema_migrations WHERE status = 'applied' ORDER BY filename LIMIT 1")
    filename, digest = cur.fetchone()
    cur.execute("UPDATE cv_analyzer.schema_migrations SET sha256 = repeat('0', 64) WHERE filename = %s", (filename,))
    try:
        checks = _inspect(built, "before")
        assert checks["applied migrations match their checksums"].status == P.FAIL
        assert P._exit_code(list(checks.values())) == 1
    finally:
        cur.execute("UPDATE cv_analyzer.schema_migrations SET sha256 = %s WHERE filename = %s", (digest, filename))
        conn.close()


def test_an_unknown_applied_migration_is_blocking(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status) VALUES ('999_unknown.sql', %s, 'applied')",
                ("a" * 64,))
    try:
        checks = _inspect(built, "before")
        assert checks["every applied migration is known to this release"].status == P.FAIL
    finally:
        cur.execute("DELETE FROM cv_analyzer.schema_migrations WHERE filename = '999_unknown.sql'")
        conn.close()


def test_a_pending_migration_is_a_warning_before_and_blocking_after(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT filename, sha256, status, replaced_by FROM cv_analyzer.schema_migrations WHERE filename = "
                "'101_job_code_sequence.sql'")
    row = cur.fetchone()
    cur.execute("DELETE FROM cv_analyzer.schema_migrations WHERE filename = '101_job_code_sequence.sql'")
    try:
        before = _inspect(built, "before")
        assert before["pending migrations of this release"].status == P.WARN
        assert P._exit_code(list(before.values())) == 0
        after = _inspect(built, "after")
        assert after["pending migrations of this release"].status == P.FAIL
        assert P._exit_code(list(after.values())) == 1
    finally:
        cur.execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status, replaced_by) VALUES (%s, %s, %s, %s)", row)
        conn.close()


def test_a_table_without_a_matrix_entry_is_blocking(built):
    with changed(built, ("CREATE TABLE cv_analyzer.probe_unmatrixed (id int)", "DROP TABLE cv_analyzer.probe_unmatrixed")):
        checks = _inspect(built, "before")
        assert checks["table probe_unmatrixed has a matrix entry"].status == P.FAIL
        assert P._exit_code(list(checks.values())) == 1


def test_a_missing_ledger_is_unknown_and_exits_two(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("ALTER TABLE cv_analyzer.schema_migrations RENAME TO schema_migrations_moved")
    try:
        checks = _inspect(built, "before")
        assert checks["migration ledger exists"].status == P.UNKNOWN
        assert P._exit_code(list(checks.values())) == 2
    finally:
        cur.execute("ALTER TABLE cv_analyzer.schema_migrations_moved RENAME TO schema_migrations")
        conn.close()


def test_an_unknown_state_is_not_reported_as_a_pass(built):
    """A state the preflight cannot establish must not exit 0 (UNKNOWN is blocking)."""
    assert P._exit_code([P.Check("x", P.PASS), P.Check("y", P.UNKNOWN)]) == 2
    assert P._exit_code([P.Check("x", P.WARN)]) == 0
    assert P._exit_code([P.Check("x", P.FAIL), P.Check("y", P.UNKNOWN)]) == 1


# ---- credentials and the command line ----

def test_the_redaction_removes_the_url_password_and_any_driver_password_pair():
    url = "postgresql://cv_app:S3cr3t-Value@db.internal/cv"
    message = 'connection failed: password authentication failed for "cv_app" (password=S3cr3t-Value) S3cr3t-Value'
    cleaned = P.redact(message, url)
    assert "S3cr3t-Value" not in cleaned
    assert "password=***" in cleaned


def test_the_command_line_never_prints_a_password_it_was_given(built, capsys, monkeypatch):
    secret = "wrong-but-present-secret"
    monkeypatch.setenv("PREFLIGHT_DATABASE_URL", f"postgresql://cv_app:{secret}@127.0.0.1:{built.cluster.port}/{built.name}")
    monkeypatch.setattr(P.psycopg2, "connect", _raise_auth_failure(secret))
    code = P.main([])
    captured = capsys.readouterr()
    assert code == 2
    assert secret not in captured.out + captured.err
    assert "***" in captured.err


def _raise_auth_failure(secret):
    def connect(*args, **kwargs):
        raise psycopg2.OperationalError(f'FATAL: password authentication failed (password={secret})')
    return connect


def test_the_command_line_requires_the_url_and_reports_missing_configuration(monkeypatch, capsys):
    monkeypatch.delenv("PREFLIGHT_DATABASE_URL", raising=False)
    assert P.main([]) == 2
    assert "PREFLIGHT_DATABASE_URL is not set" in capsys.readouterr().err


def test_the_json_report_is_machine_readable(built, capsys, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_DATABASE_URL", f"postgresql://postgres@127.0.0.1:{built.cluster.port}/{built.name}")
    code = P.main(["--phase", "after", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert all({"name", "status", "detail"} == set(item) for item in report)
    assert {item["status"] for item in report} == {"PASS"}


def test_the_preflight_writes_nothing(built):
    """The session is read-only: a write attempted inside the inspection transaction is refused by the database."""
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name)
    conn.autocommit = False
    conn.set_session(readonly=True)
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.Error) as err:
            cur.execute("CREATE TABLE cv_analyzer.probe_write (id int)")
        assert err.value.pgcode == "25006"   # read_only_sql_transaction
    finally:
        conn.rollback()
        conn.close()


def test_the_preflight_module_has_no_write_statements():
    source = (BACKEND / "scripts" / "db_preflight.py").read_text(encoding="utf-8")
    for word in ("INSERT INTO", "UPDATE cv_analyzer", "DELETE FROM", "CREATE ", "ALTER ", "GRANT ", "REVOKE ", "DROP "):
        assert word not in source, word
