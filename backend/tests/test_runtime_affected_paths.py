"""Routes that run a statement AFTER a commit on a row-level-security table, through the REAL HTTP API on real PostgreSQL.

The point is the tenant context that a commit clears. database.py re-applies the context recorded by set_rls_context at the start of every new
transaction of the session, so a post-commit query still sees the tenant's rows. CV_TEST_RUNTIME_ROLE=cv_runtime runs the same routes as the
restricted role (NOSUPERUSER, NOBYPASSRLS, no ownership): under the superuser a missing context is invisible, under the role it is not.
The control (subscription_plans, no RLS) must behave the same in both modes. Fake model and email only; nothing is sent.
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

DRIVER = BACKEND / "tests" / "runtime_affected_paths_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
RUNTIME_GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1, T2 = str(uuid.UUID(int=0x101)), str(uuid.UUID(int=0x102))
USERS = {"admin": (str(uuid.UUID(int=0x201)), T1, "admin"), "hr": (str(uuid.UUID(int=0x202)), T1, "hr_manager"),
         "other": (str(uuid.UUID(int=0x204)), T2, "admin"), "super": (str(uuid.UUID(int=0x205)), T1, "super_admin"),
         "viewer": (str(uuid.UUID(int=0x206)), T1, "viewer")}


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES (%s,'Tenant One','t1.example','active','active','organization'),(%s,'Tenant Two','t2.example','active','active','organization')", (T1, T2))
        for key, (uid, tenant, role) in USERS.items():
            db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
                 (uid, tenant, f"{key}-{uid[-4:]}@x.example", key, role))
        url = db.async_url
        if RUNTIME_ROLE:
            db.conn.cursor().execute(RUNTIME_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("paths") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8",
               "HTTP_SEED": json.dumps({"users": USERS})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        return json.loads(out.read_text(encoding="utf-8"))
    finally:
        db.drop()
        cluster.stop()


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


def test_bulk_workflow_status_updates_and_reports_candidates_after_its_commit(obs):
    st = S(obs, "bulk_workflow_status_after_commit")
    assert st["status"] == 200, st
    assert st["stored"] == ["shortlisted", "shortlisted"]


def test_tenant_user_creation_sends_the_company_name_read_after_its_commit(obs):
    st = S(obs, "tenant_user_creation_after_commit")
    assert st["status"] == 201 and st["user_created"] is True
    assert st["email_calls"] == 1
    assert st["company_name_sent"] == "Tenant One"


def test_control_subscription_plan_delete_without_rls_is_the_same_in_both_modes(obs):
    st = S(obs, "subscription_plan_delete_control")
    assert st["status"] == 200, st


def test_viewer_is_refused_the_usage_read_and_user_creation_through_the_api(obs):
    """Real HTTP authorization under the restricted role. The usage read answers HR (admin and hr_manager, the roles the route admits) with the
    plan features that migration 111 makes readable; the viewer is refused by the route, and so is user creation, with nothing stored.
    Migration 111 also lists 'viewer' among the tenant read roles; no route serves a viewer plan features today."""
    usage = S(obs, "hr_reads_usage_including_plan_features")
    assert usage["status"] == 200 and usage["has_features_key"] is True, usage
    refused = S(obs, "viewer_usage_is_refused_by_the_route")
    assert refused["status"] == 403, refused
    made = S(obs, "viewer_cannot_create_a_tenant_user")
    assert made["status"] == 403, made


@pytest.mark.xfail(strict=True, reason="GAP: PATCH /applications/bulk-workflow-status (and the single move) check only the ATS module flag, not "
                                       "the role, so a viewer moves candidates. Strict: when a role check lands this must XPASS and be flipped.")
def test_viewer_cannot_move_candidates_in_bulk(obs):
    move = S(obs, "viewer_cannot_change_workflow_status")
    assert move["status"] == 403, move
    assert move["stored"] == "under_review" and move["history_rows"] == 0, move
