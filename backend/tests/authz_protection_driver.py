"""Driver for test_authz_protection_http.py. Runs in its OWN process, against the disposable database named by DATABASE_URL. Under
CV_TEST_RUNTIME_ROLE the URL names the restricted role, so every request runs as cv_runtime; otherwise as the superuser.

It drives the REAL app over HTTP (routers, the real get_current_user dependency, the real database session and pool). Added here, in this
process only: a probe route (GET /__authz_probe) reporting the RLS context and the rows the request sees after the real dependency; recorders
in place of the Celery enqueue, the email send and the model call, so a refused request can be shown to have caused none of them; and minted
tokens whose claims deliberately disagree with the database row.

Every write is observed in the database after the request. Nothing is sent, and no model is called. Writes one JSON object to argv[1].
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
import routers.communication as communication  # noqa: E402
import services.email_service as email_service  # noqa: E402
import workers.cv_score as cv_score  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

SEED = json.loads(os.environ["HTTP_SEED"])
CFG = get_settings()
OUT: dict = {"steps": {}, "config": {"environment": CFG.environment, "allow_simulated_payment": CFG.allow_simulated_payment}}
ENQUEUED: list = []
EMAILS: list = []
MODEL_CALLS: list = []
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")

cv_score.score_cv_task.delay = lambda **kw: ENQUEUED.append(kw["application_id"])
legacy_worker.extract_criteria_task.delay = lambda *a, **k: None


async def _email(*args, **kwargs):
    EMAILS.append({"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}})


async def _send_recorder(*args, **kwargs):
    EMAILS.append({"args": [str(a) for a in args]})


email_service.send_activation_email = _email
email_service.send_cv_received_email = _email
communication._send = _send_recorder


async def _score_recorder(*args, **kwargs):
    MODEL_CALLS.append("score_cv")
    return ({}, {})


import services.ai_service as ai_service  # noqa: E402
ai_service.score_cv = _score_recorder

probe = APIRouter()


@probe.get("/__authz_probe")
async def authz_probe(current_user: CurrentUserDep, db=Depends(get_db)):
    """What the request is allowed to read after the real dependency ran: the RLS context it holds and the rows that context exposes."""
    row = (await db.execute(text("""
        SELECT current_setting('app.current_role', true) AS context_role,
               current_setting('app.current_tenant_id', true) AS context_tenant,
               (SELECT count(*) FROM applications WHERE tenant_id <> CAST(:tid AS uuid)) AS other_tenant_applications,
               (SELECT count(*) FROM users WHERE tenant_id <> CAST(:tid AS uuid)) AS other_tenant_users
    """), {"tid": current_user.tenant_id})).mappings().first()
    return {"row_role": current_user.role, "row_tenant": current_user.tenant_id, **dict(row)}


app.include_router(probe)


def token(uid, tenant, role):
    claims = {"sub": uid, "role": role}
    if tenant is not None:
        claims["tenant_id"] = tenant
    return create_access_token(claims)


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
        sql("INSERT INTO jobs (job_id, tenant_id, created_by, title, description, status) VALUES (:j,:t,:u,'Protection job','Synthetic',"
            "'active') ON CONFLICT (job_id) DO NOTHING", {"j": job, "t": tenant, "u": U["admin"] if tenant == T1 else U["other"]})
        sql("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills, weight_skills, weight_experience, weight_education, "
            "weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other, analysis_json) VALUES (:j,'completed',"
            "ARRAY['MS Office'],30,40,10,0,10,10,0,CAST(:a AS jsonb)) ON CONFLICT (job_id) DO NOTHING",
            {"j": job, "a": json.dumps({"skills": {"required": ["MS Office"], "preferred": []}})})


def new_app(tenant, job, name, status="ai_scored", source="manual_upload", file=False):
    app_id = sql("INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, submission_source, candidate_email) "
                 "VALUES (:j,:t,:n,:s,'under_review',:src,'candidate@example.com') RETURNING application_id::text AS id",
                 {"j": job, "t": tenant, "n": name, "s": status, "src": source})[0]["id"]
    if file:
        sql("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) "
            "VALUES (:a,:t,'cv.pdf','application/pdf','cvs/protection.pdf')", {"a": app_id, "t": tenant})
    return app_id


seed_world()
new_app(T1, JOB1, "Tenant One Candidate")
new_app(T2, JOB2, "Tenant Two Candidate")
T1_TAG = sql("INSERT INTO candidate_tags (tenant_id, tag_name, created_by) VALUES (:t,'Protection tag',:u) RETURNING tag_id::text AS id",
             {"t": T1, "u": U["hr"]})[0]["id"]
sql("UPDATE tenants SET max_users = 20 WHERE tenant_id = :t", {"t": T1})


def side_effects():
    return {"enqueued": len(ENQUEUED), "emails": len(EMAILS), "model_calls": len(MODEL_CALLS)}


def guarded_write(name, who, method, path, body, observe, expect_status=None):
    """A write attempted by `who`: the HTTP status, the database observed before and after, and every side effect the refusal must not cause."""
    before, effects_before = observe(), side_effects()
    st, resp = http(method, path, who_token(who), body)
    after, effects_after = observe(), side_effects()
    return {"who": who, "status": st, "db_before": before, "db_after": after, "db_changed": before != after,
            "side_effects": {k: effects_after[k] - effects_before[k] for k in effects_before},
            "response": (json.dumps(resp, default=str)[:200] if resp is not None else None)}


# ── 1. controls: pooled sessions and post-commit reads ──────────────────────────────────────────────────────────────────────────
@step("pooled_sequence_control")
def _pooled():
    """Several requests on ONE engine and ONE event loop (the production pool is reused, not disposed between requests). Each probe must report
    the context of its own token, never the previous request's."""
    plan = [("hr", "hr_manager", T1), ("other", "admin", T2), ("super", "super_admin", T1), ("admin", "admin", T1),
            ("viewer", "viewer", T1), ("other", "admin", T2)]

    async def go():
        results = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            for who, role, tenant in plan:
                r = await c.get("/__authz_probe", headers={"Authorization": f"Bearer {who_token(who)}"})
                body = r.json() if r.status_code == 200 else {}
                results.append({"who": who, "status": r.status_code, "context_role": body.get("context_role"),
                                "context_tenant": body.get("context_tenant"), "expected_role": role, "expected_tenant": tenant,
                                "other_tenant_applications": body.get("other_tenant_applications")})
        return results
    try:
        return {"requests": _run(go())}
    finally:
        pass


@step("valid_platform_admin_control")
def _platform():
    st, body = http("GET", "/__authz_probe", who_token("super"))
    dash, _ = http("GET", "/admin/dashboard", who_token("super"))
    return {"probe_status": st, "probe": body, "platform_dashboard_status": dash}


@step("post_commit_bulk_workflow_control")
def _post_commit():
    a = new_app(T1, JOB1, "Post Commit Candidate")
    st, body = http("PATCH", "/applications/bulk-workflow-status", who_token("hr"), {"workflow_status": "shortlisted", "application_ids": [a]})
    names = [c["candidate_name"] for c in (body or {}).get("updated_candidates", [])]
    return {"status": st, "updated_count": (body or {}).get("updated_count"), "post_commit_names": names}


@step("post_commit_tenant_user_control")
def _post_commit_user():
    EMAILS.clear()
    st, body = http("POST", "/tenant/users", who_token("admin"), {"email": "post.commit@t1.example", "full_name": "Post Commit", "role": "recruiter"})
    sent = [e for e in EMAILS if "args" in e]
    return {"status": st, "user_created": bool(body and body.get("success")), "company_name_sent": sent[0]["kwargs"].get("company_name") if sent else None}


# ── 2. permitted-role controls: a write by each role that may write is stored ──────────────────────────────────────────────────
@step("control_hr_comment")
def _c_hr_comment():
    a = new_app(T1, JOB1, "Control Comment HR")
    return guarded_write("comment", "hr", "POST", f"/applications/{a}/comments", {"comment_text": "hr note"},
                         lambda: count("SELECT count(*) AS n FROM candidate_comments WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("control_recruiter_comment")
def _c_rec_comment():
    a = new_app(T1, JOB1, "Control Comment Recruiter")
    return guarded_write("comment", "recruiter", "POST", f"/applications/{a}/comments", {"comment_text": "recruiter note"},
                         lambda: count("SELECT count(*) AS n FROM candidate_comments WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("control_recruiter_creates_job_preserved")
def _c_rec_job():
    return guarded_write("job", "recruiter", "POST", "/jobs", {"title": "Recruiter Created Job", "description": JOB_TEXT},
                         lambda: count("SELECT count(*) AS n FROM jobs WHERE tenant_id = :t AND title = 'Recruiter Created Job'", {"t": T1}))


@step("control_admin_adds_tag")
def _c_admin_tag():
    a = new_app(T1, JOB1, "Control Tag Admin")
    return guarded_write("tag", "admin", "POST", f"/applications/{a}/tags", {"tag_ids": [T1_TAG]},
                         lambda: count("SELECT count(*) AS n FROM application_candidate_tags WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("control_hr_scores_pending")
def _c_hr_score():
    a = new_app(T1, JOB1, "Control Pending HR", status="pending", file=True)
    ENQUEUED.clear()
    res = guarded_write("score-pending", "hr", "POST", "/applications/score-pending", {"job_id": JOB1},
                        lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND processing_status = 'queued'", {"a": a}))
    res["enqueued"] = list(ENQUEUED)
    return res


@step("control_recruiter_moves_one_candidate")
def _c_rec_move():
    a = new_app(T1, JOB1, "Control Move Recruiter")
    return guarded_write("workflow move", "recruiter", "PATCH", f"/applications/{a}/workflow-status", {"workflow_status": "shortlisted"},
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND workflow_status = 'shortlisted'", {"a": a}))


# ── 3. simulated payment: refused in production before any mutation; the development opt-in works ──────────────────────────────
@step("f1_select_plan_for_tenant")
def _f1_select():
    sql("UPDATE tenants SET subscription_status = 'pending_plan_selection', pending_plan = NULL WHERE tenant_id = :t", {"t": T1})
    st, body = http("POST", "/tenant/subscription/select-plan", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly"})
    row = sql("SELECT subscription_status, pending_plan FROM tenants WHERE tenant_id = :t", {"t": T1})[0]
    return {"status": st, "db_subscription_status": row["subscription_status"], "db_pending_plan": row["pending_plan"]}


def _tenant_row():
    return sql("SELECT subscription_status, plan, pending_plan, subscription_ends_at IS NOT NULL AS has_end FROM tenants WHERE tenant_id = :t",
               {"t": T1})[0]


@step("f1_admin_simulates_payment_in_production")
def _f1_prod():
    before = _tenant_row()
    EMAILS.clear()
    st, body = http("POST", "/tenant/subscription/simulate-payment", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    after = _tenant_row()
    return {"status": st, "body": body, "db_before": before, "db_after": after, "db_changed": before != after, "emails": len(EMAILS)}


@step("f1_hr_simulates_payment_in_production")
def _f1_hr():
    before = _tenant_row()
    st, body = http("POST", "/tenant/subscription/simulate-payment", who_token("hr"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    return {"status": st, "db_changed": before != _tenant_row()}


@step("f1_development_opt_in_control")
def _f1_dev():
    """The same request with the development environment and the explicit opt-in (set on the process settings for this step only, then restored)."""
    saved = (CFG.environment, CFG.allow_simulated_payment)
    try:
        CFG.environment, CFG.allow_simulated_payment = "development", True
        st, body = http("POST", "/tenant/subscription/simulate-payment", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    finally:
        CFG.environment, CFG.allow_simulated_payment = saved
    row = _tenant_row()
    return {"status": st, "db_subscription_status": row["subscription_status"], "db_plan": row["plan"]}


@step("f1_development_without_opt_in_is_refused")
def _f1_dev_no_opt_in():
    sql("UPDATE tenants SET subscription_status = 'pending_payment', pending_plan = 'starter' WHERE tenant_id = :t", {"t": T1})
    saved = (CFG.environment, CFG.allow_simulated_payment)
    try:
        CFG.environment, CFG.allow_simulated_payment = "development", False
        st, _ = http("POST", "/tenant/subscription/simulate-payment", who_token("admin"), {"plan": "starter", "billing_cycle": "monthly", "result": "success"})
    finally:
        CFG.environment, CFG.allow_simulated_payment = saved
    return {"status": st, "subscription_status": _tenant_row()["subscription_status"]}


# ── 4. viewer refusals: no database change, no enqueue, no email, no model call ───────────────────────────────────────────────
@step("v_comment")
def _v1():
    a = new_app(T1, JOB1, "Viewer Comment")
    return guarded_write("comment", "viewer", "POST", f"/applications/{a}/comments", {"comment_text": "viewer note"},
                         lambda: count("SELECT count(*) AS n FROM candidate_comments WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_communication_log")
def _v2():
    a = new_app(T1, JOB1, "Viewer Comm Log")
    return guarded_write("communication log", "viewer", "POST", f"/applications/{a}/communications/log",
                         {"subject": "Outreach", "body": "viewer", "status": "draft"},
                         lambda: count("SELECT count(*) AS n FROM candidate_communications WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_communication_send_email")
def _v3():
    a = new_app(T1, JOB1, "Viewer Send")
    return guarded_write("send email", "viewer", "POST", f"/applications/{a}/communications/send",
                         {"subject": "Hello", "body": "viewer", "to_email": "candidate@example.com"},
                         lambda: count("SELECT count(*) AS n FROM candidate_communications WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_tag_add")
def _v4():
    a = new_app(T1, JOB1, "Viewer Tag")
    return guarded_write("tag", "viewer", "POST", f"/applications/{a}/tags", {"tag_ids": [T1_TAG]},
                         lambda: count("SELECT count(*) AS n FROM application_candidate_tags WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_tag_create")
def _v5():
    return guarded_write("tag create", "viewer", "POST", "/tags/", {"tag_name": "Viewer Tag"},
                         lambda: count("SELECT count(*) AS n FROM candidate_tags WHERE tag_name = 'Viewer Tag'"))


@step("v_talent_pool")
def _v6():
    a = new_app(T1, JOB1, "Viewer Pool")
    return guarded_write("talent pool", "viewer", "POST", f"/applications/{a}/talent-pool", {"is_talent_pool": True},
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND is_talent_pool", {"a": a}))


@step("v_interview")
def _v7():
    a = new_app(T1, JOB1, "Viewer Interview")
    return guarded_write("interview", "viewer", "POST", f"/applications/{a}/interviews", {"interview_type": "phone_screen", "interviewers": []},
                         lambda: count("SELECT count(*) AS n FROM candidate_interviews WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_saved_view")
def _v8():
    return guarded_write("saved view", "viewer", "POST", "/candidate-saved-views",
                         {"name": "Viewer View", "filters": {}, "visibility_type": "private"},
                         lambda: count("SELECT count(*) AS n FROM candidate_saved_views WHERE name = 'Viewer View'"))


@step("v_create_job")
def _v9():
    return guarded_write("job", "viewer", "POST", "/jobs", {"title": "Viewer Created Job", "description": JOB_TEXT},
                         lambda: count("SELECT count(*) AS n FROM jobs WHERE tenant_id = :t AND title = 'Viewer Created Job'", {"t": T1}))


@step("v_update_criteria")
def _v10():
    return guarded_write("criteria", "viewer", "PUT", f"/jobs/{JOB1}/criteria", {"skills": ["Viewer Skill"]},
                         lambda: count("SELECT count(*) AS n FROM job_criteria WHERE job_id = CAST(:j AS uuid) AND 'Viewer Skill' = ANY(skills)", {"j": JOB1}))


@step("v_score_pending")
def _v11():
    a = new_app(T1, JOB1, "Viewer Pending", status="pending", file=True)
    ENQUEUED.clear()
    res = guarded_write("score-pending", "viewer", "POST", "/applications/score-pending", {"job_id": JOB1},
                        lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND processing_status = 'queued'", {"a": a}))
    res["enqueued"] = list(ENQUEUED)
    return res


@step("v_delete_uploaded_cv")
def _v12():
    a = new_app(T1, JOB1, "Viewer Upload", status="pending", source="manual_upload", file=True)
    return guarded_write("delete CV", "viewer", "DELETE", f"/applications/{a}", None,
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid)", {"a": a}))


@step("v_bulk_batch")
def _v13():
    return guarded_write("bulk batch", "viewer", "POST", "/bulk-upload/batches", {"job_id": JOB1},
                         lambda: count("SELECT count(*) AS n FROM bulk_upload_batches WHERE job_id = CAST(:j AS uuid)", {"j": JOB1}))


@step("v_workflow_move_single")
def _v14():
    a = new_app(T1, JOB1, "Viewer Move")
    return guarded_write("workflow move", "viewer", "PATCH", f"/applications/{a}/workflow-status", {"workflow_status": "shortlisted"},
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND workflow_status = 'shortlisted'", {"a": a}))


@step("v_workflow_move_bulk")
def _v15():
    a = new_app(T1, JOB1, "Viewer Bulk Move")
    return guarded_write("bulk move", "viewer", "PATCH", "/applications/bulk-workflow-status", {"workflow_status": "shortlisted", "application_ids": [a]},
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND workflow_status = 'shortlisted'", {"a": a}))


@step("v_reset_stuck")
def _v16():
    a = new_app(T1, JOB1, "Viewer Stuck", status="queued", file=True)
    sql("UPDATE applications SET updated_at = now() - interval '2 hours' WHERE application_id = CAST(:a AS uuid)", {"a": a})
    return guarded_write("reset stuck", "viewer", "POST", "/applications/reset-stuck", {"job_id": JOB1},
                         lambda: count("SELECT count(*) AS n FROM applications WHERE application_id = CAST(:a AS uuid) AND processing_status = 'queued'", {"a": a}))


# ── 4b. routes exempt from the shared dependency (they check roles inline or in a service): a viewer is still refused ──────────────
@step("e_viewer_creates_campaign")
def _e1():
    return guarded_write("campaign", "viewer", "POST", "/campaigns", {"name": "Viewer Campaign"},
                         lambda: count("SELECT count(*) AS n FROM job_campaigns WHERE name = 'Viewer Campaign'"))


@step("e_viewer_creates_client_organization")
def _e2():
    return guarded_write("client org", "viewer", "POST", "/client-organizations", {"organization_name": "Viewer Client Org"},
                         lambda: count("SELECT count(*) AS n FROM client_organizations WHERE organization_name = 'Viewer Client Org'"))


@step("e_viewer_creates_template")
def _e3():
    return guarded_write("template", "viewer", "POST", "/communication/templates/",
                         {"name": "Viewer Template", "subject": "s", "body": "b"},
                         lambda: count("SELECT count(*) AS n FROM candidate_message_templates WHERE name = 'Viewer Template'"))


@step("e_viewer_updates_workflow_policies")
def _e4():
    return guarded_write("workflow policies", "viewer", "PATCH", "/tenant/workflow-policies", {"allow_bulk_reject": False},
                         lambda: count("SELECT count(*) AS n FROM tenant_workflow_policies WHERE tenant_id = CAST(:t AS uuid) AND policies::text LIKE '%false%'", {"t": T1}))


@step("e_viewer_creates_tenant_user")
def _e5():
    return guarded_write("tenant user", "viewer", "POST", "/tenant/users", {"email": "viewer.made@t1.example", "full_name": "Made", "role": "recruiter"},
                         lambda: count("SELECT count(*) AS n FROM users WHERE email = 'viewer.made@t1.example'"))


# ── 5. token claims that no longer match the database record ───────────────────────────────────────────────────────────────────
@step("t_demoted_super_token")
def _t1():
    sql("UPDATE users SET role = 'admin' WHERE user_id = :u", {"u": U["super"]})
    stale = token(U["super"], T1, "super_admin")
    st_probe, probe = http("GET", "/__authz_probe", stale)
    st_dash, _ = http("GET", "/admin/dashboard", stale)
    fresh_st, fresh = http("GET", "/__authz_probe", token(U["super"], T1, "admin"))
    return {"db_role": sql("SELECT role FROM users WHERE user_id = :u", {"u": U["super"]})[0]["role"],
            "stale_probe_status": st_probe, "stale_probe": probe, "stale_dashboard_status": st_dash,
            "fresh_probe_status": fresh_st, "fresh_probe": fresh}


@step("t_reassigned_admin_token")
def _t2():
    sql("UPDATE users SET tenant_id = :t WHERE user_id = :u", {"t": T2, "u": U["admin"]})
    stale = token(U["admin"], T1, "admin")
    st_users, _ = http("GET", "/tenant/users", stale)
    st_probe, probe = http("GET", "/__authz_probe", stale)
    return {"db_tenant": sql("SELECT tenant_id::text AS t FROM users WHERE user_id = :u", {"u": U["admin"]})[0]["t"],
            "stale_tenant_users_status": st_users, "stale_probe_status": st_probe, "stale_probe": probe}


@step("t_reassigned_super_claim")
def _t3():
    stale = token(U["admin"], T1, "super_admin")
    st_probe, probe = http("GET", "/__authz_probe", stale)
    return {"stale_probe_status": st_probe, "stale_probe": probe}


@step("t_null_tenant_claim")
def _t4():
    st, body = http("GET", "/__authz_probe", token(U["hr"], None, "hr_manager"))
    return {"status": st, "body": body}


@step("t_deactivated_hr")
def _t5():
    sql("UPDATE users SET status = 'disabled' WHERE user_id = :u", {"u": U["hr"]})
    st, body = http("GET", "/auth/me", who_token("hr"))
    return {"db_status": sql("SELECT status FROM users WHERE user_id = :u", {"u": U["hr"]})[0]["status"], "status": st, "body": body}


@step("t_deactivated_viewer_with_super_claim")
def _t6():
    sql("UPDATE users SET status = 'disabled' WHERE user_id = :u", {"u": U["viewer"]})
    st, body = http("GET", "/__authz_probe", token(U["viewer"], T1, "super_admin"))
    return {"status": st, "body": body}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
