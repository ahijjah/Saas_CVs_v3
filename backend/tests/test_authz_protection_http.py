"""Protection tests for the three targeted authorization fixes, over the REAL HTTP API on REAL PostgreSQL, in both database modes.

  1. simulated payment is refused in production before any mutation; the development opt-in works and is the only path.
  2. a token whose tenant or role differs from the current record is refused; a disabled account is refused; a valid platform administrator,
     a valid tenant administrator, pooled sessions and post-commit reads keep working.
  3. a viewer is refused on every write route in the allowlist (no database change, no enqueue, no email, no model call); the other roles that
     may write keep their access. A static guard fails on any mutating route that is neither role-gated nor on the documented exception list.

Superuser: CV_TEST_RUNTIME_ROLE unset (default). Restricted role: CV_TEST_RUNTIME_ROLE=cv_runtime. Row-level security is checked only under the
restricted role, because the superuser bypasses it by design; every other assertion holds in both modes.
"""
import ast
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

DRIVER = BACKEND / "tests" / "authz_protection_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
RUNTIME = bool(RUNTIME_ROLE)
RUNTIME_GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1, T2 = str(uuid.UUID(int=0x501)), str(uuid.UUID(int=0x502))
USERS = {"admin": (str(uuid.UUID(int=0x511)), T1, "admin"), "hr": (str(uuid.UUID(int=0x512)), T1, "hr_manager"),
         "recruiter": (str(uuid.UUID(int=0x516)), T1, "recruiter"),
         "viewer": (str(uuid.UUID(int=0x513)), T1, "viewer"), "other": (str(uuid.UUID(int=0x514)), T2, "admin"),
         "super": (str(uuid.UUID(int=0x515)), T1, "super_admin")}


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
             "(%s,'Tenant One','t1.example','active','active','organization'),(%s,'Tenant Two','t2.example','active','active','organization')", (T1, T2))
        db.entitle_active_tenants()   # fixture: an active tenant needs a validity date (see realdb_helper)
        for key, (uid, tenant, role) in USERS.items():
            db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
                 (uid, tenant, f"{key}-{uid[-4:]}@x.example", key, role))
        for code, name, monthly, yearly, trial_days, cvs_users in (("starter", "Starter", 49, 490, 14, 3), ("professional", "Professional", 149, 1490, 14, 15), ("enterprise", "Enterprise", 499, 4990, 14, 100)):
            db.q("INSERT INTO subscription_plans (plan_code, plan_name, monthly_price, yearly_price, currency, trial_days, max_campaigns, "
                 "max_processed_cvs_per_month, max_users, status, display_order) VALUES (%s,%s,%s,%s,'USD',%s,5,500,%s,'active',1) "
                 "ON CONFLICT (plan_code) DO NOTHING", (code, name, monthly, yearly, trial_days, cvs_users))
        url = db.async_url
        if RUNTIME:
            db.conn.cursor().execute(RUNTIME_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("protect") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8", "ENVIRONMENT": "production",
               "HTTP_SEED": json.dumps({"users": USERS})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        dump = os.environ.get("AUTHZ_OBS_DUMP")
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


def no_side_effects(st):
    assert st["side_effects"] == {"enqueued": 0, "emails": 0, "model_calls": 0}, st


# ── 0. the configuration under test ────────────────────────────────────────────────────────────────────────────────────────────
def test_the_configuration_under_test_is_production(obs):
    assert obs["config"]["environment"] == "production" and obs["config"]["allow_simulated_payment"] is False


# ── 1. simulated payment ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_production_refuses_simulated_payment_before_any_mutation(obs):
    st = S(obs, "f1_admin_simulates_payment_in_production")
    assert st["status"] == 403, st
    assert st["db_changed"] is False and st["db_before"]["subscription_status"] == "pending_payment", st
    assert st["db_after"]["plan"] is None and st["db_after"]["has_end"] == st["db_before"]["has_end"] and st["emails"] == 0, st


def test_a_permitted_role_that_is_not_an_admin_is_refused_simulated_payment(obs):
    st = S(obs, "f1_hr_simulates_payment_in_production")
    assert st["status"] == 403 and st["db_changed"] is False, st


def test_development_with_the_explicit_opt_in_activates_the_subscription(obs):
    st = S(obs, "f1_development_opt_in_control")
    assert st["status"] == 200 and st["db_subscription_status"] == "active" and st["db_plan"] == "starter", st


def test_development_without_the_opt_in_is_refused_and_nothing_changes(obs):
    st = S(obs, "f1_development_without_opt_in_is_refused")
    assert st["status"] == 403 and st["subscription_status"] == "pending_payment", st


def test_the_admin_can_still_select_a_plan_as_before(obs):
    st = S(obs, "f1_select_plan_for_tenant")
    assert st["status"] == 200 and st["db_subscription_status"] == "pending_payment" and st["db_pending_plan"] == "starter", st


# ── 2. token checks, with controls ─────────────────────────────────────────────────────────────────────────────────────────────
def test_pooled_sessions_report_each_requests_own_context(obs):
    """Controls in both modes. One engine, one loop, alternating tenants and roles: each probe reports the context of its own token, and the
    restricted role never sees another tenant's rows."""
    for r in S(obs, "pooled_sequence_control")["requests"]:
        assert r["status"] == 200 and r["context_role"] == r["expected_role"] and r["context_tenant"] == r["expected_tenant"], r
        if RUNTIME and r["expected_tenant"] == T1 and r["expected_role"] != "super_admin":
            assert r["other_tenant_applications"] == 0, r


def test_a_valid_platform_administrator_keeps_the_platform_context_and_dashboard(obs):
    st = S(obs, "valid_platform_admin_control")
    assert st["probe_status"] == 200 and st["probe"]["context_role"] == "super_admin", st
    assert st["platform_dashboard_status"] == 200, st


def test_post_commit_reads_still_return_their_rows(obs):
    st = S(obs, "post_commit_bulk_workflow_control")
    assert st["status"] == 200 and st["updated_count"] == 1 and "Post Commit Candidate" in st["post_commit_names"], st


def test_post_commit_tenant_user_creation_still_reads_the_company_name(obs):
    st = S(obs, "post_commit_tenant_user_control")
    assert st["status"] == 201 and st["user_created"] is True and st["company_name_sent"] == "Tenant One", st


def test_a_demoted_super_admin_token_is_refused_and_the_fresh_token_works(obs):
    st = S(obs, "t_demoted_super_token")
    assert st["db_role"] == "admin"
    assert st["stale_probe_status"] == 401 and st["stale_dashboard_status"] == 401, st
    assert st["fresh_probe_status"] == 200 and st["fresh_probe"]["context_role"] == "admin", st


def test_a_reassigned_token_is_refused_in_both_modes(obs):
    st = S(obs, "t_reassigned_admin_token")
    assert st["db_tenant"] == T2
    assert st["stale_probe_status"] == 401 and st["stale_tenant_users_status"] == 401, st


def test_a_super_claim_on_a_reassigned_account_is_refused(obs):
    st = S(obs, "t_reassigned_super_claim")
    assert st["stale_probe_status"] == 401, st


def test_a_token_without_a_tenant_claim_is_refused_for_every_role(obs):
    st = S(obs, "t_null_tenant_claim")
    assert st["status"] == 401, st


def test_a_disabled_account_is_still_refused_with_403(obs):
    st = S(obs, "t_deactivated_hr")
    assert st["db_status"] == "disabled" and st["status"] == 403 and st["body"]["detail"] == "Account disabled", st


def test_a_disabled_account_with_a_mismatched_super_claim_is_refused(obs):
    st = S(obs, "t_deactivated_viewer_with_super_claim")
    assert st["status"] == 401, st


# ── 3. viewer read-only, with permitted-role controls ───────────────────────────────────────────────────────────────────────────
VIEWER_REFUSALS = ["v_comment", "v_communication_log", "v_communication_send_email", "v_tag_add", "v_tag_create", "v_talent_pool",
                   "v_interview", "v_saved_view", "v_create_job", "v_update_criteria", "v_score_pending", "v_delete_uploaded_cv",
                   "v_bulk_batch", "v_workflow_move_single", "v_workflow_move_bulk", "v_reset_stuck"]


@pytest.mark.parametrize("name", VIEWER_REFUSALS)
def test_a_viewer_is_refused_with_no_database_change_and_no_side_effect(obs, name):
    st = S(obs, name)
    assert st["status"] == 403, st
    assert st["db_changed"] is False, st
    no_side_effects(st)


@pytest.mark.parametrize("name", ["e_viewer_creates_campaign", "e_viewer_creates_client_organization", "e_viewer_creates_template",
                                  "e_viewer_updates_workflow_policies", "e_viewer_creates_tenant_user"])
def test_a_viewer_is_refused_on_the_routes_that_check_roles_inline(obs, name):
    st = S(obs, name)
    assert st["status"] == 403 and st["db_changed"] is False, st


def test_the_viewer_score_refusal_enqueues_nothing_and_claims_nothing(obs):
    st = S(obs, "v_score_pending")
    assert st["enqueued"] == [] and st["db_after"] == 0, st


def test_the_viewer_delete_refusal_keeps_the_upload(obs):
    st = S(obs, "v_delete_uploaded_cv")
    assert st["db_before"] == 1 and st["db_after"] == 1, st


def test_the_viewer_email_refusal_sends_nothing(obs):
    st = S(obs, "v_communication_send_email")
    assert st["status"] == 403 and st["side_effects"]["emails"] == 0, st


@pytest.mark.parametrize("name, label", [("control_hr_comment", "hr comment"), ("control_recruiter_comment", "recruiter comment"),
                                         ("control_admin_adds_tag", "admin tag"), ("control_recruiter_moves_one_candidate", "recruiter move"),
                                         ("control_recruiter_creates_job_preserved", "recruiter job (preserved)")])
def test_permitted_roles_still_write_and_the_row_is_stored(obs, name, label):
    st = S(obs, name)
    assert st["status"] in (200, 201), st
    assert st["db_changed"] is True, st


def test_hr_manager_scoring_still_enqueues_its_cv(obs):
    st = S(obs, "control_hr_scores_pending")
    assert st["status"] == 202 and len(st["enqueued"]) == 1 and st["db_after"] == 1, st


# ── 4. static guard: every mutating route is role-gated or on the documented exception list ─────────────────────────────────
ROUTERS = BACKEND / "routers"
MUTATING = {"post", "put", "patch", "delete"}
ROLE_GATES = {"RequireWriteRole", "RequireSuperAdmin", "RequireAdmin"}

# Routes that carry no shared role gate, each with its reason. A new mutating route fails the guard until it is gated or listed here.
EXEMPT = {
    "auth.login": "unauthenticated by design", "auth.register": "unauthenticated by design",
    "auth.forgot_password": "unauthenticated by design", "auth.reset_password": "unauthenticated by design",
    "auth.change_password": "self-service for the signed-in user, any role", "auth.update_profile": "self-service for the signed-in user, any role",
    "public.submit_public_application": "anonymous public intake",
    "admin.update_user_status": "inline check: super_admin or admin",
    "tenant.create_tenant_user": "inline check: tenant admin only (_require_tenant_admin)",
    "tenant.update_tenant_user_profile": "inline check: tenant admin only", "tenant.update_tenant_user_status": "inline check: tenant admin only",
    "tenant.activate_trial": "inline check: tenant admin only (free trial, not a payment path)",
    "tenant.select_plan": "inline check: tenant admin only (records a pending plan, no activation)",
    "tenant.simulate_payment": "inline check: tenant admin only; environment gate in _require_simulated_payment_allowed",
    "subscription_plans.subscribe_to_plan": "FLAGGED: inline tenant-admin check, but it sets subscription_status = active with no payment, in any environment (open decision)",
    "campaigns.create_campaign": "inline check: admin, hr_manager, super_admin (_require_write_access)",
    "campaigns.update_campaign": "inline check: admin, hr_manager, super_admin", "campaigns.transition_campaign_status": "inline check: admin, hr_manager, super_admin",
    "campaigns.delete_campaign": "inline check: admin, super_admin (_require_admin)",
    "client_organizations.create_client_organization": "inline check: admin or super_admin, agency tenants",
    "client_organizations.update_client_organization": "inline check: write roles", "client_organizations.deactivate_client_organization": "inline check: admin or super_admin",
    "client_organizations.assign_user_to_client": "inline check: admin or super_admin", "client_organizations.remove_user_from_client": "inline check: admin or super_admin",
    "communication.create_template": "inline check: admin or super_admin", "communication.update_template": "inline check: admin or super_admin",
    "communication.delete_template": "inline check: admin or super_admin", "communication.create_automation_rule": "inline check: admin or super_admin",
    "communication.update_automation_rule": "inline check: admin or super_admin", "communication.delete_automation_rule": "inline check: admin or super_admin",
    "workflow_policies.update_workflow_policies": "inline check: admin only",
    "candidate_tags.delete_tag": "inline check: admin or super_admin",
    "job_requirements.save_requirements": "service check: ensure_can_edit (admin, hr_manager)",
    "job_requirements.acknowledge_classification_warning": "service check: ensure_can_edit", "job_requirements.confirm_no_numeric_score": "service check: ensure_can_edit",
    "job_requirements.confirm_structure_review": "service check: ensure_can_edit", "job_requirements.retry_extraction": "service check: ensure_can_edit",
    "job_requirements.request_analysis": "service check: ensure_can_edit", "job_requirements.apply_analysis": "service check: ensure_can_edit",
    "job_requirements.keep_analysis": "service check: ensure_can_edit", "job_requirements.discard_analysis": "service check: ensure_can_edit",
}


def _mutating_routes():
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in MUTATING:
                    deps = " ".join(ast.unparse(k.value) for k in d.keywords if k.arg == "dependencies")
                    # a gate may also be a parameter default (for example `_guard: None = RequireSuperAdmin`)
                    defaults = " ".join(ast.unparse(v) for v in node.args.defaults + node.args.kw_defaults if v is not None)
                    gated_text = deps + " " + defaults
                    role_gated = any(g in gated_text for g in ROLE_GATES) or "require_role(" in gated_text
                    yield f"{path.stem}.{node.name}", role_gated


def test_every_mutating_route_is_role_gated_or_on_the_documented_exception_list():
    routes = list(_mutating_routes())
    ungated = sorted(k for k, gated in routes if not gated)
    unexplained = [k for k in ungated if k not in EXEMPT]
    assert unexplained == [], f"mutating routes with no role gate and no documented reason: {unexplained}"


def test_the_exception_list_has_no_stale_entries_and_every_entry_has_a_reason():
    names = {k for k, gated in _mutating_routes() if not gated}
    stale = sorted(k for k in EXEMPT if k not in names)
    assert stale == [], f"exception entries that no longer match an ungated route: {stale}"
    assert all(reason.strip() for reason in EXEMPT.values())


def test_the_shared_write_allowlist_is_explicit_and_excludes_only_the_viewer():
    text = (BACKEND / "auth" / "dependencies.py").read_text(encoding="utf-8")
    assert 'WRITE_ROLES = ("super_admin", "admin", "hr_manager", "recruiter")' in text
