"""Rollback drill for the access pause, on a RESTORED COPY of a disposable PostgreSQL 16 database, with the workers controlled.

The drill never touches the live database. It seeds paused work, dumps the database with pg_dump, restores it into a copy with pg_restore,
and then:
  * runs deploy/billing/rollback_access_pause_guard.sql on the copy with psql: it must pass, and it must change nothing (paused work, its
    markers, its tokens and the job statuses are compared before and after);
  * shows the guard refuses when the constraint cannot hold the marker;
  * runs the CURRENT watchdog against the copy: paused work is left queued and is not failed, and an unpaused stuck control row IS failed,
    which proves the watchdog's exclusion is the cause;
  * shows the hazard the runbook depends on: an OLD watchdog (without the exclusion) would fail the paused rows (counted by SQL);
  * runs the roll-forward resume on the copy: each paused application, each pending v2 attempt and each pending legacy job is dispatched
    exactly once; a second resume dispatches nothing.
Dispatch is recorded by stubs (no Celery broker), and no model is called. Requires the system PostgreSQL 16 client tools (psql, pg_dump,
pg_restore) next to the server binaries.
"""
import asyncio
import pathlib
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

GUARD = BACKEND / "deploy" / "billing" / "rollback_access_pause_guard.sql"
INVENTORY = BACKEND / "deploy" / "billing" / "inventory_legacy_validity.sql"
T1, T2, T3 = (str(uuid.UUID(int=n)) for n in (0xD01, 0xD02, 0xD03))
U1, U2, U3 = (str(uuid.UUID(int=n)) for n in (0xD11, 0xD12, 0xD13))
JD = "Responsibilities: deliver backend services. Required skills: Python and PostgreSQL. Qualifications: a degree or equivalent practice."


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


def seed(d):
    d.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type, plan, subscription_ends_at) VALUES "
        "(%s,'Suspended','s.example','active','suspended','organization','starter', now() + interval '60 days'),"
        "(%s,'Other','o.example','active','active','organization','starter', now() + interval '60 days'),"
        "(%s,'Legacy no date','l.example','active','active','organization','starter', NULL)", (T1, T2, T3))
    for uid, tid in ((U1, T1), (U2, T2), (U3, T3)):
        d.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x','A','admin','active')",
            (uid, tid, f"a-{uid[-4:]}@x.example"))
    v2_job, legacy_job, other_job = (str(uuid.uuid4()) for _ in range(3))
    d.q("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'V2 role',%s)", (v2_job, T1, U1, JD))
    d.q("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'Legacy role',%s)", (legacy_job, T1, U1, JD))
    d.q("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'Other role',%s)", (other_job, T2, U2, JD))
    d.q("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_extraction_token) "
        "VALUES (%s,'pending',2,%s)", (v2_job, str(uuid.uuid4())))
    d.q("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s,'pending')", (legacy_job,))
    d.q("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s,'completed')", (other_job,))
    apps = {}
    for label, job, tid, stopped, hours in (("paused_1", legacy_job, T1, "access_paused", 3), ("paused_2", legacy_job, T1, "access_paused", 5),
                                              ("paused_3", legacy_job, T1, "access_paused", 2), ("control_stuck", other_job, T2, None, 3)):
        aid = str(uuid.uuid4())
        apps[label] = aid
        d.q("INSERT INTO applications (application_id, job_id, tenant_id, candidate_name, candidate_email, submission_source, processing_status, "
            "queued_at, stopped_reason) VALUES (%s,%s,%s,'Candidate','c@x.example','manual_upload','queued', now() - (%s * interval '1 hour'), %s)",
            (aid, job, tid, hours, stopped))
        d.q("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) VALUES (%s,%s,'cv.pdf','application/pdf',%s)",
            (aid, tid, f"files/{aid}.pdf"))
    d.q("UPDATE tenants SET subscription_status = 'suspended' WHERE tenant_id = %s", (T1,))
    return {"v2_job": v2_job, "legacy_job": legacy_job, "other_job": other_job, "apps": apps}


def psql(port, dbname, path):
    return subprocess.run([str(rh.PG_BIN / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
                           "-d", dbname, "-f", str(path)], capture_output=True, text=True)


def restore_copy(d, tmp_path, name):
    """A restored copy: pg_dump of the disposable database, pg_restore into a new database of the same cluster."""
    dump = tmp_path / f"{name}.dump"
    subprocess.run([str(rh.PG_BIN / "pg_dump"), "-h", "127.0.0.1", "-p", str(d.cluster.port), "-U", "postgres", "-Fc", "-f", str(dump), d.name],
                   check=True, capture_output=True)
    d.admin.cursor().execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8'")
    subprocess.run([str(rh.PG_BIN / "pg_restore"), "-h", "127.0.0.1", "-p", str(d.cluster.port), "-U", "postgres", "--no-owner",
                    "-d", name, str(dump)], capture_output=True)
    return name


def url_for(d, dbname):
    return f"postgresql+asyncpg://postgres@127.0.0.1:{d.cluster.port}/{dbname}"


def sync_sql(d, dbname, sql, params=None):
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user="postgres", dbname=dbname, options="-c search_path=cv_analyzer")
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None
    finally:
        conn.close()


def paused_state(d, dbname):
    return sync_sql(d, dbname, "SELECT application_id::text, processing_status, stopped_reason FROM applications "
                               "WHERE stopped_reason = 'access_paused' ORDER BY application_id")


def job_snapshot(d, dbname):
    return sync_sql(d, dbname, "SELECT job_id::text, criteria_extraction_status, requirements_extraction_token::text FROM job_criteria ORDER BY job_id")


@pytest.fixture
def world(cluster):
    d = cluster.build()
    seeded = seed(d)
    yield d, seeded
    d.drop()


# ── the guard passes on the restored copy and changes nothing ─────────────────────────────────────────────────────────────────────
def test_the_rollback_guard_passes_on_a_restored_copy_and_changes_nothing(world, tmp_path):
    d, seeded = world
    copy = restore_copy(d, tmp_path, "rb_copy")
    assert len(paused_state(d, copy)) == 3, "the restored copy carries the paused work"
    before = (paused_state(d, copy), job_snapshot(d, copy), sync_sql(d, copy, "SELECT subscription_status FROM tenants WHERE tenant_id = %s", (T1,)))
    res = psql(d.cluster.port, copy, GUARD)
    assert res.returncode == 0, res.stderr
    assert "kept as they are: 3" in res.stderr, res.stderr
    after = (paused_state(d, copy), job_snapshot(d, copy), sync_sql(d, copy, "SELECT subscription_status FROM tenants WHERE tenant_id = %s", (T1,)))
    assert after == before, "the guard must not clear, move or rewrite anything"


def test_the_rollback_guard_refuses_when_the_constraint_cannot_hold_the_marker(world, tmp_path):
    d, _ = world
    copy = restore_copy(d, tmp_path, "rb_refuse")
    sync_sql(d, copy, "UPDATE applications SET stopped_reason = NULL WHERE stopped_reason = 'access_paused'")
    sync_sql(d, copy, "ALTER TABLE applications DROP CONSTRAINT applications_stopped_reason_check")
    sync_sql(d, copy, "ALTER TABLE applications ADD CONSTRAINT applications_stopped_reason_check CHECK (stopped_reason IN ('other'))")
    res = psql(d.cluster.port, copy, GUARD)
    assert res.returncode != 0 and "does not allow access_paused" in res.stderr, res.stderr


def test_the_inventory_lists_the_dateless_tenant_and_not_the_tenant_with_an_end_date(world):
    d, _ = world
    res = psql(d.cluster.port, d.name, INVENTORY)
    assert res.returncode == 0, res.stderr
    assert T3 in res.stdout and T1 not in res.stdout and T2 not in res.stdout, res.stdout


# ── the current watchdog on the restored copy: paused work is not failed; the hazard of an old watchdog is measured ──────────────────────
def test_after_restore_the_current_watchdog_leaves_paused_work_queued_and_fails_only_the_unpaused_control(world, tmp_path, monkeypatch):
    import config
    from workers import watchdog
    d, seeded = world
    copy = restore_copy(d, tmp_path, "rb_watchdog")
    hazard = sync_sql(d, copy, "SELECT count(*) FROM applications WHERE stopped_reason = 'access_paused' "
                               "AND queued_at < now() - interval '15 minutes'")[0][0]
    assert hazard == 3, "the paused rows are past the watchdog window: an OLD watchdog would fail them (the runbook disables it)"
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(database_url=url_for(d, copy), db_schema="cv_analyzer"))
    watchdog.reset_stuck_scoring_tasks()
    assert [r[1] for r in paused_state(d, copy)] == ["queued"] * 3, "paused work stays queued"
    control = sync_sql(d, copy, "SELECT processing_status, stopped_reason FROM applications WHERE job_id = %s", (seeded["other_job"],))
    assert control == [("failed", "processing_error")], control


# ── roll-forward: each paused item is dispatched exactly once; a second resume dispatches nothing ───────────────────────────────
def test_roll_forward_resumes_each_paused_item_exactly_once_and_a_second_resume_dispatches_nothing(world, tmp_path, monkeypatch):
    import workers.criteria_worker as criteria
    import workers.cv_score as cv_score
    import workers.requirements_v2_extraction_worker as v2worker
    from services.extraction_gate import resume_paused_extraction
    from services.paused_work_service import resume_paused_applications
    d, seeded = world
    copy = restore_copy(d, tmp_path, "rb_forward")
    rec = {"score": [], "v2": [], "legacy": [], "cand": []}
    monkeypatch.setattr(cv_score.score_cv_task, "delay", lambda **k: rec["score"].append(k["application_id"]))
    monkeypatch.setattr(v2worker.extract_requirements_v2_task, "delay", lambda *a: rec["v2"].append(a[0]))
    monkeypatch.setattr(criteria.extract_criteria_task, "delay", lambda *a: rec["legacy"].append(a[0]))
    monkeypatch.setattr(v2worker.extract_candidate_task, "delay", lambda *a: rec["cand"].append(a[0]))

    # the provisioning that restores access (an active validity), then the resume the admin route performs
    sync_sql(d, copy, "UPDATE tenants SET subscription_status = 'active', subscription_ends_at = now() + interval '60 days' WHERE tenant_id = %s", (T1,))

    async def resume(s):
        return (await resume_paused_applications(s, T1), await resume_paused_extraction(s, T1))

    def run_resume():
        async def go():
            eng = create_async_engine(url_for(d, copy), poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
            try:
                async with async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)() as s:
                    await s.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
                    return await resume(s)
            finally:
                await eng.dispose()
        return asyncio.run(go())

    dispatched_apps, extraction = run_resume()
    assert dispatched_apps == 3 and sorted(rec["score"]) == sorted(seeded["apps"][k] for k in ("paused_1", "paused_2", "paused_3"))
    assert extraction == {"v2": 1, "legacy": 1, "candidates": 0, "failed": 0}, extraction
    assert rec["legacy"] == [seeded["legacy_job"]] and rec["v2"] and len(rec["v2"]) == 1
    assert "control_stuck" not in str(rec["score"]), "another tenant's work is not dispatched"

    # A repeated resume (provisioning again) re-sends the pending extraction tasks, which carry no dispatch marker. Their duplicates are
    # refused at the claim: no model call and no state change (tests/test_extraction_access_gate.py). Paused applications are cleared by
    # their own atomic claim, so they are never dispatched twice.
    second_apps, second_extraction = run_resume()
    assert second_apps == 0, "a repeated resume never re-dispatches an application"
    assert second_extraction == {"v2": 1, "legacy": 1, "candidates": 0, "failed": 0}, second_extraction
    assert len(rec["score"]) == 3, "exactly one dispatch per paused application"
    assert paused_state(d, copy) == [], "the resumed applications are no longer marked paused"
