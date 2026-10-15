"""Queued, unstarted extraction is gated at worker execution, before any model call, on REAL PostgreSQL.

Covered: requirements-v2 initial attempts, re-analysis candidates, and legacy criteria extraction.
  * an attempt queued while entitled makes ZERO model calls while the tenant is suspended or expired, and stays exactly as it was
    (pending, same token, no failure, no usage row);
  * restoring access and resuming dispatches the work once per resume, and the claims make exactly one model call logically, even when
    several deliveries run (repeated resume, duplicate delivery);
  * a stale attempt (an older token, superseded by a retry request) is refused and makes no call;
  * resume is per tenant: another tenant's pending work is not dispatched.

The model is the harness's fake client (no network). Dispatch is recorded by a stub (no broker). Fixtures and the valid model output come from
tests/isolated_requirements_v2_extraction_postgres.py, which this module imports.
"""
import uuid

import pytest

import isolated_requirements_v2_extraction_postgres as H
from isolated_requirements_v2_extraction_postgres import (  # noqa: F401  (fixtures and helpers)
    CASES, FakeClient, T1, T2, U_ADMIN, U_OTHER, V22, _ctx, _fresh, audit, cluster, db, feature, activate_prompt, new_job, row,
    use_client, with_session, run_worker,
)
from services import requirements_v2_extraction as ext
from services.extraction_gate import resume_paused_extraction
from workers import criteria_worker as criteria
import workers.requirements_v2_extraction_worker as worker

CASE = "B01_en_hr_manager"
JD = CASES[CASE]["jd"]
TITLE = CASES[CASE]["title"]


def set_access(db, state):
    """The fixture tenant (T1) becomes entitled, suspended or expired. Expiry is the database clock's (now() - 1 second)."""
    if state == "entitled":
        db.q("UPDATE tenants SET subscription_status = 'active', subscription_ends_at = now() + interval '365 days' WHERE tenant_id = %s", (T1,))
    elif state == "suspended":
        db.q("UPDATE tenants SET subscription_status = 'suspended' WHERE tenant_id = %s", (T1,))
    elif state == "expired":
        db.q("UPDATE tenants SET subscription_status = 'active', subscription_ends_at = now() - interval '1 second' WHERE tenant_id = %s", (T1,))
    else:
        raise AssertionError(state)


@pytest.fixture
def dispatched(monkeypatch):
    """Records every dispatch the resume makes, instead of sending it to Celery."""
    rec = {"v2": [], "legacy": [], "candidates": []}
    monkeypatch.setattr(worker.extract_requirements_v2_task, "delay", lambda *a, **k: rec["v2"].append(a))
    monkeypatch.setattr(criteria.extract_criteria_task, "delay", lambda *a, **k: rec["legacy"].append(a))
    monkeypatch.setattr(worker.extract_candidate_task, "delay", lambda *a, **k: rec["candidates"].append(a))
    return rec


def resume(tenant=T1):
    return with_session(lambda s: resume_paused_extraction(s, tenant))


def job_state(db, job):
    r = row(db, job)
    return {"status": r["status"], "token": r["token"], "failed_audits": len(audit(db, "requirements_extraction_failed")),
            "usage_rows": len(H.usage(db, job))}


# ── requirements-v2 initial attempt ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_v2_attempt_queued_while_entitled_makes_no_call_while_suspended_or_expired_and_runs_once_after_restore(db, monkeypatch, dispatched):
    feature(db, True); activate_prompt(db)
    job, tok = new_job(db, JD, title=TITLE)                    # enqueued while entitled (the fixture tenant is active with a validity date)
    client = FakeClient([V22[CASE]["raw"]])
    use_client(monkeypatch, client)
    before = job_state(db, job)
    assert before == {"status": "pending", "token": tok, "failed_audits": 0, "usage_rows": 0}

    for state in ("suspended", "expired"):
        set_access(db, state)
        res = run_worker(job, tok, JD)
        assert res["outcome"] == "paused" and res["code"] == "access_paused", (state, res)
        assert client.calls == [], f"{state}: no model call may be made"
        assert job_state(db, job) == before, f"{state}: the attempt must stay exactly as it was (no claim, no failure, no usage)"

    set_access(db, "entitled")
    stale = str(uuid.uuid4())                                  # a token that is not the current attempt's
    res = run_worker(job, stale, JD)
    assert res["outcome"] == "stale" and client.calls == [], res
    assert job_state(db, job) == before

    counts = resume()
    assert counts == {"v2": 1, "legacy": 0, "candidates": 0}, counts
    assert len(dispatched["v2"]) == 1 and dispatched["v2"][0][:2] == (job, tok)
    assert dispatched["v2"][0][3]["title"] == TITLE

    # a repeated resume before the work runs dispatches it again; both deliveries run; exactly one model call results
    assert resume()["v2"] == 1
    run_worker(job, tok, JD)                                   # the first delivery claims and completes the attempt
    res = run_worker(job, tok, JD)                             # the duplicate finds it completed: refused, no call
    assert res["outcome"] == "stale", res
    assert len(client.calls) == 1, "exactly one logical model call"
    assert row(db, job)["status"] == "completed" and len(audit(db, "requirements_extraction_completed")) == 1

    # after completion, a further resume dispatches nothing
    assert resume() == {"v2": 0, "legacy": 0, "candidates": 0}


def test_a_superseded_attempt_is_refused_and_the_current_retry_runs_once(db, monkeypatch, dispatched):
    feature(db, True); activate_prompt(db)
    job, first = new_job(db, JD, title=TITLE)
    client = FakeClient([V22[CASE]["raw"]])
    use_client(monkeypatch, client)
    set_access(db, "suspended")
    assert run_worker(job, first, JD)["outcome"] == "paused"
    set_access(db, "entitled")

    async def retry(s):
        token = await ext.request_retry(s, job_id=job, actor_user=U_ADMIN, actor_tenant=T1)
        await s.commit()
        return token
    current = with_session(retry)
    assert current and current != first

    res = run_worker(job, first, JD)                            # the superseded attempt's late delivery
    assert res["outcome"] == "stale" and client.calls == [], res
    assert run_worker(job, current, JD)["outcome"] == "completed"
    assert len(client.calls) == 1 and row(db, job)["status"] == "completed"


# ── re-analysis candidate ─────────────────────────────────────────────────────────────────────────────────────────────────────
def requested_candidate(job, token):
    async def go(s):
        version_id = str(uuid.uuid4())
        await ext.append_log(s, job_id=job, tenant_id=T1, version_id=version_id, version_no=2, kind="candidate", event="requested",
                             request_key=str(uuid.uuid4()), attempt_token=token, actor_user_id=U_ADMIN, job_revision=0, details={})
        await s.commit()
        return version_id
    return with_session(go)


def test_a_candidate_queued_while_entitled_makes_no_call_while_suspended_and_runs_once_after_restore(db, monkeypatch, dispatched):
    feature(db, True); activate_prompt(db)
    db.q("UPDATE system_config SET value = 'true' WHERE key = %s", (ext.RECOVERY_KEY,))
    job, tok = new_job(db, JD, title=TITLE, status="completed")
    db.q("UPDATE job_criteria SET requirements_extraction_token = NULL WHERE job_id = %s", (job,))
    version_id = requested_candidate(job, tok)
    client = FakeClient([V22[CASE]["raw"]])
    use_client(monkeypatch, client)

    set_access(db, "suspended")
    res = worker.extract_candidate_task.apply(args=[job, version_id, tok, JD, H.JOB_META[job]]).result
    assert res["outcome"] == "paused" and client.calls == [], res
    events = {r for (r,) in db.q("SELECT event FROM requirements_analysis_log WHERE version_id = %s", (version_id,))}
    assert events == {"requested"}, "a paused candidate records no started event and no decision"

    set_access(db, "entitled")
    assert resume()["candidates"] == 1
    assert dispatched["candidates"][0][:3] == (job, version_id, tok)
    worker.extract_candidate_task.apply(args=[job, version_id, tok, JD, H.JOB_META[job]])
    res = worker.extract_candidate_task.apply(args=[job, version_id, tok, JD, H.JOB_META[job]]).result   # the duplicate delivery
    assert res["outcome"] == "stale", res
    assert len(client.calls) == 1, "one model call for the candidate"
    assert resume()["candidates"] == 0, "a candidate that has started or decided is not dispatched again"


# ── legacy criteria extraction ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_legacy_job_queued_while_entitled_is_left_pending_while_suspended_and_is_dispatched_once_on_resume(db, dispatched):
    job, _ = new_job(db, JD, title=TITLE, v2=False)
    db.q("UPDATE job_criteria SET criteria_extraction_status = 'pending' WHERE job_id = %s", (job,))
    set_access(db, "suspended")
    criteria.extract_criteria_task.apply(args=[job, JD, H.JOB_META[job]])
    assert row(db, job)["status"] == "pending", "a paused legacy job is not failed"
    assert len(audit(db, "requirements_extraction_failed")) == 0
    assert db.q("SELECT count(*) FROM ai_usage_log WHERE job_id = %s", (job,))[0][0] == 0

    set_access(db, "entitled")
    assert resume()["legacy"] == 1
    assert dispatched["legacy"][0][:2] == (job, JD)


def test_a_duplicate_legacy_delivery_does_not_claim_a_job_that_is_already_processing(db, monkeypatch):
    job, _ = new_job(db, JD, title=TITLE, v2=False)
    db.q("UPDATE job_criteria SET criteria_extraction_status = 'processing' WHERE job_id = %s", (job,))
    calls = []

    async def model(*a, **k):
        calls.append(a)
        raise AssertionError("must not run a second legacy extraction")
    import services.ai_service as ai
    monkeypatch.setattr(ai, "extract_job_criteria", model)
    criteria.extract_criteria_task.apply(args=[job, JD, H.JOB_META[job]])      # first delivery of a Celery retry-free run: not claimable
    assert calls == [] and row(db, job)["status"] == "processing"


# ── resume is per tenant ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_resume_dispatches_only_the_resumed_tenants_pending_work(db, dispatched):
    job1, _ = new_job(db, JD, title=TITLE)
    job2 = str(uuid.uuid4())
    db.q("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'Other tenant role','Other tenant text')",
         (job2, T2, U_OTHER))
    db.q("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_extraction_token) "
         "VALUES (%s,'pending',2,%s)", (job2, str(uuid.uuid4())))
    set_access(db, "entitled")
    assert resume(T1)["v2"] == 1
    assert [d[0] for d in dispatched["v2"]] == [job1]
