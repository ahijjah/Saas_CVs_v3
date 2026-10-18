"""Migration runner dry run on an empty database: every migration is reported as pending, nothing is written."""
from __future__ import annotations

import psycopg2
import pytest

from realdb_helper import Cluster, migration_files, unavailable_reason
from db.runner import runner


@pytest.fixture(scope="module")
def empty_db():
    reason = unavailable_reason()
    if reason:
        pytest.fail(reason)
    cluster = Cluster()
    try:
        admin = psycopg2.connect(cluster.dsn())
        admin.autocommit = True
        admin.cursor().execute("CREATE DATABASE e2e_dry_run ENCODING 'UTF8' TEMPLATE template0")
        admin.close()
        yield cluster.dsn("e2e_dry_run")
    finally:
        cluster.stop()


def test_migration_dry_run_reports_pending_and_writes_nothing(empty_db):
    conn = psycopg2.connect(empty_db)
    try:
        report = runner.run(conn, dry_run=True, allow_fresh=True)
        conn.rollback()
        assert report.dry_run is True
        assert report.applied == []
        pending = report.pending()
        assert pending, "a dry run on an empty database must list the migrations it would apply"
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'cv_analyzer'")
        assert cur.fetchone()[0] == 0, "a dry run must not create the application schema"
        assert set(pending) <= {path.name for path in migration_files()}, "the plan may only name real migration files"
    finally:
        conn.close()
