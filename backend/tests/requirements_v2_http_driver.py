"""Driver for test_requirements_v2_http_workflow.py. Runs in its OWN process, so the conftest stubs of the pytest run do not apply.

It drives the REAL FastAPI app (main.app: routers/jobs.py, routers/job_requirements.py, the services, the real database session and the real
row-level-security context) against the disposable PostgreSQL named by DATABASE_URL. Only the model is replaced: a fake transport that replays
the recorded v2-2 answers, or raises provider exceptions. The Celery task runs eagerly (task.apply) after each request that queues it.

Writes one JSON object of observations to argv[1]. Nothing here calls a paid AI API. It proves the wiring and the storage contract on real
PostgreSQL, not the model's accuracy (the recorded answers were produced by v2-2, not v2-3).
"""
import asyncio
import json
import os
import pathlib
import sys
import traceback
import warnings
from types import SimpleNamespace

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
import openai  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from main import app  # noqa: E402
from services import requirements_v2_extraction as ext  # noqa: E402
from workers.requirements_v2_extraction_worker import extract_requirements_v2_task as TASK  # noqa: E402

SEED = json.loads(os.environ["HTTP_SEED"])          # tenants and users created by the pytest side
MODEL = "gpt-4o-mini-2024-07-18"
CASES_DIR = BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases"
CASES = {c["id"]: c for c in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES_DIR.glob("B*.json")))}
RECORDED = {json.loads(x)["case"]: json.loads(x) for x in
            (BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(x)["run"] == "run1"}
OUT: dict = {"steps": {}}
QUEUED: list = []
SCRIPT: list = []


def token(user_key):
    uid, tenant, role = SEED["users"][user_key]
    return create_access_token({"sub": uid, "tenant_id": tenant, "role": role})


def _req():
    return httpx.Request("POST", "https://api.openai.example/v1/chat/completions")


class _Completions:
    async def create(self, **kw):
        OUT.setdefault("calls", []).append({"model": kw["model"], "temperature": kw["temperature"], "max_tokens": kw["max_tokens"],
                                            "response_format": kw["response_format"]})
        step = SCRIPT.pop(0)
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(model=MODEL, choices=[SimpleNamespace(message=SimpleNamespace(content=step[0]), finish_reason=step[1])],
                               usage=SimpleNamespace(prompt_tokens=6400, completion_tokens=900))


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())

    def with_options(self, **_):
        return self


async def fake_resolve_model(db):
    return SimpleNamespace(model_name=MODEL, client=FakeClient())


ext.resolve_model = fake_resolve_model          # the registry lookup is replaced; the prompt check (approved hash) is NOT


def recorded(case_id, finish="stop"):
    return (RECORDED[case_id]["raw"], finish)


def provider_error(kind):
    if kind == "auth":
        return openai.AuthenticationError("bad key sk-SECRET-DO-NOT-STORE", response=httpx.Response(401, request=_req()), body=None)
    if kind == "timeout":
        return openai.APITimeoutError(request=_req())
    raise AssertionError(kind)


def _run(coro):
    """One HTTP call (or one SQL statement) in its own loop; the pooled connections of the app engine are dropped afterwards, because
    each loop must own its connections."""
    async def wrapper():
        try:
            return await coro
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapper())


def http(method, url, who=None, body=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            headers = {"Authorization": f"Bearer {token(who)}"} if who else {}
            r = await c.request(method, url, headers=headers, json=body)
        return r.status_code, (r.json() if r.content else None)
    return _run(go())


def sql(statement, params=None):
    async def go():
        async with database.engine.begin() as conn:
            res = await conn.execute(text(statement), params or {})
            return [dict(r._mapping) for r in res] if res.returns_rows else []
    return _run(go())


def set_switch(on):
    sql("UPDATE system_config SET value = :v WHERE key = 'requirements_v2.enabled'", {"v": "true" if on else "false"})


def activate_prompt(on):
    sql("UPDATE ai_prompts SET is_active = :a WHERE prompt_code = 'criteria_extraction_v2' AND version = 3", {"a": bool(on)})


def run_queued(index=-1):
    """Eager execution of a queued attempt (the real task, its own retries included). Returns the task's result, the model calls it made, and the
    failure text if it failed."""
    args = QUEUED[index]
    OUT["calls"] = []
    res = TASK.apply(args=list(args))
    return {"failed": res.failed(), "result": res.result if not res.failed() else None, "model_calls": len(OUT["calls"]),
            "traceback": (str(res.traceback)[-600:] if res.failed() else None)}


def fake_delay(*args):
    QUEUED.append(list(args))


TASK.delay = fake_delay                         # queued after the commit; the driver runs it explicitly (eager, in its own loop)
import workers.criteria_worker as _legacy_worker  # noqa: E402

LEGACY_QUEUED: list = []
_legacy_worker.extract_criteria_task.delay = lambda *a, **k: LEGACY_QUEUED.append(list(a))   # the legacy path is not run here


def create_job(case_id, who="admin", fmt="v2"):
    c = CASES[case_id]
    status, body = http("POST", "/jobs", who, {"title": c["title"], "description": c["jd"], "requirements_format": fmt})
    return status, body


def job_of(body):
    return body["job_id"] if body and "job_id" in body else None


def requirements(job_id, who):
    return http("GET", f"/jobs/{job_id}/requirements", who)


def cats_summary(doc):
    cats = doc["categories"]
    return {c: {"count": len(v["items"]), "importance": sorted({i["importance"] for i in v["items"]}), "weight": v.get("weight")}
            for c, v in cats.items()}


def step(name):
    def deco(fn):
        def inner():
            try:
                OUT["steps"][name] = fn() or {}
            except Exception:
                OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
        return inner
    return deco


# ── scenarios ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@step("off_refuses_creation")
def _off():
    set_switch(False)
    QUEUED.clear()
    status, body = create_job("B01_en_hr_manager")
    return {"status": status, "detail": body.get("detail") if body else None, "queued": len(QUEUED)}


@step("english_required_preferred")
def _en():
    set_switch(True)
    activate_prompt(True)
    SCRIPT[:] = [recorded("B01_en_hr_manager")]
    QUEUED.clear()
    status, body = create_job("B01_en_hr_manager")
    job = job_of(body)
    out = {"create_status": status, "requirements_format_in_response": body.get("requirements_format") if body else None,
           "queued": len(QUEUED)}
    if job is None:
        return out
    out["worker"] = run_queued()
    out["remaining_script"] = len(SCRIPT)
    s, view = requirements(job, "admin")
    out.update({"get_status": s, "revision": view["revision"], "extraction": view.get("extraction"), "can_edit": view["can_edit"],
                "readiness_state": view["readiness"]["state"], "pipeline_status": (view.get("pipeline") or {}).get("status"),
                "categories": cats_summary(view["requirements"]), "schema_version": view["requirements"]["schema_version"],
                "original_present": view["original"] is not None})
    s, details = http("GET", f"/jobs/details?job_id={job}", "admin")
    out["details_status"] = s
    out["details_requirements_format"] = (details or {}).get("details", {}).get("requirements_format") if isinstance(details, dict) else None
    out["job_id"] = job
    out["prompt_sha_recorded"] = (view.get("pipeline") or {}).get("component_versions", {}).get("extraction_prompt", {}).get("sha256")
    return out


@step("edit_save_reload_and_authority")
def _edit():
    job = OUT["steps"]["english_required_preferred"]["job_id"]
    s, view = requirements(job, "admin")
    doc = view["requirements"]
    first = doc["categories"]["skills"]["items"][0]
    original_before = view["original"]["categories"]["skills"]["items"][0]["text"]
    first["text"] = first["text"] + " (edited)"
    rev = view["revision"]
    s_save, saved = http("PUT", f"/jobs/{job}/requirements", "admin", {"expected_revision": rev, "requirements": doc})
    s_stale, stale = http("PUT", f"/jobs/{job}/requirements", "admin", {"expected_revision": rev, "requirements": doc})
    s_reload, reloaded = requirements(job, "admin")
    s_viewer, viewer = requirements(job, "viewer")
    s_viewer_put, viewer_put = http("PUT", f"/jobs/{job}/requirements", "viewer", {"expected_revision": rev, "requirements": doc})
    s_other, other = requirements(job, "other")
    s_hr, hr = http("GET", f"/jobs/{job}/requirements", "hr")
    return {"save_status": s_save, "save_revision": (saved or {}).get("revision"), "stale_status": s_stale,
            "stale_code": ((stale or {}).get("detail") or {}).get("code") if isinstance(stale, dict) else None,
            "reloaded_text": reloaded["requirements"]["categories"]["skills"]["items"][0]["text"],
            "reloaded_revision": reloaded["revision"], "original_before": original_before,
            "original_after": reloaded["original"]["categories"]["skills"]["items"][0]["text"],
            "original_edited_flag": (reloaded.get("edited_categories") or {}).get("skills"),
            "viewer_get_status": s_viewer, "viewer_can_edit": viewer["can_edit"],
            "viewer_put_status": s_viewer_put, "other_tenant_status": s_other, "hr_get_status": s_hr, "hr_can_edit": hr["can_edit"],
            "audit_saves": sql("SELECT COUNT(*) AS n FROM audit_logs WHERE resource_id = :j", {"j": job})[0]["n"]}


@step("preferred_only")
def _pref():
    SCRIPT[:] = [recorded("B04_en_preferred_only")]
    QUEUED.clear()
    status, body = create_job("B04_en_preferred_only")
    job = job_of(body)
    worker = run_queued()
    s, view = requirements(job, "admin")
    weights = sql("SELECT weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, "
                  "weight_other, requirements_schema_version FROM job_criteria WHERE job_id = :j", {"j": job})[0]
    return {"create_status": status, "worker_failed": worker["failed"], "extraction": view.get("extraction"), "categories": cats_summary(view["requirements"]),
            "weights": {k: v for k, v in weights.items()}, "readiness_state": view["readiness"]["state"],
            "preferred_only_confirmation": view.get("preferred_only_confirmation")}


@step("arabic")
def _ar():
    SCRIPT[:] = [recorded("B07_ar_accountant")]
    QUEUED.clear()
    status, body = create_job("B07_ar_accountant")
    job = job_of(body)
    worker = run_queued()
    s, view = requirements(job, "admin")
    texts = [i["text"] for c in view["requirements"]["categories"].values() for i in c["items"]]
    return {"create_status": status, "worker_failed": worker["failed"], "extraction": view.get("extraction"),
            "categories": cats_summary(view["requirements"]), "arabic_item_texts": sum(any("؀" <= ch <= "ۿ" for ch in t) for t in texts),
            "total_items": len(texts), "readiness_state": view["readiness"]["state"]}


@step("truncated_output_fails_without_a_document")
def _trunc():
    SCRIPT[:] = [recorded("B01_en_hr_manager", finish="length")]
    QUEUED.clear()
    status, body = create_job("B02_en_data_analyst")
    job = job_of(body)
    worker = run_queued()
    s, view = requirements(job, "admin")
    row = sql("SELECT criteria_extraction_status AS st, criteria_extraction_error AS err, requirements_revision AS rev, "
              "analysis_json IS NULL AS no_analysis FROM job_criteria WHERE job_id = :j", {"j": job})[0]
    return {"worker_failed": worker["failed"], "model_calls": worker["model_calls"], "row": row, "get_status": s, "extraction": view.get("extraction"),
            "readiness_basis": view["readiness"].get("basis"), "can_edit": view["can_edit"]}


@step("failure_retry_and_stale")
def _retry():
    SCRIPT[:] = [provider_error("auth")]
    QUEUED.clear()
    status, body = create_job("B03_en_warehouse_supervisor")
    job = job_of(body)
    first = run_queued()
    first_args = list(QUEUED[0])
    s, failed_view = requirements(job, "admin")
    s_v, failed_viewer = requirements(job, "viewer")
    s_retry_viewer, retry_viewer = http("POST", f"/jobs/{job}/requirements/extraction/retry", "viewer")
    queued_before = len(QUEUED)
    s_retry, retry_body = http("POST", f"/jobs/{job}/requirements/extraction/retry", "admin")
    s_again, again = http("POST", f"/jobs/{job}/requirements/extraction/retry", "admin")
    queued_after = len(QUEUED)
    SCRIPT[:] = [recorded("B03_en_warehouse_supervisor")]
    second = run_queued(-1)
    stale = TASK.apply(args=first_args).result                   # the superseded attempt's late result (no model call: it is refused at claim)
    s_done, done = requirements(job, "admin")
    rows = sql("SELECT request_status, error_type, retry_count FROM ai_usage_log WHERE job_id = :j ORDER BY created_at", {"j": job})
    return {"first_worker": first["result"], "first_model_calls": first["model_calls"], "failed_extraction": failed_view.get("extraction"),
            "viewer_retry_available": failed_viewer["extraction"]["retry_available"],
            "viewer_retry_status": s_retry_viewer, "admin_retry_status": s_retry, "admin_retry_body_status": (retry_body or {}).get("extraction", {}).get("status"),
            "second_retry_status": s_again, "queued_for_retry": queued_after - queued_before,
            "second_worker_failed": second["failed"], "second_model_calls": second["model_calls"], "stale_outcome": stale,
            "done_extraction": done.get("extraction"),
            "done_readiness_basis": done["readiness"].get("basis"), "usage_rows": rows}


@step("transient_then_success")
def _transient():
    SCRIPT[:] = [provider_error("timeout"), recorded("B05_en_open_empty")]
    QUEUED.clear()
    status, body = create_job("B05_en_open_empty")
    job = job_of(body)
    worker = run_queued()
    s, view = requirements(job, "admin")
    rows = sql("SELECT request_status, error_type, retry_count FROM ai_usage_log WHERE job_id = :j ORDER BY created_at", {"j": job})
    return {"worker_failed": worker["failed"], "model_calls": worker["model_calls"], "extraction": view.get("extraction"),
            "usage_rows": rows, "readiness_state": view["readiness"]["state"]}


@step("legacy_job_untouched")
def _legacy():
    status, body = create_job("B01_en_hr_manager", fmt="legacy")
    legacy_job = job_of(body)
    s_req, req = requirements(legacy_job, "admin") if legacy_job else (None, None)
    s_det, det = http("GET", f"/jobs/details?job_id={legacy_job}", "admin") if legacy_job else (None, None)
    return {"create_status": status, "requirements_format_in_response": (body or {}).get("requirements_format"), "requirements_get_status": s_req,
            "requirements_get_code": ((req or {}).get("detail") or {}).get("code") if isinstance(req, dict) else None,
            "details_requirements_format": ((det or {}).get("details") or {}).get("requirements_format") if isinstance(det, dict) else None,
            "queued_v2": len([q for q in QUEUED if q and q[0] == legacy_job]), "queued_legacy": len([q for q in LEGACY_QUEUED if q and q[0] == legacy_job])}


@step("switch_off_refuses_retry")
def _off_retry():
    rows = sql("SELECT j.job_id::text AS j FROM jobs j JOIN job_criteria jc ON jc.job_id = j.job_id WHERE jc.requirements_schema_version = 2 "
               "AND jc.criteria_extraction_status = 'failed' LIMIT 1")
    if not rows:
        return {"skipped": True}
    set_switch(False)
    s, body = http("POST", f"/jobs/{rows[0]['j']}/requirements/extraction/retry", "admin")
    set_switch(True)
    return {"status": s, "code": ((body or {}).get("detail") or {}).get("code") if isinstance(body, dict) else None}


@step("live_acknowledgment_policy_governs_readiness")
def _policy():
    SCRIPT[:] = [recorded("B06_en_injection")]
    QUEUED.clear()
    status, body = create_job("B06_en_injection")
    job = job_of(body)
    worker = run_queued()
    out = {"worker_failed": worker["failed"], "job_id": job, "views": {}}
    for value in ("true", "false", "true"):
        sql("UPDATE system_config SET value = :v WHERE key = 'job_analysis.require_classification_acknowledgment'", {"v": value})
        s, view = requirements(job, "admin")
        out["views"][value + str(len(out["views"]))] = {
            "policy_flag": view["classification_policy"]["require_acknowledgment"],
            "can_proceed": view["readiness"]["can_proceed"], "state": view["readiness"]["state"],
            "reason_codes": sorted(r["code"] for r in view["readiness"]["reasons"]),
            "classification_warnings": len(view["classification_warnings"])}
    stored = sql("SELECT criteria_extraction_status AS st, requirements_revision AS rev FROM job_criteria WHERE job_id = :j", {"j": job})[0]
    out["stored"] = stored
    return out


if __name__ == "__main__":
    for fn in (_off, _en, _edit, _pref, _ar, _trunc, _retry, _transient, _legacy, _off_retry, _policy):
        fn()
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
