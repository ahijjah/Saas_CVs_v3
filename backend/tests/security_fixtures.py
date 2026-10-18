"""Shared helpers for the security re-validation suites. Every connection is a production login with the password the built database
provisioned (realdb_helper). Fixtures that create tenants and users use the bootstrap superuser, as the seed does; no check runs as
the superuser.
"""
from __future__ import annotations

import uuid

import psycopg2

SEARCH_PATH = "cv_analyzer, public"


def connect(built, role: str, *, tenant: str | None = None, autocommit: bool = False, extra: dict | None = None):
    """A psycopg2 connection as role. Tenant and extra settings are committed, so a rollback in a check does not undo them."""
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user=role, password=built.runtime_passwords[role],
                            dbname=built.name, options="-c search_path=cv_analyzer,public")
    if tenant is not None or extra:
        cur = conn.cursor()
        if tenant is not None:
            cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (tenant,))
        for key, value in (extra or {}).items():
            cur.execute("SELECT set_config(%s, %s, false)", (key, value))
        conn.commit()
    conn.autocommit = autocommit
    return conn


def bootstrap(built):
    """The bootstrap superuser, for fixtures only."""
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name,
                            options="-c search_path=cv_analyzer,public")
    conn.autocommit = True
    return conn


def sqlstate_of(conn, sql: str, params=None) -> str | None:
    """Run sql on conn and return its SQLSTATE, or None when it succeeded. The transaction is rolled back either way."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    except psycopg2.Error as exc:
        conn.rollback()
        return exc.pgcode
    conn.rollback()
    return None


def seed_identities(built) -> dict:
    """Two tenants, and in each a user for every role the schema allows plus one disabled super_admin. Created as the bootstrap
    superuser. Returns the ids the checks use."""
    conn = bootstrap(built)
    cur = conn.cursor()
    out: dict = {"tenants": {}}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"sec {label} {tag}", f"{tag}.example"))
        tenant = str(cur.fetchone()[0])
        users = {}
        for role in ("super_admin", "admin", "recruiter", "viewer"):
            cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', %s, %s) "
                        "RETURNING user_id", (tenant, f"{role}.{tag}@example.test", role, role))
            users[role] = str(cur.fetchone()[0])
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, %s, 'd') RETURNING job_id",
                    (tenant, users["admin"], f"job {label}"))
        out["tenants"][label] = {"tenant": tenant, "users": users, "job": str(cur.fetchone()[0])}
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES ('sec platform', %s) RETURNING tenant_id",
                (f"platform.{uuid.uuid4().hex[:8]}.example",))
    platform_tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status) "
                "VALUES (%s, %s, 'x', 'disabled platform', 'super_admin', 'disabled') RETURNING user_id",
                (platform_tenant, f"disabled.{uuid.uuid4().hex[:8]}@example.test"))
    out["disabled_super_admin"] = str(cur.fetchone()[0])
    out["disabled_super_admin_tenant"] = platform_tenant
    conn.close()
    return out
