"""Entitlement (expiry, suspension, read-only access, public intake, queued-work pause and resume), over the REAL HTTP API and the REAL
scoring-task entry point on REAL PostgreSQL, in both database modes. Approved rules:
  * Expiry is decided by the database clock at each request or task; no scheduler is needed.
  * Suspended and expired tenants keep reads and billing access and lose recruitment writes, AI and intake.
  * Legacy or unset validity is fail-closed for writes, and is never extended.
  * Paused scoring stays queued and is never failed; resume dispatches each paused row once.
The rules below are from the earlier billing decision, kept with the subscription tests:

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

DRIVER = BACKEND / "tests" / "entitlement_driver.py"
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


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


# ── expiry boundaries (decided by the database clock) ───────────────────────────────────────────────────────────────────────
def test_a_write_with_an_end_in_the_future_is_allowed(obs):
    st = S(obs, "write_with_end_in_the_future_is_allowed")
    assert st["status"] in (200, 201) and st["tags"] == 1, st


def test_a_write_one_second_after_the_end_is_refused_and_stores_nothing(obs):
    st = S(obs, "write_one_second_after_the_end_is_refused")
    assert st["status"] == 403 and st["tags"] == 0, st


def test_a_trial_one_second_past_its_end_is_refused(obs):
    st = S(obs, "trial_one_second_after_its_end_is_refused")
    assert st["status"] == 403 and st["tags"] == 0, st


def test_expiry_crossed_during_a_session_is_enforced_on_the_next_request(obs):
    st = S(obs, "expiry_crossed_during_the_session_is_enforced_on_the_next_request")
    assert st["first_status"] in (200, 201), st
    assert st["second_status"] == 403 and st["tags"] == 1, st


# ── validity that is missing or not activated ────────────────────────────────────────────────────────────────────────────────
def test_a_legacy_active_tenant_without_an_end_date_is_read_only_and_is_not_extended(obs):
    st = S(obs, "legacy_active_without_an_end_date_is_read_only")
    assert st["write_status"] == 403 and "no recorded end date" in st["write_body"]["detail"], st
    assert st["read_status"] == 200 and st["usage_status"] == 200, st
    assert st["access"]["state"] == "validity_missing" and st["access"]["can_write"] is False, st
    assert st["tags"] == 0 and st["after"]["subscription_status"] == "active" and st["after"]["ends_missing"] is True, st


def test_a_trial_without_an_end_date_is_read_only(obs):
    st = S(obs, "trial_without_an_end_date_is_read_only")
    assert st["status"] == 403 and st["tags"] == 0, st


def test_a_pending_activation_refuses_writes_and_reports_it(obs):
    st = S(obs, "pending_activation_refuses_writes_and_says_so")
    assert st["status"] == 403 and st["tags"] == 0, st
    assert st["access"]["state"] == "not_activated" and st["access"]["can_write"] is False, st


# ── suspension and expiry keep the data and the billing access ───────────────────────────────────────────────────────────────
def test_a_suspended_tenant_keeps_reads_billing_and_its_data_and_loses_recruitment_writes(obs):
    st = S(obs, "suspended_tenant_keeps_reads_billing_and_its_data_and_refuses_writes")
    assert st["read_status"] == 200 and st["kept_job_visible"] is True, st
    assert st["plans_status"] == 200 and st["usage_status"] == 200, st
    assert st["access"]["state"] == "suspended" and st["access"]["can_write"] is False, st
    assert st["write_status"] == 403 and st["tag_status"] == 403, st
    assert st["select_status"] == 409, st                    # a suspended tenant cannot change its own plan state
    assert st["jobs_after"] == 1, st                         # the refused create stored nothing


def test_an_expired_trial_keeps_its_data_and_refuses_writes(obs):
    st = S(obs, "expired_trial_keeps_its_data_and_refuses_writes")
    assert st["read_status"] == 200 and st["kept_job_visible"] is True, st
    assert st["write_status"] == 403, st
    assert st["access"]["state"] == "expired", st


def test_an_expired_tenant_may_request_a_plan_but_stays_read_only_until_provisioned(obs):
    st = S(obs, "expired_tenant_may_request_a_plan_but_stays_read_only_until_provisioned")
    assert st["select_status"] == 200 and st["select_body"]["subscription_status"] == "pending_payment", st
    assert st["write_status"] == 403, st
    assert st["after"]["subscription_status"] == "pending_payment" and st["after"]["plan"] == "starter", st


def test_a_full_access_tenant_reports_full_state(obs):
    st = S(obs, "full_access_reports_full_state")
    assert st["status"] == 200 and st["access"] == {"state": "full", "can_write": True, "message": ""}, st


# ── public intake ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_public_intake_is_refused_while_suspended_and_stores_nothing(obs):
    st = S(obs, "public_intake_is_refused_while_suspended_and_stores_nothing")
    assert st["status"] == 422 and "not currently accepting" in st["body"]["detail"], st
    assert "subscription" not in st["body"]["detail"].lower(), "the public response must not reveal billing state"
    assert st["apps_before"] == st["apps_after"] == 0 and st["dispatch_added"] == 0, st
    assert st["log_statuses"] == ["INTAKE_BLOCKED"] and "suspended" in st["log_reasons"][0], st   # the entitlement gate fired, not the analysis check


def test_public_intake_is_accepted_while_active(obs):
    st = S(obs, "public_intake_is_accepted_while_active_control")
    assert st["status"] == 201 and st["apps"] == 1 and st["dispatch_added"] == 1, (st["status"], st["log_statuses"], st["log_reasons"])


# ── queued work: pause, watchdog and resume ───────────────────────────────────────────────────────────────────────────────────
def test_queued_scoring_pauses_while_suspended_and_is_not_failed_or_retried(obs):
    st = S(obs, "queued_scoring_pauses_while_suspended_without_failing")
    assert st["raised"] is None, st                          # a raise would make Celery retry
    assert st["row"]["processing_status"] == "queued" and st["row"]["stopped_reason"] == "access_paused", st
    assert st["scored"] == 0 and st["dispatch_added"] == 0, st


def test_the_watchdog_leaves_paused_work_and_still_fails_an_unpaused_stuck_row(obs):
    st = S(obs, "watchdog_leaves_paused_work_and_still_fails_an_unpaused_stuck_row")
    assert st["control"]["processing_status"] == "failed", "the control row proves the watchdog runs"
    assert st["paused"]["processing_status"] == "queued" and st["paused"]["stopped_reason"] == "access_paused", st


def test_reactivation_resumes_paused_work_once_with_its_file(obs):
    st = S(obs, "reactivation_resumes_paused_work_once")
    assert st["status"] == 200 and st["resumed"] == 1, st
    assert st["row"]["processing_status"] == "queued" and st["row"]["stopped_reason"] is None and st["row"]["recently_queued"] is True, st
    assert st["dispatched_after"] - st["dispatched_before"] == 1, st
    assert st["args"]["tenant_id"] == st["tenant"] and st["args"]["file_path"].endswith(".pdf"), st


def test_a_repeated_provisioning_dispatches_nothing_more(obs):
    st = S(obs, "a_second_resume_dispatches_nothing_more")
    assert st["status"] == 200 and st["resumed"] == 0, st
    assert st["dispatched_after"] == st["dispatched_before"], st


def test_a_failed_dispatch_re_pauses_the_row_so_it_is_not_lost(obs):
    st = S(obs, "failed_dispatch_repauses_the_row_and_does_not_lose_it")
    assert st["status"] == 200 and st["resumed"] == 0, st
    assert st["row"]["stopped_reason"] == "access_paused" and st["row"]["processing_status"] == "queued", st
