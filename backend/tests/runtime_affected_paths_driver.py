"""Driver for test_runtime_affected_paths.py. Runs in its OWN process (the conftest stubs of the pytest run do not apply), against the disposable
database named by DATABASE_URL. Under CV_TEST_RUNTIME_ROLE the URL names the restricted role, so every request below runs as cv_runtime.

It drives the REAL routes that run a SQL statement AFTER a commit on an RLS-protected table (see deploy/runtime_role/context_audit.py):
  - PATCH /applications/bulk-workflow-status  (applications; the candidate-name SELECT runs after db.commit())
  - POST  /tenant/users                       (tenants; the company-name SELECT runs after db.commit())
  - DELETE /admin/subscription-plans/{id}     (subscription_plans, NO RLS: the control)
Outbound email is replaced by a recorder; nothing is sent. No model is called. Writes one JSON object to argv[1].
"""
import asyncio
import json
import os
import pathlib
import sys
import traceback
import warnings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from main import app  # noqa: E402
import services.email_service as email_service  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}}
SENT: list = []
legacy_worker.extract_criteria_task.delay = lambda *a, **k: None   # the legacy extraction is not part of these paths


async def _recorder(**kwargs):
    SENT.append(kwargs)


email_service.send_activation_email = _recorder


def token(who):
    uid, tenant, role = SEED["users"][who]
    return create_access_token({"sub": uid, "tenant_id": tenant, "role": role})


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapper())


def http(method, url, who, body=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {token(who)}"}, json=body)
            await asyncio.sleep(0.05)          # let the fire-and-forget email task run inside this loop
        return r.status_code, (r.json() if r.content else None)
    return _run(go())


def sql(statement, params=None):
    """Test-side observation and seeding. It sets the super_admin context (as the workers do) so it sees and writes every row."""
    async def go():
        async with database.engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
            res = await conn.execute(text(statement), params or {})
            return [dict(r._mapping) for r in res] if res.returns_rows else []
    return _run(go())


def step(name):
    def deco(fn):
        try:
            OUT["steps"][name] = fn() or {}
        except Exception:
            OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
    return deco


T1 = SEED["users"]["hr"][1]
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")


@step("bulk_workflow_status_after_commit")
def _bulk():
    st, job = http("POST", "/jobs", "hr", {"title": "Runtime path job", "description": JOB_TEXT})
    assert st == 201, (st, job)
    job_id = job["job_id"]
    app_ids = []
    for name in ("Candidate One", "Candidate Two"):
        row = sql("INSERT INTO applications (job_id, tenant_id, candidate_name, processing_status, workflow_status, submission_source) "
                  "VALUES (:j, :t, :n, 'ai_scored', 'under_review', 'manual_upload') RETURNING application_id", {"j": job_id, "t": T1, "n": name})
        app_ids.append(str(row[0]["application_id"]))
    st, body = http("PATCH", "/applications/bulk-workflow-status", "hr", {"workflow_status": "shortlisted", "application_ids": app_ids})
    stored = sql("SELECT workflow_status FROM applications WHERE application_id = ANY(CAST(:a AS uuid[])) ORDER BY candidate_name",
                 {"a": app_ids})
    return {"status": st, "body": body, "stored": [r["workflow_status"] for r in stored]}


@step("tenant_user_creation_after_commit")
def _tenant_user():
    SENT.clear()
    sql("UPDATE tenants SET max_users = 10 WHERE tenant_id = :t", {"t": T1})     # the seed has four users in tenant 1: keep the seat limit clear
    st, body = http("POST", "/tenant/users", "admin", {"email": "new.recruiter@t1.example", "full_name": "New Recruiter", "role": "recruiter"})
    return {"status": st, "body": body, "user_created": bool(body and body.get("success")), "email_calls": len(SENT),
            "company_name_sent": SENT[0]["company_name"] if SENT else None}


@step("subscription_plan_delete_control")
def _plan():
    row = sql("INSERT INTO subscription_plans (plan_code, plan_name) VALUES ('runtime_probe', 'Runtime probe') RETURNING plan_id")
    st, body = http("DELETE", f"/admin/subscription-plans/{row[0]['plan_id']}", "super")
    return {"status": st, "body": body}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
