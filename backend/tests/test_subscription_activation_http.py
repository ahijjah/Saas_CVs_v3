"""Every path that sets a tenant's subscription to active, over the REAL HTTP API on REAL PostgreSQL, in both database modes.

Classification (the plan catalogue: trial = free; starter, professional = paid; enterprise = sales-contact):
  * FREE: the trial plan (price 0) may be activated without payment.
  * TRIAL: POST /tenant/subscription/activate-trial sets status 'trial' (not active), once per email domain.
  * PAID: a paid plan may be active only after payment, or after an authorized platform provisioning (super_admin).
  * ENTERPRISE: a sales-contact plan may be active only after the sales conversation (a platform provisioning).

REPRODUCES tests assert the current behaviour of the defective paths; they pass while the defect exists and must be inverted when fixed.
"""
import json
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND), str(BACKEND / "services")]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

DRIVER = BACKEND / "tests" / "subscription_activation_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
RUNTIME_GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
SUPER_TENANT = str(uuid.UUID(int=0x601))
SUPER = (str(uuid.UUID(int=0x602)), SUPER_TENANT, "super_admin")


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
             "(%s,'Platform','platform.example','active','active','organization')", (SUPER_TENANT,))
        db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,'platform@x.example','x','P','super_admin','active')",
             (SUPER[0], SUPER_TENANT))
        url = db.async_url
        if RUNTIME_ROLE:
            db.conn.cursor().execute(RUNTIME_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("subs") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8", "ENVIRONMENT": "production",
               "HTTP_SEED": json.dumps({"super": list(SUPER)})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        dump = os.environ.get("SUBS_OBS_DUMP")
        if dump:
            pathlib.Path(dump).write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        return json.loads(out.read_text(encoding="utf-8"))
    finally:
        db.drop()
        cluster.stop()


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


RUNTIME = bool(RUNTIME_ROLE)


def active(st):
    return st["after"]["subscription_status"] == "active"


# ── free plan: legitimate activation without payment ─────────────────────────────────────────────────────────────────────────
def test_free_plan_activates_without_payment_as_intended(obs):
    st = S(obs, "free_plan_trial_via_subscribe")
    assert st["status"] == 200 and active(st) and st["after"]["plan"] == "trial", st


# ── paid plans: the defect ───────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["paid_starter_from_pending_plan_selection", "paid_starter_from_pending_payment",
                                  "paid_professional_from_suspended", "paid_professional_from_trial"])
def test_reproduces_a_tenant_admin_activates_a_paid_plan_with_no_payment(obs, name):
    """REPRODUCES. A tenant admin activates a paid plan from any state, with no payment and no platform provisioning. The desired result: refused
    (no activation; the account stays where it was) until the approved billing path exists."""
    st = S(obs, name)
    assert st["status"] == 200 and active(st), st
    assert st["changed"] is True and st["after"]["plan"] is not None, st
    assert st["after"]["ends"] is False, st                         # a paid activation with no end date: never expires
    assert st["audit_rows_added"] == 0, st                          # and nothing records who activated it


def test_reproduces_the_paying_state_is_bypassed(obs):
    """REPRODUCES. select-plan stores pending_payment; subscribe then skips the payment entirely."""
    st = S(obs, "paid_starter_from_pending_payment")
    assert st["before"]["subscription_status"] == "pending_payment" and active(st), st


def test_reproduces_enterprise_activates_without_the_sales_conversation(obs):
    st = S(obs, "enterprise_from_pending_sales_contact")
    assert st["status"] == 200 and active(st) and st["after"]["plan"] == "enterprise", st


# ── controls ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_control_hr_manager_cannot_subscribe(obs):
    st = S(obs, "control_hr_manager_subscribe")
    assert st["status"] == 403 and st["changed"] is False, st


def test_control_select_plan_only_stores_pending_payment(obs):
    st = S(obs, "control_select_plan_stores_pending_payment")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "pending_payment" and st["after"]["pending_plan"] == "starter", st
    assert st["after"]["plan"] is None, st                          # select-plan records the choice, not the plan


def test_control_trial_is_not_active_and_is_once_per_domain(obs):
    st = S(obs, "control_activate_trial_once_per_domain")
    assert st["first_status"] == 200 and st["first_after"]["subscription_status"] == "trial" and st["first_after"]["trial_ends"] is True, st
    assert st["second_status"] == 409 and st["second_after"]["subscription_status"] == "pending_plan_selection", st


def test_control_platform_administrator_provisions_a_plan(obs):
    """Authorized platform provisioning (super_admin, the tenant subscription route). The platform role is the authorization; no payment record is
    checked. Its audit trail is checked separately."""
    st = S(obs, "control_platform_admin_assign_plan")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "active" and st["after"]["plan"] == "professional", st


def test_reproduces_the_subscribe_path_writes_no_audit_row(obs):
    """REPRODUCES. The tenant-admin activation leaves no audit row, so nothing shows who turned a paid plan on."""
    for name in ("paid_starter_from_pending_plan_selection", "paid_professional_from_suspended"):
        assert S(obs, name)["audit_rows_added"] == 0


# ── eligibility, duration and suspension ──────────────────────────────────────────────────────────────────────────────────────
def test_reproduces_a_suspended_tenant_keeps_writing(obs):
    """REPRODUCES. Nothing in the backend reads subscription_status to refuse a request, so a suspended tenant's writes are served (a tag is created)."""
    st = S(obs, "suspended_tenant_creates_job")
    assert st["status"] == 200 and st["stored"] is True and st["after"]["subscription_status"] == "suspended", st


def test_reproduces_a_second_tenants_first_job_collides_under_the_restricted_role(obs):
    """REPRODUCES a rollout blocker. The job code is counted over the rows the request can see, but it is unique across tenants. The superuser sees
    every tenant and never collides; the restricted role sees only its own, so the second tenant's first job fails with 500 (unique violation)."""
    st = S(obs, "job_code_collision_under_restricted_role")
    assert st["a_status"] == 201, st
    if RUNTIME:
        assert st["b_status"] == 500, st
    else:
        assert st["b_status"] == 201, st


def test_reproduces_an_expired_trial_keeps_writing(obs):
    """REPRODUCES. The trial end date is set but never enforced: no worker expires it and no request checks it."""
    st = S(obs, "trial_past_its_end_creates_job")
    assert st["status"] == 200 and st["stored"] is True and st["after"]["subscription_status"] == "trial", st


def test_reproduces_subscribing_to_the_trial_plan_bypasses_the_one_trial_per_domain_rule(obs):
    """REPRODUCES. The domain's one trial is used by tenant A; tenant B is refused activate-trial (control) but subscribing to the trial plan
    activates it."""
    st = S(obs, "trial_eligibility_domain_bypass")
    assert st["a_trial_status"] == 200 and st["b_activate_trial_status"] == 409, st
    assert st["b_subscribe_status"] == 200 and st["b_after_subscribe"]["subscription_status"] == "active", st


def test_reproduces_subscribing_to_the_trial_plan_removes_the_trial_duration(obs):
    """REPRODUCES. A tenant on its 14-day trial subscribes to the trial plan: the status becomes active, the end date is left set and unused, so the
    account never expires."""
    st = S(obs, "trial_duration_bypass_by_subscribing_to_trial")
    assert st["on_trial"]["subscription_status"] == "trial"
    assert st["subscribe_status"] == 200 and st["after"]["subscription_status"] == "active" and st["after"]["ends"] is False, st
    assert st["trial_end_still_set"] is True, st
