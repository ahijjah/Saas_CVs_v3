"""Driver for test_subscription_activation_http.py. Runs in its OWN process against the disposable database named by DATABASE_URL (the
superuser, or the restricted role under CV_TEST_RUNTIME_ROLE). It drives the REAL routes over HTTP and reads the tenant row and the audit log back
from the database after each request. Each scenario has its own tenant, so states do not leak between scenarios. Nothing is paid or sent; no
model is called. Writes one JSON object to argv[1]."""
import asyncio
import json
import os
import pathlib
import sys
import traceback
import uuid
import warnings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from config import get_settings  # noqa: E402
from main import app  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

legacy_worker.extract_criteria_task.delay = lambda *a, **k: None   # job creation queues extraction; not part of these checks

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}, "config": {"environment": get_settings().environment, "allow_simulated_payment": get_settings().allow_simulated_payment}}
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=bool(os.environ.get("SUBS_RAISE"))), base_url="http://test") as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {bearer}"}, json=body)
            await asyncio.sleep(0.05)
        try:
            payload = r.json() if r.content else None
        except ValueError:
            payload = {"non_json_body": r.text[:300]}
        return r.status_code, payload
    return _run(go())


def step(name):
    def deco(fn):
        try:
            OUT["steps"][name] = fn() or {}
        except Exception:
            OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
    return deco


# the plan catalogue (migrations 010 and 024 seed these; the stand-in build may not, so they are ensured here with the same codes and prices)
for code, name, monthly, yearly, trial_days, cvs_users in PLANS:
    sql("INSERT INTO subscription_plans (plan_code, plan_name, monthly_price, yearly_price, currency, trial_days, max_campaigns, "
        "max_processed_cvs_per_month, max_users, status, display_order) VALUES (:c,:n,:m,:y,'USD',:td,5,500,:u,'active',1) "
        "ON CONFLICT (plan_code) DO NOTHING", {"c": code, "n": name, "m": monthly, "y": yearly, "td": trial_days, "u": cvs_users})


def tenant_state(tid):
    return sql("SELECT subscription_status, plan, pending_plan, subscription_started_at IS NOT NULL AS started, "
               "subscription_ends_at IS NOT NULL AS ends, trial_end_at IS NOT NULL AS trial_ends FROM tenants WHERE tenant_id = :t",
               {"t": tid})[0]


def audit_count():
    return sql("SELECT count(*) AS n FROM audit_logs")[0]["n"]


SCENARIOS = {}


def scenario(key, status, pending=None, domain=None):
    """A fresh tenant with one admin, one hr_manager and one super_admin is created for each scenario, in the state given."""
    tid, admin, hr = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    dom = domain or f"{key}.example"
    sql("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, pending_plan, tenant_type, max_users) "
        "VALUES (:t,:n,:d,'active',:s,:p,'organization',20)", {"t": tid, "n": key, "d": dom, "s": status, "p": pending})
    for uid, role in ((admin, "admin"), (hr, "hr_manager")):
        sql("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (:u,:t,:e,'x',:n,:r,'active')",
            {"u": uid, "t": tid, "e": f"{role}-{uid[:8]}@{dom}", "n": role, "r": role})
    SCENARIOS[key] = {"tenant": tid, "admin": admin, "hr": hr}
    return SCENARIOS[key]


def run_subscribe(key, label, status, plan_code, pending=None, who="admin"):
    s = scenario(key, status, pending)
    before, audit_before = tenant_state(s["tenant"]), audit_count()
    uid = s["admin"] if who == "admin" else s["hr"]
    role = "admin" if who == "admin" else "hr_manager"
    st, body = http("POST", "/subscription/subscribe", uid, s["tenant"], role, {"plan_code": plan_code})
    after = tenant_state(s["tenant"])
    return {"label": label, "status": st, "body": body, "before": before, "after": after, "changed": before != after,
            "audit_rows_added": audit_count() - audit_before}


# ── subscribe: every state and plan class ───────────────────────────────────────────────────────────────────────────────────────
@step("free_plan_trial_via_subscribe")
def _a():
    return run_subscribe("free", "free plan (trial, price 0) from pending_plan_selection", "pending_plan_selection", "trial")


@step("paid_starter_from_pending_plan_selection")
def _b():
    return run_subscribe("paid_fresh", "paid starter, never selected or paid", "pending_plan_selection", "starter")


@step("paid_starter_from_pending_payment")
def _c():
    return run_subscribe("paid_pending", "paid starter, the account is awaiting payment", "pending_payment", "starter", pending="starter")


@step("paid_professional_from_suspended")
def _d():
    return run_subscribe("paid_susp", "paid professional, the account is suspended", "suspended", "professional")


@step("paid_professional_from_trial")
def _e():
    return run_subscribe("paid_trial", "paid professional, the account is on a trial", "trial", "professional")


@step("enterprise_from_pending_sales_contact")
def _f():
    return run_subscribe("enterprise", "enterprise, the sales conversation has not happened", "pending_sales_contact", "enterprise", pending="enterprise")


@step("control_hr_manager_subscribe")
def _g():
    return run_subscribe("hr_ctl", "hr_manager subscribes (control)", "pending_payment", "starter", pending="starter", who="hr")


# ── the other paths that can set active ──────────────────────────────────────────────────────────────────────────────────────
@step("control_select_plan_stores_pending_payment")
def _h():
    s = scenario("select", "pending_plan_selection")
    before = tenant_state(s["tenant"])
    st, body = http("POST", "/tenant/subscription/select-plan", s["admin"], s["tenant"], "admin", {"plan": "starter", "billing_cycle": "monthly"})
    return {"status": st, "before": before, "after": tenant_state(s["tenant"])}


@step("control_activate_trial_once_per_domain")
def _i():
    first = scenario("trial_a", "pending_plan_selection", domain="shared-trial.example")
    st1, _ = http("POST", "/tenant/subscription/activate-trial", first["admin"], first["tenant"], "admin")
    second = scenario("trial_b", "pending_plan_selection", domain="shared-trial.example")
    st2, body2 = http("POST", "/tenant/subscription/activate-trial", second["admin"], second["tenant"], "admin")
    return {"first_status": st1, "first_after": tenant_state(first["tenant"]), "second_status": st2, "second_body": body2,
            "second_after": tenant_state(second["tenant"])}


@step("control_platform_admin_assign_plan")
def _j():
    s = scenario("platform", "pending_payment", pending="professional")
    before, audit_before = tenant_state(s["tenant"]), audit_count()
    st, body = http("PATCH", f"/admin/tenants/{s['tenant']}/subscription", SEED["super"][0], SEED["super"][1], "super_admin",
                    {"action": "assign_plan", "plan_code": "professional"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit_rows_added": audit_count() - audit_before}


JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")


def tenant_write(key, status, trial_end_sql=None):
    """Does a suspended or expired tenant still get its writes served? (suspension is not enforced anywhere else)"""
    s = scenario(key, status)
    if trial_end_sql:
        sql(trial_end_sql, {"t": s["tenant"]})
    before, audit_before = tenant_state(s["tenant"]), audit_count()
    # a tag: a plain tenant write that does not generate a job code (see job_code_collision_under_restricted_role)
    st, body = http("POST", "/tags/", s["admin"], s["tenant"], "admin", {"tag_name": f"{key} tag"})
    tags = sql("SELECT count(*) AS n FROM candidate_tags WHERE tenant_id = :t", {"t": s["tenant"]})[0]["n"]
    return {"status": st, "before": before, "after": tenant_state(s["tenant"]), "changed": before != tenant_state(s["tenant"]),
            "audit_rows_added": audit_count() - audit_before, "stored": st in (200, 201) and tags == 1, "tags": tags}


@step("job_code_collision_under_restricted_role")
def _collision():
    """Two tenants, each creating its first job in the same year. The job code is counted over the rows the requester can see, but the code is
    unique across tenants: under the restricted role the second tenant's first job collides with the first tenant's."""
    a = scenario("code_a", "active")
    b = scenario("code_b", "active")
    st_a, body_a = http("POST", "/jobs", a["admin"], a["tenant"], "admin", {"title": "Code A job", "description": JOB_TEXT})
    st_b, body_b = http("POST", "/jobs", b["admin"], b["tenant"], "admin", {"title": "Code B job", "description": JOB_TEXT})
    codes = sql("SELECT job_code, tenant_id::text AS t FROM jobs WHERE title IN ('Code A job','Code B job') ORDER BY title")
    return {"a_status": st_a, "b_status": st_b, "b_body": body_b, "codes": codes}


@step("suspended_tenant_creates_job")
def _sus():
    return tenant_write("sus", "suspended")


@step("trial_past_its_end_creates_job")
def _expired():
    return tenant_write("expired_trial", "trial", "UPDATE tenants SET trial_start_at = now() - interval '30 days', "
                        "trial_end_at = now() - interval '16 days' WHERE tenant_id = :t")


@step("trial_eligibility_domain_bypass")
def _domain():
    """Tenant A uses the one trial for the domain; tenant B on the same domain is refused activate-trial but can subscribe to the trial plan."""
    a = scenario("dom_a", "pending_plan_selection", domain="shared-domain.example")
    st_a, _ = http("POST", "/tenant/subscription/activate-trial", a["admin"], a["tenant"], "admin")
    b = scenario("dom_b", "pending_plan_selection", domain="shared-domain.example")
    st_b_trial, body_b = http("POST", "/tenant/subscription/activate-trial", b["admin"], b["tenant"], "admin")
    before = tenant_state(b["tenant"])
    st_b_sub, _ = http("POST", "/subscription/subscribe", b["admin"], b["tenant"], "admin", {"plan_code": "trial"})
    return {"a_trial_status": st_a, "b_activate_trial_status": st_b_trial, "b_activate_trial_body": body_b,
            "b_before_subscribe": before, "b_subscribe_status": st_b_sub, "b_after_subscribe": tenant_state(b["tenant"])}


@step("trial_duration_bypass_by_subscribing_to_trial")
def _duration():
    """A tenant on its 14-day trial subscribes to the trial plan: the status becomes active and the trial end date is not used any more."""
    s = scenario("dur", "pending_plan_selection")
    st1, _ = http("POST", "/tenant/subscription/activate-trial", s["admin"], s["tenant"], "admin")
    on_trial = tenant_state(s["tenant"])
    st2, _ = http("POST", "/subscription/subscribe", s["admin"], s["tenant"], "admin", {"plan_code": "trial"})
    after = tenant_state(s["tenant"])
    end = sql("SELECT trial_end_at IS NOT NULL AS has_end, trial_end_at > now() + interval '20 days' AS far FROM tenants WHERE tenant_id = :t",
              {"t": s["tenant"]})[0]
    return {"activate_status": st1, "on_trial": on_trial, "subscribe_status": st2, "after": after, "trial_end_still_set": end["has_end"]}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
