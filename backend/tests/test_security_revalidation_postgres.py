"""Security re-validation on a disposable cluster built from the real chain, with the production logins.

Each class checks one claim from docs/DATABASE_ROLES.md. A check that cannot run fails; it is not skipped. The one deliberate exception
is the residual limitation at the end: it asserts the documented behaviour, so the limitation stays visible rather than hidden.
"""
from __future__ import annotations

import sys
import pathlib
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import realdb_helper as H  # noqa: E402
from security_fixtures import bootstrap, connect, seed_identities, sqlstate_of  # noqa: E402
from runtime_engines import async_engine  # noqa: E402

RUNTIME = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")
DENIED = "42501"   # insufficient_privilege: the only correct refusal for a missing grant, role or ownership


@pytest.fixture(scope="module")
def built():
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


@pytest.fixture(scope="module")
def ids(built):
    return seed_identities(built)


# ---- A. runtime-role isolation: no SET ROLE, no session authorization, no membership escalation ----

TARGETS = ("cv_migrator", "cv_migration_operator", "cv_app", "cv_worker", "cv_admin", "cv_secrets", "postgres")


ROLE_TARGET_PAIRS = [(role, target) for role in RUNTIME for target in TARGETS if target != role]   # a login may name itself


@pytest.mark.parametrize("role,target", ROLE_TARGET_PAIRS)
def test_a_runtime_role_cannot_set_role_into_another_role(built, role, target):
    conn = connect(built, role)
    try:
        assert sqlstate_of(conn, f"SET ROLE {target}") == DENIED
    finally:
        conn.close()


@pytest.mark.parametrize("role", RUNTIME)
def test_a_runtime_role_cannot_set_the_session_authorization(built, role):
    conn = connect(built, role)
    try:
        for target in ("postgres", "cv_migrator", "cv_admin", "cv_app"):
            if target == role:
                continue    # a login may name itself; it is not an escalation
            assert sqlstate_of(conn, f"SET SESSION AUTHORIZATION {target}") == DENIED, target
    finally:
        conn.close()


@pytest.mark.parametrize("role", RUNTIME)
@pytest.mark.parametrize("statement", [
    # CREATE DATABASE cannot run inside a transaction block, so these run in autocommit (below) and the error is the permission one.
    "CREATE ROLE probe_role_{tag}",
    "CREATE DATABASE probe_db_{tag}",
    "CREATE SCHEMA probe_schema_{tag}",
    "CREATE TABLE cv_analyzer.probe_{tag} (id int)",
    "CREATE FUNCTION cv_analyzer.probe_{tag}() RETURNS int LANGUAGE sql AS 'select 1'",
    "ALTER SCHEMA cv_analyzer OWNER TO {role}",
    "ALTER TABLE cv_analyzer.jobs OWNER TO {role}",
    "ALTER ROLE {role} BYPASSRLS",
    "ALTER ROLE {role} SUPERUSER",
    "ALTER ROLE {role} CREATEROLE",
    "ALTER ROLE {role} CREATEDB",
    "GRANT cv_migrator TO {role}",
])
def test_a_runtime_role_cannot_create_own_or_elevate(built, role, statement):
    conn = connect(built, role, autocommit=True)
    try:
        sql = statement.format(role=role, tag="x")
        assert sqlstate_of(conn, sql) == DENIED, sql
    finally:
        conn.close()


def test_no_runtime_role_is_related_to_the_operator_or_the_migrator(built):
    """The operator login and the migrator are not reachable from any runtime login, in either direction."""
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("""
        SELECT r.rolname, m.rolname FROM pg_auth_members am
        JOIN pg_roles r ON r.oid = am.member JOIN pg_roles m ON m.oid = am.roleid
        WHERE r.rolname = ANY(%s) OR m.rolname = ANY(%s)""", (list(RUNTIME), list(RUNTIME)))
    assert cur.fetchall() == [], "a runtime role is a member of, or has a member among, the roles"
    cur.execute("SELECT r.rolname, m.rolname FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.member "
                "JOIN pg_roles m ON m.oid = am.roleid WHERE r.rolname = 'cv_migration_operator'")
    assert cur.fetchall() == [("cv_migration_operator", "cv_migrator")]
    conn.close()


def test_the_operator_cannot_read_application_data_directly(built):
    conn = connect(built, "cv_migration_operator")
    assert sqlstate_of(conn, "SELECT count(*) FROM cv_analyzer.jobs") == DENIED
    conn.close()


def test_the_operator_cannot_become_a_runtime_role(built):
    conn = connect(built, "cv_migration_operator")
    try:
        for target in RUNTIME + ("postgres",):
            assert sqlstate_of(conn, f"SET ROLE {target}") == DENIED, target
    finally:
        conn.close()


# ---- B. ownership and role attributes ----

def test_every_runtime_role_is_a_plain_login_with_no_elevated_attribute(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("""SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolinherit
                   FROM pg_roles WHERE rolname = ANY(%s)""", (list(RUNTIME),))
    rows = cur.fetchall()
    assert {r[0] for r in rows} == set(RUNTIME)
    for name, login, superuser, bypass, createdb, createrole, replication, inherit in rows:
        assert login and not superuser and not bypass and not createdb and not createrole and not replication and not inherit, name
    conn.close()


def test_no_runtime_role_owns_the_database_a_schema_a_relation_a_routine_or_a_sequence(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (built.name,))
    assert cur.fetchone()[0] not in RUNTIME
    cur.execute("""SELECT 'namespace', nspname FROM pg_namespace WHERE pg_get_userbyid(nspowner) = ANY(%s)
                   UNION ALL SELECT 'relation', relname FROM pg_class WHERE pg_get_userbyid(relowner) = ANY(%s)
                   UNION ALL SELECT 'routine', proname FROM pg_proc WHERE pg_get_userbyid(proowner) = ANY(%s)""",
                (list(RUNTIME), list(RUNTIME), list(RUNTIME)))
    assert cur.fetchall() == []
    conn.close()


def test_the_secret_tables_and_functions_are_owned_by_the_migrator_not_by_a_login(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relname = 'platform_secrets'")
    assert cur.fetchone()[0] == "cv_migrator"
    cur.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname IN ('get_platform_secret', 'set_platform_secret')")
    owners = {r[0] for r in cur.fetchall()}
    assert owners == {"cv_migrator"}, owners
    conn.close()


@pytest.mark.parametrize("role", RUNTIME)
def test_a_runtime_role_holds_no_database_schema_or_public_create_privilege(built, role):
    """The facts behind every create check, read from the catalogue. A CREATE grant would make the statements above succeed."""
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT has_database_privilege(%s, current_database(), 'CREATE'), "
                "has_schema_privilege(%s, 'cv_analyzer', 'CREATE'), has_schema_privilege(%s, 'public', 'CREATE')",
                (role, role, role))
    assert cur.fetchone() == (False, False, False)
    conn.close()


def test_a_grant_of_schema_create_to_a_runtime_role_by_a_non_owner_changes_nothing(built):
    """PostgreSQL only warns when a non-owner grants a privilege it does not hold, so the check is that the privilege stays absent."""
    conn = connect(built, "cv_app", autocommit=True)
    assert sqlstate_of(conn, "GRANT CREATE ON SCHEMA cv_analyzer TO cv_app") is None
    conn.close()
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT has_schema_privilege('cv_app', 'cv_analyzer', 'CREATE')")
    assert cur.fetchone()[0] is False
    conn.close()


def test_the_runtime_roles_cannot_alter_the_secret_tables_or_functions(built):
    conn = connect(built, "cv_admin")
    assert sqlstate_of(conn, "ALTER FUNCTION cv_analyzer.set_platform_secret(text, text, text, text, text) SECURITY INVOKER") == DENIED
    conn.close()
    conn = connect(built, "cv_secrets")
    assert sqlstate_of(conn, "ALTER TABLE cv_analyzer.platform_secrets ADD COLUMN probe int") == DENIED
    conn.close()


# ---- C. platform identity: verified in the database, never from the token ----

@pytest.fixture
def admin_pool_calls(built, monkeypatch):
    """Replace the administration pool with one that records every session it opens, so a refused identity can be shown to never
    reach it. The replacement is the real cv_admin login (NullPool, so nothing is left open between checks)."""
    import auth.dependencies as deps
    opened: list[str] = []
    real = async_sessionmaker(bind=async_engine(built, "cv_admin"), class_=AsyncSession, expire_on_commit=False)

    class Recording:
        def __call__(self):
            opened.append("admin")
            return real()

    monkeypatch.setattr(deps, "AdminSessionLocal", Recording())
    yield opened


def _token(claims: dict, secret: str | None = None) -> HTTPAuthorizationCredentials:
    from config import get_settings
    settings = get_settings()
    body = {**claims, "exp": datetime.now(timezone.utc) + timedelta(hours=1), "iat": datetime.now(timezone.utc)}
    key = settings.jwt_secret if secret is None else secret
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=jwt.encode(body, key, algorithm=get_settings().jwt_algorithm))


async def _current_user(built, credentials):
    from auth.dependencies import get_current_user
    engine = async_engine(built, "cv_app")
    try:
        async with AsyncSession(engine) as db:
            return await get_current_user(credentials, db)
    finally:
        await engine.dispose()


def _declared_platform_checker():
    """The callable that get_platform_db declares for its current_user parameter. The tests call this, not a copy of it, so a change
    to the declared dependency is what they check."""
    import inspect
    from fastapi import params
    from auth.dependencies import get_platform_db
    annotation = inspect.signature(get_platform_db).parameters["current_user"].annotation
    (depends,) = [m for m in annotation.__metadata__ if isinstance(m, params.Depends)]
    return depends.dependency


async def _platform_session(built, user, admin_pool_calls):
    from auth.dependencies import get_platform_db
    user = await _declared_platform_checker()(user)
    gen = get_platform_db(current_user=user)
    session = await gen.__anext__()
    try:
        return (await session.execute(text("SELECT current_user"))).scalar_one()
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_a_verified_super_admin_reaches_the_administration_login(built, ids, admin_pool_calls):
    sa = ids["tenants"]["a"]
    user_id = sa["users"]["super_admin"]
    credentials = _token({"sub": user_id, "tenant_id": sa["tenant"], "role": "super_admin"})
    user = await _current_user(built, credentials)
    assert user.role == "super_admin"
    assert await _platform_session(built, user, admin_pool_calls) == "cv_admin"
    assert admin_pool_calls == ["admin"]


@pytest.mark.asyncio
@pytest.mark.parametrize("db_role", ["admin", "recruiter", "viewer"])
async def test_a_token_claiming_super_admin_gets_the_database_role_not_the_claim(built, ids, admin_pool_calls, db_role):
    """The role claim is forged to super_admin. The database says otherwise, so the platform dependency refuses and the
    administration pool is never opened."""
    sa = ids["tenants"]["a"]
    credentials = _token({"sub": sa["users"][db_role], "tenant_id": sa["tenant"], "role": "super_admin"})
    user = await _current_user(built, credentials)
    assert user.role == db_role
    with pytest.raises(HTTPException) as err:
        await _platform_session(built, user, admin_pool_calls)
    assert err.value.status_code == 403
    assert admin_pool_calls == []


@pytest.mark.asyncio
async def test_a_token_signed_with_another_secret_is_refused_before_any_database_lookup(built, ids, admin_pool_calls):
    sa = ids["tenants"]["a"]
    forged = _token({"sub": sa["users"]["super_admin"], "tenant_id": sa["tenant"], "role": "super_admin"}, secret="not-the-secret")
    with pytest.raises(HTTPException) as err:
        await _current_user(built, forged)
    assert err.value.status_code == 401
    assert admin_pool_calls == []


@pytest.mark.asyncio
async def test_an_unsigned_token_is_refused(built, ids):
    sa = ids["tenants"]["a"]
    claims = {"sub": sa["users"]["super_admin"], "tenant_id": sa["tenant"], "role": "super_admin"}
    unsigned = HTTPAuthorizationCredentials(scheme="Bearer", credentials=jwt.encode(claims, key="", algorithm="none"))
    with pytest.raises(HTTPException) as err:
        await _current_user(built, unsigned)
    assert err.value.status_code == 401


@pytest.mark.asyncio
async def test_a_token_for_a_user_absent_from_the_database_is_refused(built, ids, admin_pool_calls):
    sa = ids["tenants"]["a"]
    import uuid as _uuid
    with pytest.raises(HTTPException) as err:
        await _current_user(built, _token({"sub": str(_uuid.uuid4()), "tenant_id": sa["tenant"], "role": "super_admin"}))
    assert err.value.status_code == 401
    assert err.value.detail == "User not found"


@pytest.mark.asyncio
async def test_a_disabled_super_admin_is_refused_and_the_pool_is_not_opened(built, ids, admin_pool_calls):
    credentials = _token({"sub": ids["disabled_super_admin"], "tenant_id": ids["disabled_super_admin_tenant"], "role": "super_admin"})
    with pytest.raises(HTTPException) as err:
        await _current_user(built, credentials)
    assert err.value.status_code == 403
    assert err.value.detail == "Account disabled"
    assert admin_pool_calls == []


@pytest.mark.asyncio
async def test_a_super_admin_token_for_another_tenant_is_invisible_under_row_level_security(built, ids, admin_pool_calls):
    """The user exists, but in tenant a. Presenting tenant b in the claim hides the row, so the identity is not found."""
    sa = ids["tenants"]["a"]
    other = ids["tenants"]["b"]["tenant"]
    with pytest.raises(HTTPException) as err:
        await _current_user(built, _token({"sub": sa["users"]["super_admin"], "tenant_id": other, "role": "super_admin"}))
    assert err.value.status_code == 401
    assert admin_pool_calls == []


@pytest.mark.asyncio
async def test_a_token_without_its_required_claims_is_refused(built, ids):
    sa = ids["tenants"]["a"]
    for claims in ({"tenant_id": sa["tenant"], "role": "super_admin"},
                   {"sub": sa["users"]["super_admin"], "role": "super_admin"},
                   {"sub": sa["users"]["super_admin"], "tenant_id": sa["tenant"]}):
        with pytest.raises(HTTPException) as err:
            await _current_user(built, _token(claims))
        assert err.value.status_code == 401
        assert err.value.detail == "Invalid token claims"


# ---- D. cross-tenant isolation under the production logins ----

def test_the_tenant_login_sees_no_row_of_another_tenant_in_any_tenant_table_it_reads(built, ids):
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"])
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {r[0] for r in cur.fetchall()} == {a["job"]}
    cur.execute("SELECT user_id FROM users")
    visible = {r[0] for r in cur.fetchall()}
    assert b["users"]["admin"] not in visible and a["users"]["admin"] in visible
    cur.execute("SELECT tenant_id FROM tenants")
    assert b["tenant"] not in {str(r[0]) for r in cur.fetchall()}
    conn.close()


def test_the_tenant_login_cannot_write_into_or_move_rows_to_another_tenant(built, ids):
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"])
    cur = conn.cursor()
    cur.execute("UPDATE users SET full_name = 'x' WHERE user_id = %s", (b["users"]["admin"],))
    assert cur.rowcount == 0
    conn.rollback()
    assert sqlstate_of(conn, "INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd')",
                       (b["tenant"], a["users"]["admin"])) == "42501"
    assert sqlstate_of(conn, "INSERT INTO audit_logs (tenant_id, action) VALUES (%s, 'x')", (b["tenant"],)) == "42501"
    conn.close()


def test_the_tenant_login_cannot_reassign_its_row_to_another_tenant(built, ids):
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"])
    assert sqlstate_of(conn, "UPDATE jobs SET tenant_id = %s WHERE job_id = %s", (b["tenant"], a["job"])) == "42501"
    conn.close()


def test_missing_tenant_context_fails_closed_on_every_tenant_table_the_login_reads(built, ids):
    conn = connect(built, "cv_app")
    cur = conn.cursor()
    for table in ("jobs", "users", "applications", "job_criteria"):
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, table
    conn.close()


def test_the_forged_role_variable_grants_nothing(built, ids):
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"], extra={"app.current_role": "super_admin"})
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {r[0] for r in cur.fetchall()} == {a["job"]}
    conn.close()


def test_the_tenant_variable_is_application_controlled_residual_limitation(built, ids):
    """Documented residual limitation (docs/DATABASE_ROLES.md). A compromised tenant session can set the tenant variable to another
    tenant and read that tenant. This asserts that behaviour so it stays visible. It is not a passing guarantee."""
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"])
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (b["tenant"],))
    cur.execute("SELECT job_id FROM jobs")
    assert {r[0] for r in cur.fetchall()} == {b["job"]}
    conn.close()


def test_the_worker_login_reads_across_tenants_but_writes_only_its_granted_tables(built, ids):
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_worker")
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM jobs")
    assert {a["job"], b["job"]} <= {r[0] for r in cur.fetchall()}
    conn.close()
    conn = connect(built, "cv_worker")
    assert sqlstate_of(conn, f"INSERT INTO jobs (tenant_id, created_by, title, description) VALUES ('{a['tenant']}', "
                             f"'{a['users']['admin']}', 't', 'd')") == DENIED
    conn.close()


def test_the_append_only_logs_are_not_readable_by_the_tenant_or_the_worker(built, ids):
    for role in ("cv_app", "cv_worker"):
        for table in ("audit_logs", "ai_usage_log"):
            conn = connect(built, role)
            assert sqlstate_of(conn, f"SELECT count(*) FROM {table}") == DENIED, (role, table)
            conn.close()


# ---- E. secrets: PUBLIC, runtime roles, the getter and setter, forbidden keys ----

def test_public_cannot_read_modify_or_execute_any_secret_object(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    checks = [
        ("table", "cv_analyzer.platform_secrets", "SELECT"),
        ("table", "cv_analyzer.platform_secrets", "INSERT"),
        ("table", "cv_analyzer.platform_secrets", "UPDATE"),
        ("table", "cv_analyzer.platform_secrets", "DELETE"),
        ("table", "cv_analyzer.platform_secret_readable", "SELECT"),
        ("column", "cv_analyzer.platform_secrets.value", "SELECT"),
        ("column", "cv_analyzer.platform_secrets.value", "UPDATE"),
        ("function", "cv_analyzer.get_platform_secret(text)", "EXECUTE"),
        ("function", "cv_analyzer.set_platform_secret(text, text, text, text, text)", "EXECUTE"),
    ]
    for kind, obj, privilege in checks:
        if kind == "table":
            cur.execute("SELECT has_table_privilege('public', %s, %s)", (obj, privilege))
        elif kind == "column":
            table, column = obj.rsplit(".", 1)
            cur.execute("SELECT has_column_privilege('public', %s, %s, %s)", (table, column, privilege))
        else:
            cur.execute("SELECT has_function_privilege('public', %s, %s)", (obj, privilege))
        assert cur.fetchone()[0] is False, (obj, privilege)
    conn.close()


@pytest.mark.parametrize("role", RUNTIME)
def test_no_runtime_role_can_select_the_secret_value_column(built, role):
    conn = connect(built, role)
    assert sqlstate_of(conn, "SELECT value FROM cv_analyzer.platform_secrets") == DENIED
    conn.close()


@pytest.mark.parametrize("role", ["cv_app", "cv_worker", "cv_admin"])
def test_only_the_secret_reader_may_call_the_getter(built, role):
    conn = connect(built, role)
    assert sqlstate_of(conn, "SELECT cv_analyzer.get_platform_secret('SMTP_HOST')") == DENIED
    conn.close()


@pytest.mark.parametrize("role", ["cv_app", "cv_worker", "cv_secrets"])
def test_only_the_administration_login_may_call_the_setter(built, role):
    conn = connect(built, role)
    assert sqlstate_of(conn, "SELECT cv_analyzer.set_platform_secret('SMTP_HOST', 'v', 'm', NULL, 'e')") == DENIED
    conn.close()


def test_exactly_one_runtime_role_may_execute_each_secret_function(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    for signature, expected in (("get_platform_secret(text)", "cv_secrets"),
                                ("set_platform_secret(text, text, text, text, text)", "cv_admin")):
        granted = []
        for role in RUNTIME:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, f"cv_analyzer.{signature}"))
            if cur.fetchone()[0]:
                granted.append(role)
        assert granted == [expected], signature
    conn.close()


@pytest.mark.parametrize("key", ["DB_PASSWORD", "JWT_SECRET", "db_password", "jwt_secret", "NOT_ON_THE_ALLOWLIST"])
def test_the_getter_refuses_forbidden_and_unlisted_keys_with_no_value(built, key):
    conn = connect(built, "cv_secrets")
    cur = conn.cursor()
    with pytest.raises(Exception) as err:
        cur.execute("SELECT cv_analyzer.get_platform_secret(%s)", (key,))
    assert "not readable through the accessor" in str(err.value)
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("key", ["DB_PASSWORD", "JWT_SECRET"])
def test_the_setter_refuses_environment_only_secrets(built, key):
    conn = connect(built, "cv_admin")
    cur = conn.cursor()
    with pytest.raises(Exception) as err:
        cur.execute("SELECT cv_analyzer.set_platform_secret(%s, 'v', 'm', NULL, 'e')", (key,))
    assert "cannot be set through the accessor" in str(err.value)
    conn.rollback()
    conn.close()


def test_the_setter_refuses_an_empty_value(built):
    conn = connect(built, "cv_admin")
    cur = conn.cursor()
    with pytest.raises(Exception) as err:
        cur.execute("SELECT cv_analyzer.set_platform_secret('SMTP_HOST', '', 'm', NULL, 'e')")
    assert "must not be empty" in str(err.value)
    conn.rollback()
    conn.close()


def test_a_value_set_by_the_administration_login_is_read_back_only_by_the_secret_reader(built):
    """The setter (cv_admin) stores a value; the getter (cv_secrets) returns exactly that value. The getter is the only read path, so
    the value is returned to the reader and to no other login. This runs on the disposable database."""
    admin = connect(built, "cv_admin", autocommit=True)
    cur = admin.cursor()
    cur.execute("SELECT cv_analyzer.set_platform_secret('SMTP_HOST', 'smtp.round-trip.example', 'smtp.***', NULL, 'ops@example.test')")
    assert cur.fetchone()[0] is True
    admin.close()
    secrets = connect(built, "cv_secrets", autocommit=True)
    cur = secrets.cursor()
    cur.execute("SELECT cv_analyzer.get_platform_secret('SMTP_HOST')")
    assert cur.fetchone()[0] == "smtp.round-trip.example"
    secrets.close()
    app = connect(built, "cv_app", autocommit=True)
    assert sqlstate_of(app, "SELECT value FROM cv_analyzer.platform_secrets WHERE key = 'SMTP_HOST'") == DENIED
    app.close()


# ---- F. search path: resolved on fresh connections and after rollback, for every login ----

@pytest.mark.parametrize("role", RUNTIME + ("cv_migration_operator",))
def test_the_search_path_is_the_application_schema_on_a_fresh_connection(built, role):
    conn = psycopg_connect_without_options(built, role)
    cur = conn.cursor()
    cur.execute("SHOW search_path")
    assert cur.fetchone()[0].replace(" ", "") == "cv_analyzer,public"
    conn.close()


@pytest.mark.parametrize("role", RUNTIME)
def test_the_role_default_survives_an_error_and_a_rollback(built, role):
    conn = psycopg_connect_without_options(built, role)
    cur = conn.cursor()
    cur.execute("SET search_path = public")                 # inside the transaction, so the rollback below reverts it
    cur.execute("SHOW search_path")
    assert cur.fetchone()[0] == "public"
    conn.rollback()
    cur.execute("SHOW search_path")
    assert cur.fetchone()[0].replace(" ", "") == "cv_analyzer,public", "rollback must restore the role default"
    conn.close()
    fresh = psycopg_connect_without_options(built, role)
    cur = fresh.cursor()
    cur.execute("SHOW search_path")
    assert cur.fetchone()[0].replace(" ", "") == "cv_analyzer,public", "a new connection resolves the role default"
    fresh.close()


def psycopg_connect_without_options(built, role):
    """A connection that passes no search_path option, so only the role default can apply."""
    import psycopg2
    return psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user=role, password=built.runtime_passwords[role],
                            dbname=built.name)


# ---- G. the access that remains: forbidden keys even when allowlisted, forged role variables on a log, the request pool ----

@pytest.mark.parametrize("key", ["DB_PASSWORD", "JWT_SECRET"])
def test_an_environment_only_key_is_refused_even_when_it_is_on_the_allowlist(built, key):
    """The refusal is a rule of the accessor, not a side effect of the allowlist. The key is put on the allowlist here, committed so
    that the reader's connection sees it, and removed afterwards. The getter must still refuse it."""
    admin = bootstrap(built)
    cur = admin.cursor()
    cur.execute("INSERT INTO cv_analyzer.platform_secrets (key, value, has_value, masked_value, description, category, is_critical) "
                "VALUES (%s, 'x', TRUE, '***', 'test', 'test', FALSE) ON CONFLICT (key) DO UPDATE SET value = 'x'", (key,))
    cur.execute("INSERT INTO cv_analyzer.platform_secret_readable (key, reason) VALUES (%s, 'test') ON CONFLICT DO NOTHING", (key,))
    try:
        reader = connect(built, "cv_secrets", autocommit=True)
        rcur = reader.cursor()
        with pytest.raises(Exception) as err:
            rcur.execute("SELECT cv_analyzer.get_platform_secret(%s)", (key,))
        assert "not readable through the accessor" in str(err.value)
        reader.close()
    finally:
        cur.execute("DELETE FROM cv_analyzer.platform_secret_readable WHERE key = %s AND reason = 'test'", (key,))
        cur.execute("DELETE FROM cv_analyzer.platform_secrets WHERE key = %s AND category = 'test'", (key,))
        admin.close()


def test_a_forged_role_variable_cannot_write_into_another_tenants_usage_log(built, ids):
    """The usage log is append-only for the tenant login (INSERT only). With the role variable forged to super_admin, an insert for
    another tenant is still refused by the policy's check."""
    a, b = ids["tenants"]["a"], ids["tenants"]["b"]
    conn = connect(built, "cv_app", tenant=a["tenant"], extra={"app.current_role": "super_admin"})
    assert sqlstate_of(conn, "INSERT INTO ai_usage_log (tenant_id, stage, model) VALUES (%s, 'probe', 'probe')", (b["tenant"],)) == "42501"
    assert sqlstate_of(conn, "INSERT INTO ai_usage_log (tenant_id, stage, model) VALUES (%s, 'probe', 'probe')", (a["tenant"],)) is None
    conn.close()


def test_the_tenant_request_engine_is_the_cv_app_login_and_never_the_administration_pool():
    """Source-level and configuration-level: get_db binds the cv_app engine. The administration pool is separate and is reached only
    through get_platform_db or get_admin_db."""
    import database
    from sqlalchemy.engine import make_url
    from config import get_settings
    username = database.engine.url.username
    assert username == make_url(get_settings().database_url).username == "cv_app"
    assert database.AsyncSessionLocal.kw["bind"] is database.engine
    assert database.AdminSessionLocal.kw["bind"] is database.admin_engine
    assert database.admin_engine is not database.engine


REQUIRED_TESTS = (
    "test_an_unsigned_token_is_refused",
    "test_a_verified_super_admin_reaches_the_administration_login",
    "test_a_token_claiming_super_admin_gets_the_database_role_not_the_claim",
    "test_public_cannot_read_modify_or_execute_any_secret_object",
    "test_the_getter_refuses_forbidden_and_unlisted_keys_with_no_value",
    "test_the_role_default_survives_an_error_and_a_rollback",
    "test_the_tenant_request_engine_is_the_cv_app_login_and_never_the_administration_pool",
)


def test_security_suite_keeps_its_required_tests():
    """A required check cannot be renamed or deleted without this failing: the definition must be present in this file."""
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    missing = [name for name in REQUIRED_TESTS if f"def {name}(" not in source]
    assert not missing, missing
