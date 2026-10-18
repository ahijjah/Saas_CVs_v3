"""Runtime database roles, privileges, row-level security and secret accessors, on a disposable cluster.

The operator scripts (db/ops/0004 and 0005) are applied with generated passwords, then every check runs as a real login role. No
check runs as the cluster superuser except to create fixtures and to read the catalogue. Passwords exist only in this process.
"""
from __future__ import annotations

import pathlib
import secrets
import subprocess
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import realdb_helper as H  # noqa: E402
from db import runtime_grants as G  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parents[1]
OPS = BACKEND / "db" / "ops"
pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")


@pytest.fixture(scope="module")
def built():
    with H.built_chain() as db:
        yield db


@pytest.fixture(scope="module")
def passwords():
    return {role: secrets.token_urlsafe(18) for role in G.RUNTIME_ROLES}


@pytest.fixture(scope="module")
def hardened(built, passwords):
    """Apply the operator scripts exactly as an operator would, then return the fixture data the checks rely on."""
    variables = []
    for role in G.RUNTIME_ROLES:
        variables += ["-v", f"{role}_password={passwords[role]}"]
    dsn_port = built.cluster.port
    base = ["psql", "-h", "127.0.0.1", "-p", str(dsn_port), "-U", "postgres", "-d", built.name, "-X", "-q",
            "-v", "ON_ERROR_STOP=1"]
    for script in ("0004_runtime_roles.sql.template", "0005_runtime_privileges.sql.template"):
        proc = subprocess.run(base + variables + ["-f", str(OPS / script)], capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"{script}: {proc.stderr}"
    return built


def _connect(built, passwords, role: str, *, tenant: str | None = None, extra: dict | None = None):
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user=role, password=passwords[role],
                            dbname=built.name, options="-c search_path=cv_analyzer")
    # Session settings are committed here, so a rollback in a test (after an expected error) does not undo them.
    cur = conn.cursor()
    if tenant is not None:
        cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (tenant,))
    for key, value in (extra or {}).items():
        cur.execute("SELECT set_config(%s, %s, false)", (key, value))
    conn.commit()
    return conn


def _superuser(built):
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name,
                            options="-c search_path=cv_analyzer")
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def tenants(hardened):
    """Two tenants, a user and a job in each, created as the superuser (fixtures only)."""
    conn = _superuser(hardened)
    cur = conn.cursor()
    out = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"tenant {label} {tag}", f"{tag}.example"))
        tenant = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') "
                    "RETURNING user_id", (tenant, f"{tag}@example.test"))
        user = str(cur.fetchone()[0])
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, %s, 'd') RETURNING job_id",
                    (tenant, user, f"job {label}"))
        out[label] = {"tenant": tenant, "user": user, "job": str(cur.fetchone()[0])}
    conn.close()
    return out


# ---- attributes, ownership, membership, schema access ----

def test_every_runtime_role_is_a_non_superuser_without_rls_bypass_that_can_login(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = ANY(%s)",
                (list(G.RUNTIME_ROLES),))
    rows = cur.fetchall()
    assert len(rows) == len(G.RUNTIME_ROLES)
    for name, rolsuper, bypass, createdb, createrole, login in rows:
        assert (rolsuper, bypass, createdb, createrole, login) == (False, False, False, False, True), name
    conn.close()


def test_no_runtime_role_owns_any_object_in_the_application_schema(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'cv_analyzer' AND pg_get_userbyid(c.relowner) = ANY(%s)""", (list(G.RUNTIME_ROLES),))
    assert cur.fetchall() == []
    cur.execute("""
        SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'cv_analyzer' AND pg_get_userbyid(p.proowner) = ANY(%s)""", (list(G.RUNTIME_ROLES),))
    assert cur.fetchall() == []
    cur.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'cv_analyzer'")
    assert cur.fetchone()[0] not in G.RUNTIME_ROLES
    conn.close()


def test_runtime_roles_are_members_of_no_role_and_have_no_members(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("""
        SELECT count(*) FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.member OR r.oid = m.roleid
        WHERE r.rolname = ANY(%s)""", (list(G.RUNTIME_ROLES),))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_no_runtime_role_can_create_objects_in_the_application_or_public_schema(hardened, passwords):
    for role in G.RUNTIME_ROLES:
        conn = _connect(hardened, passwords, role)
        cur = conn.cursor()
        for schema in ("cv_analyzer", "public"):
            cur.execute("SELECT has_schema_privilege(current_user, %s, 'CREATE')", (schema,))
            assert cur.fetchone()[0] is False, (role, schema)
        conn.close()


def test_public_holds_no_schema_table_or_routine_privilege(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema = 'cv_analyzer' AND grantee = 'PUBLIC'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM information_schema.routine_privileges WHERE routine_schema = 'cv_analyzer' AND grantee = 'PUBLIC'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM pg_namespace n, LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a "
                "WHERE n.nspname = 'cv_analyzer' AND a.grantee = 0")
    assert cur.fetchone()[0] == 0
    conn.close()


# ---- the privilege matrix, checked against the live catalogue ----

def test_table_privileges_equal_the_matrix_for_every_table_and_runtime_role(hardened, passwords):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'cv_analyzer'")
    tables = {r[0] for r in cur.fetchall()}
    unmatched = tables - set(G.MATRIX) - {"platform_secrets", "platform_secret_readable", "schema_migrations"}
    assert unmatched == set(), f"tables without a matrix entry: {sorted(unmatched)}"
    for table in sorted(tables):
        for role in G.RUNTIME_ROLES:
            for verb, privilege in G.VERB_PRIVILEGES.items():
                cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"cv_analyzer.{table}", privilege))
                granted = cur.fetchone()[0]
                assert granted == (privilege in G.expected(table, role)), f"{role} {privilege} on {table}"
    conn.close()


def test_platform_secrets_grants_metadata_columns_and_never_the_value(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    for role, columns in G.PLATFORM_SECRETS_COLUMN_GRANTS.items():
        for column in G.PLATFORM_SECRETS_COLUMNS:
            cur.execute("SELECT has_column_privilege(%s, 'cv_analyzer.platform_secrets', %s, 'SELECT')", (role, column))
            assert cur.fetchone()[0] == (column in columns), f"{role}.{column}"
    for role in G.RUNTIME_ROLES:
        cur.execute("SELECT has_column_privilege(%s, 'cv_analyzer.platform_secrets', 'value', 'SELECT')", (role,))
        assert cur.fetchone()[0] is False, role
        cur.execute("SELECT has_table_privilege(%s, 'cv_analyzer.platform_secrets', 'UPDATE')", (role,))
        assert cur.fetchone()[0] is False, role
    conn.close()


def test_routine_execution_is_granted_to_exactly_the_intended_role(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    for signature, intended in G.FUNCTION_EXECUTE.items():
        for role in G.RUNTIME_ROLES:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, f"cv_analyzer.{signature}"))
            assert cur.fetchone()[0] == (role == intended), f"{role} on {signature}"
        cur.execute("SELECT count(*) FROM pg_proc p, LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                    "WHERE p.oid = %s::regprocedure AND a.grantee = 0", (f"cv_analyzer.{signature}",))
        assert cur.fetchone()[0] == 0, f"PUBLIC can execute {signature}"
    conn.close()


def test_the_generated_operator_script_is_current(tmp_path):
    assert (OPS / "0005_runtime_privileges.sql.template").read_text(encoding="utf-8") == G.generate_sql()


# ---- row-level security, as real login roles ----

def test_tenant_session_sees_only_its_own_rows(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app", tenant=tenants["a"]["tenant"])
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {r[0] for r in cur.fetchall()} == {tenants["a"]["job"]}
    conn.close()


def test_tenant_session_cannot_update_or_insert_into_another_tenant(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app", tenant=tenants["a"]["tenant"])
    cur = conn.cursor()
    cur.execute("UPDATE jobs SET title = 'changed' WHERE job_id = %s", (tenants["b"]["job"],))
    assert cur.rowcount == 0
    conn.rollback()
    with pytest.raises(psycopg2.Error):
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd')",
                    (tenants["b"]["tenant"], tenants["b"]["user"]))
    conn.rollback()
    conn.close()


def test_missing_tenant_context_fails_closed(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM jobs")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_a_forged_role_variable_grants_no_cross_tenant_access(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app", tenant=tenants["a"]["tenant"],
                    extra={"app.current_role": "super_admin"})
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {r[0] for r in cur.fetchall()} == {tenants["a"]["job"]}
    conn.close()


def test_platform_administration_sees_every_tenant_by_identity(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_admin")
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {tenants["a"]["job"], tenants["b"]["job"]} <= {r[0] for r in cur.fetchall()}
    conn.close()


def test_worker_reads_across_tenants_only_on_the_tables_it_is_granted(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_worker")
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {tenants["a"]["job"], tenants["b"]["job"]} <= {r[0] for r in cur.fetchall()}
    with pytest.raises(psycopg2.Error):
        cur.execute("DELETE FROM jobs WHERE job_id = %s", (tenants["b"]["job"],))
    conn.rollback()
    conn.close()


# ---- append-only logs and global administration tables ----

def test_audit_log_is_append_only_for_the_tenant_runtime(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app", tenant=tenants["a"]["tenant"])
    cur = conn.cursor()
    for statement in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
        with pytest.raises(psycopg2.Error) as err:
            cur.execute(statement)
        assert err.value.pgcode == "42501", statement
        conn.rollback()
    conn.close()


def test_the_tenant_runtime_cannot_mutate_global_administration_tables(hardened, passwords, tenants):
    conn = _connect(hardened, passwords, "cv_app", tenant=tenants["a"]["tenant"])
    cur = conn.cursor()
    for statement in ("UPDATE system_config SET value = '1'", "INSERT INTO subscription_plans (plan_code) VALUES ('x')",
                      "DELETE FROM ai_prompts", "UPDATE ai_model_registry SET enabled = true"):
        with pytest.raises(psycopg2.Error) as err:
            cur.execute(statement)
        assert err.value.pgcode == "42501", statement
        conn.rollback()
    conn.close()


# ---- secrets: the getter and the setter, and their refusal paths ----

@pytest.fixture(scope="module")
def secret_fixture(hardened):
    conn = _superuser(hardened)
    cur = conn.cursor()
    cur.execute("UPDATE platform_secrets SET value = 'mail.example.test', has_value = TRUE WHERE key = 'SMTP_HOST'")
    cur.execute("UPDATE platform_secrets SET value = 'must-not-leave', has_value = TRUE WHERE key = 'DB_PASSWORD'")
    conn.close()


def test_only_the_secret_reader_may_execute_the_getter(hardened, passwords, secret_fixture):
    conn = _connect(hardened, passwords, "cv_secrets")
    cur = conn.cursor()
    cur.execute("SELECT cv_analyzer.get_platform_secret('SMTP_HOST')")
    assert cur.fetchone()[0] == "mail.example.test"
    conn.close()
    for role in ("cv_app", "cv_worker", "cv_admin"):
        conn = _connect(hardened, passwords, role)
        cur = conn.cursor()
        with pytest.raises(psycopg2.Error) as err:
            cur.execute("SELECT cv_analyzer.get_platform_secret('SMTP_HOST')")
        assert err.value.pgcode == "42501", role
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("key", ["DB_PASSWORD", "JWT_SECRET", "NOT_ON_THE_ALLOWLIST"])
def test_the_getter_refuses_keys_outside_its_allowlist_without_returning_a_value(hardened, passwords, secret_fixture, key):
    conn = _connect(hardened, passwords, "cv_secrets")
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error) as err:
        cur.execute("SELECT cv_analyzer.get_platform_secret(%s)", (key,))
    assert err.value.pgcode == "42501"
    assert "must-not-leave" not in str(err.value)
    conn.close()


def test_the_setter_is_executable_only_by_the_administration_role(hardened, passwords, secret_fixture):
    conn = _connect(hardened, passwords, "cv_admin")
    cur = conn.cursor()
    cur.execute("SELECT cv_analyzer.set_platform_secret('SMTP_USER', 'mailer', 'm***', 'admin-id', 'admin@example.test')")
    assert cur.fetchone()[0] is True
    conn.commit()
    conn.close()
    for role in ("cv_app", "cv_worker", "cv_secrets"):
        conn = _connect(hardened, passwords, role)
        cur = conn.cursor()
        with pytest.raises(psycopg2.Error) as err:
            cur.execute("SELECT cv_analyzer.set_platform_secret('SMTP_USER', 'x', 'x', 'id', 'e')")
        assert err.value.pgcode == "42501", role
        conn.rollback()
        conn.close()


def test_the_setter_refuses_the_environment_only_secrets(hardened, passwords, secret_fixture):
    conn = _connect(hardened, passwords, "cv_admin")
    cur = conn.cursor()
    for key in ("DB_PASSWORD", "JWT_SECRET"):
        with pytest.raises(psycopg2.Error) as err:
            cur.execute("SELECT cv_analyzer.set_platform_secret(%s, 'replacement', 'r***', 'id', 'e')", (key,))
        assert err.value.pgcode == "42501", key
        conn.rollback()
    conn.close()


def test_the_administration_role_cannot_read_a_secret_value_from_the_table(hardened, passwords, secret_fixture):
    conn = _connect(hardened, passwords, "cv_admin")
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error) as err:
        cur.execute("SELECT value FROM platform_secrets")
    assert err.value.pgcode == "42501"
    conn.rollback()
    conn.close()
