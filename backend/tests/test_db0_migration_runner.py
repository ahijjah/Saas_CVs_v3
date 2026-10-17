"""DB0: the migration runner, its ledger and its boundary rules.

Offline tests check the statement scanner and the manifest. Database tests run the real runner against throw-away databases
on a disposable PostgreSQL 16 cluster, using synthetic migrations in a temporary directory, so every failure mode can be
produced exactly. The runner is the code path the chain build uses, and these tests do not grant anything to any role."""
from __future__ import annotations

import pathlib
import uuid
from contextlib import contextmanager

import psycopg2
import pytest

import realdb_helper as H
from db.runner import runner
from db.runner.ledger import LEDGER_DDL
from db.runner.manifest import MANIFEST, Entry
from db.runner.sqltext import SqlTextError, migration_body, top_level_statements


# ── offline: statement scanning and transaction boundaries ────────────────────────────────────────────────────────────

def test_apostrophe_inside_a_comment_is_not_a_quote():
    sql = "-- the file's own comment\nCREATE TABLE cv_analyzer.t (id int);\nSELECT 1;"
    assert [s.text for s in top_level_statements(sql)] == ["CREATE TABLE CV_ANALYZER.T (ID INT)", "SELECT 1"]


def test_semicolon_inside_a_string_or_dollar_body_does_not_split():
    sql = "SELECT 'a;b';\nDO $$ BEGIN PERFORM 1; END $$;"
    assert len(top_level_statements(sql)) == 2


def test_plpgsql_begin_inside_a_dollar_body_is_not_transaction_control():
    sql = "CREATE FUNCTION cv_analyzer.f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;"
    assert migration_body(sql).began is False


def test_wrapping_begin_and_commit_are_removed_from_the_body():
    body = migration_body("SET search_path TO cv_analyzer;\nBEGIN;\nCREATE TABLE x (i int);\nCOMMIT;\n")
    assert body.began and body.committed
    assert "BEGIN" not in body.sql.upper() and "COMMIT" not in body.sql.upper()
    assert "SET search_path" in body.sql and "CREATE TABLE x" in body.sql


def test_commit_only_at_the_end_is_removed():
    body = migration_body("CREATE TABLE x (i int);\nCOMMIT;")
    assert body.committed and not body.began and "COMMIT" not in body.sql.upper()


@pytest.mark.parametrize("sql", [
    "BEGIN; SELECT 1;",                       # BEGIN without COMMIT
    "BEGIN; COMMIT; BEGIN; COMMIT;",          # two pairs
    "BEGIN; COMMIT; SELECT 1;",               # COMMIT not last
    "SELECT 1; ROLLBACK;",                    # ROLLBACK in a migration
    "SAVEPOINT s; SELECT 1;",                 # SAVEPOINT in a migration
])
def test_other_transaction_control_is_rejected_before_anything_runs(sql):
    with pytest.raises(SqlTextError):
        migration_body(sql)


# ── offline: manifest ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_real_manifest_matches_the_migration_directory():
    runner.validate_manifest(MANIFEST, runner.MIGRATIONS_DIR)


def test_replacement_must_sit_immediately_after_its_original(tmp_path):
    files = {"001_old.sql": "SELECT 1;", "002_mid.sql": "SELECT 2;", "003_new.sql": "SELECT 3;"}
    d = _write_dir(tmp_path, files)
    bad = (Entry("001_old.sql", superseded_by="003_new.sql"), Entry("002_mid.sql"), Entry("003_new.sql", replaces="001_old.sql"))
    with pytest.raises(runner.ManifestError):
        runner.validate_manifest(bad, d)


def test_directory_file_missing_from_manifest_is_rejected(tmp_path):
    d = _write_dir(tmp_path, {"001_a.sql": "SELECT 1;", "002_b.sql": "SELECT 2;"})
    with pytest.raises(runner.ManifestError):
        runner.validate_manifest((Entry("001_a.sql"),), d)


# ── database tests ────────────────────────────────────────────────────────────────────────────────────────────────────

@contextmanager
def empty_database():
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    cluster = H.Cluster()
    try:
        name = "r_" + uuid.uuid4().hex[:10]
        admin = psycopg2.connect(cluster.dsn())
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
        conn = psycopg2.connect(cluster.dsn(name))
        conn.autocommit = True
        conn.cursor().execute("CREATE SCHEMA cv_analyzer")
        try:
            yield conn
        finally:
            conn.close()
            admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin.close()
    finally:
        cluster.stop()


def _write_dir(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    d = tmp_path / "migrations"
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _entries(files: dict[str, str]) -> tuple[Entry, ...]:
    return tuple(Entry(name) for name in sorted(files))


def _scalar(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()[0]


def _relation_count(conn) -> int:
    return _scalar(conn, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                         "WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r'")


def _ledger(conn) -> dict[str, str]:
    cur = conn.cursor()
    cur.execute("SELECT filename, status FROM cv_analyzer.schema_migrations")
    return dict(cur.fetchall())


TWO_FILES = {
    "001_create_a.sql": "CREATE TABLE cv_analyzer.a (id int);",
    "002_create_b.sql": "CREATE TABLE cv_analyzer.b (id int);",
}


def test_fresh_run_records_each_file_and_a_second_run_performs_no_migrations(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    entries = _entries(TWO_FILES)
    with empty_database() as conn:
        first = runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        assert first.applied == ["001_create_a.sql", "002_create_b.sql"]
        assert _ledger(conn) == {"001_create_a.sql": "applied", "002_create_b.sql": "applied"}
        second = runner.run(conn, directory=d, entries=entries)
        assert second.applied == [] and second.pending() == []


def test_failed_migration_rolls_back_only_its_own_transaction_and_is_not_recorded(tmp_path):
    files = {"001_create_a.sql": TWO_FILES["001_create_a.sql"],
             "002_fails.sql": "CREATE TABLE cv_analyzer.b (id int);\nSELECT 1/0;"}
    d = _write_dir(tmp_path, files)
    entries = _entries(files)
    with empty_database() as conn:
        with pytest.raises(runner.MigrationError) as info:
            runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        assert info.value.file == "002_fails.sql"
        assert _ledger(conn) == {"001_create_a.sql": "applied"}
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.a') IS NOT NULL") is True
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.b') IS NULL") is True
        # The same file, corrected, applies: the failed attempt left nothing behind that blocks it.
        (d / "002_fails.sql").write_text("CREATE TABLE cv_analyzer.b (id int);", encoding="utf-8")
        report = runner.run(conn, directory=d, entries=entries)
        assert report.applied == ["002_fails.sql"]


def test_checksum_mismatch_on_a_recorded_file_stops_before_anything_runs(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    entries = _entries(TWO_FILES)
    with empty_database() as conn:
        runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        (d / "001_create_a.sql").write_text("CREATE TABLE cv_analyzer.a (id bigint);", encoding="utf-8")
        (d / "003_new.sql").write_text("CREATE TABLE cv_analyzer.c (id int);", encoding="utf-8")
        entries = _entries({**TWO_FILES, "003_new.sql": ""})
        with pytest.raises(runner.ChecksumMismatch):
            runner.run(conn, directory=d, entries=entries)
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.c') IS NULL") is True


def test_superseded_file_without_an_applied_replacement_stops_the_run(tmp_path):
    files = {"001_old.sql": "CREATE TABLE cv_analyzer.old (id int);",
             "002_new.sql": "CREATE TABLE cv_analyzer.new (id int);"}
    d = _write_dir(tmp_path, files)
    entries = (Entry("001_old.sql", superseded_by="002_new.sql"), Entry("002_new.sql", replaces="001_old.sql"))
    with empty_database() as conn:
        runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        conn.cursor().execute("DELETE FROM cv_analyzer.schema_migrations WHERE filename = '002_new.sql'")
        with pytest.raises(runner.MissingReplacement):
            runner.run(conn, directory=d, entries=entries)


def test_superseded_original_never_runs_and_its_replacement_runs_before_its_dependent(tmp_path):
    files = {
        "001_old.sql": "SELECT 1/0;",                                     # would fail if it ever ran
        "002_new.sql": "CREATE TABLE cv_analyzer.replacement (id int);",
        "003_dependent.sql": "SELECT * FROM cv_analyzer.replacement;",    # needs the replacement first
    }
    d = _write_dir(tmp_path, files)
    entries = (Entry("001_old.sql", superseded_by="002_new.sql"), Entry("002_new.sql", replaces="001_old.sql"),
               Entry("003_dependent.sql"))
    with empty_database() as conn:
        report = runner.run(conn, allow_fresh=True, directory=d, entries=entries)
        assert report.applied == ["002_new.sql", "003_dependent.sql"]
        assert report.superseded == ["001_old.sql"]
        assert _ledger(conn) == {"001_old.sql": "superseded", "002_new.sql": "applied", "003_dependent.sql": "applied"}


def test_absent_ledger_without_fresh_initialisation_is_an_unknown_baseline(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        with pytest.raises(runner.UnknownBaseline):
            runner.run(conn, directory=d, entries=_entries(TWO_FILES))
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NULL") is True


def test_migration_objects_without_a_ledger_are_an_unknown_baseline(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        conn.cursor().execute("CREATE TABLE cv_analyzer.ai_model_registry (model_id uuid)")
        with pytest.raises(runner.UnknownBaseline):
            runner.run(conn, allow_fresh=True, directory=d, entries=_entries(TWO_FILES))


def test_ledger_gap_is_an_unknown_baseline(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        conn.cursor().execute(LEDGER_DDL)
        import hashlib
        sha = hashlib.sha256((d / "002_create_b.sql").read_bytes()).hexdigest()
        conn.cursor().execute("INSERT INTO cv_analyzer.schema_migrations (filename, sha256, status) "
                              "VALUES ('002_create_b.sql', %s, 'applied')", (sha,))
        with pytest.raises(runner.UnknownBaseline):
            runner.run(conn, directory=d, entries=_entries(TWO_FILES))


def test_dry_run_describes_the_pending_order_and_writes_nothing(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        before = _relation_count(conn)
        report = runner.run(conn, dry_run=True, allow_fresh=True, directory=d, entries=_entries(TWO_FILES))
        assert report.pending() == ["001_create_a.sql", "002_create_b.sql"]
        assert report.applied == []
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NULL") is True
        assert _relation_count(conn) == before


def test_a_held_advisory_lock_prevents_a_second_runner_from_changing_anything(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        holder = psycopg2.connect(conn.dsn)
        holder.autocommit = True
        holder.cursor().execute("SELECT pg_advisory_lock(%s)", (runner.LOCK_KEY,))
        try:
            with pytest.raises(runner.LockHeld):
                runner.run(conn, allow_fresh=True, directory=d, entries=_entries(TWO_FILES))
            assert _scalar(conn, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NULL") is True
        finally:
            holder.cursor().execute("SELECT pg_advisory_unlock(%s)", (runner.LOCK_KEY,))
            holder.close()
        assert runner.run(conn, allow_fresh=True, directory=d, entries=_entries(TWO_FILES)).applied == [
            "001_create_a.sql", "002_create_b.sql"]


def test_owner_role_that_does_not_exist_stops_the_run_before_any_change(tmp_path):
    d = _write_dir(tmp_path, TWO_FILES)
    with empty_database() as conn:
        with pytest.raises(runner.MigrationError):
            runner.run(conn, allow_fresh=True, directory=d, entries=_entries(TWO_FILES),
                       owner_role="cv_no_such_role")
        assert _scalar(conn, "SELECT to_regclass('cv_analyzer.schema_migrations') IS NULL") is True
