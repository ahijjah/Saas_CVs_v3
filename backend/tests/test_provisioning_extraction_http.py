"""Provisioning to extraction, end to end, over the REAL admin HTTP route and REAL PostgreSQL, in both database modes.

The driver (tests/provisioning_extraction_driver.py) queues work through the real routes while the tenant is entitled (POST /jobs for legacy
and requirements-v2 jobs, and the recovery request route for a re-analysis candidate), captures each queued delivery at the broker boundary,
and then executes the captured deliveries through the real worker entry points (Task.apply). Its only replaced edges are the model (a counted
fake that returns a known-valid output) and the broker (capture only).

What these tests establish, for the work that was queued while entitled and for each of the three kinds of work:
  * suspension or expiry before execution: the queued deliveries make ZERO model calls and leave the work exactly as it was;
  * provisioning through the admin route resumes the paused work of THIS tenant, and the work completes;
  * a repeated provisioning, and duplicate deliveries of the same work, make no further model call and change no result;
  * another tenant's queued work is not dispatched and is not changed;
  * an audit failure applies no activation and dispatches nothing;
  * a dispatch failure leaves the work recoverable: it is still pending, and the next provisioning completes it.

These are guarantees about the tested paths, not a claim of universal exactly-once execution: the broker may deliver a message more than once,
and the claims refuse the second run without a model call; they do not remove the queue message itself.
"""
import json
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

DRIVER = BACKEND / "tests" / "provisioning_extraction_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
RUNTIME_GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
SUPER_TENANT = str(uuid.UUID(int=0x601))
SUPER = (str(uuid.UUID(int=0x602)), SUPER_TENANT, "super_admin")


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.entitle_active_tenants()
        url = db.async_url
        if RUNTIME_ROLE:
            db.conn.cursor().execute(RUNTIME_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("prov") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8", "ENVIRONMENT": "production",
               "HTTP_SEED": json.dumps({"super": list(SUPER)})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=1200, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        return json.loads(out.read_text(encoding="utf-8"))
    finally:
        db.drop()
        cluster.stop()


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


def outcomes_for(step, phase_prefix):
    return [o for o in step["outcomes"] if o["phase"].startswith(phase_prefix)]


def queued_jobs(obs, tenant):
    return set(obs["steps"]["queue_while_entitled"]["jobs"][tenant].values()) - {obs["steps"]["queue_while_entitled"]["jobs"][tenant]["version"]}


# ── the queue: every kind of work was queued through the real routes, while entitled ─────────────────────────────────────────────
def test_all_three_kinds_of_work_were_queued_while_the_tenant_was_entitled(obs):
    st = S(obs, "queue_while_entitled")
    for tenant in ("A", "B", "C"):
        assert set(st["jobs"][tenant]) == {"legacy", "initial", "base", "version"}, st["jobs"]
    assert st["queued"] == 12, "four deliveries per tenant: a legacy job, a v2 job, a v2 base, and a candidate"


# ── suspension and expiry before execution: zero model calls ──────────────────────────────────────────────────────────────────
def test_queued_work_run_after_expiry_makes_zero_model_calls_and_stays_exactly_as_it_was(obs):
    st = S(obs, "expired_tenant_runs_queued_work_with_zero_model_calls")
    assert st["model_calls_during"] == 0, st
    assert st["before"] == st["after"], "the expired tenant's work must not change"
    kinds = {o["kind"]: o["result"] for o in st["outcomes"] if o["phase"] == "queue-A"}
    assert kinds["v2"] == {"outcome": "paused", "code": "access_paused"}
    assert kinds["candidate"] == {"outcome": "paused", "code": "access_paused"}
    assert st["before"]["legacy_calls"] == st["after"]["legacy_calls"] == 0


def test_queued_work_run_while_suspended_makes_zero_model_calls_and_stays_exactly_as_it_was(obs):
    st = S(obs, "tenant_c_is_suspended_and_its_queued_work_makes_no_model_call")
    assert st["suspend_status"] == 200, st
    assert st["model_calls_during"] == 0 and st["legacy_calls_during"] == 0, st
    assert st["before"] == st["after"], st


# ── provisioning through the admin route: the paused work resumes and completes ───────────────────────────────────────────────
def test_provisioning_over_http_resumes_exactly_the_queued_work_of_that_tenant_and_it_completes(obs):
    st = S(obs, "reactivation_through_http_resumes_and_completes_the_queued_work")
    assert st["status"] == 200 and st["body"]["success"] is True, st
    assert st["body"]["resumed_extraction"] == {"v2": 1, "legacy": 1, "candidates": 1, "failed": 0}, st["body"]
    assert st["body"]["resumed_applications"] == 0
    assert st["resumed_kinds"] == ["candidate", "legacy", "v2"], st["resumed_kinds"]
    assert set(st["resumed_jobs"]) <= queued_jobs(obs, "A"), "only tenant A's work is resumed"
    after = st["after"]
    assert after["legacy"]["status"] == "completed" and st["after"]["legacy_calls"] == 1
    assert after["initial"]["status"] == "completed" and after["initial"]["revision"] == 1 and after["initial"]["token"] is None
    assert [e[2] for e in after["events"]] == ["requested", "started", "completed"], after["events"]
    assert st["audit_reactivate"] == 1


def test_the_completed_outcomes_are_the_real_worker_outcomes(obs):
    st = S(obs, "reactivation_through_http_resumes_and_completes_the_queued_work")
    first = {o["kind"]: o["result"] for o in outcomes_for(st, "queue-A")}
    assert first["v2"] == {"outcome": "completed", "code": None}
    assert first["candidate"] == {"outcome": "completed", "code": None}


# ── repeated provisioning and duplicate delivery: no further model call, no changed result ────────────────────────────────────
def test_repeated_provisioning_and_duplicate_deliveries_make_no_further_model_call_and_change_no_result(obs):
    st = S(obs, "repeated_provisioning_and_duplicate_delivery_create_no_extra_calls")
    assert st["status"] == 200, st
    assert st["resumed"] == {"v2": 0, "legacy": 0, "candidates": 0, "failed": 0}, "completed work is not resumed again"
    assert st["calls_before"] == st["calls_after"], st
    assert st["legacy_before"] == st["legacy_after"] == 1, st
    assert st["state_before"] == st["state_after"], "a repeated provisioning changes no result"
    for o in st["outcomes"]:
        if o["kind"] in ("v2", "candidate"):
            assert o["result"]["outcome"] == "stale", o                 # the duplicate is refused at the claim, with no model call


# ── another tenant's work is untouched ────────────────────────────────────────────────────────────────────────────────────────
def test_another_tenants_queued_work_is_not_dispatched_by_provisioning_and_is_untouched(obs):
    st = S(obs, "another_tenant_is_untouched_throughout")
    assert st["snapshot"] == st["at_queue"], "tenant B's rows, calls and events are unchanged from the moment its work was queued"
    assert st["snapshot"]["model_calls"]["initial"] == 0, "tenant B's queued work was never run and never called the model"
    reactivate = S(obs, "reactivation_through_http_resumes_and_completes_the_queued_work")
    assert not (set(reactivate["resumed_jobs"]) & queued_jobs(obs, "B")), "provisioning A does not dispatch B's work"


# ── an audit failure applies nothing ──────────────────────────────────────────────────────────────────────────────────────────
def test_an_audit_failure_applies_no_activation_and_dispatches_nothing(obs):
    st = S(obs, "audit_failure_causes_no_activation_and_no_resume")
    assert st["status"] == 503, st
    assert st["state_before"] == st["state_after"] == {"subscription_status": "suspended"}, st
    assert st["new_captures"] == 0, "no resume is queued when the change is not recorded"
    assert st["snapshot_before"] == st["snapshot_after"]


# ── a dispatch failure leaves the work recoverable ─────────────────────────────────────────────────────────────────────────────
def test_a_dispatch_failure_is_reported_leaves_the_work_pending_and_a_later_provisioning_completes_it(obs):
    st = S(obs, "dispatch_failure_leaves_the_work_recoverable_and_a_later_provisioning_completes_it")
    assert st["status"] == 200, "the activation is committed, so the route answers normally and reports the dispatch failures"
    assert st["body"]["resumed_extraction"] == {"v2": 0, "legacy": 0, "candidates": 0, "failed": 3}, st["body"]
    assert st["tenant_state"] == {"subscription_status": "active"}
    assert st["before"] == st["after_failed"], "the work is still pending, unchanged, with its tokens: recoverable, not failed"
    assert st["retry_status"] == 200 and st["retry_resumed_kinds"] == ["candidate", "legacy", "v2"]
    after = st["after"]
    assert after["legacy"]["status"] == "completed" and after["initial"]["status"] == "completed" and after["initial"]["revision"] == 1
    assert [e[2] for e in after["events"]] == ["requested", "started", "completed"], after["events"]
    assert st["audit_reactivate"] == 1


# ── exactly one model call per logical job, across every path above ──────────────────────────────────────────────────────────
def test_each_logical_job_received_exactly_one_model_call_across_all_paths(obs):
    reactivate = S(obs, "reactivation_through_http_resumes_and_completes_the_queued_work")
    repeat = S(obs, "repeated_provisioning_and_duplicate_delivery_create_no_extra_calls")
    assert reactivate["after"]["legacy_calls"] == 1
    assert reactivate["after"]["model_calls"]["initial"] == 1                     # the v2 initial attempt
    assert reactivate["after"]["model_calls"]["base"] == 2                       # the base analysis (1) and its candidate (1)
    assert repeat["calls_after"] == {"initial": 1, "base": 2}
    dispatch = S(obs, "dispatch_failure_leaves_the_work_recoverable_and_a_later_provisioning_completes_it")
    assert dispatch["after"]["legacy_calls"] == 1
    assert dispatch["after"]["model_calls"] == {"initial": 1, "base": 2}
