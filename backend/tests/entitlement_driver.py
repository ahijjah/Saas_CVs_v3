"""Driver for test_entitlement_http.py. Runs in its OWN process against the disposable database named by DATABASE_URL (the superuser, or the
restricted role under CV_TEST_RUNTIME_ROLE). Drives the REAL routes over HTTP and the REAL scoring-task entry point, and reads the database
back after each step. Nothing is paid, sent or scored: the scoring dispatch is recorded by a stub, and no model is called. Expiry is set
with the database clock (now() + interval), so the boundaries are the database's, not this process's. Writes one JSON object to argv[1]."""
import asyncio
import json
import os
import pathlib
import sys
import time
import traceback
import uuid
import warnings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
import routers.admin as admin_router  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from config import get_settings  # noqa: E402
from main import app  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402
import workers.cv_score as cv_score  # noqa: E402
from workers.watchdog import reset_stuck_scoring_tasks  # noqa: E402

legacy_worker.extract_criteria_task.delay = lambda *a, **k: None   # job creation queues extraction; not part of these checks

DISPATCHED: list[dict] = []          # every scoring dispatch, recorded instead of sent to Celery
FAIL_DISPATCH = {"on": False}


def _record_dispatch(**kwargs):
    if FAIL_DISPATCH["on"]:
        raise RuntimeError("broker unavailable (simulated)")
    DISPATCHED.append(kwargs)


cv_score.score_cv_task.delay = _record_dispatch

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}, "config": {"environment": get_settings().environment}}
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")
PLANS = [("trial", "Trial", 0, 0, 14, 2), ("starter", "Starter", 49, 490, 14, 3),
         ("professional", "Professional", 149, 1490, 14, 15), ("enterprise", "Enterprise", 499, 4990, 14, 100)]


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapper())


def sql(statement, params=None):
    async def go():
        async with database.engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
            res = await conn.execute(text(statement), params or {})
            return [dict(r._mapping) for r in res] if res.returns_rows else []
    return _run(go())


def http(method, url, uid, tenant, role, body=None):
    bearer = create_access_token({"sub": uid, "tenant_id": tenant, "role": role})

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {bearer}"}, json=body)
            await asyncio.sleep(0.05)
        return r.status_code, _payload(r)
    return _run(go())


def public_apply(job_code, name, email):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.post("/applications/public", data={"job_code": job_code, "candidate_name": name, "email": email},
                             files={"file": ("cv.pdf", b"%PDF-1.4 minimal test cv", "application/pdf")})
            await asyncio.sleep(0.05)
        return r.status_code, _payload(r)
    return _run(go())


def _payload(r):
    try:
        return r.json() if r.content else None
    except ValueError:
        return {"non_json_body": r.text[:300]}


def step(name):
    def deco(fn):
        try:
            OUT["steps"][name] = fn() or {}
        except Exception:
            OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
    return deco


for code, name, monthly, yearly, trial_days, cvs_users in PLANS:
    sql("INSERT INTO subscription_plans (plan_code, plan_name, monthly_price, yearly_price, currency, trial_days, max_campaigns, "
        "max_processed_cvs_per_month, max_users, status, display_order) VALUES (:c,:n,:m,:y,'USD',:td,5,500,:u,'active',1) "
        "ON CONFLICT (plan_code) DO NOTHING", {"c": code, "n": name, "m": monthly, "y": yearly, "td": trial_days, "u": cvs_users})


SCENARIOS: dict = {}


def scenario(key, status, plan=None, pending=None, ends_sql=None, trial_sql=None, domain=None):
    """A fresh tenant with one admin and one hr_manager. ``ends_sql`` / ``trial_sql`` are database expressions (e.g. "now() + interval '1 hour'")
    or None for NULL, so the expiry is set by the database clock."""
    tid, admin, hr = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    dom = domain or f"{key}.example"
    sql("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, pending_plan, plan, tenant_type, max_users) "
        "VALUES (:t,:n,:d,'active',:s,:p,:pl,'organization',20)", {"t": tid, "n": key, "d": dom, "s": status, "p": pending, "pl": plan})
    set_dates(tid, ends_sql, trial_sql)
    for uid, role in ((admin, "admin"), (hr, "hr_manager")):
        sql("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (:u,:t,:e,'x',:n,:r,'active')",
            {"u": uid, "t": tid, "e": f"{role}-{uid[:8]}@{dom}", "n": role, "r": role})
    SCENARIOS[key] = {"tenant": tid, "admin": admin, "hr": hr}
    return SCENARIOS[key]


def set_dates(tid, ends_sql=None, trial_sql=None):
    sql(f"UPDATE tenants SET subscription_ends_at = {ends_sql or 'NULL'}, trial_end_at = {trial_sql or 'NULL'}, "
        f"trial_start_at = CASE WHEN {trial_sql or 'NULL'} IS NULL THEN NULL ELSE now() END WHERE tenant_id = CAST(:t AS uuid)", {"t": tid})


def set_status(tid, status):
    sql("UPDATE tenants SET subscription_status = :s WHERE tenant_id = CAST(:t AS uuid)", {"s": status, "t": tid})


def call(key, method, url, body=None, who="admin"):
    s = SCENARIOS[key]
    uid = s["admin"] if who == "admin" else s["hr"]
    role = "admin" if who == "admin" else "hr_manager"
    return http(method, url, uid, s["tenant"], role, body)


def platform(method, url, body):
    return http(method, url, SEED["super"][0], SEED["super"][1], "super_admin", body)


def tags_count(key):
    return sql("SELECT count(*) AS n FROM candidate_tags WHERE tenant_id = :t", {"t": SCENARIOS[key]["tenant"]})[0]["n"]


def make_job(key, title):
    st, body = call(key, "POST", "/jobs", {"title": title, "description": JOB_TEXT})
    assert st == 201, (st, body)
    row = sql("SELECT job_id::text AS job_id, job_code FROM jobs WHERE title = :t AND tenant_id = :tid", {"t": title, "tid": SCENARIOS[key]["tenant"]})[0]
    return row


def insert_application(key, job_id, status="queued", stopped=None, queued_hours=3):
    aid = str(uuid.uuid4())
    tid = SCENARIOS[key]["tenant"]
    sql("INSERT INTO applications (application_id, job_id, tenant_id, candidate_name, candidate_email, submission_source, processing_status, "
        "queued_at, stopped_reason) VALUES (:a, :j, :t, 'Queued Candidate', 'q@x.example', 'manual_upload', :s, "
        "now() - (CAST(:h AS integer) * interval '1 hour'), :r)",
        {"a": aid, "j": job_id, "t": tid, "s": status, "h": queued_hours, "r": stopped})
    sql("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) VALUES (:a, :t, 'cv.pdf', 'application/pdf', :p)",
        {"a": aid, "t": tid, "p": f"files/{aid}.pdf"})
    return aid


def application_row(aid):
    return sql("SELECT processing_status, stopped_reason, queued_at > now() - interval '5 minutes' AS recently_queued FROM applications "
               "WHERE application_id = CAST(:a AS uuid)", {"a": aid})[0]


def scored_count(aid):
    return sql("SELECT count(*) AS n FROM application_scores WHERE application_id = CAST(:a AS uuid)", {"a": aid})[0]["n"]


def dispatched_for(aid):
    return [d for d in DISPATCHED if d.get("application_id") == aid]


# ── expiry boundaries (the database clock decides) ──────────────────────────────────────────────────────────────────────────
@step("write_with_end_in_the_future_is_allowed")
def _a():
    scenario("b_future", "active", plan="starter", ends_sql="now() + interval '1 hour'")
    st, body = call("b_future", "POST", "/tags/", {"tag_name": "future tag"})
    return {"status": st, "tags": tags_count("b_future")}


@step("write_one_second_after_the_end_is_refused")
def _b():
    scenario("b_past", "active", plan="starter", ends_sql="now() - interval '1 second'")
    st, body = call("b_past", "POST", "/tags/", {"tag_name": "late tag"})
    return {"status": st, "body": body, "tags": tags_count("b_past")}


@step("trial_one_second_after_its_end_is_refused")
def _c():
    scenario("t_past", "trial", trial_sql="now() - interval '1 second'")
    st, body = call("t_past", "POST", "/tags/", {"tag_name": "trial late tag"})
    return {"status": st, "body": body, "tags": tags_count("t_past")}


@step("expiry_crossed_during_the_session_is_enforced_on_the_next_request")
def _d():
    scenario("cross", "active", plan="starter", ends_sql="now() + interval '1500 milliseconds'")
    first_status, _ = call("cross", "POST", "/tags/", {"tag_name": "before"})
    time.sleep(2.0)
    second_status, second_body = call("cross", "POST", "/tags/", {"tag_name": "after"})
    return {"first_status": first_status, "second_status": second_status, "second_body": second_body, "tags": tags_count("cross")}


# ── states that carry no current validity ──────────────────────────────────────────────────────────────────────────────────
@step("legacy_active_without_an_end_date_is_read_only")
def _e():
    scenario("legacy", "active", plan="starter", ends_sql=None)
    st_w, body_w = call("legacy", "POST", "/tags/", {"tag_name": "legacy tag"})
    st_r, _ = call("legacy", "GET", "/jobs")
    st_u, usage = call("legacy", "GET", "/tenant/usage")
    return {"write_status": st_w, "write_body": body_w, "read_status": st_r, "usage_status": st_u,
            "access": (usage or {}).get("access"), "tags": tags_count("legacy"),
            "after": sql("SELECT subscription_status, subscription_ends_at IS NULL AS ends_missing FROM tenants WHERE tenant_id = :t",
                         {"t": SCENARIOS["legacy"]["tenant"]})[0]}


@step("trial_without_an_end_date_is_read_only")
def _f():
    scenario("trial_nodate", "trial", trial_sql=None)
    st, body = call("trial_nodate", "POST", "/tags/", {"tag_name": "nodate tag"})
    return {"status": st, "body": body, "tags": tags_count("trial_nodate")}


@step("pending_activation_refuses_writes_and_says_so")
def _g():
    scenario("pending", "pending_payment", pending="starter")
    st, body = call("pending", "POST", "/tags/", {"tag_name": "pending tag"})
    st_u, usage = call("pending", "GET", "/tenant/usage")
    return {"status": st, "body": body, "tags": tags_count("pending"), "access": (usage or {}).get("access")}


# ── suspension and expiry keep the data and allow billing and reads ───────────────────────────────────────────────────────
@step("suspended_tenant_keeps_reads_billing_and_its_data_and_refuses_writes")
def _h():
    scenario("sus", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("sus", "Suspended Keeps Job")
    set_status(SCENARIOS["sus"]["tenant"], "suspended")
    st_read, jobs = call("sus", "GET", "/jobs")
    st_write, write_body = call("sus", "POST", "/jobs", {"title": "Blocked Job", "description": JOB_TEXT})
    st_tag, _ = call("sus", "POST", "/tags/", {"tag_name": "blocked"})
    st_plans, _ = http("GET", "/subscription/plans", SCENARIOS["sus"]["admin"], SCENARIOS["sus"]["tenant"], "admin")
    st_usage, usage = call("sus", "GET", "/tenant/usage")
    st_select, select_body = call("sus", "POST", "/tenant/subscription/select-plan", {"plan": "starter", "billing_cycle": "monthly"})
    return {"read_status": st_read, "kept_job_visible": job["job_code"] in json.dumps(jobs), "job_code": job["job_code"],
            "write_status": st_write, "write_body": write_body, "tag_status": st_tag, "plans_status": st_plans,
            "usage_status": st_usage, "access": (usage or {}).get("access"), "select_status": st_select,
            "select_body": select_body, "jobs_after": sql("SELECT count(*) AS n FROM jobs WHERE tenant_id = :t", {"t": SCENARIOS["sus"]["tenant"]})[0]["n"]}


@step("expired_trial_keeps_its_data_and_refuses_writes")
def _i():
    scenario("exp_trial", "trial", trial_sql="now() + interval '1 hour'")
    make_job("exp_trial", "Trial Kept Job")
    sql("UPDATE tenants SET trial_end_at = now() - interval '1 second' WHERE tenant_id = :t", {"t": SCENARIOS["exp_trial"]["tenant"]})
    st_read, jobs = call("exp_trial", "GET", "/jobs")
    st_write, body = call("exp_trial", "POST", "/jobs", {"title": "After Expiry", "description": JOB_TEXT})
    st_u, usage = call("exp_trial", "GET", "/tenant/usage")
    kept = sql("SELECT job_code FROM jobs WHERE title = 'Trial Kept Job' AND tenant_id = :t", {"t": SCENARIOS["exp_trial"]["tenant"]})[0]["job_code"]
    return {"read_status": st_read, "kept_job_visible": kept in json.dumps(jobs), "write_status": st_write, "write_body": body,
            "access": (usage or {}).get("access")}


@step("expired_tenant_may_request_a_plan_but_stays_read_only_until_provisioned")
def _j():
    scenario("exp_plan", "active", plan="starter", ends_sql="now() - interval '1 hour'")
    st_sel, sel = call("exp_plan", "POST", "/tenant/subscription/select-plan", {"plan": "professional", "billing_cycle": "monthly"})
    st_w, body = call("exp_plan", "POST", "/tags/", {"tag_name": "still blocked"})
    st_u, usage = call("exp_plan", "GET", "/tenant/usage")
    return {"select_status": st_sel, "select_body": sel, "write_status": st_w, "write_body": body, "access": (usage or {}).get("access"),
            "after": sql("SELECT subscription_status, plan, pending_plan FROM tenants WHERE tenant_id = :t", {"t": SCENARIOS["exp_plan"]["tenant"]})[0]}


@step("full_access_reports_full_state")
def _k():
    scenario("full", "active", plan="starter", ends_sql="now() + interval '1 hour'")
    st, usage = call("full", "GET", "/tenant/usage")
    return {"status": st, "access": (usage or {}).get("access")}


# ── public intake ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@step("public_intake_is_refused_while_suspended_and_stores_nothing")
def _l():
    scenario("pub_sus", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("pub_sus", "Public Suspended Job")
    sql("UPDATE job_criteria SET criteria_extraction_status = 'completed' WHERE job_id = CAST(:j AS uuid)", {"j": job["job_id"]})
    set_status(SCENARIOS["pub_sus"]["tenant"], "suspended")
    before_apps = sql("SELECT count(*) AS n FROM applications WHERE job_id = CAST(:j AS uuid)", {"j": job["job_id"]})[0]["n"]
    before_dispatch = len(DISPATCHED)
    st, body = public_apply(job["job_code"], "Pat Candidate", "pat@candidate.example")
    after_apps = sql("SELECT count(*) AS n FROM applications WHERE job_id = CAST(:j AS uuid)", {"j": job["job_id"]})[0]["n"]
    logs = sql("SELECT status, error_message FROM application_intake_log WHERE tenant_id = :t AND job_id = CAST(:j AS uuid)",
               {"t": SCENARIOS["pub_sus"]["tenant"], "j": job["job_id"]})
    return {"status": st, "body": body, "apps_before": before_apps, "apps_after": after_apps,
            "dispatch_added": len(DISPATCHED) - before_dispatch, "log_statuses": [r["status"] for r in logs],
            "log_reasons": [r["error_message"] for r in logs]}


@step("public_intake_is_accepted_while_active_control")
def _m():
    scenario("pub_ok", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("pub_ok", "Public Active Job")
    # extraction is stubbed in this driver, so the job analysis is completed here; intake would otherwise refuse for analysis, not billing
    sql("UPDATE job_criteria SET criteria_extraction_status = 'completed' WHERE job_id = CAST(:j AS uuid)", {"j": job["job_id"]})
    before_dispatch = len(DISPATCHED)
    st, body = public_apply(job["job_code"], "Active Candidate", "active@candidate.example")
    apps = sql("SELECT count(*) AS n FROM applications WHERE job_id = CAST(:j AS uuid)", {"j": job["job_id"]})[0]["n"]
    logs = sql("SELECT status, error_message FROM application_intake_log WHERE tenant_id = :t AND job_id = CAST(:j AS uuid)",
               {"t": SCENARIOS["pub_ok"]["tenant"], "j": job["job_id"]})
    return {"status": st, "body": body, "apps": apps, "dispatch_added": len(DISPATCHED) - before_dispatch,
            "log_reasons": [r["error_message"] for r in logs], "log_statuses": [r["status"] for r in logs]}


# ── queued work: pause, watchdog, resume ──────────────────────────────────────────────────────────────────────────────────
@step("queued_scoring_pauses_while_suspended_without_failing")
def _n():
    scenario("q_pause", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("q_pause", "Queue Pause Job")
    aid = insert_application("q_pause", job["job_id"], queued_hours=3)
    set_status(SCENARIOS["q_pause"]["tenant"], "suspended")
    before_dispatch = len(DISPATCHED)
    raised = None
    try:
        result = cv_score.score_cv_task(application_id=aid, job_id=job["job_id"], tenant_id=SCENARIOS["q_pause"]["tenant"],
                                        file_path=f"files/{aid}.pdf", mime_type="application/pdf")
    except Exception as exc:  # noqa: BLE001 - a raise here would mean the task retries
        raised = repr(exc)
        result = None
    return {"raised": raised, "result": result, "row": application_row(aid), "scored": scored_count(aid),
            "dispatch_added": len(DISPATCHED) - before_dispatch, "aid": aid}


@step("watchdog_leaves_paused_work_and_still_fails_an_unpaused_stuck_row")
def _o():
    scenario("q_ctl", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("q_ctl", "Watchdog Control Job")
    control = insert_application("q_ctl", job["job_id"], queued_hours=3)
    paused = insert_application("q_pause", job["job_id"], queued_hours=3, stopped="access_paused")
    reset_stuck_scoring_tasks()
    return {"control": application_row(control), "paused": application_row(paused)}


@step("reactivation_resumes_paused_work_once")
def _p():
    scenario("q_resume", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("q_resume", "Resume Job")
    aid = insert_application("q_resume", job["job_id"], queued_hours=3, stopped="access_paused")
    set_status(SCENARIOS["q_resume"]["tenant"], "suspended")
    before_dispatch = len(dispatched_for(aid))
    body = {"action": "reactivate", "expires_at": "2099-01-01T00:00:00Z", "reason": "bank transfer confirmed"}
    st, resp = platform("PATCH", f"/admin/tenants/{SCENARIOS['q_resume']['tenant']}/subscription", body)
    dispatched = dispatched_for(aid)
    return {"status": st, "resumed": (resp or {}).get("resumed_applications"), "row": application_row(aid),
            "dispatched_before": before_dispatch, "dispatched_after": len(dispatched),
            "args": {k: dispatched[-1][k] for k in ("tenant_id", "job_id", "file_path", "mime_type")} if dispatched else None,
            "aid": aid, "tenant": SCENARIOS["q_resume"]["tenant"]}


def _p_aid():
    return OUT["steps"]["reactivation_resumes_paused_work_once"]["aid"]


@step("a_second_resume_dispatches_nothing_more")
def _q():
    s = SCENARIOS["q_resume"]
    aid = _p_aid()
    before = len(dispatched_for(aid))
    st, resp = platform("PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                        {"action": "assign_plan", "plan_code": "starter", "expires_at": "2099-02-01T00:00:00Z", "reason": "repeat provisioning"})
    return {"status": st, "resumed": (resp or {}).get("resumed_applications"), "dispatched_before": before, "dispatched_after": len(dispatched_for(aid))}


def _p_aid():
    return OUT["steps"]["reactivation_resumes_paused_work_once"]["aid"]


@step("failed_dispatch_repauses_the_row_and_does_not_lose_it")
def _r():
    scenario("q_fail", "active", plan="starter", ends_sql="now() + interval '10 days'")
    job = make_job("q_fail", "Failing Dispatch Job")
    aid = insert_application("q_fail", job["job_id"], queued_hours=3, stopped="access_paused")
    set_status(SCENARIOS["q_fail"]["tenant"], "suspended")
    FAIL_DISPATCH["on"] = True
    try:
        st, resp = platform("PATCH", f"/admin/tenants/{SCENARIOS['q_fail']['tenant']}/subscription",
                            {"action": "reactivate", "expires_at": "2099-03-01T00:00:00Z", "reason": "dispatch failure drill"})
    finally:
        FAIL_DISPATCH["on"] = False
    return {"status": st, "resumed": (resp or {}).get("resumed_applications"), "row": application_row(aid)}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
