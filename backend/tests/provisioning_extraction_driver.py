"""Driver for test_provisioning_extraction_http.py. Runs in its OWN process against the disposable database named by DATABASE_URL (the
superuser, or the restricted role under CV_TEST_RUNTIME_ROLE).

Everything that matters goes through the REAL code:
  * the tenant and platform actions are real HTTP requests (ASGI transport) to the real routes: POST /jobs (creation queues the initial
    extraction), POST /jobs/{id}/requirements/recovery/requests (queues a re-analysis candidate), PATCH /admin/tenants/{id}/subscription
    (suspend, reactivate, with the audit row and the resume);
  * the queued work is CAPTURED at the broker boundary (each task's .delay records its arguments) and then EXECUTED through the real worker
    entry points with Task.apply, which runs the real claim, gate, pipeline and writes;
  * only two external edges are replaced: the model (a fake client that counts calls and returns a known-valid output, or, for the legacy
    criteria path, a counted stub returning a valid analysis) and the Celery broker (capture only).

Nothing is paid, sent or scored, and no real model is called. Writes one JSON object to argv[1]."""
import asyncio
import json
import os
import pathlib
import sys
import traceback
import uuid
import warnings
from copy import deepcopy
from types import SimpleNamespace

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
import routers.admin as admin_router  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from config import get_settings  # noqa: E402
from main import app  # noqa: E402
import services.ai_service as ai  # noqa: E402
import services.requirements_v2_extraction as ext  # noqa: E402
import workers.criteria_worker as criteria  # noqa: E402
import workers.cv_score as cv_score  # noqa: E402
import workers.requirements_v2_extraction_worker as v2worker  # noqa: E402
from isolated_requirements_v2_extraction_postgres import CASES, V22  # noqa: E402  (the valid model output and the job text)
from test_qualifying_context_persistence import fresh_analysis  # noqa: E402  (a valid legacy analysis)

CASE = "B01_en_hr_manager"
JD = CASES[CASE]["jd"]
RAW = V22[CASE]["raw"]
MODEL = "gpt-4o-mini-2024-07-18"
SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}, "config": {"environment": get_settings().environment}}

CAPTURED: list[dict] = []        # every queued task, in the order it was queued: {kind, args|kwargs, phase}
PHASE = {"name": "queue"}        # which step queued it (initial queueing, or a resume)
MODEL_CALLS: list[str] = []      # the text of every model call's messages (the job is identified by its title in them)
LEGACY_CALLS: list[str] = []     # the description of every legacy extraction call
FAIL = {"dispatch": False}       # when True, every captured dispatch raises (the broker is down)


class BrokerDown(RuntimeError):
    pass


def _capture(kind):
    def delay(*args, **kwargs):
        if FAIL["dispatch"]:
            raise BrokerDown("broker unavailable (simulated)")
        CAPTURED.append({"kind": kind, "args": list(args), "kwargs": kwargs, "phase": PHASE["name"]})
    return delay


criteria.extract_criteria_task.delay = _capture("legacy")
v2worker.extract_requirements_v2_task.delay = _capture("v2")
v2worker.extract_candidate_task.delay = _capture("candidate")
cv_score.score_cv_task.delay = _capture("score")


class FakeModel:
    """Stands in for the registry's client. Counts every call and returns the known-valid output."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_kw):
        return self

    async def _create(self, **kwargs):
        MODEL_CALLS.append(json.dumps(kwargs.get("messages", []), ensure_ascii=False))
        return SimpleNamespace(model=MODEL, choices=[SimpleNamespace(message=SimpleNamespace(content=RAW), finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))


async def _resolve_model(db):
    return SimpleNamespace(model_name=MODEL, client=FakeModel())


ext.resolve_model = _resolve_model


async def _legacy_extract(description, **kwargs):
    LEGACY_CALLS.append(description)
    return deepcopy(fresh_analysis())


ai.extract_job_criteria = _legacy_extract


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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.request(method, url, headers={"Authorization": f"Bearer {bearer}"}, json=body)
            await asyncio.sleep(0.02)
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
            OUT["steps"][name] = {"exception": traceback.format_exc()[-2000:]}
    return deco


def set_flag(key, value):
    sql("UPDATE system_config SET value = :v WHERE key = :k", {"v": "true" if value else "false", "k": key})


for code, name, monthly, yearly, trial_days, cvs_users in (("starter", "Starter", 49, 490, 14, 3),):
    sql("INSERT INTO subscription_plans (plan_code, plan_name, monthly_price, yearly_price, currency, trial_days, max_campaigns, "
        "max_processed_cvs_per_month, max_users, status, display_order) VALUES (:c,:n,:m,:y,'USD',:td,5,500,:u,'active',1) "
        "ON CONFLICT (plan_code) DO NOTHING", {"c": code, "n": name, "m": monthly, "y": yearly, "td": trial_days, "u": cvs_users})
sql("UPDATE ai_prompts SET is_active = FALSE WHERE prompt_code = :p", {"p": ext.PROMPT_CODE})
sql("UPDATE ai_prompts SET is_active = TRUE WHERE prompt_code = :p AND version = 3", {"p": ext.PROMPT_CODE})
sql("INSERT INTO system_config (key, value) VALUES ('job_analysis.require_classification_acknowledgment', 'true') "
    "ON CONFLICT (key) DO UPDATE SET value = 'true'")

TENANTS: dict[str, dict] = {}
# the platform administrator (the authority for provisioning): a tenant and a super_admin user, as the activation drivers seed them
sql("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
    "(:t,'Platform','platform.example','active','active','organization') ON CONFLICT (tenant_id) DO NOTHING", {"t": SEED["super"][1]})
sql("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (:u,:t,'platform@x.example','x','P','super_admin','active') "
    "ON CONFLICT (user_id) DO NOTHING", {"u": SEED["super"][0], "t": SEED["super"][1]})


def make_tenant(key):
    tid, admin = str(uuid.uuid4()), str(uuid.uuid4())
    sql("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, plan, tenant_type, max_users, max_jobs, subscription_ends_at) "
        "VALUES (:t,:n,:d,'active','active','starter','organization',20,1000, now() + interval '365 days')",
        {"t": tid, "n": key, "d": f"{key}.example"})
    sql("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (:u,:t,:e,'x','A','admin','active')",
        {"u": admin, "t": tid, "e": f"{key}-{admin[:8]}@{key}.example"})
    TENANTS[key] = {"tenant": tid, "admin": admin, "jobs": {}}
    return TENANTS[key]


def admin_call(key, method, url, body=None):
    t = TENANTS[key]
    return http(method, url, t["admin"], t["tenant"], "admin", body)


def platform(tid, body):
    return http("PATCH", f"/admin/tenants/{tid}/subscription", SEED["super"][0], SEED["super"][1], "super_admin", body)


def future(days=60):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def job_row(job):
    return sql("SELECT jc.criteria_extraction_status AS status, jc.requirements_revision AS revision, "
               "jc.requirements_extraction_token::text AS token, jc.requirements_schema_version AS marker, "
               "jc.original_analysis_json IS NOT NULL AS has_original FROM job_criteria jc WHERE jc.job_id = CAST(:j AS uuid)", {"j": job})[0]


def events(job):
    rows = sql("SELECT version_id::text AS v, kind, event FROM requirements_analysis_log WHERE job_id = CAST(:j AS uuid) ORDER BY log_id", {"j": job})
    return [[r["v"], r["kind"], r["event"]] for r in rows]


def audits(tid, action):
    return sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = CAST(:t AS uuid) AND action = :a", {"t": tid, "a": action})[0]["n"]


def calls_for(title):
    return sum(1 for m in MODEL_CALLS if title in m)


def legacy_calls_for(description):
    return sum(1 for d in LEGACY_CALLS if d == description)


def deliver(job_ids, kinds=None):
    """Execute, through the real worker entry points, every captured delivery for these jobs, in capture order. Returns the outcomes."""
    outcomes = []
    for item in list(CAPTURED):
        if item.get("done"):
            continue
        job = str(item["args"][0]) if item["args"] else str(item["kwargs"].get("application_id", ""))
        if item["kind"] == "score" or job not in job_ids:
            continue
        if kinds and item["kind"] not in kinds:
            continue
        if item["kind"] == "legacy":
            task, args = criteria.extract_criteria_task, item["args"]
        elif item["kind"] == "v2":
            task, args = v2worker.extract_requirements_v2_task, item["args"]
        else:
            task, args = v2worker.extract_candidate_task, item["args"]
        res = task.apply(args=args)
        outcomes.append({"kind": item["kind"], "job": job, "phase": item["phase"], "failed": bool(res.failed()),
                         "result": res.result if not res.failed() else None})
    return outcomes


def queue_work(key):
    """Queue this tenant's work WHILE ENTITLED through the real routes: a legacy job, a v2 job (pending), and a v2 job whose analysis completes
    and is then changed, so that a re-analysis candidate can be requested. Returns the job ids."""
    t = TENANTS[key]
    PHASE["name"] = f"queue-{key}"
    set_flag(ext.FEATURE_KEY, True)
    st, body = admin_call(key, "POST", "/jobs", {"title": f"{key} legacy role", "description": f"Legacy {key} {JD}"})
    assert st == 201, (st, body)
    legacy = body["job_id"]
    st, body = admin_call(key, "POST", "/jobs", {"title": f"{key} v2 pending", "description": JD, "requirements_format": "v2"})
    assert st == 201, (st, body)
    initial = body["job_id"]
    st, body = admin_call(key, "POST", "/jobs", {"title": f"{key} v2 base", "description": JD, "requirements_format": "v2"})
    assert st == 201, (st, body)
    base = body["job_id"]
    base_item = [c for c in CAPTURED if c["kind"] == "v2" and str(c["args"][0]) == base and c["phase"] == f"queue-{key}"][0]
    v2worker.extract_requirements_v2_task.apply(args=base_item["args"])          # the base analysis completes (entitled, the real worker)
    base_item["done"] = True
    sql("UPDATE jobs SET title = title || ' (revised)' WHERE job_id = CAST(:j AS uuid)", {"j": base})   # the base's inputs now differ
    set_flag(ext.RECOVERY_KEY, True)
    rev = job_row(base)["revision"]
    st, body = admin_call(key, "POST", f"/jobs/{base}/requirements/recovery/requests", {"expected_revision": rev, "request_key": str(uuid.uuid4())})
    assert st == 200, (st, body)
    version = body["version"]["version_id"]
    t["jobs"] = {"legacy": legacy, "initial": initial, "base": base, "version": version}
    return t["jobs"]


# ── setup: three tenants. A expires before execution and is provisioned through HTTP; C is suspended, its provisioning fails twice before it
# succeeds; B is never provisioned and must stay untouched throughout. ───────────────────────────────────────────────────────────────────
for key in ("A", "B", "C"):
    make_tenant(key)


def snapshot(key):
    j = TENANTS[key]["jobs"]
    return {"legacy": job_row(j["legacy"]), "initial": job_row(j["initial"]), "base": job_row(j["base"]),
            "events": events(j["base"]), "legacy_calls": legacy_calls_for(f"Legacy {key} {JD}"),
            "model_calls": {k: calls_for(v) for k, v in {"initial": f"{key} v2 pending", "base": f"{key} v2 base"}.items()}}


def ids(key):
    j = TENANTS[key]["jobs"]
    return {j["legacy"], j["initial"], j["base"]}


_B_AT_QUEUE: dict = {}


@step("queue_while_entitled")
def _queue():
    out = {}
    for key in ("A", "B", "C"):
        out[key] = queue_work(key)
        if key == "B":
            _B_AT_QUEUE["snapshot"] = snapshot("B")
    set_flag(ext.FEATURE_KEY, True)
    return {"jobs": {k: v for k, v in out.items()}, "queued": len(CAPTURED)}



@step("expired_tenant_runs_queued_work_with_zero_model_calls")
def _expired():
    before = snapshot("A")
    sql("UPDATE tenants SET subscription_ends_at = now() - interval '1 second' WHERE tenant_id = :t", {"t": TENANTS["A"]["tenant"]})
    PHASE["name"] = "run-expired"
    model_before = len(MODEL_CALLS)
    outcomes = deliver(ids("A"))
    return {"before": before, "after": snapshot("A"), "outcomes": outcomes, "model_calls_during": len(MODEL_CALLS) - model_before}


def suspended_run(key):
    """Suspend the tenant through the real route, then run its queued work. Returns the state before, after and the outcomes."""
    before = snapshot(key)
    model_before = len(MODEL_CALLS)
    legacy_before = len(LEGACY_CALLS)
    st, body = platform(TENANTS[key]["tenant"], {"action": "suspend", "reason": "suspension drill before execution"})
    PHASE["name"] = f"run-suspended-{key}"
    outcomes = deliver(ids(key))
    return {"suspend_status": st, "before": before, "after": snapshot(key), "outcomes": outcomes,
            "model_calls_during": len(MODEL_CALLS) - model_before, "legacy_calls_during": len(LEGACY_CALLS) - legacy_before}


@step("reactivation_through_http_resumes_and_completes_the_queued_work")
def _reactivate_A():
    t = TENANTS["A"]
    PHASE["name"] = "provision-A"
    n_before = len(CAPTURED)
    st, body = platform(t["tenant"], {"action": "reactivate", "expires_at": future(), "reason": "bank transfer confirmed for A"})
    resumed = CAPTURED[n_before:]
    PHASE["name"] = "deliver-A"
    outcomes = deliver(ids("A"))
    return {"status": st, "body": body, "resumed_kinds": sorted(c["kind"] for c in resumed),
            "resumed_jobs": sorted({str(c["args"][0]) for c in resumed if c["args"]}), "outcomes": outcomes,
            "after": snapshot("A"), "audit_reactivate": audits(t["tenant"], "subscription_reactivate")}


@step("repeated_provisioning_and_duplicate_delivery_create_no_extra_calls")
def _repeat_A():
    t = TENANTS["A"]
    calls_before = {k: calls_for(v) for k, v in {"initial": f"A v2 pending", "base": "A v2 base"}.items()}
    legacy_before = legacy_calls_for(f"Legacy A {JD}")
    status_before = snapshot("A")
    PHASE["name"] = "provision-A-again"
    st, body = platform(t["tenant"], {"action": "assign_plan", "plan_code": "starter", "expires_at": future(90), "reason": "second provisioning"})
    outcomes = deliver(ids("A"))                       # the duplicates of every item, delivered again
    return {"status": st, "resumed": body.get("resumed_extraction") if isinstance(body, dict) else None, "outcomes": outcomes,
            "calls_before": calls_before, "calls_after": {k: calls_for(v) for k, v in {"initial": "A v2 pending", "base": "A v2 base"}.items()},
            "legacy_before": legacy_before, "legacy_after": legacy_calls_for(f"Legacy A {JD}"),
            "state_before": status_before, "state_after": snapshot("A")}


@step("tenant_c_is_suspended_and_its_queued_work_makes_no_model_call")
def _suspend_C():
    return suspended_run("C")


@step("audit_failure_causes_no_activation_and_no_resume")
def _audit_C():
    t = TENANTS["C"]
    before_state = sql("SELECT subscription_status FROM tenants WHERE tenant_id = :t", {"t": t["tenant"]})[0]
    before_snapshot = snapshot("C")
    n_before = len(CAPTURED)
    original = admin_router.log_action

    async def failing(*a, **k):
        raise RuntimeError("audit store unavailable (simulated)")
    admin_router.log_action = failing
    try:
        PHASE["name"] = "audit-failure-C"
        st, body = platform(t["tenant"], {"action": "reactivate", "expires_at": future(), "reason": "audit failure drill"})
    finally:
        admin_router.log_action = original
    return {"status": st, "state_before": before_state, "state_after": sql("SELECT subscription_status FROM tenants WHERE tenant_id = :t", {"t": t["tenant"]})[0],
            "new_captures": len(CAPTURED) - n_before, "snapshot_before": before_snapshot, "snapshot_after": snapshot("C")}


@step("dispatch_failure_leaves_the_work_recoverable_and_a_later_provisioning_completes_it")
def _dispatch_C():
    t = TENANTS["C"]
    PHASE["name"] = "dispatch-failure-C"
    before = snapshot("C")
    FAIL["dispatch"] = True
    try:
        st, body = platform(t["tenant"], {"action": "reactivate", "expires_at": future(), "reason": "broker outage drill"})
    finally:
        FAIL["dispatch"] = False
    state = sql("SELECT subscription_status FROM tenants WHERE tenant_id = :t", {"t": t["tenant"]})[0]
    after_failed = snapshot("C")
    PHASE["name"] = "provision-C-retry"
    n_before = len(CAPTURED)
    st2, body2 = platform(t["tenant"], {"action": "assign_plan", "plan_code": "starter", "expires_at": future(), "reason": "provisioning after outage"})
    resumed = CAPTURED[n_before:]
    PHASE["name"] = "deliver-C"
    outcomes = deliver(ids("C"))
    return {"status": st, "body": body, "tenant_state": state, "before": before, "after_failed": after_failed,
            "retry_status": st2, "retry_resumed_kinds": sorted(c["kind"] for c in resumed), "outcomes": outcomes,
            "after": snapshot("C"), "audit_reactivate": audits(t["tenant"], "subscription_reactivate")}


@step("another_tenant_is_untouched_throughout")
def _b_untouched():
    return {"at_queue": _B_AT_QUEUE.get("snapshot"), "snapshot": snapshot("B"), "captured_for_b": [c["kind"] for c in CAPTURED if c["args"] and str(c["args"][0]) in ids("B")]}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
