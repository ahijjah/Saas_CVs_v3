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

import pathlib
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


FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "restored_production"
# The stand-in for public.set_updated_at(), which the production trigger trg_ail_updated_at calls. The schema-only dump does not contain
# it: it is an assumption of this fixture, and the conversion verifies the real one (db/legacy/production.external_dependencies).
PUBLIC_SET_UPDATED_AT = """
CREATE FUNCTION public.set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
"""


def restored_production(cluster, *, public_function: str | None = PUBLIC_SET_UPDATED_AT) -> LegacyDatabase:
    """The schema of the restored production backup (tests/fixtures/restored_production/schema.sql), loaded as the bootstrap superuser.
    Objects are owned by it, as on production where no runtime role exists yet. Seeded rows are not in a schema-only dump, so a test
    either adds them (add_seed_rows) or accepts them as absent."""
    name = "prod_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(cluster.dsn())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
    conn = psycopg2.connect(cluster.dsn(name))
    conn.autocommit = True
    conn.set_client_encoding("UTF8")
    c = conn.cursor()
    c.execute("CREATE SCHEMA cv_analyzer")
    c.execute("SET search_path = cv_analyzer")
    c.execute("CREATE EXTENSION pgcrypto")
    c.execute('CREATE EXTENSION "uuid-ossp"')
    if public_function:
        c.execute(public_function)
    c.execute((FIXTURE_DIR / "schema.sql").read_text(encoding="utf-8"))
    c.execute("SET search_path = cv_analyzer")
    database = LegacyDatabase(cluster, name, admin, conn, [], None)
    # cv_app belongs to the databases built from the legacy files; the restored schema has no runtime role, so it never drops that one.
    database.ROLES = tuple(r for r in LegacyDatabase.ROLES if r != "cv_app")
    return database


# The counts the real report showed: migration 077 (86 rows), 081 (2) and 092 (524).
REAL_COUNTS = {"077_clean_stopped_workflow_pollution.sql": 86, "081_fix_knockout_answer_value_encoding.sql": 2,
               "092_repair_legacy_new_workflow_status.sql": 524}


def add_seed_rows(target: LegacyDatabase, source: LegacyDatabase) -> int:
    """Copy the seeded configuration rows (system_config, plans, prompts, model registry, platform secret rows) from a database built
    from the legacy files, because a schema-only dump carries none. Returns the number of rows copied."""
    import io
    from db.legacy import fingerprint as F
    copied = 0
    src, dst = source.bootstrap(), target.bootstrap()
    try:
        for table, _ in F.SEED_TABLES:
            # Column order differs between a chain build and the dump, so the columns are named.
            cur = src.cursor()
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"cv_analyzer.{table}",))
            if not cur.fetchone()[0]:
                continue         # created by a later main migration, not part of the legacy lineage
            cur.execute("SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) FROM pg_attribute WHERE attrelid = %s::regclass "
                        "AND attnum > 0 AND NOT attisdropped", (f"cv_analyzer.{table}",))
            columns = cur.fetchone()[0]
            buffer = io.StringIO()
            cur.copy_expert(f"COPY cv_analyzer.{table} ({columns}) TO STDOUT", buffer)
            buffer.seek(0)
            dst.cursor().copy_expert(f"COPY cv_analyzer.{table} ({columns}) FROM STDIN", buffer)
            copied += buffer.getvalue().count("\n")
    finally:
        src.close()
        dst.close()
    return copied


def seed_restored(database: LegacyDatabase, *, password_hash: str, residual_new: int = 0, orphan_answer: bool = False) -> dict:
    """Rows in the restored production schema, with the counts the real report showed: 86 stopped applications that carry a workflow
    status (077), 524 scored applications still on the retired 'new' (092), two knockout answers that need 081 (one JSON-quoted, one
    empty), plus ordinary rows. `residual_new` adds applications in other states that also hold 'new' (the 057 backfill)."""
    conn = database.bootstrap()
    cur = conn.cursor()
    cur.execute("SET search_path = cv_analyzer, public")
    ids: dict = {}
    for label, domain in (("a", "acme.example"), ("b", "other.example")):
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"tenant {label}", domain))
        ids["tenant_" + label] = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status, must_change_password) "
                    "VALUES (%s, %s, %s, %s, 'admin', 'active', false) RETURNING user_id",
                    (ids["tenant_" + label], f"admin@{domain}", password_hash, f"Admin {label.upper()}"))
        ids["admin_" + label] = str(cur.fetchone()[0])
    for name, tenant, creator, code in (("job_a", "tenant_a", "admin_a", "JOB-2026-0007"), ("job_b", "tenant_b", "admin_b", "JOB-2026-0045")):
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, %s, 'Python and SQL.', %s) "
                    "RETURNING job_id", (ids[tenant], ids[creator], name, code))
        ids[name] = str(cur.fetchone()[0])
        cur.execute("INSERT INTO job_criteria (job_id, weight_skills, weight_experience, weight_education, weight_certifications, "
                    "weight_soft_skills, weight_domain_knowledge, weight_other) VALUES (%s, 30, 25, 15, 10, 10, 5, 5)", (ids[name],))

    def bulk(count, processing_status, workflow_status, stopped_reason=None, tag="x"):
        cur.execute(
            "INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, stopped_reason, "
            "submission_source) SELECT %s, %s, %s || g, %s, %s, %s, 'manual_upload' FROM generate_series(1, %s) g",
            (ids["job_a"], ids["tenant_a"], tag, processing_status, workflow_status, stopped_reason, count))

    reasons = ("security_blocked", "extraction_failed", "processing_error", "duplicate_blocked")
    per, extra = divmod(86, len(reasons))
    for n, reason in enumerate(reasons):
        bulk(per + (1 if n < extra else 0), "failed", "new", reason, f"stopped-{reason}-")
    bulk(524, "ai_scored", "new", None, "scored-new-")
    bulk(12, "ai_scored", "awaiting_review", None, "scored-ok-")
    bulk(5, "ai_scored", "under_review", None, "scored-review-")
    bulk(4, "failed", None, "processing_error", "stopped-clean-")
    if residual_new:
        bulk(residual_new, "pending", "new", None, "pending-new-")
    cur.execute("SELECT application_id FROM applications WHERE candidate_name = 'scored-ok-1'")
    ids["app_ok"] = str(cur.fetchone()[0])
    cur.execute("SELECT application_id FROM applications WHERE candidate_name = 'scored-review-1'")
    ids["app_review"] = str(cur.fetchone()[0])
    cur.execute("SELECT application_id FROM applications WHERE candidate_name = 'scored-ok-2'")
    ids["app_ok2"] = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, question_type) VALUES (%s, %s, 'Do you hold a licence?', 'yes_no') "
                "RETURNING question_id", (ids["job_a"], ids["tenant_a"]))
    ids["question"] = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, question_type) VALUES (%s, %s, 'Can you relocate?', 'yes_no') "
                "RETURNING question_id", (ids["job_a"], ids["tenant_a"]))
    ids["question2"] = str(cur.fetchone()[0])
    for application, question, value in ((ids["app_ok"], ids["question"], "yes"), (ids["app_ok"], ids["question2"], '"no"'),
                                         (ids["app_review"], ids["question"], '""'), (ids["app_review"], ids["question2"], "no")):
        cur.execute("INSERT INTO application_knockout_answers (application_id, question_id, answer_value, tenant_id) VALUES (%s, %s, %s, %s)",
                    (application, question, value, ids["tenant_a"]))
    if orphan_answer:
        # the application is removed afterwards with its foreign key absent: the answer is left pointing at nothing
        cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, submission_source) "
                    "VALUES (%s, %s, 'doomed', 'ai_scored', 'awaiting_review', 'manual_upload') RETURNING application_id",
                    (ids["job_a"], ids["tenant_a"]))
        doomed = cur.fetchone()[0]
        cur.execute("INSERT INTO application_knockout_answers (application_id, question_id, answer_value, tenant_id) VALUES (%s, %s, 'yes', %s)",
                    (doomed, ids["question"], ids["tenant_a"]))
        cur.execute("DELETE FROM applications WHERE application_id = %s", (doomed,))
    cur.execute("INSERT INTO application_intake_log (tenant_id, job_id, intake_method, status) VALUES (%s, %s, 'manual_upload', 'received') "
                "RETURNING intake_log_id", (ids["tenant_a"], ids["job_a"]))
    ids["intake_log"] = str(cur.fetchone()[0])
    conn.close()
    return ids
