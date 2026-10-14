"""Driver for test_subscription_activation_http.py. Runs in its OWN process against the disposable database named by DATABASE_URL (the
superuser, or the restricted role under CV_TEST_RUNTIME_ROLE). It drives the REAL routes over HTTP and reads the tenant row and the audit log
back from the database after each request. Each scenario has its own tenant, so states do not leak between scenarios. Nothing is paid or
sent; no model is called. Writes one JSON object to argv[1].

Every step here is a PROTECTION: the approved billing rules (trial 14 days once per domain, no self-activation, platform provisioning with a
reason and a future expiry, audited in the same transaction). The step names say what is protected."""
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
import routers.admin as admin_router  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from config import get_settings  # noqa: E402
from main import app  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

legacy_worker.extract_criteria_task.delay = lambda *a, **k: None   # job creation queues extraction; not part of these checks

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}, "config": {"environment": get_settings().environment}}
PLANS = [("trial", "Trial", 0, 0, 14, 2), ("starter", "Starter", 49, 490, 14, 3),
         ("professional", "Professional", 149, 1490, 14, 15), ("enterprise", "Enterprise", 499, 4990, 14, 100)]
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")


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
               "subscription_ends_at IS NOT NULL AS ends, trial_end_at IS NOT NULL AS trial_ends, "
               "COALESCE(trial_end_at > now() + interval '13 days', false) AS trial_is_14_days "
               "FROM tenants WHERE tenant_id = :t", {"t": tid})[0]


def audit_rows(tid):
    return sql("SELECT action, resource_type, resource_id::text AS resource_id, user_id::text AS user_id, user_email, details "
               "FROM audit_logs WHERE tenant_id = :t ORDER BY created_at", {"t": tid})


def audit_count():
    return sql("SELECT count(*) AS n FROM audit_logs")[0]["n"]


SCENARIOS = {}


def scenario(key, status, pending=None, domain=None, plan=None):
    """A fresh tenant with one admin and one hr_manager, in the state given. Each scenario has its own tenant."""
    tid, admin, hr = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    dom = domain or f"{key}.example"
    sql("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, pending_plan, plan, tenant_type, max_users) "
        "VALUES (:t,:n,:d,'active',:s,:p,:pl,'organization',20)", {"t": tid, "n": key, "d": dom, "s": status, "p": pending, "pl": plan})
    for uid, role in ((admin, "admin"), (hr, "hr_manager")):
        sql("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (:u,:t,:e,'x',:n,:r,'active')",
            {"u": uid, "t": tid, "e": f"{role}-{uid[:8]}@{dom}", "n": role, "r": role})
    SCENARIOS[key] = {"tenant": tid, "admin": admin, "hr": hr}
    return SCENARIOS[key]


def tenant_admin_call(key, method, url, body, who="admin"):
    s = SCENARIOS[key]
    uid = s["admin"] if who == "admin" else s["hr"]
    role = "admin" if who == "admin" else "hr_manager"
    return http(method, url, uid, s["tenant"], role, body)


def platform_call(tid, method, url, body):
    return http(method, url, SEED["super"][0], SEED["super"][1], "super_admin", body)


def future_iso(days=30):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ── subscribe: a tenant can REQUEST a plan, never activate one ───────────────────────────────────────────────────────────────────
@step("subscribe_trial_refused_from_pending_plan_selection")
def _a():
    s = scenario("trial_sub", "pending_plan_selection")
    before, audits = tenant_state(s["tenant"]), len(audit_rows(s["tenant"]))
    st, body = tenant_admin_call("trial_sub", "POST", "/subscription/subscribe", {"plan_code": "trial"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit_added": len(audit_rows(s["tenant"])) - audits}


@step("subscribe_paid_requests_pending_payment")
def _b():
    s = scenario("paid_fresh", "pending_plan_selection")
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("paid_fresh", "POST", "/subscription/subscribe", {"plan_code": "starter"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("subscribe_paid_from_pending_payment_is_idempotent")
def _c():
    s = scenario("paid_pending", "pending_payment", pending="starter")
    st, body = tenant_admin_call("paid_pending", "POST", "/subscription/subscribe", {"plan_code": "professional"})
    return {"status": st, "body": body, "after": tenant_state(s["tenant"])}


@step("subscribe_refused_from_suspended")
def _d():
    s = scenario("sus_sub", "suspended")
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("sus_sub", "POST", "/subscription/subscribe", {"plan_code": "starter"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("subscribe_refused_from_live_trial")
def _e():
    s = scenario("trial_live", "trial", plan=None)
    sql("UPDATE tenants SET trial_start_at = now(), trial_end_at = now() + interval '10 days' WHERE tenant_id = :t", {"t": s["tenant"]})
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("trial_live", "POST", "/subscription/subscribe", {"plan_code": "professional"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("subscribe_refused_from_live_active_plan")
def _f():
    s = scenario("active_sub", "active", plan="starter")
    sql("UPDATE tenants SET subscription_ends_at = now() + interval '20 days' WHERE tenant_id = :t", {"t": s["tenant"]})
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("active_sub", "POST", "/subscription/subscribe", {"plan_code": "professional"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("subscribe_enterprise_requests_sales_contact")
def _g():
    s = scenario("enterprise", "pending_plan_selection")
    st, body = tenant_admin_call("enterprise", "POST", "/subscription/subscribe", {"plan_code": "enterprise"})
    return {"status": st, "body": body, "after": tenant_state(s["tenant"])}


@step("subscribe_unknown_plan_not_found")
def _h():
    s = scenario("unknown_plan", "pending_plan_selection")
    st, body = tenant_admin_call("unknown_plan", "POST", "/subscription/subscribe", {"plan_code": "no-such-plan"})
    return {"status": st, "body": body, "after": tenant_state(s["tenant"])}


@step("subscribe_hr_manager_refused")
def _i():
    s = scenario("hr_sub", "pending_plan_selection")
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("hr_sub", "POST", "/subscription/subscribe", {"plan_code": "starter"}, who="hr")
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("trial_plan_via_subscribe_cannot_bypass_domain_rule")
def _j():
    """The domain's one trial is used by tenant A. Tenant B on the same domain is refused activate-trial, and subscribing to 'trial' is refused too."""
    a = scenario("dom_a", "pending_plan_selection", domain="shared-domain.example")
    st_a, _ = tenant_admin_call("dom_a", "POST", "/tenant/subscription/activate-trial", None)
    b = scenario("dom_b", "pending_plan_selection", domain="shared-domain.example")
    st_b_trial, body_b = tenant_admin_call("dom_b", "POST", "/tenant/subscription/activate-trial", None)
    before = tenant_state(b["tenant"])
    st_b_sub, body_sub = tenant_admin_call("dom_b", "POST", "/subscription/subscribe", {"plan_code": "trial"})
    return {"a_trial_status": st_a, "b_activate_trial_status": st_b_trial, "b_activate_trial_body": body_b,
            "b_before": before, "b_subscribe_status": st_b_sub, "b_subscribe_body": body_sub, "b_after": tenant_state(b["tenant"])}


@step("trial_is_14_days_and_not_bypassed_by_subscribe")
def _k():
    s = scenario("dur", "pending_plan_selection")
    st1, _ = tenant_admin_call("dur", "POST", "/tenant/subscription/activate-trial", None)
    on_trial = tenant_state(s["tenant"])
    st2, body2 = tenant_admin_call("dur", "POST", "/subscription/subscribe", {"plan_code": "trial"})
    return {"activate_status": st1, "on_trial": on_trial, "subscribe_status": st2, "subscribe_body": body2, "after": tenant_state(s["tenant"])}


# ── controls: the approved paths still work ──────────────────────────────────────────────────────────────────────────────────
@step("control_select_plan_requests_pending_payment")
def _l():
    s = scenario("select", "pending_plan_selection")
    before = tenant_state(s["tenant"])
    st, body = tenant_admin_call("select", "POST", "/tenant/subscription/select-plan", {"plan": "starter", "billing_cycle": "monthly"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"])}


@step("control_activate_trial_once_per_domain")
def _m():
    first = scenario("trial_a", "pending_plan_selection", domain="shared-trial.example")
    st1, _ = tenant_admin_call("trial_a", "POST", "/tenant/subscription/activate-trial", None)
    second = scenario("trial_b", "pending_plan_selection", domain="shared-trial.example")
    st2, body2 = tenant_admin_call("trial_b", "POST", "/tenant/subscription/activate-trial", None)
    return {"first_status": st1, "first_after": tenant_state(first["tenant"]), "second_status": st2, "second_body": body2,
            "second_after": tenant_state(second["tenant"])}


@step("control_hr_manager_cannot_provision")
def _n():
    s = scenario("prov_hr", "pending_plan_selection")
    before = tenant_state(s["tenant"])
    st, body = http("PATCH", f"/admin/tenants/{s['tenant']}/subscription", s["hr"], s["tenant"], "hr_manager",
                    {"action": "assign_plan", "plan_code": "starter", "expires_at": future_iso(), "reason": "should be refused"})
    return {"status": st, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


# ── platform provisioning: reason, future expiry, previous and new state, one transaction ───────────────────────────────────
@step("provisioning_assign_plan_is_audited")
def _o():
    s = scenario("prov_ok", "pending_payment", pending="professional")
    before = tenant_state(s["tenant"])
    exp = future_iso(30)
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "assign_plan", "plan_code": "professional", "expires_at": exp, "reason": "Invoice 4411 paid by bank transfer"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"]), "expires_requested": exp}


@step("provisioning_refused_without_reason")
def _p():
    s = scenario("prov_noreason", "pending_payment", pending="starter")
    before = tenant_state(s["tenant"])
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "assign_plan", "plan_code": "starter", "expires_at": future_iso(), "reason": "   "})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_refused_without_expiry")
def _q():
    s = scenario("prov_noexp", "pending_payment", pending="starter")
    before = tenant_state(s["tenant"])
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "assign_plan", "plan_code": "starter", "reason": "no expiry given"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_refused_with_past_expiry")
def _r():
    s = scenario("prov_past", "pending_payment", pending="starter")
    before = tenant_state(s["tenant"])
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "assign_plan", "plan_code": "starter", "expires_at": "2001-01-01T00:00:00Z", "reason": "backdated attempt"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_is_atomic_with_its_audit_row")
def _s():
    """If the audit row cannot be written, the change is NOT applied and nothing is recorded."""
    s = scenario("prov_atomic", "pending_payment", pending="starter")
    before = tenant_state(s["tenant"])
    original = admin_router.log_action

    async def failing(*a, **k):
        raise RuntimeError("audit store unavailable (simulated)")
    admin_router.log_action = failing
    try:
        st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                                 {"action": "assign_plan", "plan_code": "starter", "expires_at": future_iso(), "reason": "audit failure drill"})
    finally:
        admin_router.log_action = original
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_suspend_requires_reason_and_is_audited")
def _t():
    s = scenario("prov_sus", "active", plan="starter")
    sql("UPDATE tenants SET subscription_ends_at = now() + interval '20 days' WHERE tenant_id = :t", {"t": s["tenant"]})
    st_no, _ = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription", {"action": "suspend", "reason": ""})
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "suspend", "reason": "chargeback under review"})
    return {"no_reason_status": st_no, "status": st, "body": body, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_extend_trial_accepts_expiry_and_legacy_field")
def _u():
    s = scenario("prov_trial", "pending_plan_selection")
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription",
                             {"action": "extend_trial", "trial_end_at": future_iso(10), "reason": "support extension for onboarding"})
    no_reason, _ = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription", {"action": "extend_trial", "expires_at": future_iso(10), "reason": ""})
    return {"status": st, "body": body, "no_reason_status": no_reason, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


@step("provisioning_reactivate_requires_expiry")
def _v():
    s = scenario("prov_react", "suspended", plan="starter")
    before = tenant_state(s["tenant"])
    st, body = platform_call(s["tenant"], "PATCH", f"/admin/tenants/{s['tenant']}/subscription", {"action": "reactivate", "reason": "payment received"})
    return {"status": st, "body": body, "before": before, "after": tenant_state(s["tenant"]), "audit": audit_rows(s["tenant"])}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
