"""DB0 pre-merge audit: operator scripts fail clearly on objects they do not handle, and the bootstrap template cannot be
replayed over existing roles or a ledger. Runs only against disposable clusters."""
from __future__ import annotations

import pathlib

import psycopg2
import pytest

import realdb_helper as H
from scripts.generate_ownership_transfer import GenerationError, generate
from test_db0_migration_runner import empty_database

OPS = pathlib.Path(__file__).resolve().parents[1] / "db" / "ops"


def _bootstrap_script() -> str:
    template = (OPS / "0001_roles_and_ledger.sql.template").read_text(encoding="utf-8")
    script = "\n".join(line for line in template.splitlines() if not line.startswith("\\set"))
    return script.replace(":'migration_operator_password'", "'disposable-test-only'")


@pytest.mark.parametrize("ddl, expected", [
    ("CREATE AGGREGATE cv_analyzer.agg_sum(int) (SFUNC = int4pl, STYPE = int)", "aggregate or window function"),
    ("CREATE TYPE cv_analyzer.standalone_pair AS (a int, b int)", "is not handled"),
])
def test_generator_stops_on_objects_it_does_not_handle(ddl, expected):
    with H.built_chain() as db:
        db.conn.cursor().execute(ddl)
        with pytest.raises(GenerationError, match=expected):
            generate(db.conn, "cv_migrator")


def test_generator_quotes_the_owner_identifier_and_never_emits_reassign():
    with H.built_chain() as db:
        db.conn.cursor().execute("CREATE ROLE cv_migrator NOLOGIN")
        lines = generate(db.conn, "cv_migrator")
        assert any(line.startswith('ALTER SCHEMA cv_analyzer OWNER TO "cv_migrator";') for line in lines)
        assert not any("REASSIGN" in line.upper() for line in lines)


def test_bootstrap_template_cannot_be_replayed_over_existing_roles_or_ledger():
    script = _bootstrap_script()
    with empty_database() as conn:
        conn.cursor().execute(script)
        with pytest.raises(psycopg2.Error):
            conn.cursor().execute(script)


def test_bootstrap_template_creates_the_operator_login_without_inherited_privilege():
    script = _bootstrap_script()
    with empty_database() as conn:
        conn.cursor().execute(script)
        cur = conn.cursor()
        cur.execute("""SELECT r.rolinherit, r.rolcanlogin, r.rolsuper, r.rolbypassrls
                       FROM pg_roles r WHERE r.rolname = 'cv_migration_operator'""")
        assert cur.fetchone() == (False, True, False, False)
        cur.execute("""SELECT m.admin_option, m.inherit_option, m.set_option
                       FROM pg_auth_members m
                       JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member
                       WHERE r.rolname = 'cv_migrator' AND u.rolname = 'cv_migration_operator'""")
        assert cur.fetchone() == (False, False, True)
