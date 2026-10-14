"""Subscription activation, over the REAL HTTP API on REAL PostgreSQL, in both database modes. PROTECTION tests (inverted from the earlier
reproductions of the bypass). Approved rules:

  * A tenant may REQUEST a plan (select-plan or subscribe). A request never activates it: the status becomes pending_payment or
    pending_sales_contact and the plan stays unset.
  * The 14-day trial starts only through activate-trial, once per email domain. Subscribe cannot start it.
  * Only platform provisioning (super_admin, PATCH /admin/tenants/{id}/subscription) activates a plan. It needs a reason and a future expiry,
    and it is audited in the same transaction. If the audit cannot be written, nothing changes.
  * Controls: the approved paths still work and the unauthorized roles are still refused.
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


PROVISIONING_AUDIT = "subscription_assign_plan"


# ── subscribe: a tenant requests a plan and cannot activate it ───────────────────────────────────────────────────────────────
def test_subscribe_cannot_start_the_trial_and_changes_nothing(obs):
    st = S(obs, "subscribe_trial_refused_from_pending_plan_selection")
    assert st["status"] == 409, st
    assert st["after"] == st["before"] and st["audit_added"] == 0, st


def test_subscribe_to_a_paid_plan_requests_pending_payment_and_does_not_activate(obs):
    st = S(obs, "subscribe_paid_requests_pending_payment")
    assert st["status"] == 200 and st["body"]["subscription_status"] == "pending_payment", st
    assert st["after"]["subscription_status"] == "pending_payment" and st["after"]["pending_plan"] == "starter", st
    assert st["after"]["plan"] is None and st["after"]["ends"] is False, st


def test_subscribe_is_idempotent_while_activation_is_pending(obs):
    st = S(obs, "subscribe_paid_from_pending_payment_is_idempotent")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "pending_payment" and st["after"]["pending_plan"] == "professional", st
    assert st["after"]["plan"] is None, st


@pytest.mark.parametrize("name", ["subscribe_refused_from_suspended", "subscribe_refused_from_live_trial", "subscribe_refused_from_live_active_plan"])
def test_subscribe_cannot_change_a_suspended_trial_or_live_plan_from_the_tenant_side(obs, name):
    st = S(obs, name)
    assert st["status"] == 409 and st["after"] == st["before"], st


def test_subscribe_enterprise_requests_the_sales_contact_state_and_does_not_activate(obs):
    st = S(obs, "subscribe_enterprise_requests_sales_contact")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "pending_sales_contact" and st["after"]["plan"] is None, st


def test_subscribe_unknown_plan_is_not_found_and_changes_nothing(obs):
    st = S(obs, "subscribe_unknown_plan_not_found")
    assert st["status"] == 404 and st["after"]["subscription_status"] == "pending_plan_selection", st


def test_subscribe_refuses_the_hr_manager(obs):
    st = S(obs, "subscribe_hr_manager_refused")
    assert st["status"] == 403 and st["after"] == st["before"], st


# ── trial: 14 days, once per email domain, and not bypassed by subscribe ─────────────────────────────────────────────────────
def test_the_trial_cannot_be_bypassed_through_subscribe_for_a_second_tenant_on_the_same_domain(obs):
    st = S(obs, "trial_plan_via_subscribe_cannot_bypass_domain_rule")
    assert st["a_trial_status"] == 200, st
    assert st["b_activate_trial_status"] == 409, st
    assert st["b_subscribe_status"] == 409 and st["b_after"] == st["b_before"], st


def test_the_trial_is_fourteen_days_and_subscribing_to_the_trial_plan_cannot_end_or_extend_it(obs):
    st = S(obs, "trial_is_14_days_and_not_bypassed_by_subscribe")
    assert st["activate_status"] == 200 and st["on_trial"]["subscription_status"] == "trial" and st["on_trial"]["trial_is_14_days"] is True, st
    assert st["subscribe_status"] == 409, st
    assert st["after"]["subscription_status"] == "trial" and st["after"]["trial_ends"] is True, st


# ── controls ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_control_select_plan_requests_pending_payment_and_does_not_activate(obs):
    st = S(obs, "control_select_plan_requests_pending_payment")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "pending_payment" and st["after"]["pending_plan"] == "starter", st
    assert st["after"]["plan"] is None, st


def test_control_trial_is_not_active_and_is_once_per_domain(obs):
    st = S(obs, "control_activate_trial_once_per_domain")
    assert st["first_status"] == 200 and st["first_after"]["subscription_status"] == "trial" and st["first_after"]["trial_ends"] is True, st
    assert st["second_status"] == 409 and st["second_after"]["subscription_status"] == "pending_plan_selection", st


def test_control_hr_manager_cannot_provision(obs):
    st = S(obs, "control_hr_manager_cannot_provision")
    assert st["status"] == 403 and st["after"] == st["before"] and st["audit"] == [], st


# ── platform provisioning ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_platform_provisioning_activates_with_reason_and_future_expiry_and_is_audited(obs):
    st = S(obs, "provisioning_assign_plan_is_audited")
    assert st["status"] == 200, st
    after = st["after"]
    assert after["subscription_status"] == "active" and after["plan"] == "professional" and after["pending_plan"] is None, st
    assert after["ends"] is True, st
    rows = [r for r in st["audit"] if r["action"] == PROVISIONING_AUDIT]
    assert len(rows) == 1, st["audit"]
    row = rows[0]
    assert row["resource_type"] == "tenant" and row["resource_id"], row             # the audited resource is the tenant
    d = row["details"]
    if isinstance(d, str):
        d = json.loads(d)
    assert d["reason"] == "Invoice 4411 paid by bank transfer", d
    assert d["previous"]["subscription_status"] == "pending_payment" and d["previous"]["plan"] is None, d
    assert d["new"]["subscription_status"] == "active" and d["new"]["plan"] == "professional", d
    assert d["new"]["subscription_ends_at"] is not None and d["expires_at"] is not None, d
    assert row["user_email"] and row["user_id"], row


@pytest.mark.parametrize("name", ["provisioning_refused_without_reason", "provisioning_refused_without_expiry",
                                  "provisioning_refused_with_past_expiry"])
def test_provisioning_refuses_a_missing_reason_or_expiry_or_a_past_expiry_without_any_change(obs, name):
    st = S(obs, name)
    assert st["status"] == 422, st
    assert st["after"] == st["before"] and st["audit"] == [], st


def test_provisioning_audit_and_change_are_atomic(obs):
    st = S(obs, "provisioning_is_atomic_with_its_audit_row")
    assert st["status"] == 503, st
    assert st["after"] == st["before"], "a change whose audit row fails must not be applied"
    assert st["audit"] == [], st


def test_suspend_requires_a_reason_and_is_audited(obs):
    st = S(obs, "provisioning_suspend_requires_reason_and_is_audited")
    assert st["no_reason_status"] == 422, st
    assert st["status"] == 200 and st["after"]["subscription_status"] == "suspended", st
    assert [r["action"] for r in st["audit"]] == ["subscription_suspend"], st["audit"]


def test_extend_trial_accepts_the_expiry_and_the_legacy_field_and_requires_a_reason(obs):
    st = S(obs, "provisioning_extend_trial_accepts_expiry_and_legacy_field")
    assert st["status"] == 200 and st["after"]["subscription_status"] == "trial" and st["after"]["trial_ends"] is True, st
    assert st["no_reason_status"] == 422, st
    assert [r["action"] for r in st["audit"]] == ["subscription_extend_trial"], st["audit"]


def test_reactivate_requires_a_future_expiry(obs):
    st = S(obs, "provisioning_reactivate_requires_expiry")
    assert st["status"] == 422 and st["after"] == st["before"] and st["audit"] == [], st
