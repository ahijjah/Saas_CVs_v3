"""Driver for test_authz_reproduction_http.py. Runs in its OWN process, against the disposable database named by DATABASE_URL. Under
CV_TEST_RUNTIME_ROLE the URL names the restricted role, so every request runs as cv_runtime; otherwise as the superuser.

It drives the REAL app over HTTP (routers, the real get_current_user dependency, the real database session). Three things are added here, and
only in this process: a probe route (GET /__authz_probe) that reports the row-level-security context and the rows the request can see after the
real dependency has run; a recorder in place of the Celery enqueue; and minted tokens (signed with the test secret) that carry claims which no
longer match the database row, which is the situation after demotion, reassignment or deactivation. Nothing is sent and no model is called.

Writes one JSON object to argv[1]: every observation, with the database state read back after each request (not only the HTTP status).
"""
import asyncio
import json
import os
import pathlib
import sys
import traceback
import warnings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from fastapi import APIRouter, Depends  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
from auth.dependencies import CurrentUserDep  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from config import get_settings  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402
import workers.cv_score as cv_score  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}, "config": {"environment": get_settings().environment, "debug": get_settings().debug}}
ENQUEUED: list = []
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")

cv_score.score_cv_task.delay = lambda **kw: ENQUEUED.append(kw["application_id"])
legacy_worker.extract_criteria_task.delay = lambda *a, **k: None

probe = APIRouter()


@probe.get("/__authz_probe")
async def authz_probe(current_user: CurrentUserDep, db=Depends(get_db)):
    """What the request is allowed to read after the real dependency ran: the RLS context it holds and the rows that context exposes."""
    row = (await db.execute(text("""
        SELECT current_setting('app.current_role', true) AS context_role,
               current_setting('app.current_tenant_id', true) AS context_tenant,
               (SELECT count(*) FROM applications) AS visible_applications,
               (SELECT count(*) FROM applications WHERE tenant_id <> CAST(:tid AS uuid)) AS other_tenant_applications,
               (SELECT count(*) FROM users WHERE tenant_id <> CAST(:tid AS uuid)) AS other_tenant_users
    """), {"tid": current_user.tenant_id})).mappings().first()
    return {"row_role": current_user.role, "row_tenant": current_user.tenant_id, **dict(row)}


app.include_router(probe)


def token(uid, tenant, role):
    return create_access_token({"sub": uid, "tenant_id": tenant, "role": role})


def who_token(who):
    uid, tenant, role = SEED["users"][who]
    return token(uid, tenant, role)


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapper())


def http(method, url, bearer, body=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {bearer}"}, json=body)
            await asyncio.sleep(0.05)
        return r.status_code, (r.json() if r.content else None)
    return _run(go())


def sql(statement, params=None):
    """Test-side observation and seeding: the super_admin context, so the observer sees every row."""
    async def go():
        async with database.engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
            res = await conn.execute(text(statement), params or {})
            return [dict(r._mapping) for r in res] if res.returns_rows else []
    return _run(go())


def count(statement, params=None):
    return sql(statement, params)[0]["n"]


def step(name):
    def deco(fn):
        try:
            OUT["steps"][name] = fn() or {}
        except Exception:
            OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
    return deco


T1 = SEED["users"]["hr"][1]
T2 = SEED["users"]["other"][1]
U = {k: v[0] for k, v in SEED["users"].items()}
JOB1 = "00000000-0000-0000-0000-0000000000f1"
JOB2 = "00000000-0000-0000-0000-0000000000f2"


def seed_world():
    for job, tenant in ((JOB1, T1), (JOB2, T2)):
        sql("INSERT INTO jobs (job_id, tenant_id, created_by, title, description, status) VALUES (:j,:t,:u,'Repro job','Synthetic',"
            "'active') ON CONFLICT (job_id) DO NOTHING", {"j": job, "t": tenant, "u": U["admin"] if tenant == T1 else U["other"]})
        sql("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills, weight_skills, weight_experience, weight_education, "
            "weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other, analysis_json) VALUES (:j,'completed',"
            "ARRAY['MS Office'],30,40,10,0,10,10,0,CAST(:a AS jsonb)) ON CONFLICT (job_id) DO NOTHING",
            {"j": job, "a": json.dumps({"skills": {"required": ["MS Office"], "preferred": []}})})


def new_app(tenant, job, name, status="ai_scored", source="manual_upload", file=False):
    row = sql("INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, submission_source) "
              "VALUES (:j,:t,:n,:s,'under_review',:src) RETURNING application_id::text AS id", {"j": job, "t": tenant, "n": name, "s": status, "src": source})
    app_id = row[0]["id"]
    if file:
        sql("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) "
            "VALUES (:a,:t,'cv.pdf','application/pdf','cvs/repro.pdf')", {"a": app_id, "t": tenant})
    return app_id


seed_world()
new_app(T1, JOB1, "Tenant One Candidate")
new_app(T2, JOB2, "Tenant Two Candidate")          # the other tenant's row: what a cross-tenant read would expose
T1_TAG = sql("INSERT INTO candidate_tags (tenant_id, tag_name, created_by) VALUES (:t,'Repro tag',:u) RETURNING tag_id::text AS id",
             {"t": T1, "u": U["hr"]})[0]["id"]


# ── Finding 1: simulated payment under the production configuration ─────────────────────────────────────────────────────────────
@step("f1_production_configuration")
def _cfg():
    return {"environment": OUT["config"]["environment"], "debug": OUT["config"]["debug"]}


@step("f1_permitted_admin_selects_plan")
def _f1_select():
    sql("UPDATE tenants SET subscription_status = 'pending_plan_selection', pending_plan = NULL WHERE tenant_id = :t", {"t": T1})
    st, body = http("POST", "/tenant/subscription/select-plan", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly"})
    row = sql("SELECT subscription_status, pending_plan FROM tenants WHERE tenant_id = :t", {"t": T1})[0]
    return {"status": st, "body": body, "db_subscription_status": row["subscription_status"], "db_pending_plan": row["pending_plan"]}


@step("f1_control_hr_manager_cannot_simulate_payment")
def _f1_control():
    st, body = http("POST", "/tenant/subscription/simulate-payment", who_token("hr"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    row = sql("SELECT subscription_status FROM tenants WHERE tenant_id = :t", {"t": T1})[0]
    return {"status": st, "db_subscription_status": row["subscription_status"]}


@step("f1_admin_simulates_payment_in_production_configuration")
def _f1_pay():
    st, body = http("POST", "/tenant/subscription/simulate-payment", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    row = sql("SELECT subscription_status, plan, pending_plan, subscription_ends_at IS NOT NULL AS has_end FROM tenants WHERE tenant_id = :t", {"t": T1})[0]
    return {"status": st, "body": body, "db_subscription_status": row["subscription_status"], "db_plan": row["plan"],
            "db_pending_plan": row["pending_plan"], "db_has_subscription_end": row["has_end"]}


# ── Finding 2: viewer writes beyond the workflow move, with the database read back ──────────────────────────────────────────────
def write_probe(name, who, method, path, body, observe, app_id=None):
    before = observe()
    st, resp = http(method, path, who_token(who), body)
    after = observe()
    return {"who": who, "status": st, "db_before": before, "db_after": after, "db_changed": before != after,
            "response": (json.dumps(resp)[:160] if resp is not None else None)}


@step("f2_viewer_posts_comment")
def _f2_comment():
    a = new_app(T1, JOB1, "Comment Target")
    OUT["f2_comment_app"] = a
    return write_probe("comment", "viewer", "POST", f"/applications/{a}/comments", {"comment_text": "viewer note"},
                       lambda: count("SELECT count(*) AS n FROM candidate_comments WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_control_hr_posts_comment")
def _f2_comment_hr():
    a = new_app(T1, JOB1, "Comment Control")
    return write_probe("comment", "hr", "POST", f"/applications/{a}/comments", {"comment_text": "hr note"},
                       lambda: count("SELECT count(*) AS n FROM candidate_comments WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_viewer_logs_communication")
def _f2_comm():
    a = new_app(T1, JOB1, "Comm Target")
    return write_probe("communication log", "viewer", "POST", f"/applications/{a}/communications/log",
                       {"subject": "Outreach", "body": "Logged by a viewer", "status": "draft"},
                       lambda: count("SELECT count(*) AS n FROM candidate_communications WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_viewer_adds_tag")
def _f2_tag():
    a = new_app(T1, JOB1, "Tag Target")
    return write_probe("tag", "viewer", "POST", f"/applications/{a}/tags", {"tag_ids": [T1_TAG]},
                       lambda: count("SELECT count(*) AS n FROM application_candidate_tags WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_viewer_toggles_talent_pool")
def _f2_pool():
    a = new_app(T1, JOB1, "Pool Target")
    return write_probe("talent pool", "viewer", "POST", f"/applications/{a}/talent-pool", {"is_talent_pool": True},
                       lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND is_talent_pool", {"a": a}))


@step("f2_viewer_creates_interview")
def _f2_interview():
    a = new_app(T1, JOB1, "Interview Target")
    return write_probe("interview", "viewer", "POST", f"/applications/{a}/interviews", {"interview_type": "phone_screen", "interviewers": []},
                       lambda: count("SELECT count(*) AS n FROM candidate_interviews WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_viewer_creates_job")
def _f2_job():
    return write_probe("job", "viewer", "POST", "/jobs", {"title": "Viewer Created Job", "description": JOB_TEXT},
                       lambda: count("SELECT count(*) AS n FROM jobs WHERE tenant_id = :t AND title = 'Viewer Created Job'", {"t": T1}))


@step("f2_control_hr_creates_job")
def _f2_job_hr():
    return write_probe("job", "hr", "POST", "/jobs", {"title": "HR Created Job", "description": JOB_TEXT},
                       lambda: count("SELECT count(*) AS n FROM jobs WHERE tenant_id = :t AND title = 'HR Created Job'", {"t": T1}))


@step("f2_viewer_scores_pending_cvs")
def _f2_score():
    a = new_app(T1, JOB1, "Pending For Scoring", status="pending", file=True)
    ENQUEUED.clear()
    res = write_probe("score-pending", "viewer", "POST", "/applications/score-pending", {"job_id": JOB1},
                      lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND processing_status = 'queued'", {"a": a}))
    res["enqueued"] = list(ENQUEUED)
    return res


@step("f2_viewer_deletes_uploaded_cv")
def _f2_delete():
    a = new_app(T1, JOB1, "Pending Upload", status="pending", source="manual_upload", file=True)
    return write_probe("delete uploaded CV", "viewer", "DELETE", f"/applications/{a}", None,
                       lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("f2_viewer_creates_bulk_batch")
def _f2_batch():
    return write_probe("bulk-upload batch", "viewer", "POST", "/bulk-upload/batches", {"job_id": JOB1},
                       lambda: count("SELECT count(*) AS n FROM bulk_upload_batches WHERE job_id = CAST(:j AS uuid)", {"j": JOB1}))


# ── Finding 3: a token whose claims no longer match the database row ────────────────────────────────────────────────────────────
@step("f3_probe_fresh_token_super_admin")
def _f3_fresh():
    st, body = http("GET", "/__authz_probe", token(U["super"], T1, "super_admin"))
    return {"status": st, "probe": body}


@step("f3_demotion_then_stale_super_token")
def _f3_demote():
    sql("UPDATE users SET role = 'admin' WHERE user_id = :u", {"u": U["super"]})
    stale = token(U["super"], T1, "super_admin")               # minted before the demotion, still inside its 24-hour life
    st_probe, probe_body = http("GET", "/__authz_probe", stale)
    st_admin, admin_body = http("GET", "/admin/dashboard", stale)
    row = sql("SELECT role FROM users WHERE user_id = :u", {"u": U["super"]})[0]
    fresh_st, fresh_body = http("GET", "/__authz_probe", token(U["super"], T1, "admin"))
    return {"db_role_after_demotion": row["role"], "stale_probe_status": st_probe, "stale_probe": probe_body,
            "stale_admin_dashboard_status": st_admin, "fresh_admin_probe": fresh_body, "fresh_admin_probe_status": fresh_st}


@step("f3_reassignment_then_stale_admin_token")
def _f3_reassign():
    sql("UPDATE users SET tenant_id = :t WHERE user_id = :u", {"t": T2, "u": U["admin"]})
    stale = token(U["admin"], T1, "admin")                     # the token still names tenant 1
    st_users, users_body = http("GET", "/tenant/users", stale)
    st_probe, probe_body = http("GET", "/__authz_probe", stale)
    row = sql("SELECT tenant_id::text AS t FROM users WHERE user_id = :u", {"u": U["admin"]})[0]
    return {"db_tenant_after_reassignment": row["t"], "stale_tenant_users_status": st_users, "stale_probe_status": st_probe,
            "stale_probe": probe_body}


@step("f3_reassignment_then_stale_super_claim")
def _f3_reassign_super():
    stale = token(U["admin"], T1, "super_admin")               # a super claim issued before the account was reassigned and demoted
    st_probe, probe_body = http("GET", "/__authz_probe", stale)
    return {"stale_probe_status": st_probe, "stale_probe": probe_body}


@step("f3_deactivation_then_stale_token")
def _f3_disable():
    sql("UPDATE users SET status = 'disabled' WHERE user_id = :u", {"u": U["hr"]})
    st_me, me_body = http("GET", "/auth/me", who_token("hr"))
    row = sql("SELECT status FROM users WHERE user_id = :u", {"u": U["hr"]})[0]
    return {"db_status": row["status"], "stale_me_status": st_me, "stale_me": me_body}


@step("f3_deactivation_then_stale_super_claim")
def _f3_disable_super():
    sql("UPDATE users SET status = 'disabled' WHERE user_id = :u", {"u": U["viewer"]})
    stale = token(U["viewer"], T1, "super_admin")
    st_probe, probe_body = http("GET", "/__authz_probe", stale)
    return {"stale_probe_status": st_probe, "stale_probe": probe_body}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
