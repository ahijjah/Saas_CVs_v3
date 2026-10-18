"""Upgrade from the previous main state (migrations 001 to 098) to this release, following docs/DATABASE_ROLES.md.

The previous state is rebuilt from this branch's migrations 001 to 098 (identical to main; see the final diff check in the release notes)
plus the legacy application role of main's schema.sql: `cv_app` as a LOGIN with the committed default password and broad DML on every
table. The operator procedure is then run in order (roles, ownership transfer, migrations as the operator, runtime logins and grants),
and the preflight is run before and after it.
"""
from __future__ import annotations

import pathlib
import sys
import uuid

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import realdb_helper as H  # noqa: E402
from db.runner import runner  # noqa: E402
from db.runner.manifest import MANIFEST  # noqa: E402
from scripts import db_preflight as P  # noqa: E402
from test_privilege_justifications import diff, expected_tuples, live_tuples, _manifest  # noqa: E402

pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")

LEGACY_APPLICATION_ROLE = """
ALTER ROLE cv_app LOGIN INHERIT PASSWORD 'CHANGE_ME_IN_ENV';
GRANT USAGE ON SCHEMA cv_analyzer TO cv_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cv_analyzer TO cv_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA cv_analyzer TO cv_app;
"""


def _previous_main_state(tmp_path: pathlib.Path):
    """The database as main leaves it: schema.sql, migrations up to 098, the legacy application role. Returns (cluster, H.Database).

    The runner checks the manifest against the migration directory, so the previous state gets a directory that holds only the
    files main had. The files are copied from this branch, where 001 to 098 are unchanged."""
    cut = [e.file for e in MANIFEST].index("099_platform_secret_accessors.sql")
    directory = tmp_path / "migrations"
    directory.mkdir()
    for entry in MANIFEST[:cut]:
        (directory / entry.file).write_bytes((H.DB_DIR / "migrations" / entry.file).read_bytes())
    cluster = H.Cluster()
    try:
        admin = psycopg2.connect(cluster.dsn())
        admin.autocommit = True
        name = "upg_" + uuid.uuid4().hex[:10]
        admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
        conn = psycopg2.connect(cluster.dsn(name))
        conn.autocommit = True
        conn.set_client_encoding("UTF8")
        conn.cursor().execute(H.schema_sql())
        conn.cursor().execute(LEGACY_APPLICATION_ROLE)
        runner.run(conn, allow_fresh=True, directory=directory, entries=MANIFEST[:cut])
        return cluster, H.Database(cluster, name, admin, conn, failures=[])
    except BaseException:
        cluster.stop()
        raise


def _inspect(database, phase):
    conn = psycopg2.connect(host="127.0.0.1", port=database.cluster.port, user="postgres", dbname=database.name)
    conn.autocommit = False
    try:
        conn.set_session(readonly=True)
        return P.run(conn, phase=phase)
    finally:
        conn.rollback()
        conn.close()


def test_the_previous_state_is_what_main_leaves_and_the_before_phase_reports_only_procedure_gaps(tmp_path):
    cluster, database = _previous_main_state(tmp_path)
    try:
        checks = {c.name: c for c in _inspect(database, "before")}
        assert not [c for c in checks.values() if c.status == P.FAIL], [c for c in checks.values() if c.status == P.FAIL]
        assert not [c for c in checks.values() if c.status == P.UNKNOWN]
        assert checks["pending migrations of this release"].status == P.WARN
        assert "099" in checks["pending migrations of this release"].detail
        assert checks["table privileges hold no grant beyond the matrix"].status == P.WARN
        assert checks["runtime role cv_app is hardened"].status == P.WARN
        assert checks["runtime role cv_app is hardened"].detail.startswith("createdb=")
        assert P._exit_code(list(checks.values())) == 0
    finally:
        database.close()
        cluster.stop()


def test_the_operator_procedure_turns_the_previous_state_into_a_passing_after_state_and_keeps_the_data(tmp_path):
    cluster, database = _previous_main_state(tmp_path)
    try:
        # A tenant and an issued job code, from before the upgrade. The code must never be issued again.
        admin = database.conn
        cur = admin.cursor()
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES ('upgrade', 'upgrade.example') RETURNING tenant_id")
        tenant = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, 'u@upgrade.example', 'x', 'u', 'admin') "
                    "RETURNING user_id", (tenant,))
        user = str(cur.fetchone()[0])
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, 'before upgrade', 'd', "
                    "'JOB-2026-0007') RETURNING job_id", (tenant, user))
        job = str(cur.fetchone()[0])

        # Step 2 (0000), step 3 (ownership), step 5 (migrations as the operator), step 6 and 7 (runtime logins and grants).
        H.provision_migration_roles(database)
        H.transfer_ownership(database)
        H.run_migrations_as_operator(database)
        H.provision_runtime_logins(database)

        checks = _inspect(database, "after")
        failing = [c for c in checks if c.status != P.PASS]
        assert not failing, failing
        assert P._exit_code(checks) == 0

        undeclared, missing = diff(expected_tuples(_manifest()), live_tuples(psycopg2.connect(
            host="127.0.0.1", port=cluster.port, user="postgres", dbname=database.name)))
        assert not undeclared and not missing, (undeclared, missing)

        # The tenant's job survives, visible to its own tenant login, and the next code is above the issued one.
        app = psycopg2.connect(host="127.0.0.1", port=cluster.port, user="cv_app", password=database.runtime_passwords["cv_app"],
                               dbname=database.name, options="-c search_path=cv_analyzer,public")
        app_cur = app.cursor()
        app_cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (tenant,))
        app_cur.execute("SELECT job_id FROM jobs")
        assert {str(r[0]) for r in app_cur.fetchall()} == {job}
        app.close()
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        assert cur.fetchone()[0] >= 8
    finally:
        database.close()
        cluster.stop()
