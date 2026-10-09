"""Requirements-v2 API and migrations 106 / 107 against a REAL, isolated PostgreSQL.

Optional: needs the `pgserver` package (not a project dependency; the whole module skips without it). Each test builds
its own throw-away database inside a temporary pgserver cluster, with minimal tables that reuse the real constraint
definitions (schema.sql / migration 104), applies the migration files exactly as written, and runs the ACTUAL service
code (services/requirements_api.py, routers/platform_config.py) through SQLAlchemy + asyncpg. Concurrency uses separate
pooled connections. No production, VPS or network database is touched; nothing here is a deployment.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pgserver = pytest.importorskip("pgserver")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

import test_p001_scoring_path as p001  # noqa: E402  (restores the real sqlalchemy / config modules other tests stub)
from test_requirements_v2_api import (  # noqa: E402
    EXP_TEXT, ALT_TEXT, client_doc, item, preferred_only_job, preferred_structured_job, structured_job, to_preferred,
    to_required,
)
from test_requirements_v2_classification_ack import flagged_job, ids_by_text  # noqa: E402
from test_requirements_v2_migration import (  # noqa: E402
    MIGRATION as MIGRATION_106, MIGRATIONS, _synthetic_schema_sql,
)
from services.requirements_v2 import CATEGORIES, POLICY_KEY  # noqa: E402

MIGRATION_107 = MIGRATIONS / "107_requirements_v2_revision.sql"
T1, T2 = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))
JOB = str(uuid.UUID(int=0xA1))
JOB2 = str(uuid.UUID(int=0xA2))
U_ADMIN, U_HR, U_AGENCY = (str(uuid.UUID(int=0x100 + n)) for n in range(3))
ORG = str(uuid.UUID(int=0x900))

EXTRA_SCHEMA = """
    ALTER TABLE job_criteria ADD COLUMN original_analysis_json JSONB;
    ALTER TABLE job_criteria ADD COLUMN last_edited_by UUID;
    ALTER TABLE job_criteria ADD COLUMN last_edited_at TIMESTAMPTZ;
    CREATE TABLE jobs (job_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, client_organization_id UUID);
    CREATE TABLE agency_user_clients (user_id UUID NOT NULL, client_organization_id UUID NOT NULL, tenant_id UUID NOT NULL);
    CREATE TABLE system_config (
        key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL,
        type VARCHAR(20) NOT NULL DEFAULT 'string' CHECK (type IN ('string','number','boolean','json')),
        category VARCHAR(50) NOT NULL DEFAULT 'general'
            CHECK (category IN ('scoring','ai','email','queue','subscription','security','general')),
        editable BOOLEAN NOT NULL DEFAULT true, description TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by UUID);
    CREATE TABLE audit_logs (
        log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, user_id UUID, user_email VARCHAR(255),
        action VARCHAR(100) NOT NULL, resource_type VARCHAR(100), resource_id TEXT, details JSONB,
        ip_address VARCHAR(50), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


def user(role="hr_manager", tenant=T1, uid=U_HR):
    return SimpleNamespace(user_id=uid, tenant_id=tenant, email=f"{role}@example.com", role=role, full_name=role)


_FRESH = ("services.audit_service", "services.requirements_api", "routers.platform_config", "routers.job_requirements")


@pytest.fixture(autouse=True)
def _real_modules():
    """Other test modules leave MagicMock stand-ins for sqlalchemy and parts of its package in sys.modules. For the
    duration of each test this module drops EVERY sqlalchemy module (stub or real) so the real package is imported
    afresh and consistently, together with the modules that bound `text` at import time. patch.dict puts back exactly
    what was there before, so other test modules are unaffected."""
    with patch.dict(sys.modules, p001._REAL_MODULES):
        for name in [n for n in sys.modules if n == "sqlalchemy" or n.startswith("sqlalchemy.") or n in _FRESH]:
            del sys.modules[name]
        yield


@pytest.fixture(scope="module")
def pg_server():
    server = pgserver.get_server(tempfile.mkdtemp(prefix="req_v2_api_pg_"))
    yield server
    server.cleanup()


class Env:
    """One throw-away database: a synchronous admin connection (DDL, seeding, assertions from OUTSIDE the service) and an
    async engine with a real connection pool (what the service runs on)."""

    def __init__(self, sync, engine, sessionmaker):
        self.sync, self.engine, self.sessionmaker = sync, engine, sessionmaker

    def q(self, sql, params=None):
        with self.sync.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def session(self):
        return self.sessionmaker()

    def row(self, job=JOB):
        (r,) = self.q("""SELECT analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education,
                                weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other,
                                to_jsonb(job_criteria) ->> 'requirements_revision',
                                to_jsonb(job_criteria) -> 'requirements_retired_item_ids', last_edited_by::text
                         FROM job_criteria WHERE job_id = %s""", (job,))
        return {"analysis": r[0], "original": r[1], "weights": dict(zip(CATEGORIES, r[2:9])),
                "revision": None if r[9] is None else int(r[9]), "retired": r[10], "edited_by": r[11]}

    def audit(self, action=None):
        sql = "SELECT action, user_id::text, tenant_id::text, resource_id, details FROM audit_logs"
        rows = self.q(sql + (" WHERE action = %s ORDER BY created_at, log_id" if action else " ORDER BY created_at, log_id"),
                      (action,) if action else None)
        return [{"action": a, "user_id": u, "tenant_id": t, "resource_id": r, "details": d} for a, u, t, r, d in rows]

    def seed(self, result, *, job=JOB, tenant=T1, org=None, marker=2, legacy=False, shape_only=False, weights=None):
        doc, original = result.requirements, result.original
        self.q("INSERT INTO jobs (job_id, tenant_id, client_organization_id) VALUES (%s, %s, %s)", (job, tenant, org))
        if legacy:
            self.q("INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json) VALUES (%s, %s, %s)",
                   (job, json.dumps({"skills": {"required": ["Python"]}}), json.dumps({"skills": {"required": ["Python"]}})))
            return
        w = weights or [doc["categories"][c]["weight"] for c in CATEGORIES]
        cols = ("job_id, analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education, "
                "weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other")
        vals = [job, json.dumps({"requirements": doc, "keep": {"me": True}}), json.dumps({"requirements": original}), *w]
        ph = ", ".join(["%s"] * len(vals))
        if marker is not None and not shape_only:
            cols, ph, vals = cols + ", requirements_schema_version", ph + ", %s", [*vals, marker]
        self.q(f"INSERT INTO job_criteria ({cols}) VALUES ({ph})", vals)


def _build(pg_server, stage):
    import psycopg2
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    name = "t_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(pg_server.get_uri())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    sync = psycopg2.connect(pg_server.get_uri(name))
    sync.autocommit = True
    with sync.cursor() as cur:
        cur.execute(_synthetic_schema_sql())
        cur.execute("SET search_path = cv_analyzer")
        cur.execute(EXTRA_SCHEMA)
        if stage in ("106", "full"):
            cur.execute(MIGRATION_106.read_text(encoding="utf-8"))
            cur.execute("SET search_path = cv_analyzer")
        if stage == "full":
            cur.execute(MIGRATION_107.read_text(encoding="utf-8"))
            cur.execute("SET search_path = cv_analyzer")
    uri = pg_server.get_uri(name)                      # postgresql://postgres:@/name?host=/socket/dir
    host = uri.split("host=")[1]
    # NullPool: every session opens its own connection (separate backends, no pool shared across event loops)
    engine = create_async_engine(f"postgresql+asyncpg://postgres@/{name}?host={host}", poolclass=NullPool,
                                 connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    env = Env(sync, engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    return env, admin, name


def _teardown(env, admin, name):
    env.sync.close()
    admin.cursor().execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


@pytest.fixture
def pg(pg_server):
    env, admin, name = _build(pg_server, "full")
    yield env
    _teardown(env, admin, name)


@pytest.fixture
def pg_base(pg_server):
    env, admin, name = _build(pg_server, "base")
    yield env
    _teardown(env, admin, name)


@pytest.fixture
def api():
    import importlib
    return importlib.import_module("services.requirements_api")


async def call(env, fn, *args):
    async with env.session() as db:
        return await fn(db, *args)


async def raises(coro, status, code=None):
    with pytest.raises(Exception) as ei:
        await coro
    assert getattr(ei.value, "http_status", None) == status, repr(ei.value)
    if code:
        assert ei.value.code == code
    return ei.value


# ══ migrations on real PostgreSQL ═════════════════════════════════════════════════════════════════════════════════

class TestMigrations:

    @pytest.mark.asyncio
    async def test_106_and_107_leave_existing_legacy_rows_untouched(self, pg_server):
        env, admin, name = _build(pg_server, "base")
        try:
            for n in range(3):
                env.q("INSERT INTO job_criteria (job_id, analysis_json) VALUES (%s, %s)", (str(uuid.UUID(int=n + 1)), json.dumps({"n": n})))
            before = env.q("SELECT job_id::text, analysis_json, weight_skills, weight_experience, weight_education, weight_certifications, "
                           "weight_soft_skills, weight_domain_knowledge, weight_other FROM job_criteria ORDER BY job_id")
            for m in (MIGRATION_106, MIGRATION_107):
                env.q(m.read_text(encoding="utf-8"))
                env.q("SET search_path = cv_analyzer")
            after = env.q("SELECT job_id::text, analysis_json, weight_skills, weight_experience, weight_education, weight_certifications, "
                          "weight_soft_skills, weight_domain_knowledge, weight_other FROM job_criteria ORDER BY job_id")
            assert before == after
            assert env.q("SELECT count(*) FILTER (WHERE requirements_schema_version IS NULL), count(*) FILTER (WHERE requirements_revision = 0), "
                         "count(*) FILTER (WHERE requirements_retired_item_ids = '[]'::jsonb) FROM job_criteria") == [(3, 3, 3)]
        finally:
            _teardown(env, admin, name)

    @pytest.mark.asyncio
    async def test_both_migrations_are_idempotent_and_keep_a_changed_setting(self, pg):
        assert pg.q("SELECT value, type, category, editable FROM system_config WHERE key = %s", (POLICY_KEY,)) == [("true", "boolean", "general", True)]
        pg.q("UPDATE system_config SET value = 'false' WHERE key = %s", (POLICY_KEY,))
        for m in (MIGRATION_106, MIGRATION_107, MIGRATION_106, MIGRATION_107):
            pg.q(m.read_text(encoding="utf-8"))
            pg.q("SET search_path = cv_analyzer")
        assert pg.q("SELECT count(*), min(value) FROM system_config WHERE key = %s", (POLICY_KEY,)) == [(1, "false")]

    @pytest.mark.asyncio
    async def test_weight_constraint_legacy_versus_v2(self, pg):
        import psycopg2

        def insert(weights, marker=None):
            cols = "weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other"
            if marker is None:
                pg.q(f"INSERT INTO job_criteria ({cols}) VALUES (%s,%s,%s,%s,%s,%s,%s)", weights)
            else:
                pg.q(f"INSERT INTO job_criteria ({cols}, requirements_schema_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (*weights, marker))

        insert((30, 25, 15, 10, 10, 5, 5))                                        # legacy, 100
        insert((100, 0, 0, 0, 0, 0, 0), 2)                                        # v2 weighted
        insert((0, 0, 0, 0, 0, 0, 0), 2)                                          # v2 preferred-only / incomplete
        for weights, marker in (((0,) * 7, None), ((30, 25, 15, 10, 10, 5, 4), None), ((50, 0, 0, 0, 0, 0, 0), 2),
                                ((100, 0, 0, 0, 0, 0, 1), 2)):
            with pytest.raises(psycopg2.errors.CheckViolation):
                insert(weights, marker)
        for marker in (1, 3, 0):
            with pytest.raises(psycopg2.errors.CheckViolation):
                insert((100, 0, 0, 0, 0, 0, 0), marker)

    @pytest.mark.asyncio
    async def test_107_column_constraints(self, pg):
        import psycopg2
        pg.q("INSERT INTO job_criteria (job_id) VALUES (%s)", (JOB,))
        with pytest.raises(psycopg2.errors.CheckViolation):
            pg.q("UPDATE job_criteria SET requirements_revision = -1")
        with pytest.raises(psycopg2.errors.CheckViolation):
            pg.q("UPDATE job_criteria SET requirements_retired_item_ids = '{}'::jsonb")
        with pytest.raises(psycopg2.errors.NotNullViolation):
            pg.q("UPDATE job_criteria SET requirements_retired_item_ids = NULL")
        pg.q("UPDATE job_criteria SET requirements_retired_item_ids = '[\"req_a\"]'::jsonb, requirements_revision = 7")
        assert pg.row()["revision"] == 7 and pg.row()["retired"] == ["req_a"]


# ══ reads, saves and the transaction on real SQL ═════════════════════════════════════════════════════════════════

class TestApiOnPostgres:

    @pytest.mark.asyncio
    async def test_get_returns_the_stored_document_original_readiness_and_flags(self, pg, api):
        r = flagged_job()
        pg.seed(r)
        v = await call(pg, api.get_requirements, user(), JOB)
        assert v["revision"] == 0 and v["readiness"]["state"] == "needs_classification_review"
        assert v["original"]["categories"] == r.original["categories"] and v["edited_categories"] == {c: False for c in CATEGORIES}
        assert len(v["classification_warnings"]) == 2 and v["classification_policy"]["require_acknowledgment"] is True

    @pytest.mark.asyncio
    async def test_a_save_commits_json_weights_revision_retired_ids_and_audit_together(self, pg, api):
        r = flagged_job(policy=False)
        pg.seed(r)
        before = pg.row()
        ids = ids_by_text(r.requirements)
        v = await call(pg, api.get_requirements, user(), JOB)
        doc = client_doc(v)
        doc["categories"]["skills"]["items"] = [i for i in doc["categories"]["skills"]["items"] if i["text"] != "SQL"]
        to_required(doc, "Docker")                                                # skills: Python 50 / Docker 50
        doc["categories"]["education"]["items"].append({"text": "BSc", "importance": "required", "weight": 100})
        doc["categories"]["skills"]["weight"], doc["categories"]["education"]["weight"] = 70, 30
        out = await call(pg, api.save_requirements, user(uid=U_ADMIN, role="admin"), JOB, 0, doc)
        row = pg.row()
        req = row["analysis"]["requirements"]
        assert out["revision"] == row["revision"] == 1
        assert row["weights"] == {c: req["categories"][c]["weight"] for c in CATEGORIES}
        assert row["weights"]["skills"] == 70 and row["weights"]["education"] == 30 and sum(row["weights"].values()) == 100
        assert row["retired"] == [ids["SQL"]] and ids["SQL"] not in json.dumps(req)
        assert row["analysis"]["keep"] == {"me": True} and row["edited_by"] == U_ADMIN         # other analysis keys kept
        assert row["original"] == before["original"]                                           # snapshot untouched
        (saved,) = pg.audit("requirements_saved")
        assert saved["resource_id"] == JOB and saved["tenant_id"] == T1 and saved["user_id"] == U_ADMIN
        assert saved["details"]["previous_revision"] == 0 and saved["details"]["revision"] == 1
        assert saved["details"]["removed"] == [ids["SQL"]] and saved["details"]["edited_categories"] == ["skills", "education"]
        # one transaction: the job row and the audit row were written by the same transaction id
        assert pg.q("SELECT (SELECT xmin::text FROM job_criteria WHERE job_id = %s) = (SELECT xmin::text FROM audit_logs "
                    "WHERE action = 'requirements_saved')", (JOB,)) == [(True,)]

    @pytest.mark.asyncio
    async def test_preferred_only_weights_of_zero_are_accepted_for_a_marked_v2_job(self, pg, api):
        pg.seed(preferred_only_job())
        out = await call(pg, api.confirm_no_score, user(), JOB, 0)
        assert out["readiness"]["scoring_mode"] == "none" and sum(pg.row()["weights"].values()) == 0

    @pytest.mark.asyncio
    async def test_invalid_saves_store_nothing(self, pg, api):
        pg.seed(flagged_job(policy=False))
        before = pg.row()
        doc = client_doc(await call(pg, api.get_requirements, user(), JOB))
        item(doc, "Docker")[1]["importance"], item(doc, "Docker")[1]["weight"] = "required", 30
        await raises(call(pg, api.save_requirements, user(), JOB, 0, doc), 422, "invalid_requirements")
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_the_original_snapshot_survives_every_operation(self, pg, api):
        pg.seed(structured_job())
        original = pg.row()["original"]
        v = await call(pg, api.get_requirements, user(), JOB)
        doc = client_doc(v)
        item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
        v = await call(pg, api.save_requirements, user(), JOB, 0, doc)
        iid = item(v["requirements"], "SQL or Postgres")[1]["id"]
        v = await call(pg, api.confirm_structure_review, user(), JOB, 1, iid)
        assert pg.row()["original"] == original and v["original"]["categories"] == original["requirements"]["categories"]

    @pytest.mark.asyncio
    async def test_server_owned_state_is_never_taken_from_the_client(self, pg, api):
        r = preferred_structured_job(cue=None)
        pg.seed(r)
        wid = f"{ids_by_text(r.requirements)['Docker or Podman']}:preferred_cue_missing"
        v = await call(pg, api.get_requirements, user(), JOB)
        # forged acknowledgment + forged preferred-only confirmation + forged structure record in a save
        from services.requirements_v2 import acknowledge_classification_warning, basis_hash as conf_hash
        from services.requirements_v2.structure import basis_hash, basis_of
        doc = client_doc(v)
        doc["classification_review"] = acknowledge_classification_warning(
            r.requirements, wid, user_id="mallory", acknowledged_at="2020-01-01T00:00:00Z")["classification_review"]
        doc["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "mallory", "confirmed_at": "2020-01-01T00:00:00Z",
                                       "basis_hash": conf_hash(r.requirements)}
        item(doc, "Docker or Podman")[1]["text"] = "Docker or Podman or LXC"
        basis = basis_of(item(doc, "Docker or Podman or LXC")[1])
        doc["structure_review"] = {"records": [{"item_id": item(doc, "Docker or Podman or LXC")[1]["id"], "kind": "confirmed",
                                                "user_id": "mallory", "recorded_at": "2020-01-01T00:00:00Z", "basis": basis,
                                                "basis_hash": basis_hash(basis)}]}
        out = await call(pg, api.save_requirements, user(), JOB, 0, doc)
        stored = pg.row()["analysis"]["requirements"]
        assert stored["classification_review"]["acknowledgments"] == [] and stored["scoring_confirmation"] is None
        assert stored.get("structure_review", {"records": []})["records"] == []
        assert {"scoring_confirmation", "classification_review", "structure_review"} <= set(out["discarded_client_fields"])
        # the real flows record the authenticated user and the database time, not client values
        out = await call(pg, api.acknowledge_warning, user(uid=U_ADMIN, role="admin"), JOB, 1, wid)
        (ack,) = pg.row()["analysis"]["requirements"]["classification_review"]["acknowledgments"]
        assert ack["user_id"] == U_ADMIN and ack["acknowledged_at"].startswith("20") and ack["acknowledged_at"] != "2020-01-01T00:00:00Z"
        iid = item(out["requirements"], "Docker or Podman or LXC")[1]["id"]
        out = await call(pg, api.confirm_structure_review, user(uid=U_ADMIN, role="admin"), JOB, 2, iid)
        (rec,) = pg.row()["analysis"]["requirements"]["structure_review"]["records"]
        assert rec["user_id"] == U_ADMIN and rec["kind"] == "confirmed" and rec["basis"]["text"] == "Docker or Podman or LXC"
        assert {a["action"] for a in pg.audit()} >= {"requirements_saved", "requirements_classification_acknowledged",
                                                      "requirements_structure_confirmed"}

    @pytest.mark.asyncio
    async def test_preferred_only_confirmation_lifecycle(self, pg, api):
        pg.seed(preferred_only_job())
        v = await call(pg, api.confirm_no_score, user(uid=U_ADMIN, role="admin"), JOB, 0)
        conf = pg.row()["analysis"]["requirements"]["scoring_confirmation"]
        assert conf["user_id"] == U_ADMIN and v["readiness"]["state"] == "ready"
        await raises(call(pg, api.confirm_no_score, user(), JOB, 1), 409, "nothing_to_confirm")
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL 2"
        out = await call(pg, api.save_requirements, user(), JOB, 1, doc)
        assert pg.row()["analysis"]["requirements"]["scoring_confirmation"] is None
        assert out["readiness"]["state"] == "needs_confirmation"
        assert len(pg.audit("requirements_preferred_only_confirmation_invalidated")) == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation_and_client_org_access_on_real_sql(self, pg, api):
        pg.seed(flagged_job(policy=False), org=ORG)
        await raises(call(pg, api.get_requirements, user(role="admin", tenant=T2), JOB), 404, "not_found")
        await raises(call(pg, api.get_requirements, user(), "not-a-uuid"), 404, "not_found")
        await raises(call(pg, api.get_requirements, user(), JOB), 404, "not_found")             # agency HR, not assigned
        pg.q("INSERT INTO agency_user_clients VALUES (%s, %s, %s)", (U_HR, ORG, T1))
        assert (await call(pg, api.get_requirements, user(), JOB))["revision"] == 0
        assert (await call(pg, api.get_requirements, user(role="admin", uid=U_ADMIN), JOB))["revision"] == 0
        await raises(call(pg, api.save_requirements, user(role="recruiter"), JOB, 0, {}), 403, "forbidden")
        await raises(call(pg, api.save_requirements, user(role="admin", tenant=T2), JOB, 0, {}), 404, "not_found")
        assert pg.row()["revision"] == 0 and pg.audit() == []

    @pytest.mark.asyncio
    async def test_the_policy_setting_is_read_from_system_config(self, pg, api):
        pg.seed(flagged_job())
        assert (await call(pg, api.get_requirements, user(), JOB))["readiness"]["state"] == "needs_classification_review"
        pg.q("UPDATE system_config SET value = 'false' WHERE key = %s", (POLICY_KEY,))
        assert (await call(pg, api.get_requirements, user(), JOB))["readiness"]["state"] == "ready"
        pg.q("UPDATE system_config SET value = 'banana' WHERE key = %s", (POLICY_KEY,))
        assert (await call(pg, api.get_requirements, user(), JOB))["classification_policy"]["require_acknowledgment"] is True
        pg.q("DELETE FROM system_config WHERE key = %s", (POLICY_KEY,))
        assert (await call(pg, api.get_requirements, user(), JOB))["classification_policy"]["require_acknowledgment"] is True

    @pytest.mark.asyncio
    async def test_changing_the_setting_is_audited_in_the_same_transaction(self, pg):
        import importlib
        from fastapi import HTTPException
        pc = importlib.import_module("routers.platform_config")
        sa = user(role="super_admin", uid=U_ADMIN)
        async with pg.session() as db:
            out = await pc.update_platform_config(POLICY_KEY, pc.UpdateConfigValueRequest(value="false"), sa, db)
        assert out["value"] == "false"
        assert pg.q("SELECT value, updated_by::text FROM system_config WHERE key = %s", (POLICY_KEY,)) == [("false", U_ADMIN)]
        (ev,) = pg.audit("requirements_classification_policy_changed")
        assert ev["details"] == {"key": POLICY_KEY, "old_value": "true", "new_value": "false"} and ev["user_id"] == U_ADMIN
        async with pg.session() as db:
            with pytest.raises(HTTPException) as ei:
                await pc.update_platform_config(POLICY_KEY, pc.UpdateConfigValueRequest(value="maybe"), sa, db)
        assert ei.value.status_code == 422
        assert pg.q("SELECT value FROM system_config WHERE key = %s", (POLICY_KEY,)) == [("false",)]
        # an audit failure rolls the setting back too
        pg.q("CREATE OR REPLACE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit down'; END $$")
        pg.q("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION fail_audit()")
        async with pg.session() as db:
            with pytest.raises(Exception, match="audit down"):
                await pc.update_platform_config(POLICY_KEY, pc.UpdateConfigValueRequest(value="true"), sa, db)
        assert pg.q("SELECT value FROM system_config WHERE key = %s", (POLICY_KEY,)) == [("false",)]

    @pytest.mark.asyncio
    async def test_the_scoring_guard_still_refuses_a_job_the_api_saved(self, pg, api):
        from services.requirements_guard import UnsupportedEvaluationError, ensure_job_evaluable, ensure_job_legacy
        pg.seed(flagged_job(policy=False))
        pg.seed(flagged_job(), job=JOB2, legacy=True)
        for entry in (ensure_job_evaluable, ensure_job_legacy):
            async with pg.session() as db:
                with pytest.raises(UnsupportedEvaluationError):
                    await entry(db, JOB, "test")
        async with pg.session() as db:
            await ensure_job_evaluable(db, JOB2, "test")                                    # legacy: passes, unchanged


# ══ audit failure rolls everything back ═══════════════════════════════════════════════════════════════════════════

class TestAuditFailureRollsBack:

    def break_audit(self, pg):
        pg.q("CREATE OR REPLACE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit down'; END $$")
        pg.q("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION fail_audit()")

    def fix_audit(self, pg):
        pg.q("DROP TRIGGER fail_audit ON audit_logs")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["save", "acknowledge", "confirm_structure", "confirm_no_score"])
    async def test_nothing_changes_and_no_lock_is_left_behind(self, pg, api, operation):
        if operation == "confirm_no_score":
            pg.seed(preferred_only_job())
        elif operation == "confirm_structure":
            pg.seed(structured_job())
        else:
            r = flagged_job()
            pg.seed(r)
        before = pg.row()
        audit_rows = pg.q("SELECT count(*) FROM audit_logs")
        self.break_audit(pg)
        if operation == "save":
            doc = client_doc(await call(pg, api.get_requirements, user(), JOB))
            doc["categories"]["skills"]["items"] = [i for i in doc["categories"]["skills"]["items"] if i["text"] != "SQL"]
            doc["categories"]["education"]["items"].append({"text": "BSc", "importance": "preferred", "weight": None})
            coro = call(pg, api.save_requirements, user(), JOB, 0, doc)
        elif operation == "acknowledge":
            coro = call(pg, api.acknowledge_warning, user(), JOB, 0, f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing")
        elif operation == "confirm_no_score":
            coro = call(pg, api.confirm_no_score, user(), JOB, 0)
        else:
            doc = client_doc(await call(pg, api.get_requirements, user(), JOB))
            item(doc, ALT_TEXT)[1]["text"] = "SQL or Postgres"
            self.fix_audit(pg)
            v = await call(pg, api.save_requirements, user(), JOB, 0, doc)
            before = pg.row()
            audit_rows = pg.q("SELECT count(*) FROM audit_logs")
            self.break_audit(pg)
            coro = call(pg, api.confirm_structure_review, user(), JOB, 1, item(v["requirements"], "SQL or Postgres")[1]["id"])
        with pytest.raises(Exception, match="audit down"):
            await coro
        assert pg.row() == before                                           # JSON, 7 weight columns, revision, retired, editor
        assert pg.q("SELECT count(*) FROM audit_logs") == audit_rows                  # no audit row survived either
        # the row lock was released: another connection can take it immediately
        assert pg.q("SELECT job_id FROM job_criteria WHERE job_id = %s FOR UPDATE NOWAIT", (JOB,))
        self.fix_audit(pg)
        # and the same request now succeeds from the unchanged revision
        if operation == "acknowledge":
            assert (await call(pg, api.acknowledge_warning, user(), JOB, 0, f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"))["revision"] == 1


# ══ real concurrency: separate connections ════════════════════════════════════════════════════════════════════════

class TestConcurrencyOnPostgres:

    @pytest.mark.asyncio
    async def test_only_one_of_many_simultaneous_saves_at_one_revision_wins(self, pg, api):
        pg.seed(flagged_job(policy=False))
        base = client_doc(await call(pg, api.get_requirements, user(), JOB))
        docs = []
        for n in range(8):
            d = copy.deepcopy(base)
            item(d, "SQL")[1]["text"] = f"edit {n}"
            docs.append(d)
        results = await asyncio.gather(*[call(pg, api.save_requirements, user(), JOB, 0, d) for d in docs], return_exceptions=True)
        wins = [r for r in results if isinstance(r, dict)]
        losses = [r for r in results if isinstance(r, api.ApiError)]
        assert len(wins) == 1 and len(losses) == 7, results
        assert all(e.code == "requirements_revision_conflict" for e in losses)
        row = pg.row()
        assert row["revision"] == 1 and len(pg.audit("requirements_saved")) == 1
        texts = [i["text"] for i in row["analysis"]["requirements"]["categories"]["skills"]["items"]]
        assert sum(t.startswith("edit ") for t in texts) == 1                              # exactly one edit, no blend

    @pytest.mark.asyncio
    async def test_a_save_and_an_acknowledgment_racing_each_other(self, pg, api):
        r = flagged_job()
        pg.seed(r)
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        doc = client_doc(await call(pg, api.get_requirements, user(), JOB))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        results = await asyncio.gather(call(pg, api.save_requirements, user(), JOB, 0, doc),
                                       call(pg, api.acknowledge_warning, user(), JOB, 0, wid),
                                       call(pg, api.confirm_structure_review, user(), JOB, 0, "req_none"), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in results) == 1
        assert pg.row()["revision"] == 1

    @pytest.mark.asyncio
    async def test_sequential_writers_each_advance_the_revision_by_one(self, pg, api):
        r = flagged_job()
        pg.seed(r)
        w1 = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        w2 = f"{ids_by_text(r.requirements)['Kubernetes']}:preferred_cue_not_linked_to_item"
        await call(pg, api.acknowledge_warning, user(), JOB, 0, w1)
        out = await call(pg, api.acknowledge_warning, user(), JOB, 1, w2)
        assert out["revision"] == 2 == pg.row()["revision"] and out["readiness"]["state"] == "ready"
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 1, w2), 409, "requirements_revision_conflict")

    @pytest.mark.asyncio
    async def test_the_revision_guarded_update_alone_stops_a_stale_writer(self, pg, api):
        """Defence in depth: even if two writers pass the revision check (no lock), only one UPDATE can match."""
        pg.seed(flagged_job(policy=False))
        update_sql = api._UPDATE_SQL
        from sqlalchemy import text
        params = lambda rev: {"aj": json.dumps({"requirements": pg.row()["analysis"]["requirements"]}), "retired": "[]",
                              "uid": U_HR, "jid": JOB, "rev": rev, **{f"w_{c}": pg.row()["weights"][c] for c in CATEGORIES}}
        async with pg.session() as a, pg.session() as b:
            ra = await a.execute(text(update_sql), params(0))
            await a.commit()
            rb = await b.execute(text(update_sql), params(0))
            await b.commit()
        assert (ra.rowcount, rb.rowcount) == (1, 0) and pg.row()["revision"] == 1


# ══ compatibility before the migrations, and missing columns ═══════════════════════════════════════════════════════

class TestBeforeMigrations:

    @pytest.mark.asyncio
    async def test_legacy_jobs_are_refused_cleanly_on_an_unmigrated_database(self, pg_base, api):
        pg_base.seed(flagged_job(), legacy=True)
        before = pg_base.row()
        await raises(call(pg_base, api.get_requirements, user(), JOB), 409, "not_requirements_v2")
        await raises(call(pg_base, api.save_requirements, user(), JOB, 0, {}), 409, "not_requirements_v2")
        await raises(call(pg_base, api.acknowledge_warning, user(), JOB, 0, "a:b"), 409, "not_requirements_v2")
        assert pg_base.row() == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["base", "106"])
    async def test_a_v2_shaped_job_reads_but_every_write_fails_clearly_before_107(self, pg_server, api, stage):
        env, admin, name = _build(pg_server, stage)
        try:
            r = flagged_job(policy=False)
            env.seed(r, shape_only=True)
            v = await call(env, api.get_requirements, user(), JOB)                  # no system_config row either: default Yes
            assert v["revision"] == 0 and v["classification_policy"]["require_acknowledgment"] is True
            before = env.q("SELECT analysis_json, weight_skills FROM job_criteria")
            doc = client_doc(v)
            item(doc, "SQL")[1]["text"] = "SQL 2"
            await raises(call(env, api.save_requirements, user(), JOB, 0, doc), 503, "requirements_migration_missing")
            await raises(call(env, api.acknowledge_warning, user(), JOB, 0, "a:b"), 503, "requirements_migration_missing")
            await raises(call(env, api.confirm_no_score, user(), JOB, 0), 503, "requirements_migration_missing")
            await raises(call(env, api.confirm_structure_review, user(), JOB, 0, "req_x"), 503, "requirements_migration_missing")
            assert env.q("SELECT analysis_json, weight_skills FROM job_criteria") == before
            assert env.q("SELECT job_id FROM job_criteria FOR UPDATE NOWAIT")                    # no lock left behind
            assert env.q("SELECT count(*) FROM audit_logs") == [(0,)]
        finally:
            _teardown(env, admin, name)

    @pytest.mark.asyncio
    async def test_the_guard_sql_works_before_the_migrations(self, pg_base):
        from services.requirements_guard import UnsupportedEvaluationError, ensure_job_evaluable
        pg_base.seed(flagged_job(), legacy=True)
        pg_base.seed(flagged_job(policy=False), job=JOB2, shape_only=True)
        async with pg_base.session() as db:
            await ensure_job_evaluable(db, JOB, "x")
        async with pg_base.session() as db:
            with pytest.raises(UnsupportedEvaluationError):
                await ensure_job_evaluable(db, JOB2, "x")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["requirements_retired_item_ids", "requirements_revision", "both"])
    async def test_a_missing_107_column_is_a_clear_503_before_any_write(self, pg, api, missing):
        """A hand-made partial state (the migration file itself is one transaction): refuse up front, change nothing."""
        pg.seed(flagged_job(policy=False))
        r = flagged_job()
        for col in (["requirements_retired_item_ids", "requirements_revision"] if missing == "both" else [missing]):
            pg.q(f"ALTER TABLE job_criteria DROP COLUMN {col}")
        before = pg.q("SELECT analysis_json, weight_skills FROM job_criteria")
        v = await call(pg, api.get_requirements, user(), JOB)                      # reads keep working
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL 2"
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        for coro in (call(pg, api.save_requirements, user(), JOB, 0, doc), call(pg, api.acknowledge_warning, user(), JOB, 0, wid),
                     call(pg, api.confirm_no_score, user(), JOB, 0), call(pg, api.confirm_structure_review, user(), JOB, 0, "req_x")):
            await raises(coro, 503, "requirements_migration_missing")
        assert pg.q("SELECT analysis_json, weight_skills FROM job_criteria") == before
        assert pg.q("SELECT count(*) FROM audit_logs") == [(0,)]
        assert pg.q("SELECT job_id FROM job_criteria WHERE job_id = %s FOR UPDATE NOWAIT", (JOB,))      # lock released

    @pytest.mark.asyncio
    async def test_a_v2_shaped_job_without_the_marker_is_refused_for_every_write(self, pg, api):
        """Marker NULL after migration 106+107: reads and scoring safeguards work, writes are a clear 409, and the
        marker is NOT set silently."""
        pg.seed(flagged_job(policy=False), shape_only=True)
        r = flagged_job()
        before = pg.row()
        v = await call(pg, api.get_requirements, user(), JOB)
        assert v["revision"] == 0 and v["readiness"]["state"] == "needs_classification_review"
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "SQL 2"
        wid = f"{ids_by_text(r.requirements)['Docker']}:preferred_cue_missing"
        for coro in (call(pg, api.save_requirements, user(), JOB, 0, doc), call(pg, api.acknowledge_warning, user(), JOB, 0, wid),
                     call(pg, api.confirm_no_score, user(), JOB, 0), call(pg, api.confirm_structure_review, user(), JOB, 0, "req_x")):
            exc = await raises(coro, 409, "requirements_schema_marker_missing")
            assert "not set" in exc.message
        assert pg.row() == before and pg.audit() == []
        assert pg.q("SELECT requirements_schema_version FROM job_criteria") == [(None,)]               # not set implicitly
        assert pg.q("SELECT job_id FROM job_criteria WHERE job_id = %s FOR UPDATE NOWAIT", (JOB,))
        from services.requirements_guard import UnsupportedEvaluationError, ensure_job_evaluable, ensure_job_legacy
        for entry in (ensure_job_evaluable, ensure_job_legacy):                                          # safeguards intact
            async with pg.session() as db:
                with pytest.raises(UnsupportedEvaluationError):
                    await entry(db, JOB, "test")

    @pytest.mark.asyncio
    async def test_a_preferred_only_shaped_job_without_the_marker_gets_the_same_clear_409(self, pg, api):
        pg.seed(preferred_only_job(), shape_only=True, weights=[100, 0, 0, 0, 0, 0, 0])
        before = pg.row()
        await raises(call(pg, api.confirm_no_score, user(), JOB, 0), 409, "requirements_schema_marker_missing")
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_marked_v2_and_legacy_jobs_are_unaffected_by_the_new_checks(self, pg, api):
        pg.seed(flagged_job(policy=False))
        pg.seed(flagged_job(), job=JOB2, legacy=True)
        doc = client_doc(await call(pg, api.get_requirements, user(), JOB))
        item(doc, "SQL")[1]["text"] = "SQL 2"
        assert (await call(pg, api.save_requirements, user(), JOB, 0, doc))["revision"] == 1
        await raises(call(pg, api.get_requirements, user(), JOB2), 409, "not_requirements_v2")
        await raises(call(pg, api.save_requirements, user(), JOB2, 0, doc), 409, "not_requirements_v2")
