"""Requirements-v2 extraction, end to end at the worker and service level, on a REAL PostgreSQL 16 (disposable cluster, the project's real schema and
ALL migrations including the prepared 108). The Celery task `extract_requirements_v2_task` runs in eager mode with its real phases; only the MODEL
TRANSPORT is a fake: it replays recorded responses (the stored v2-2 benchmark answers, labelled as such) or raises provider errors of the real
openai classes. No model is called. Passing shows the wiring, persistence, transitions and authorisation work; it says nothing about model accuracy.

Covered: persistence (marker, requirements, pipeline record, immutable snapshot, weight columns, revision, audit, usage), EN/AR, Required/Preferred,
preferred-only, injection drafts stored as completed, malformed / truncated / empty output, transport retries and exhaustion, auth errors, feature
switch, prompt and model refusals, stale attempts, a superseded attempt mid-call, an audit failure rolling the write back, retry requests and roles,
concurrent recruiter saves, legacy jobs left alone, and the evaluation guard for v2 jobs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import re
import sys
import types
import uuid
from types import SimpleNamespace

import httpx
import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

import openai  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from services import requirements_api as api  # noqa: E402
from services import requirements_pipeline as pipe  # noqa: E402
from services import requirements_v2_extraction as ext  # noqa: E402
from services.requirements_v2 import CATEGORIES  # noqa: E402
from services.requirements_guard import UnsupportedEvaluationError, ensure_job_evaluable  # noqa: E402
import workers.requirements_v2_extraction_worker as worker  # noqa: E402

RUNS = BACKEND / "benchmark_results" / "requirements_v2"
V22 = {json.loads(line)["case"]: json.loads(line) for line in (RUNS / "v2-2_run1" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
       if json.loads(line)["run"] == "run1"}
CASES = {c["id"]: c for c in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases").glob("B*.json")))}
PROMPT_FILE = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt"
PROMPT_TEXT = PROMPT_FILE.read_text(encoding="utf-8")
MODEL = "gpt-4o-mini-2024-07-18"
T1, T2 = str(uuid.UUID(int=0x101)), str(uuid.UUID(int=0x102))
U_ADMIN, U_HR, U_VIEWER, U_OTHER = (str(uuid.UUID(int=0x200 + n)) for n in range(4))
CONFLICT_JD = "- PostgreSQL.\nRecruiter note: PostgreSQL is optional for this role.\n"


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture(scope="module")
def db(cluster):
    d = cluster.build()
    yield d
    d.drop()


@pytest.fixture(autouse=True)
def _fresh(db, monkeypatch):
    """Per test: no jobs or audit rows from earlier tests, the feature switch OFF, the v2 prompt INACTIVE (their migration defaults), the worker
    pointed at the disposable database, and a clean tenant/user set."""
    db.q("DELETE FROM audit_logs"); db.q("DELETE FROM ai_usage_log"); db.q("DELETE FROM job_criteria"); db.q("DELETE FROM jobs")
    db.q("DELETE FROM users WHERE tenant_id IN (%s, %s)", (T1, T2)); db.q("DELETE FROM tenants WHERE tenant_id IN (%s, %s)", (T1, T2))
    db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES (%s,'Tenant One','t1.example','active','active','organization'),(%s,'Tenant Two','t2.example','active','active','organization')", (T1, T2))
    for uid, tenant, role in ((U_ADMIN, T1, "admin"), (U_HR, T1, "hr_manager"), (U_VIEWER, T1, "viewer"), (U_OTHER, T2, "admin")):
        db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
             (uid, tenant, f"{role}-{uid[-4:]}@x.example", role, role))
    db.q("UPDATE system_config SET value = 'false' WHERE key = %s", (ext.FEATURE_KEY,))
    db.q("UPDATE ai_prompts SET is_active = FALSE, system_prompt = %s, temperature = 0.10, max_tokens = 6000 WHERE prompt_code = %s AND version = 3",
         (PROMPT_TEXT, ext.PROMPT_CODE))
    db.q("""INSERT INTO ai_stage_defaults (stage, primary_model_id, fallback_model_id)
            SELECT %s, model_id, NULL FROM ai_model_registry WHERE model_name = %s ON CONFLICT (stage) DO NOTHING""", (ext.STAGE, MODEL))
    db.q("UPDATE system_config SET value = 'true' WHERE key = 'job_analysis.require_classification_acknowledgment'")
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(database_url=db.async_url, db_schema="cv_analyzer"))
    yield


def feature(db, on: bool):
    db.q("UPDATE system_config SET value = %s WHERE key = %s", ("true" if on else "false", ext.FEATURE_KEY))


def activate_prompt(db, version=3):
    db.q("UPDATE ai_prompts SET is_active = FALSE WHERE prompt_code = %s", (ext.PROMPT_CODE,))
    db.q("UPDATE ai_prompts SET is_active = TRUE WHERE prompt_code = %s AND version = %s", (ext.PROMPT_CODE, version))


def new_job(db, description: str, *, v2=True, title="Synthetic role", original=None, status="pending") -> tuple[str, str]:
    job_id, token = str(uuid.uuid4()), str(uuid.uuid4())
    db.q("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,%s,%s)", (job_id, T1, U_ADMIN, title, description))
    if v2:
        db.q("""INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_extraction_token)
                VALUES (%s, %s, 2, %s)""", (job_id, status, token))
    else:
        db.q("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills) VALUES (%s, 'completed', ARRAY['Excel'])", (job_id,))
    return job_id, token


def _sha(text_):
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


def row(db, job_id):
    (r,) = db.q("""SELECT analysis_json, original_analysis_json, criteria_extraction_status, criteria_extraction_error, requirements_extraction_token::text,
                          requirements_revision, requirements_schema_version, weight_skills, weight_experience, weight_education, weight_certifications,
                          weight_soft_skills, weight_domain_knowledge, weight_other, ai_model
                   FROM job_criteria WHERE job_id = %s""", (job_id,))
    keys = ("analysis", "original", "status", "error", "token", "revision", "marker", "w_skills", "w_experience", "w_education", "w_certifications",
            "w_soft", "w_domain", "w_other", "ai_model")
    out = dict(zip(keys, r))
    out["weights"] = {c: out[k] for c, k in zip(CATEGORIES, ("w_skills", "w_experience", "w_education", "w_certifications", "w_soft", "w_domain", "w_other"))}
    return out


def audit(db, action=None):
    rows = db.q("SELECT action, user_id::text, resource_id, details FROM audit_logs " + ("WHERE action = %s " if action else "") + "ORDER BY created_at, log_id",
                (action,) if action else None)
    return [{"action": a, "user_id": u, "resource_id": r, "details": d} for a, u, r, d in rows]


def usage(db, job_id):
    return db.q("SELECT stage, model, prompt_key, request_status, retry_count, error_type, metadata FROM ai_usage_log WHERE job_id = %s ORDER BY created_at", (job_id,))


# ── the fake transport: replays recorded responses / raises real provider exceptions ─────────────────────────────────────────────────
def _resp(content, finish="stop", model=MODEL):
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=6400, completion_tokens=900))


def _req():
    return httpx.Request("POST", "https://api.openai.example/v1/chat/completions")


def err(kind: str):
    if kind == "rate_limit":
        return openai.RateLimitError("rl", response=httpx.Response(429, request=_req()), body=None)
    if kind == "timeout":
        return openai.APITimeoutError(request=_req())
    if kind == "connection":
        return openai.APIConnectionError(request=_req())
    if kind == "auth":
        return openai.AuthenticationError("bad key sk-SECRETSECRETSECRETSECRET", response=httpx.Response(401, request=_req()), body=None)
    if kind == "server":
        return openai.InternalServerError("boom", response=httpx.Response(503, request=_req()), body=None)
    raise AssertionError(kind)


class FakeClient:
    """Stands in for the registry's AsyncOpenAI client. `script` items are a response (str = content of a 'stop' answer, or a _resp), an exception
    instance, or a callable run before answering (used to change the database mid-call)."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_kw):
        return self

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if callable(step) and not isinstance(step, (str, BaseException)):
            step = step()
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, str):
            return _resp(step)
        return step


def use_client(monkeypatch, client, *, model=MODEL):
    async def fake_resolve(db):
        return SimpleNamespace(model_name=model, client=client)
    monkeypatch.setattr(ext, "resolve_model", fake_resolve)


def run_worker(job_id, token, description, meta=None):
    """Eager Celery execution of the real task (its retries included)."""
    res = worker.extract_requirements_v2_task.apply(args=[job_id, token, description, meta])
    assert not res.failed(), res.traceback
    return res.result


def run(coro):
    return asyncio.run(coro)


def session_factory():
    eng = create_async_engine(f"postgresql+asyncpg://postgres@127.0.0.1:{_cluster_port()}/{_db_name()}", poolclass=NullPool,
                              connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


_CTX = {}


def _cluster_port():
    return _CTX["db"].cluster.port


def _db_name():
    return _CTX["db"].name


@pytest.fixture(autouse=True)
def _ctx(db):
    _CTX["db"] = db
    yield


def user(uid, tenant, role):
    return SimpleNamespace(user_id=uid, tenant_id=tenant, role=role, email=f"{role}@x.example", full_name=role)


def with_session(fn):
    """Run an async service call in its own loop and engine against the disposable database."""
    async def inner():
        eng, S = session_factory()
        try:
            async with S() as db:
                return await fn(db)
        finally:
            await eng.dispose()
    return run(inner())


# ══ 1. the feature switch, the prompt and the model are checked before any call ═══════════════════════════════════════════════════════
def test_switch_off_fails_the_attempt_without_any_model_call(db, monkeypatch):
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    client = FakeClient([])
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res["outcome"] == "failed" and res["code"] == "feature_disabled" and client.calls == []
    r = row(db, job)
    assert r["status"] == "failed" and r["analysis"] is None and r["original"] is None and r["token"] is None
    assert audit(db, "requirements_extraction_failed")[0]["details"]["code"] == "feature_disabled"


def test_an_inactive_prompt_is_refused_before_any_call(db, monkeypatch):
    feature(db, True)                                       # the prompt row stays inactive (migration default)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    client = FakeClient([])
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "failed", "code": "prompt_not_active"} and client.calls == []


EDITED = PROMPT_TEXT + "\n"                 # an administrator's edit: a different text, a different hash, the same rules for the output


def _sha(text_):
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


def test_an_edited_prompt_text_is_used_and_its_new_hash_is_recorded(db, monkeypatch):
    feature(db, True)
    db.q("UPDATE ai_prompts SET system_prompt = %s WHERE prompt_code = %s AND version = 3", (EDITED, ext.PROMPT_CODE))
    activate_prompt(db)
    c = CASES["B01_en_hr_manager"]
    job, tok = new_job(db, c["jd"])
    client = FakeClient([V22["B01_en_hr_manager"]["raw"]])
    use_client(monkeypatch, client)
    assert run_worker(job, tok, c["jd"])["outcome"] == "completed"
    assert client.calls[0]["messages"][0]["content"] == EDITED                  # the text actually sent is the edited one
    (done,) = audit(db, "requirements_extraction_completed")
    assert done["details"]["prompt_sha256"] == _sha(EDITED) and done["details"]["prompt_version"] == 3
    assert usage(db, job)[0][6]["prompt_sha256"] == _sha(EDITED)


def test_an_admin_added_version_is_used_with_its_own_version_text_and_settings(db, monkeypatch):
    feature(db, True)
    cols = [c for (c,) in db.q("""SELECT column_name FROM information_schema.columns WHERE table_schema = 'cv_analyzer' AND table_name = 'ai_prompts'
                                  AND column_name NOT IN ('prompt_id', 'version', 'is_active', 'created_at', 'updated_at')""")]
    copied = ", ".join(cols)
    try:
        db.q(f"""INSERT INTO ai_prompts (version, is_active, {copied})
                 SELECT 4, FALSE, {copied} FROM ai_prompts WHERE prompt_code = %s AND version = 3""", (ext.PROMPT_CODE,))
        db.q("UPDATE ai_prompts SET system_prompt = %s, temperature = 0.30, max_tokens = 5000 WHERE prompt_code = %s AND version = 4",
             (EDITED, ext.PROMPT_CODE))
        activate_prompt(db, version=4)
        c = CASES["B01_en_hr_manager"]
        job, tok = new_job(db, c["jd"])
        client = FakeClient([V22["B01_en_hr_manager"]["raw"]])
        use_client(monkeypatch, client)
        assert run_worker(job, tok, c["jd"])["outcome"] == "completed"
        sent = client.calls[0]
        assert sent["temperature"] == pytest.approx(0.3) and sent["max_tokens"] == 5000
        (done,) = audit(db, "requirements_extraction_completed")
        d = done["details"]
        assert d["prompt_version"] == 4 and d["prompt_label"] == "criteria_extraction_v2-4" and d["prompt_sha256"] == _sha(EDITED)
        assert d["settings"] == {"temperature": pytest.approx(0.3), "max_tokens": 5000, "response_format": {"type": "json_object"}, "timeout_s": 90}
        assert usage(db, job)[0][6]["prompt_version"] == 4
    finally:
        db.q("DELETE FROM ai_prompts WHERE prompt_code = %s AND version = 4", (ext.PROMPT_CODE,))   # the shared database keeps only v3


def test_the_database_itself_refuses_a_temperature_outside_zero_to_two(db):
    # the database's own CHECK is the first guard for temperature; the worker re-checks the stored value for max_tokens and the text
    with pytest.raises(Exception, match="temperature"):
        db.q("UPDATE ai_prompts SET temperature = 3.0 WHERE prompt_code = %s AND version = 3", (ext.PROMPT_CODE,))


@pytest.mark.parametrize("column,value,code", [
    ("max_tokens", 0, "prompt_settings_invalid"),         # the prompt admin API allows 1..32000
    ("max_tokens", 40000, "prompt_settings_invalid"),
    ("system_prompt", "   ", "prompt_empty"),
])
def test_out_of_range_or_empty_configuration_is_refused_before_any_call(db, monkeypatch, column, value, code):
    feature(db, True)
    db.q(f"UPDATE ai_prompts SET {column} = %s WHERE prompt_code = %s AND version = 3", (value, ext.PROMPT_CODE))
    activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    client = FakeClient([])
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "failed", "code": code} and client.calls == []
    assert row(db, job)["status"] == "failed"


def test_no_stage_model_fails_closed_with_the_real_registry_lookup(db, monkeypatch):
    feature(db, True)
    activate_prompt(db)
    db.q("DELETE FROM ai_stage_defaults WHERE stage = %s", (ext.STAGE,))
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    # the REAL resolve_model (no fake is installed here): no stage row -> refused, with no fallback to a legacy model
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "failed", "code": "model_not_configured"}, res


# ══ 2. a successful attempt persists everything, atomically ═══════════════════════════════════════════════════════════════════════════
def _weights(doc):
    return {c: doc["categories"][c]["weight"] for c in CATEGORIES}


@pytest.mark.parametrize("case", ["B01_en_hr_manager", "B07_ar_accountant"])
def test_a_required_and_preferred_job_is_persisted_completely(db, monkeypatch, case):
    feature(db, True); activate_prompt(db)
    c = CASES[case]
    job, tok = new_job(db, c["jd"], title=c["title"])
    client = FakeClient([V22[case]["raw"]])
    use_client(monkeypatch, client)
    meta = {"title": c["title"], "location": c.get("job_metadata", {}).get("location")}
    res = run_worker(job, tok, c["jd"], meta)
    assert res == {"outcome": "completed", "code": None}, res
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == MODEL and call["temperature"] == 0.1 and call["max_tokens"] == 6000 and call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["content"] == PROMPT_TEXT                                         # the configured text, exactly
    assert call["messages"][1]["content"].count(c["jd"]) == 1                                    # the JD verbatim, once
    r = row(db, job)
    assert r["status"] == "completed" and r["marker"] == 2 and r["revision"] == 1 and r["token"] is None and r["error"] is None
    analysis = r["analysis"]
    assert set(analysis) == {"requirements", pipe.STORAGE_KEY}
    doc = analysis["requirements"]
    assert doc["schema_version"] == 2 and r["weights"] == _weights(doc)                           # the weight columns are synchronised
    assert sum(r["weights"].values()) in (0, 100)
    assert pipe.verify_record(analysis[pipe.STORAGE_KEY], r["original"]["requirements"]) == []     # the record matches the immutable snapshot
    assert analysis[pipe.STORAGE_KEY]["provenance"] == {"extraction_prompt": {"version": "criteria_extraction_v2-3", "sha256": _sha(PROMPT_TEXT)},
                                                       "model": MODEL}
    assert analysis[pipe.STORAGE_KEY]["raw_response"]["text"] == V22[case]["raw"]                # the raw AI text is kept
    assert r["ai_model"] == MODEL
    (done,) = audit(db, "requirements_extraction_completed")
    d = done["details"]
    assert d["prompt_version"] == 3 and d["prompt_code"] == ext.PROMPT_CODE and d["requested_model"] == MODEL and d["returned_model"] == MODEL
    assert d["prompt_sha256"] == _sha(PROMPT_TEXT) and d["acknowledgment_policy"] is True          # the live policy of this attempt
    assert d["settings"] == {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}, "timeout_s": 90}
    assert d["revision"] == 1 and d["weights"] == r["weights"]
    (u,) = usage(db, job)
    assert u[0] == ext.STAGE and u[1] == MODEL and u[2] == ext.PROMPT_CODE and u[3] == "success"
    assert u[6]["prompt_sha256"] == _sha(PROMPT_TEXT) and u[6]["requested_model"] == MODEL
    assert u[6]["settings"] == {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}, "timeout_s": 90}


def test_an_injection_draft_is_stored_as_completed_and_its_blockers_are_not_extraction_failures(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    c = CASES["B06_en_injection"]
    job, tok = new_job(db, c["jd"])
    use_client(monkeypatch, FakeClient([V22["B06_en_injection"]["raw"]]))
    assert run_worker(job, tok, c["jd"]) == {"outcome": "completed", "code": None}
    r = row(db, job)
    assert r["status"] == "completed"
    rec = r["analysis"][pipe.STORAGE_KEY]
    assert any(i["status"] == "open" for i in rec["review_records"]["injection"]["issues"])          # the guard's findings are kept, open
    assert audit(db, "requirements_extraction_failed") == []
    # the readiness that the editor will show is the server's, derived, and it is blocked
    view = with_session(lambda db2: api.get_requirements(db2, user(U_HR, T1, "hr_manager"), job))
    assert view["readiness"]["state"] == "needs_injection_review" and view["readiness"]["can_proceed"] is False


def test_a_preferred_only_job_is_stored_with_zero_weights_and_needs_confirmation(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    c = CASES["B10_ar_preferred_only"]
    job, tok = new_job(db, c["jd"])
    use_client(monkeypatch, FakeClient([V22["B10_ar_preferred_only"]["raw"]]))
    assert run_worker(job, tok, c["jd"])["outcome"] == "completed"
    r = row(db, job)
    assert all(v == 0 for v in r["weights"].values())
    view = with_session(lambda db2: api.get_requirements(db2, user(U_ADMIN, T1, "admin"), job))
    assert view["readiness"]["state"] == "needs_confirmation" and view["readiness"]["scoring_mode"] is None


def test_an_open_job_with_no_items_is_stored_as_a_draft_needing_items(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    c = CASES["B05_en_open_empty"]
    job, tok = new_job(db, c["jd"])
    use_client(monkeypatch, FakeClient([V22["B05_en_open_empty"]["raw"]]))
    assert run_worker(job, tok, c["jd"])["outcome"] == "completed"
    view = with_session(lambda db2: api.get_requirements(db2, user(U_ADMIN, T1, "admin"), job))
    assert view["readiness"]["state"] == "needs_items" and view["revision"] == 1


def test_the_stored_provenance_names_the_approved_label(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    rec = row(db, job)["analysis"][pipe.STORAGE_KEY]
    assert rec["provenance"]["extraction_prompt"]["version"] == "criteria_extraction_v2-3"


# ══ 3. malformed, truncated and empty output; provider failures; retries ═══════════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,finish,code", [
    ("this is not json", "stop", "invalid_json"),
    ('{"categories": {', "stop", "invalid_json"),
    (V22["B01_en_hr_manager"]["raw"], "length", "output_truncated"),
    ("", "stop", "no_response"),
], ids=["not_json", "truncated_json", "finish_length", "empty"])
def test_malformed_or_truncated_output_fails_the_attempt_without_a_retry(db, monkeypatch, raw, finish, code):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    client = FakeClient([_resp(raw, finish)])
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "failed", "code": code}, res
    assert len(client.calls) == 1                                                       # never retried
    r = row(db, job)
    assert r["status"] == "failed" and r["analysis"] is None and r["original"] is None and r["revision"] == 0 and r["marker"] == 2
    assert code in r["error"] or r["error"]
    (f,) = audit(db, "requirements_extraction_failed")
    assert f["details"]["code"] == code and f["details"]["finish_reason"] in (finish, None)
    assert usage(db, job)[0][3] == "success"                                             # the call itself succeeded; its output did not


@pytest.mark.parametrize("kinds,expected_calls,outcome,usage_statuses", [
    (["rate_limit", "rate_limit", None], 3, "completed", ["failed", "failed", "success"]),
    (["timeout", "connection", "server"], 3, "failed", ["failed", "failed", "failed"]),        # retryable failures on attempts 1..3, then exhausted
    (["rate_limit", "rate_limit", "rate_limit"], 3, "failed", ["failed", "failed", "failed"]),
], ids=["recovers_after_two", "exhausted_mixed", "exhausted_rate_limit"])
def test_transient_errors_are_retried_with_the_same_token_and_then_settle(db, monkeypatch, kinds, expected_calls, outcome, usage_statuses):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    script = [(err(k) if k else V22["B01_en_hr_manager"]["raw"]) for k in kinds]
    client = FakeClient(script)
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res["outcome"] == outcome, res
    assert len(client.calls) == expected_calls
    rows = usage(db, job)
    assert [u[3] for u in rows] == usage_statuses, rows
    assert [u[4] for u in rows] == list(range(len(rows)))                                # retry_count per attempt
    if outcome == "completed":
        r = row(db, job)
        assert r["status"] == "completed" and r["revision"] == 1 and len(audit(db, "requirements_extraction_completed")) == 1
    else:
        r = row(db, job)
        assert r["status"] == "failed" and r["analysis"] is None and r["original"] is None and r["token"] is None


@pytest.mark.parametrize("kind", ["auth"])
def test_a_provider_auth_error_is_terminal_and_the_error_text_is_not_stored(db, monkeypatch, kind):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    client = FakeClient([err("auth")])
    use_client(monkeypatch, client)
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "failed", "code": "auth"} and len(client.calls) == 1
    r = row(db, job)
    assert "SECRET" not in json.dumps(r, default=str) and "SECRET" not in json.dumps(audit(db), default=str)


# ══ 4. stale attempts, superseded results and the snapshot never overwritten ═══════════════════════════════════════════════════════════
def test_a_result_for_a_superseded_attempt_is_discarded_and_changes_nothing(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])

    def supersede():                                       # a retry request arrives while the model is answering
        db.q("UPDATE job_criteria SET requirements_extraction_token = %s, criteria_extraction_status = 'pending' WHERE job_id = %s", (str(uuid.uuid4()), job))
        return V22["B01_en_hr_manager"]["raw"]
    use_client(monkeypatch, FakeClient([supersede]))
    res = run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    assert res == {"outcome": "stale", "code": None}, res
    r = row(db, job)
    assert r["analysis"] is None and r["original"] is None and r["revision"] == 0 and r["status"] == "pending"
    stale = audit(db, "requirements_extraction_stale_discarded")
    assert stale and stale[0]["details"]["reason"] == "superseded_attempt"


def test_a_stale_claim_from_an_old_worker_is_reclaimed_only_after_the_window(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, old = new_job(db, CASES["B01_en_hr_manager"]["jd"], status="processing")
    db.q("UPDATE job_criteria SET requirements_extraction_started_at = now() - interval '5 minutes' WHERE job_id = %s", (job,))
    use_client(monkeypatch, FakeClient([]))
    assert run_worker(job, str(uuid.uuid4()), CASES["B01_en_hr_manager"]["jd"]) == {"outcome": "stale", "code": "not_claimable"}   # 5 min: still owned
    db.q("UPDATE job_criteria SET requirements_extraction_started_at = now() - interval '45 minutes' WHERE job_id = %s", (job,))
    client = FakeClient([V22["B01_en_hr_manager"]["raw"]])
    use_client(monkeypatch, client)
    assert run_worker(job, str(uuid.uuid4()), CASES["B01_en_hr_manager"]["jd"])["outcome"] == "completed"   # 45 min: the crashed claim is taken over


def test_a_job_that_already_has_a_snapshot_is_never_extracted_again(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    db.q("UPDATE job_criteria SET original_analysis_json = CAST(%s AS jsonb) WHERE job_id = %s", (json.dumps({"requirements": {"schema_version": 2}}), job))
    client = FakeClient([])
    use_client(monkeypatch, client)
    assert run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"]) == {"outcome": "stale", "code": "not_claimable"} and client.calls == []
    assert row(db, job)["original"] == {"requirements": {"schema_version": 2}}


def test_a_completed_job_rejects_a_second_attempt_and_keeps_the_recruiters_edit(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    before = row(db, job)
    client = FakeClient([V22["B07_ar_accountant"]["raw"]])
    use_client(monkeypatch, client)
    assert run_worker(job, str(uuid.uuid4()), CASES["B01_en_hr_manager"]["jd"]) == {"outcome": "stale", "code": "not_claimable"}
    assert client.calls == [] and row(db, job) == before


def test_an_audit_failure_rolls_the_whole_write_back_and_leaves_the_attempt_owned(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    db.q("CREATE OR REPLACE FUNCTION fail_audit_v2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit down'; END $$")
    db.q("CREATE TRIGGER fail_audit_v2 BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION fail_audit_v2()")
    try:
        use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
        before = row(db, job)
        res = worker.extract_requirements_v2_task.apply(args=[job, tok, CASES["B01_en_hr_manager"]["jd"], None])
        assert res.failed() and "audit down" in res.traceback
        r = row(db, job)
        assert r["analysis"] is None and r["original"] is None and r["weights"] == before["weights"] and r["revision"] == 0
        assert r["status"] == "processing" and r["token"] == tok                       # nothing written; the claim is still held (stale after 30 min)
    finally:
        db.q("DROP TRIGGER fail_audit_v2 ON audit_logs")
    assert audit(db) == []


def test_a_weight_total_that_is_not_normalised_is_refused_and_rolled_back(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    monkeypatch.setattr(ext, "weights_ok", lambda doc: False)                           # the guard that protects the weight columns
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    assert run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"]) == {"outcome": "failed", "code": "weights_not_normalized"}
    r = row(db, job)
    assert r["analysis"] is None and r["original"] is None and r["status"] == "failed"


# ══ 5. retry requests, roles, concurrent recruiters, and the editor's API view ═════════════════════════════════════════════════════════
def test_a_pending_or_failed_job_is_read_as_pending_and_refuses_edits(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    view = with_session(lambda s: api.get_requirements(s, user(U_HR, T1, "hr_manager"), job))
    assert view["readiness"]["state"] == "extraction_pending" and view["extraction"]["status"] == "pending" and view["original"] is None
    assert view["readiness"]["guarded"] is False and view["can_edit"] is False
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.save_requirements(s, user(U_HR, T1, "hr_manager"), job, 0, {"schema_version": 2, "categories": {}}))
    assert e.value.code == "requirements_extraction_not_ready" and e.value.http_status == 409
    assert row(db, job)["analysis"] is None


def test_a_failed_job_offers_a_retry_to_editors_only_and_the_retry_runs_the_worker(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    use_client(monkeypatch, FakeClient([_resp("not json")]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    view = with_session(lambda s: api.get_requirements(s, user(U_HR, T1, "hr_manager"), job))
    assert view["extraction"]["status"] == "failed" and view["extraction"]["retry_available"] is True and view["readiness"]["state"] == "extraction_failed"
    viewer_view = with_session(lambda s: api.get_requirements(s, user(U_VIEWER, T1, "viewer"), job))
    assert viewer_view["extraction"]["retry_available"] is False
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.request_extraction_retry(s, user(U_VIEWER, T1, "viewer"), job))
    assert e.value.http_status == 403
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.request_extraction_retry(s, user(U_OTHER, T2, "admin"), job))
    assert e.value.http_status == 404

    queued = []
    monkeypatch.setattr(worker.extract_requirements_v2_task, "delay", lambda *a, **k: queued.append(a))
    out = with_session(lambda s: api.request_extraction_retry(s, user(U_HR, T1, "hr_manager"), job))
    assert out["extraction"]["status"] == "pending" and len(queued) == 1
    new_token = queued[0][1]
    assert new_token != tok and row(db, job)["token"] == new_token and row(db, job)["status"] == "pending"
    assert audit(db, "requirements_extraction_retry_requested")[0]["details"]["new_attempt_token"] == new_token
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    assert run_worker(job, new_token, CASES["B01_en_hr_manager"]["jd"])["outcome"] == "completed"


def test_a_retry_is_refused_while_an_attempt_runs_or_after_completion(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"], status="processing")
    db.q("UPDATE job_criteria SET requirements_extraction_started_at = now() WHERE job_id = %s", (job,))
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.request_extraction_retry(s, user(U_ADMIN, T1, "admin"), job))
    assert e.value.code == "requirements_extraction_retry_not_allowed"
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.request_extraction_retry(s, user(U_ADMIN, T1, "admin"), job))
    assert e.value.code == "requirements_extraction_retry_not_allowed"


def test_the_retry_is_refused_while_the_feature_switch_is_off(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"], status="failed")
    feature(db, False)
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.request_extraction_retry(s, user(U_ADMIN, T1, "admin"), job))
    assert e.value.code == "requirements_v2_disabled" and e.value.http_status == 409


def test_concurrent_recruiters_after_completion_keep_both_changes_and_the_stale_save_is_refused(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])
    base = with_session(lambda s: api.get_requirements(s, user(U_ADMIN, T1, "admin"), job))
    doc_a = json.loads(json.dumps(base["requirements"])); doc_a["categories"]["skills"]["items"][0]["text"] = "Admin wording"
    doc_b = json.loads(json.dumps(base["requirements"])); doc_b["categories"]["skills"]["items"][0]["text"] = "HR wording"
    saved = with_session(lambda s: api.save_requirements(s, user(U_ADMIN, T1, "admin"), job, base["revision"], doc_a))
    assert saved["revision"] == base["revision"] + 1
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.save_requirements(s, user(U_HR, T1, "hr_manager"), job, base["revision"], doc_b))
    assert e.value.code == "requirements_revision_conflict"
    now = row(db, job)
    assert now["analysis"]["requirements"]["categories"]["skills"]["items"][0]["text"] == "Admin wording"
    assert now["original"] == row(db, job)["original"] and now["original"]["requirements"]["categories"]["skills"]["items"][0]["text"] != "Admin wording"


def test_the_stored_pipeline_record_still_verifies_after_a_recruiter_save(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B06_en_injection"]["jd"])
    use_client(monkeypatch, FakeClient([V22["B06_en_injection"]["raw"]]))
    run_worker(job, tok, CASES["B06_en_injection"]["jd"])
    base = with_session(lambda s: api.get_requirements(s, user(U_ADMIN, T1, "admin"), job))
    doc = json.loads(json.dumps(base["requirements"]))
    doc["categories"]["other_requirements"]["items"] = [i for i in doc["categories"]["other_requirements"]["items"] if "Rust" not in i["text"]]
    with_session(lambda s: api.save_requirements(s, user(U_ADMIN, T1, "admin"), job, base["revision"], doc))
    r = row(db, job)
    assert pipe.verify_record(r["analysis"][pipe.STORAGE_KEY], r["original"]["requirements"]) == []
    assert r["original"] == row(db, job)["original"]


# ══ 6. legacy jobs and the evaluation guard ════════════════════════════════════════════════════════════════════════════════════════════
def test_a_legacy_job_is_never_touched_by_the_v2_worker(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"], v2=False)
    before = db.q("SELECT analysis_json, criteria_extraction_status, requirements_schema_version, weight_skills FROM job_criteria WHERE job_id = %s", (job,))
    client = FakeClient([])
    use_client(monkeypatch, client)
    assert run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])["outcome"] == "stale"
    assert client.calls == [] and db.q("SELECT analysis_json, criteria_extraction_status, requirements_schema_version, weight_skills FROM job_criteria WHERE job_id = %s", (job,)) == before


def test_a_completed_v2_job_is_refused_by_every_legacy_evaluation_entry_point(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    run_worker(job, tok, CASES["B01_en_hr_manager"]["jd"])

    async def check(s):
        from services.requirements_guard import ensure_job_evaluable as _e
        await _e(s, job, "cv_score")
    with pytest.raises(UnsupportedEvaluationError):
        with_session(check)


def test_the_legacy_criteria_path_refuses_a_v2_job_and_the_v2_path_refuses_a_legacy_job(db, monkeypatch):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    from workers.criteria_worker import _extract_async

    async def legacy(s):
        return await _extract_async(job, CASES["B01_en_hr_manager"]["jd"], s, None)

    async def run_legacy():
        eng, S = session_factory()
        try:
            await legacy(S)
        finally:
            await eng.dispose()
    monkeypatch.setattr("services.ai_service.extract_job_criteria", lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy model call")))
    run(run_legacy())
    assert row(db, job)["status"] == "failed" and "requirements-v2" in (row(db, job)["error"] or "")


def test_the_editor_view_of_a_legacy_job_is_still_refused_by_the_requirements_api(db):
    job, _ = new_job(db, CASES["B01_en_hr_manager"]["jd"], v2=False)
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.get_requirements(s, user(U_ADMIN, T1, "admin"), job))
    assert e.value.code == "not_requirements_v2" and e.value.http_status == 409


def test_authorisation_on_create_and_read_is_the_tenant_and_role_of_the_job(db):
    job, _ = new_job(db, CASES["B01_en_hr_manager"]["jd"])
    with pytest.raises(api.ApiError) as e:
        with_session(lambda s: api.get_requirements(s, user(U_OTHER, T2, "admin"), job))
    assert e.value.http_status == 404


# ══ 7. the stage's own helpers ═════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_feature_switch_reads_only_the_exact_value_true(db):
    for value, expected in (("true", True), (" TRUE ", True), ("false", False), ("yes", False), ("1", False), ("", False)):
        db.q("UPDATE system_config SET value = %s WHERE key = %s", (value, ext.FEATURE_KEY))
        assert with_session(lambda s: ext.feature_enabled(s)) is expected, value
    db.q("DELETE FROM system_config WHERE key = %s", (ext.FEATURE_KEY,))
    assert with_session(lambda s: ext.feature_enabled(s)) is False
    db.q("INSERT INTO system_config (key, value) VALUES (%s, 'false')", (ext.FEATURE_KEY,))


def test_the_prompt_loader_returns_the_approved_reference_only_when_active(db):
    with pytest.raises(ext.ExtractionUnavailable) as e:
        with_session(lambda s: ext.load_prompt(s))
    assert e.value.code == "prompt_not_active"
    activate_prompt(db)
    ref = with_session(lambda s: ext.load_prompt(s))
    assert (ref.label, ref.version, ref.sha256) == ("criteria_extraction_v2-3", 3, hashlib.sha256(PROMPT_TEXT.encode()).hexdigest())
    assert ref.system_prompt == PROMPT_TEXT


def test_the_audit_snapshot_records_the_live_policy_it_ran_under(db, monkeypatch):
    feature(db, True)
    activate_prompt(db)
    db.q("UPDATE system_config SET value = 'false' WHERE key = 'job_analysis.require_classification_acknowledgment'")
    c = CASES["B01_en_hr_manager"]
    job, tok = new_job(db, c["jd"])
    use_client(monkeypatch, FakeClient([V22["B01_en_hr_manager"]["raw"]]))
    assert run_worker(job, tok, c["jd"])["outcome"] == "completed"
    (done,) = audit(db, "requirements_extraction_completed")
    assert done["details"]["acknowledgment_policy"] is False
    db.q("UPDATE system_config SET value = 'true' WHERE key = 'job_analysis.require_classification_acknowledgment'")
