"""Rebuild the legacy production database on a disposable PostgreSQL 16 cluster (test use only).

The state is the one production source commit 63f3bd7 leaves, rebuilt from the committed legacy files, byte-identical to the originals
(db/legacy/lineage_63f3bd7):

  * the legacy db/schema.sql and seed.sql, run as the legacy application role: `cv_app`, a LOGIN SUPERUSER BYPASSRLS role that owns
    every object, as in production. It is the only runtime role; cv_worker, cv_admin and cv_secrets do not exist;
  * the migrations 002 to 092 of the main chain, in file-name order, each in its own transaction, then the sixteen legacy migrations
    093 to 108;
  * no schema_migrations ledger.

Four of the base files do not apply on a clean chain: 010, 024 (needs the plans 010 creates), 070 (a syntax error) and 087 (needs a
column nothing creates). Two states are built:

  mode="production"  what a working production database must be: 010 and 087 were applied by hand with their fixed form (095 and 097
                     at the position of the originals), 024 then applies, and 070 never applied, so the SLA index and defaults are
                     missing. Only 096 is needed to complete it.
  mode="clean"       the chain exactly as the files produce it: 010, 024, 070 and 087 failed and were rolled back."""
from __future__ import annotations

import re
import secrets
import uuid

import psycopg2

import realdb_helper as H
from db.legacy import lineage as L
from db.runner.sqltext import migration_body

LEGACY_DIR = L.LEGACY_DIR
MAIN_MIGRATIONS = H.DB_DIR / "migrations"
REPLACEMENTS_NOT_IN_LEGACY = ("095_repair_010_platform_control.sql", "096_repair_070_sla_policy_index.sql",
                              "097_repair_087_knockout_answer_tenant.sql")
HAND_APPLIED = {"010_platform_control.sql": "095_repair_010_platform_control.sql",
                "087_autosave_deterministic_answers.sql": "097_repair_087_knockout_answer_tenant.sql"}


def legacy_schema_sql() -> str:
    lines = (LEGACY_DIR / "schema.sql").read_text(encoding="utf-8").split("\n")
    for n in range(0, 96):      # the same forward-reference patch the test chain has always needed
        lines[n] = re.sub(r"\s+REFERENCES users \(user_id\) ON DELETE SET NULL", "", lines[n])
    return "\n".join(lines)


def legacy_files(mode: str = "production") -> list[tuple[str, str]]:
    """(label, sql) in the order production applied them: main files 002..092 by name, then the legacy 093..108."""
    names = sorted(p.name for p in MAIN_MIGRATIONS.glob("*.sql"))
    base = [n for n in names if n[:3] <= "092" and n not in REPLACEMENTS_NOT_IN_LEGACY]
    out = []
    for n in base:
        source = MAIN_MIGRATIONS / (HAND_APPLIED[n] if mode == "production" and n in HAND_APPLIED else n)
        sql = source.read_text(encoding="utf-8")
        if source.name != n:
            sql = "BEGIN;\n" + migration_body(sql).sql + "\nCOMMIT;\n"
        out.append((n, sql))
    out += [(p.name, p.read_text(encoding="utf-8")) for p in sorted((LEGACY_DIR / "migrations").glob("*.sql"))]
    return out


class LegacyDatabase:
    def __init__(self, cluster, name, admin, conn, failures, cv_app_password):
        self.cluster, self.name, self.admin, self.conn, self.failures = cluster, name, admin, conn, failures
        self.cv_app_password = cv_app_password
        self.runtime_passwords: dict[str, str] = {}

    def bootstrap(self):
        """A fresh superuser connection (postgres), never cv_app."""
        c = psycopg2.connect(self.cluster.dsn(self.name))
        c.autocommit = True
        c.set_client_encoding("UTF8")
        return c

    ROLES = ("cv_app", "cv_worker", "cv_admin", "cv_secrets", "cv_migration_operator", "cv_migrator")

    def close(self):
        """Drop the database, then the roles. Roles belong to the cluster, not to a database, so a test that creates them must remove
        them or the next test on the same cluster starts from a different state."""
        try:
            self.conn.close()
        finally:
            cur = self.admin.cursor()
            cur.execute(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
            for role in self.ROLES:
                cur.execute(f"DROP ROLE IF EXISTS {role}")
            self.admin.close()


def build_legacy(cluster, *, mode: str = "production") -> LegacyDatabase:
    name = "legacy_" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(18)
    admin = psycopg2.connect(cluster.dsn())
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'cv_app'")
    if cur.fetchone() is None:
        cur.execute("CREATE ROLE cv_app LOGIN SUPERUSER BYPASSRLS PASSWORD %s", (password,))
    conn = psycopg2.connect(cluster.dsn(name))
    conn.autocommit = True
    conn.set_client_encoding("UTF8")
    c = conn.cursor()
    c.execute("SET ROLE cv_app")           # everything below is created and owned by the legacy application role
    failures: list[str] = []
    steps = [("schema.sql", legacy_schema_sql())] + legacy_files(mode) + [("seed.sql", (LEGACY_DIR / "seed.sql").read_text(encoding="utf-8"))]
    # The legacy schema.sql declares two foreign keys to users before users exists, which a clean database refuses; the forward-reference
    # patch above removes them. They are added back after users exists, exactly as this release's schema.sql does, so the rebuilt
    # schema has the foreign keys a working production database has.
    forward_keys = """
        ALTER TABLE system_config ADD CONSTRAINT system_config_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES users (user_id) ON DELETE SET NULL;
        ALTER TABLE subscription_plans ADD CONSTRAINT subscription_plans_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES users (user_id) ON DELETE SET NULL;
    """
    steps.insert(1, ("forward foreign keys", forward_keys))
    for label, sql in steps:
        try:
            c.execute(sql)
        except psycopg2.Error as exc:
            failures.append(label)
            c.execute("ROLLBACK")
        c.execute("SET search_path = cv_analyzer")
    return LegacyDatabase(cluster, name, admin, conn, failures, password)
