"""The gate routes over HTTP against a real PostgreSQL 16: who may read, record, override and clear; strict request bodies; a tenant
only ever reaches its own applications; and the audit rows the writes leave behind carry ids and states, never the reason text."""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from routers import gates as GR
from test_gates_postgres import (  # noqa: F401  (fixtures and seeding helpers)
    _answer, _app_row, _application, _doc, _job, _question, built, engines, world)

pytestmark = pytest.mark.asyncio

REASON = "CANARY-REASON-TEXT approved by the hiring manager"


def _client(engine, world, label, role):
    app = FastAPI()
    app.include_router(GR.router)
    user = CurrentUser(world[label]["users"][role], world[label]["tenant"], f"{role}@example.test", role, "U")
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _manual(built, world):
    job = _job(built, world["a"], _doc(("req_py", {"verification_method": "manual"})))
    return job, _application(built, world["a"], job)


async def test_every_tenant_role_reads_the_gate_summary(built, engines, world):
    job, app = _manual(built, world)
    for role in ("admin", "hr_manager", "recruiter", "viewer"):
        async with _client(engines(), world, "a", role) as client:
            response = await client.get(f"/applications/{app}/gates")
            assert response.status_code == 200, role
            assert response.json()["gates"][0]["gate_key"] == "req:req_py"


async def test_recording_a_result_then_overriding_it_leaves_the_history_and_an_audit_row_without_the_reason(built, engines, world):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "hr_manager") as client:
        recorded = await client.post(f"/applications/{app}/gates/req:req_py/result", json={"result": "failed", "summary": "No licence seen"})
        assert recorded.status_code == 200 and recorded.json()["eligibility_status"] == "not_eligible"
        overridden = await client.post(f"/applications/{app}/gates/req:req_py/override", json={"reason": REASON})
        assert overridden.status_code == 200
        body = overridden.json()
        assert body["eligibility_status"] == "eligible"
        [gate] = body["gates"]
        assert gate["result"] == "overridden" and gate["computed_result"] == "failed" and gate["override_reason"] == REASON
        assert [e["event_type"] for e in gate["history"]] == ["evaluated", "recorded", "overridden"] or \
               [e["event_type"] for e in gate["history"]][-2:] == ["recorded", "overridden"]
        override = gate["history"][-1]
        assert (override["original_result"], override["new_result"]) == ("failed", "overridden") and override["by"] and override["at"]
    cur = built.conn.cursor()
    cur.execute("SELECT action, details::text FROM audit_logs WHERE resource_id = %s ORDER BY created_at", (app,))
    rows = cur.fetchall()
    assert [r[0] for r in rows] == ["gate_result_recorded", "gate_result_overridden"]
    assert all("CANARY" not in r[1] and "No licence" not in r[1] for r in rows)


@pytest.mark.parametrize("role", ["recruiter", "viewer"])
async def test_a_recruiter_and_a_viewer_cannot_record_override_clear_or_evaluate(built, engines, world, role):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", role) as client:
        key = "req:req_py"
        assert (await client.post(f"/applications/{app}/gates/{key}/result", json={"result": "passed"})).status_code == 403
        assert (await client.post(f"/applications/{app}/gates/{key}/override", json={"reason": "waive"})).status_code == 403
        assert (await client.delete(f"/applications/{app}/gates/{key}/override")).status_code == 403
        assert (await client.post(f"/applications/{app}/gates/evaluate")).status_code == 403
    cur = built.conn.cursor()
    cur.execute("SELECT count(*) FROM application_gate_results WHERE application_id = %s", (app,))
    assert cur.fetchone()[0] == 0 and _app_row(built, app)[0] == "not_applicable"


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "x", "result": "passed"}, {"reason": 5}, {"reason": "x" * 1001}])
async def test_an_override_body_is_strict_and_needs_a_reason(built, engines, world, body):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "admin") as client:
        assert (await client.post(f"/applications/{app}/gates/req:req_py/override", json=body)).status_code == 422


@pytest.mark.parametrize("body", [{"result": "overridden"}, {"result": "maybe"}, {"result": "passed", "extra": 1}, {}])
async def test_a_recorded_result_is_one_of_the_three_recordable_values(built, engines, world, body):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "admin") as client:
        assert (await client.post(f"/applications/{app}/gates/req:req_py/result", json=body)).status_code == 422


async def test_an_unknown_gate_and_a_malformed_application_id_are_404(built, engines, world):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "admin") as client:
        assert (await client.post(f"/applications/{app}/gates/req:nope/override", json={"reason": "r"})).status_code == 404
        assert (await client.get("/applications/not-a-uuid/gates")).status_code == 404
        assert (await client.get(f"/applications/{uuid.uuid4()}/gates")).status_code == 404


async def test_another_tenant_gets_the_same_404_and_changes_nothing(built, engines, world):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "admin") as client:
        await client.post(f"/applications/{app}/gates/req:req_py/result", json={"result": "failed"})
    async with _client(engines(), world, "b", "admin") as foreign:
        assert (await foreign.get(f"/applications/{app}/gates")).status_code == 404
        assert (await foreign.post(f"/applications/{app}/gates/req:req_py/override", json={"reason": "mine now"})).status_code == 404
        assert (await foreign.post(f"/applications/{app}/gates/req:req_py/result", json={"result": "passed"})).status_code == 404
        assert (await foreign.delete(f"/applications/{app}/gates/req:req_py/override")).status_code == 404
    assert _app_row(built, app)[0] == "not_eligible"
    cur = built.conn.cursor()
    cur.execute("SELECT count(*) FROM application_gate_result_events WHERE event_type = 'overridden' AND application_id = %s", (app,))
    assert cur.fetchone()[0] == 0


async def test_the_override_can_be_withdrawn(built, engines, world):
    job, app = _manual(built, world)
    async with _client(engines(), world, "a", "admin") as client:
        await client.post(f"/applications/{app}/gates/req:req_py/result", json={"result": "failed"})
        await client.post(f"/applications/{app}/gates/req:req_py/override", json={"reason": "waived"})
        cleared = await client.delete(f"/applications/{app}/gates/req:req_py/override")
        assert cleared.status_code == 200 and cleared.json()["eligibility_status"] == "not_eligible"
        assert (await client.delete(f"/applications/{app}/gates/req:req_py/override")).status_code == 409


async def test_a_gate_result_can_not_be_recorded_for_a_candidate_question_gate(built, engines, world):
    job = _job(built, world["a"])
    _question(built, world["a"], job, gate=True)
    app = _application(built, world["a"], job)
    async with _client(engines(), world, "a", "admin") as client:
        view = (await client.get(f"/applications/{app}/gates")).json()
        key = view["gates"][0]["gate_key"]
        response = await client.post(f"/applications/{app}/gates/{key}/result", json={"result": "passed"})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "not_recordable"
