"""Reproduction of the three highest-priority authorization findings through the REAL HTTP API on REAL PostgreSQL. Each reproduction test asserts
the behaviour the code currently has (so it passes while the defect exists) and is marked REPRODUCES; when a fix lands, the assertion inverts and
the test must be flipped. Permitted-role controls are ordinary assertions. Every write is checked against the database, not only the status.

  1. simulate-payment activates a subscription under the production configuration (tenant admin, no environment gate).
  2. viewers can perform writes beyond the workflow move (comments, communications, tags, talent pool, interviews, job creation, score-pending,
     delete of an uploaded CV, bulk-upload batches).
  3. a token keeps the database context of its claims after demotion, reassignment or deactivation; the cross-tenant effect is measured only
     under cv_runtime, because the superuser bypasses row-level security by design.

Superuser: CV_TEST_RUNTIME_ROLE unset (default). Restricted role: CV_TEST_RUNTIME_ROLE=cv_runtime.
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

DRIVER = BACKEND / "tests" / "authz_repro_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
RUNTIME_GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1, T2 = str(uuid.UUID(int=0x401)), str(uuid.UUID(int=0x402))
USERS = {"admin": (str(uuid.UUID(int=0x411)), T1, "admin"), "hr": (str(uuid.UUID(int=0x412)), T1, "hr_manager"),
         "viewer": (str(uuid.UUID(int=0x413)), T1, "viewer"), "other": (str(uuid.UUID(int=0x414)), T2, "admin"),
         "super": (str(uuid.UUID(int=0x415)), T1, "super_admin")}


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
             "(%s,'Tenant One','t1.example','active','active','organization'),(%s,'Tenant Two','t2.example','active','active','organization')", (T1, T2))
        for key, (uid, tenant, role) in USERS.items():
            db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
                 (uid, tenant, f"{key}-{uid[-4:]}@x.example", key, role))
        url = db.async_url
        if RUNTIME_ROLE:
            db.conn.cursor().execute(RUNTIME_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("authz") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8", "ENVIRONMENT": "production",
               "HTTP_SEED": json.dumps({"users": USERS})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        dump = os.environ.get("AUTHZ_OBS_DUMP")          # optional: keep the raw observations for review
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
ok = lambda st: 200 <= st < 300  # noqa: E731


# ── 1. simulate-payment under the production configuration ──────────────────────────────────────────────────────────────────────
def test_production_configuration_is_the_one_under_test(obs):
    assert S(obs, "f1_production_configuration") == {"environment": "production", "debug": False}


def test_control_hr_manager_cannot_simulate_payment(obs):
    st = S(obs, "f1_control_hr_manager_cannot_simulate_payment")
    assert st["status"] == 403 and st["db_subscription_status"] == "pending_payment"


def test_control_admin_can_select_a_plan_and_it_is_stored_as_pending(obs):
    st = S(obs, "f1_permitted_admin_selects_plan")
    assert st["status"] == 200 and st["db_subscription_status"] == "pending_payment" and st["db_pending_plan"] == "starter", st


def test_reproduces_simulated_payment_activates_the_subscription_in_production(obs):
    """REPRODUCES finding 1. Flip when the route is refused outside development: the expected result is then a refusal with the tenant unchanged."""
    st = S(obs, "f1_admin_simulates_payment_in_production_configuration")
    assert st["status"] == 200 and st["body"]["success"] is True, st
    assert st["db_subscription_status"] == "active" and st["db_plan"] == "starter" and st["db_has_subscription_end"] is True, st


# ── 2. viewer writes beyond the workflow move ───────────────────────────────────────────────────────────────────────────────────
VIEWER_WRITES = ["f2_viewer_posts_comment", "f2_viewer_logs_communication", "f2_viewer_adds_tag", "f2_viewer_toggles_talent_pool",
                 "f2_viewer_creates_interview", "f2_viewer_creates_job", "f2_viewer_scores_pending_cvs", "f2_viewer_deletes_uploaded_cv",
                 "f2_viewer_creates_bulk_batch"]


@pytest.mark.parametrize("name", VIEWER_WRITES)
def test_reproduces_viewer_write_is_accepted_and_stored(obs, name):
    """REPRODUCES finding 2 for this route: the viewer's request is accepted and the database changes. Flip to 403 with no change when fixed."""
    st = S(obs, name)
    assert ok(st["status"]) and st["db_changed"] is True, st


def test_reproduces_viewer_scoring_claims_the_cv_and_enqueues_it(obs):
    st = S(obs, "f2_viewer_scores_pending_cvs")
    assert len(st["enqueued"]) == 1 and st["db_after"] == 1, st


@pytest.mark.parametrize("name, label", [("f2_control_hr_posts_comment", "comment"), ("f2_control_hr_creates_job", "job")])
def test_control_hr_manager_writes_are_permitted_and_stored(obs, name, label):
    st = S(obs, name)
    assert ok(st["status"]) and st["db_changed"] is True, st


# ── 3. a token whose claims no longer match the database row ───────────────────────────────────────────────────────────────────
def test_control_fresh_super_admin_token_gets_the_platform_context(obs):
    st = S(obs, "f3_probe_fresh_token_super_admin")
    assert st["status"] == 200 and st["probe"]["context_role"] == "super_admin", st


def test_reproduces_demoted_super_admin_keeps_the_platform_context(obs):
    """REPRODUCES finding 3a. The row now says admin: the route check refuses the platform dashboard, but the request's database context is still
    super_admin for the token's life. Cross-tenant visibility is asserted only under the restricted role."""
    st = S(obs, "f3_demotion_then_stale_super_token")
    assert st["db_role_after_demotion"] == "admin"
    assert st["stale_admin_dashboard_status"] == 403, st                   # the application check uses the row: refused
    assert st["stale_probe_status"] == 200 and st["stale_probe"]["context_role"] == "super_admin", st
    if RUNTIME:
        assert st["stale_probe"]["other_tenant_applications"] > 0, st       # the stale token reads another tenant's rows
    fresh = st["fresh_admin_probe"]
    assert st["fresh_admin_probe_status"] == 200 and fresh["context_role"] == "admin"
    if RUNTIME:
        assert fresh["other_tenant_applications"] == 0


def test_reassigned_admin_token_is_refused_only_because_of_the_restricted_role(obs):
    """Tenant reassignment with a token that still names the old tenant. The user row is read under the CLAIMED tenant. Under cv_runtime the row
    is not visible to that context, so the request is refused (401). Under the superuser (the production connection today) RLS does not apply:
    the row is read anyway and the request is served as the NEW tenant. The authorization result therefore depends on the database role, not on
    the token. REPRODUCES the mode dependence; the desired result is a refusal (401) in both modes."""
    st = S(obs, "f3_reassignment_then_stale_admin_token")
    assert st["db_tenant_after_reassignment"] == T2
    if RUNTIME:
        assert st["stale_probe_status"] == 401 and st["stale_tenant_users_status"] in (401, 403), st
    else:
        assert st["stale_probe_status"] == 200 and st["stale_probe"]["row_tenant"] == T2, st
        assert st["stale_tenant_users_status"] == 200, st


def test_reproduces_stale_super_claim_survives_reassignment_and_demotion(obs):
    """REPRODUCES finding 3b. The same moved user with a super_admin claim names tenant 1; the users row is read under the super branch, the request
    proceeds, and its context stays super_admin although the row is an admin in tenant 2."""
    st = S(obs, "f3_reassignment_then_stale_super_claim")
    assert st["stale_probe_status"] == 200, st
    assert st["stale_probe"]["context_role"] == "super_admin", st
    if RUNTIME:
        assert st["stale_probe"]["other_tenant_applications"] > 0, st


def test_deactivation_refuses_the_token_at_the_row(obs):
    st = S(obs, "f3_deactivation_then_stale_token")
    assert st["db_status"] == "disabled" and st["stale_me_status"] == 403, st


def test_reproduces_deactivated_super_claim_still_reaches_the_probe_context(obs):
    """REPRODUCES finding 3c (the context of a deactivated account is not refused before the database context is set). The request is refused at
    the status check, so no data is read; this asserts the refusal and records that the context was established first."""
    st = S(obs, "f3_deactivation_then_stale_super_claim")
    assert st["stale_probe_status"] == 403, st
