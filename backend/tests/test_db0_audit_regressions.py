"""DB0 pre-merge audit regressions: scanner edge cases, checksum binding, lock lifecycle, atomicity under connection loss and
cancellation, dry-run state, operator-script rejection paths. Database tests use real PostgreSQL 16 sessions on the disposable
cluster and grant nothing to any role."""
from __future__ import annotations

import hashlib
import threading
import time

import psycopg2
import pytest

import realdb_helper as H
from db.runner import runner
from db.runner.manifest import MANIFEST, Entry
from db.runner.sqltext import SqlTextError, migration_body, top_level_statements
from test_db0_migration_runner import _entries, _ledger, _scalar, _write_dir, empty_database


# ── scanner edge cases ────────────────────────────────────────────────────────────────────────────────────────────────

def test_escape_string_with_backslash_quote_does_not_end_early_or_hide_a_split():
    sql = "SELECT E'it\\'s; COMMIT; x';\nSELECT 2;"
    assert [s.text for s in top_level_statements(sql)] == ["SELECT E'IT\\'S; COMMIT; X'", "SELECT 2"]
    assert migration_body(sql).began is False


def test_standard_string_treats_backslash_literally():
    # Without the E prefix the backslash is an ordinary character, so the literal 'a\' ends at the quote after it.
    sql = "SELECT 'a\\'; SELECT 2;"
    assert [s.text for s in top_level_statements(sql)] == ["SELECT 'A\\'", "SELECT 2"]


def test_dollar_sign_inside_an_identifier_is_not_a_dollar_quote():
    sql = "SELECT 1 AS foo$bar$;\nSELECT 2;"
    assert [s.text for s in top_level_statements(sql)] == ["SELECT 1 AS FOO$BAR$", "SELECT 2"]


def test_nested_block_comments_are_masked_as_postgresql_masks_them():
    sql = "/* outer /* inner */ still comment ; */ SELECT 1;"
    assert [s.text for s in top_level_statements(sql)] == ["SELECT 1"]


def test_unterminated_nested_block_comment_is_an_error():
    with pytest.raises(SqlTextError):
        top_level_statements("/* outer /* inner */ SELECT 1;")


@pytest.mark.parametrize("sql", [
    "\\set ON_ERROR_STOP on\nSELECT 1;",
    "SELECT 1;\n  \\echo hello",
    "SELECT 1;\n\\i other.sql",
])
def test_psql_meta_commands_are_rejected(sql):
    with pytest.raises(SqlTextError, match="meta-command"):
        migration_body(sql)


@pytest.mark.parametrize("sql", [
    "VACUUM cv_analyzer.jobs;",
    "CREATE DATABASE other;",
    "DROP DATABASE other;",
    "ALTER SYSTEM SET work_mem = '1MB';",
    "SET ROLE cv_migrator;",
    "RESET ROLE;",
    "SET SESSION AUTHORIZATION postgres;",
    "CREATE INDEX CONCURRENTLY ix ON cv_analyzer.jobs (title);",
    "DROP INDEX CONCURRENTLY cv_analyzer.ix;",
    "REINDEX INDEX CONCURRENTLY cv_analyzer.ix;",
    "REINDEX DATABASE cv_analyzer;",
])
def test_statements_that_cannot_run_in_a_transaction_or_change_role_are_rejected(sql):
    with pytest.raises(SqlTextError):
        migration_body(sql)


def test_concurrently_inside_a_function_body_is_not_a_statement_form():
    sql = "CREATE FUNCTION cv_analyzer.f() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;\n" \
          "DO $$ BEGIN PERFORM 1; /* CREATE INDEX CONCURRENTLY */ END $$;"
    assert migration_body(sql).sql.strip().startswith("CREATE FUNCTION")


def test_migration_036_shape_is_still_accepted():
    body = migration_body("SET search_path TO cv_analyzer;\n\nBEGIN;\nALTER TABLE tenants ADD COLUMN x int;\nCOMMIT;\n")
    assert body.began and body.committed
    assert "SET search_path" in body.sql and "ALTER TABLE tenants" in body.sql


# ── checksum is bound to the bytes that are executed ───────────────────────────────────────────────────────────────────

def test_a_file_rewritten_after_its_checksum_is_taken_is_executed_as_hashed(tmp_path, monkeypatch):
    original = "CREATE TABLE cv_analyzer.original_table (id int);"
    d = _write_dir(tmp_path, {"001_a.sql": original})
    entries = (Entry("001_a.sql"),)
    real_plan_run = runner._plan_run

    def rewrite_after_plan(*args, **kwargs):
        result = real_plan_run(*args, **kwargs)
        (d / "001_a.sql").write_text("CREATE TABLE cv_analyzer.rewritten_table (id int);", encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "_plan_run", rewrite_after_plan)
    with empty_database() as conn:
        runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.original_table') IS NOT NULL") is True
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.rewritten_table') IS NULL") is True
        recorded = _ledger(conn)
        assert recorded == {"001_a.sql": "applied"}
        cur = conn.cursor()
        cur.execute("SELECT sha256 FROM cv_analyzer.schema_migrations WHERE filename = '001_a.sql'")
        assert cur.fetchone()[0] == hashlib.sha256(original.encode("utf-8")).hexdigest()


# ── advisory lock lifecycle ────────────────────────────────────────────────────────────────────────────────────────────

def test_lock_is_session_level_deterministic_and_never_transaction_scoped():
    import inspect
    source = inspect.getsource(runner)
    assert "pg_advisory_xact_lock" not in source
    assert "hash(" not in source.replace("sha256(", "").replace("hashlib", "")
    assert isinstance(runner.LOCK_KEY, int) and runner.LOCK_KEY == 0x43564D49


def test_second_runner_is_refused_while_the_first_holds_the_lock_between_commits(tmp_path):
    """Runner A has committed migration 001 and is still inside migration 002. Runner B must be refused and must not record
    anything. Once A finishes, B finds nothing to do."""
    files = {
        "001_create_a.sql": "CREATE TABLE cv_analyzer.a (id int);",
        "002_slow.sql": "SELECT pg_sleep(4);\nCREATE TABLE cv_analyzer.b (id int);",
    }
    d = _write_dir(tmp_path, files)
    entries = _entries(files)
    with empty_database() as conn:
        dsn = conn.dsn
        a_errors: list[BaseException] = []

        def run_a():
            session = psycopg2.connect(dsn)
            session.autocommit = True
            try:
                runner.run(session, allow_fresh=True, directory=d, entries=entries)
            except BaseException as exc:          # surfaced to the main thread below
                a_errors.append(exc)
            finally:
                session.close()

        worker = threading.Thread(target=run_a)
        worker.start()
        observer = psycopg2.connect(dsn)
        observer.autocommit = True
        cur = observer.cursor()
        deadline = time.time() + 30
        while True:
            cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT count(*) FROM cv_analyzer.schema_migrations WHERE filename = '001_create_a.sql'")
                if cur.fetchone()[0] == 1:
                    break
            assert time.time() < deadline and worker.is_alive(), "runner A did not reach the second migration"
            time.sleep(0.05)
        cur.execute("SELECT count(*) FROM cv_analyzer.schema_migrations WHERE filename = '002_slow.sql'")
        assert cur.fetchone()[0] == 0, "runner A must still be inside migration 002"

        contender = psycopg2.connect(dsn)
        contender.autocommit = True
        with pytest.raises(runner.LockHeld):
            runner.run(contender, allow_fresh=True, directory=d, entries=entries)
        contender.close()
        cur.execute("SELECT count(*) FROM cv_analyzer.schema_migrations WHERE filename = '002_slow.sql'")
        assert cur.fetchone()[0] == 0

        worker.join(timeout=60)
        assert not worker.is_alive() and a_errors == []
        assert _ledger(conn) == {"001_create_a.sql": "applied", "002_slow.sql": "applied"}
        observer.close()
        follow_up = runner.run(conn, directory=d, entries=entries)
        assert follow_up.applied == []


def _wait_for_running_sleep(observer, application_name: str, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    cur = observer.cursor()
    while time.time() < deadline:
        cur.execute("SELECT pid FROM pg_stat_activity WHERE application_name = %s AND query LIKE '%%pg_sleep%%'",
                    (application_name,))
        row = cur.fetchone()
        if row:
            return row[0]
        time.sleep(0.05)
    raise AssertionError("the runner session never reached the sleeping statement")


@pytest.mark.parametrize("interrupt", ["terminate", "cancel"])
def test_connection_loss_or_cancellation_between_sql_and_ledger_leaves_no_trace(tmp_path, interrupt):
    files = {"001_slow.sql": "CREATE TABLE cv_analyzer.a (id int);\nSELECT pg_sleep(8);"}
    d = _write_dir(tmp_path, files)
    entries = _entries(files)
    name = f"db0_victim_{interrupt}"
    with empty_database() as conn:
        dsn = conn.dsn
        outcome: list[BaseException] = []

        def victim():
            session = psycopg2.connect(dsn, application_name=name)
            session.autocommit = True
            try:
                runner.run(session, allow_fresh=True, directory=d, entries=entries)
            except BaseException as exc:
                outcome.append(exc)
            finally:
                try:
                    session.close()
                except Exception:
                    pass

        worker = threading.Thread(target=victim)
        worker.start()
        observer = psycopg2.connect(dsn)
        observer.autocommit = True
        pid = _wait_for_running_sleep(observer, name)
        ocur = observer.cursor()
        if interrupt == "terminate":
            ocur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        else:
            ocur.execute("SELECT pg_cancel_backend(%s)", (pid,))
        worker.join(timeout=60)
        assert not worker.is_alive(), "the runner must return once its session is interrupted"
        assert outcome, "the interrupted run must report an error"
        assert _scalar(observer, "SELECT to_regclass('cv_analyzer.a') IS NULL") is True
        assert _ledger(observer).get("001_slow.sql") is None
        assert _scalar(observer, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted") == 0
        observer.close()


# ── dry-run: no writes, no lasting state, exact order ─────────────────────────────────────────────────────────────────

def _state(conn) -> tuple:
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations') IS NOT NULL")
    ledger_present = cur.fetchone()[0]
    rows = None
    if ledger_present:
        cur.execute("SELECT filename, sha256, status, coalesce(replaced_by, '') FROM cv_analyzer.schema_migrations "
                    "ORDER BY filename")
        rows = cur.fetchall()
    cur.execute("""SELECT c.relname, c.relkind, c.relpersistence FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'cv_analyzer' ORDER BY 1""")
    relations = cur.fetchall()
    cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")
    advisory = cur.fetchone()[0]
    return ledger_present, rows, relations, advisory


def test_dry_run_on_an_empty_database_changes_nothing_and_reports_the_exact_order(tmp_path):
    with empty_database() as conn:
        before = _state(conn)
        report = runner.run(conn, dry_run=True, allow_fresh=True)
        assert _state(conn) == before
        pending = [a.file for a in report.actions if a.kind == "apply"]
        assert pending == [e.file for e in MANIFEST if not e.superseded_by]
        by_file = {a.file: a for a in report.actions}
        assert by_file["010_platform_control.sql"].kind == "skip-replaced"
        assert "095_repair_010_platform_control.sql" in by_file["010_platform_control.sql"].reason
        assert by_file["095_repair_010_platform_control.sql"].reason == "replaces 010_platform_control.sql"
        assert pending.index("095_repair_010_platform_control.sql") < pending.index("024_plan_features_seed.sql")
        # Nothing held: a separate session can take and release the lock.
        probe = psycopg2.connect(conn.dsn)
        probe.autocommit = True
        cur = probe.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (runner.LOCK_KEY,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT pg_advisory_unlock(%s)", (runner.LOCK_KEY,))
        probe.close()


def test_dry_run_on_the_built_chain_changes_no_catalog_or_ledger_state():
    with H.built_chain() as db:
        before = _state(db.conn)
        report = runner.run(db.conn, dry_run=True)
        assert report.pending() == []
        assert _state(db.conn) == before


# ── ledger integrity: unknown and duplicate entries stop the run ──────────────────────────────────────────────────────

def test_duplicate_manifest_entry_stops_before_anything_runs(tmp_path):
    files = {"001_a.sql": "SELECT 1;"}
    d = _write_dir(tmp_path, files)
    with pytest.raises(runner.ManifestError, match="more than once"):
        runner.validate_manifest((Entry("001_a.sql"), Entry("001_a.sql")), d)


def test_ledger_row_for_a_file_not_in_the_repository_stops_the_run(tmp_path):
    files = {"001_a.sql": "CREATE TABLE cv_analyzer.a (id int);"}
    d = _write_dir(tmp_path, files)
    with empty_database() as conn:
        runner.run(conn, allow_fresh=True, directory=d, entries=_entries(files))
        conn.cursor().execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status) "
                              "VALUES ('999_ghost.sql', %s, 'applied')", ("0" * 64,))
        with pytest.raises(runner.ManifestError, match="not in the repository"):
            runner.run(conn, directory=d, entries=_entries(files))
