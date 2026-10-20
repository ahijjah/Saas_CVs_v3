"""Authenticated requirements-v2 API on a real PostgreSQL 16: the real routes, a real async session per request, the disposable
chain. Only the authenticated user and the session factory are overridden. Login roles are used wherever row-level security must
apply, and one role has no INSERT privilege on audit_logs to prove the atomic rollback."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import httpx
import psycopg2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from runtime_engines import async_engine
from runtime_engines import sync_connection
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import job_requirements as R
from services.requirements_v2 import contract as C

pytestmark = pytest.mark.asyncio

JD = "We need Python and SQL. Five years of backend work is preferred."
CANARY = "CANARY"
WRITER = "cv_app"           # may read and update the criteria, and insert audit rows (non-owner: RLS applies)
NO_AUDIT = "cv_admin"        # like the writer, but no INSERT on audit_logs
REFUSED_ROLES = [None, "", "owner", "Admin", "ADMIN", "super_admin"]               # neither route
READ_ONLY_ROLES = ["recruiter", "viewer"]                                          # may read, may not edit
SAVE_TEXT = {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"}]}


def _item(item_id, text, importance, weight=None, **kw):
    return C.make_item(text, importance, item_id=item_id, weight=weight, origin=kw.pop("origin", C.ORIGIN_STATED),
                       source_text=kw.pop("source_text", text), **kw)


def _doc() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        _item("req_py", "Python", C.IMPORTANCE_REQUIRED, 60, alternatives=["Python", "Python 3"]),
        _item("req_sql", "SQL", C.IMPORTANCE_REQUIRED, 40),
    ]}
    doc["categories"]["experience"] = {"weight": 0, "items": [
        _item("req_exp", "Backend work", C.IMPORTANCE_PREFERRED, source_text="Five years of backend work",
              experience={"subject": "backend work", "min_years": 5}),
    ]}
    return doc


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        cur = db.conn.cursor()
        for role, audit in ((WRITER, "SELECT, INSERT"), (NO_AUDIT, "SELECT")):
            cur.execute(f"GRANT USAGE ON SCHEMA cv_analyzer TO {role}")
            cur.execute(f"GRANT SELECT, UPDATE ON cv_analyzer.jobs, cv_analyzer.job_criteria TO {role}")
            cur.execute(f"GRANT {audit} ON cv_analyzer.audit_logs TO {role}")
        yield db


@pytest_asyncio.fixture()
async def engines(built):
    made = []

    def make(role: str = "cv_app"):
        engine = async_engine(built, role)
        made.append(engine)
        return engine

    yield make
    for engine in made:
        await engine.dispose()


@pytest.fixture()
def seeded(built):
    cur = built.conn.cursor()
    tags = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                    (f"tenant {label} {tag}", f"{label}-{tag}.example"))
        tenant = str(cur.fetchone()[0])
        users = {}
        for role in ("admin", "hr_manager", "recruiter", "viewer"):
            cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                        "VALUES (%s, %s, 'x', 'u', %s) RETURNING user_id",
                        (tenant, f"{label}-{role}-{tag}@example.test", role))
            users[role] = str(cur.fetchone()[0])
        tags[label] = {"tenant": tenant, "users": users}
        tags[label]["job"] = _job(built, tags[label])
    return tags


def _job(built, owner, *, marker=2, analysis=None, with_criteria=True) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (owner["tenant"], owner["users"]["admin"], JD))
    job = str(cur.fetchone()[0])
    if with_criteria:
        if analysis is None:
            analysis = {"requirements": _doc(), "extraction_notes": {"model": "fixture-model"}}
        cur.execute(
            "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version,"
            " requirements_revision, weight_skills, weight_experience, weight_education, weight_certifications,"
            " weight_soft_skills, weight_domain_knowledge, weight_other)"
            " VALUES (%s, %s, %s, %s, 0, 100, 0, 0, 0, 0, 0, 0)",
            (job, json.dumps(analysis), json.dumps({"requirements": copy.deepcopy(_doc())}), marker))
    return job


def _snapshot(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json, requirements_schema_version, requirements_revision, requirements_retired_item_ids,"
                " weight_skills, last_edited_by, last_edited_at, updated_at FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _audit(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT action, resource_type, resource_id, tenant_id, user_id, user_email, details"
                " FROM audit_logs WHERE resource_id = %s", (job,))
    return cur.fetchall()


def _user(tags, label, role="admin") -> CurrentUser:
    return CurrentUser(user_id=tags[label]["users"]["admin"] if role == "admin" else tags[label]["users"][role],
                       tenant_id=tags[label]["tenant"], email=f"{label}-{role}@example.test", role=role, full_name="u")


def _app(user, engine) -> FastAPI:
    app = FastAPI()
    app.include_router(R.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


async def _get(app, job):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/jobs/{job}/requirements")


async def _post(app, job, body=SAVE_TEXT):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/jobs/{job}/requirements/edits", json=body)


def _statements(engine):
    seen: list[str] = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda *args, **kwargs: seen.append(args[2]))
    return seen


# ---- reads ----

async def test_every_tenant_role_can_read(built, engines, seeded):
    job = seeded["a"]["job"]
    for role in ("admin", "hr_manager", "recruiter", "viewer"):
        response = await _get(_app(_user(seeded, "a", role), engines()), job)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"job_id", "requirements_revision", "schema_version", "document", "review"}
        assert body["requirements_revision"] == 0 and body["schema_version"] == 2
        assert set(body["document"]["categories"]) == set(C.CATEGORIES)


async def test_the_read_never_locks_the_criteria_row(built, engines, seeded):
    job = seeded["a"]["job"]
    locker = sync_connection(built, "cv_app")
    try:
        locker.cursor().execute("SELECT 1 FROM job_criteria WHERE job_id = %s FOR UPDATE", (job,))
        response = await asyncio.wait_for(_get(_app(_user(seeded, "a"), engines()), job), timeout=5)
        assert response.status_code == 200
    finally:
        locker.rollback()
        locker.close()


# ---- writes ----

async def test_an_admin_and_an_hr_manager_can_save_and_the_response_is_only_the_revision(built, engines, seeded):
    for label_role, expected in (("admin", 1), ("hr_manager", 2)):
        job = seeded["a"]["job"]
        body = {"client_revision": expected - 1, "operations": [{"op": "set_text", "item_id": "req_sql", "text": f"v{expected}"}]}
        response = await _post(_app(_user(seeded, "a", label_role), engines(WRITER)), job, body)
        assert response.status_code == 200, response.text
        assert response.json() == {"requirements_revision": expected}
        assert _snapshot(built, job)[2] == expected


async def test_a_successful_save_writes_one_audit_row_with_the_same_transaction(built, engines, seeded):
    job = seeded["a"]["job"]
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job)
    assert response.status_code == 200
    (row,) = _audit(built, job)
    action, resource_type, resource_id, tenant_id, user_id, email, details = row
    assert (action, resource_type, str(resource_id)) == ("requirements_v2_saved", "job", job)
    assert str(tenant_id) == seeded["a"]["tenant"] and str(user_id) == seeded["a"]["users"]["admin"]
    assert details["revision_before"] == 0 and details["revision_after"] == 1
    assert details["touched_item_ids"] == ["req_sql"]


async def test_the_audit_payload_carries_no_free_text(built, engines, seeded):
    job = seeded["a"]["job"]
    body = {"client_revision": 0, "operations": [
        {"op": "set_text", "item_id": "req_sql", "text": f"{CANARY}-REQUIREMENT-TEXT"},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", f"{CANARY}-ALTERNATIVE"]},
    ]}
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job, body)
    assert response.status_code == 200, response.text
    (row,) = _audit(built, job)
    assert CANARY not in json.dumps(row[6])


async def test_an_audit_failure_rolls_back_the_edit_and_the_revision(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _post(_app(_user(seeded, "a"), engines(NO_AUDIT)), job)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "audit_failed"
    assert _snapshot(built, job) == before
    assert _audit(built, job) == []


@pytest.mark.parametrize("body", [
    {"client_revision": 0, "operations": [{"op": "set_item_weight", "item_id": "req_py", "weight": 70}]},
    {"client_revision": 0, "operations": [{"op": "rename_item", "item_id": "req_sql"}]},
    {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "x", "origin": "stated"}]},
    {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_gone", "text": "x"}]},
], ids=["invalid_weight_total", "unknown_operation", "protected_field", "unknown_item"])
async def test_invalid_operations_change_neither_the_criteria_nor_the_audit(built, engines, seeded, body):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job, body)
    assert response.status_code == 422, response.text
    assert _snapshot(built, job) == before
    assert _audit(built, job) == []


async def test_a_stale_revision_returns_the_current_revision_and_changes_nothing(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job, {**SAVE_TEXT, "client_revision": 5})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "current_revision"}
    assert (detail["code"], detail["current_revision"]) == ("revision_conflict", 0)
    assert _snapshot(built, job) == before


async def test_two_concurrent_saves_at_one_revision_exactly_one_succeeds(built, engines, seeded):
    job = seeded["a"]["job"]
    app = _app(_user(seeded, "a"), engines(WRITER))
    first, second = await asyncio.gather(
        _post(app, job, {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "A"}]}),
        _post(app, job, {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "B"}]}),
    )
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["current_revision"] == 1
    assert _snapshot(built, job)[2] == 1
    assert len(_audit(built, job)) == 1


# ---- refusals, isolation and fail-closed cases ----

@pytest.mark.parametrize("role", REFUSED_ROLES, ids=[repr(r) for r in REFUSED_ROLES])
async def test_every_other_role_is_refused_on_both_routes_before_any_sql(built, engines, seeded, role):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    engine = engines(WRITER)
    statements = _statements(engine)
    caller = CurrentUser(user_id=seeded["a"]["users"]["admin"], tenant_id=seeded["a"]["tenant"],
                         email="x@example.test", role=role, full_name="u")
    app = _app(caller, engine)
    read, write = await _get(app, job), await _post(app, job)
    for response in (read, write):
        assert response.status_code == 403, response.text
    assert statements == [], "a refused role must not run any SQL"
    assert _snapshot(built, job) == before and _audit(built, job) == []


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
async def test_a_recruiter_and_a_viewer_cannot_edit_and_the_refusal_runs_no_sql(built, engines, seeded, role):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    engine = engines(WRITER)
    statements = _statements(engine)
    # An invalid body is still a 403: the role is checked before the body is validated.
    for body in ({}, SAVE_TEXT):
        response = await _post(_app(_user(seeded, "a", role), engine), job, body)
        assert response.status_code == 403, response.text
    assert statements == []
    assert _snapshot(built, job) == before and _audit(built, job) == []


async def test_a_missing_job_and_another_tenants_job_return_identical_404s(built, engines, seeded):
    own_admin = _user(seeded, "a")
    missing_id = str(uuid.uuid4())
    other_tenant_job = seeded["b"]["job"]
    for engine_role in ("cv_app",):
        app = _app(own_admin, engines(engine_role))
        missing_get, missing_post = await _get(app, missing_id), await _post(app, missing_id)
        cross_get, cross_post = await _get(app, other_tenant_job), await _post(app, other_tenant_job)
        assert missing_get.status_code == cross_get.status_code == 404
        assert missing_get.json() == cross_get.json() == {"detail": {"code": "job_not_found", "message": "Job not found."}}
        assert missing_post.json() == cross_post.json()


async def test_another_tenants_admin_cannot_read_or_write_under_row_level_security(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    app = _app(_user(seeded, "b"), engines(WRITER))
    assert (await _get(app, job)).status_code == 404
    assert (await _post(app, job)).status_code == 404
    assert _snapshot(built, job) == before and _audit(built, job) == []


async def test_the_owning_tenant_still_works_under_row_level_security(built, engines, seeded):
    job = seeded["a"]["job"]
    app = _app(_user(seeded, "a"), engines(WRITER))
    assert (await _get(app, job)).status_code == 200
    assert (await _post(app, job)).status_code == 200


async def test_a_null_schema_marker_is_refused_on_both_routes(built, engines, seeded):
    job = _job(built, seeded["a"], marker=None)
    app = _app(_user(seeded, "a"), engines(WRITER))
    read, write = await _get(app, job), await _post(app, job)
    assert read.status_code == write.status_code == 409
    assert read.json()["detail"]["code"] == write.json()["detail"]["code"] == "not_requirements_v2"


async def test_a_schema_marker_other_than_two_cannot_be_stored(built, seeded):
    cur = built.conn.cursor()
    job = _job(built, seeded["a"], with_criteria=False)
    with pytest.raises(psycopg2.Error):
        cur.execute("INSERT INTO job_criteria (job_id, requirements_schema_version) VALUES (%s, 1)", (job,))


async def test_a_job_without_a_criteria_row_has_no_document(built, engines, seeded):
    job = _job(built, seeded["a"], with_criteria=False)
    app = _app(_user(seeded, "a"), engines(WRITER))
    assert (await _get(app, job)).json()["detail"]["code"] == "no_requirements_document"
    assert (await _post(app, job)).status_code == 409


# ---- what is returned, and what is refused ----

async def test_retired_ids_and_server_owned_blocks_are_never_returned(built, engines, seeded):
    job = _job(built, seeded["a"], analysis={
        "requirements": {**_doc(), "structure_review": {"records": []},
                         "classification_review": {"warnings": [], "acknowledgments": []}},
        "extraction_notes": {"model": "fixture-model"},
    })
    app = _app(_user(seeded, "a"), engines(WRITER))
    save = await _post(app, job, {"client_revision": 0, "operations": [{"op": "remove_item", "item_id": "req_exp"}]})
    assert save.status_code == 200, save.text
    assert _snapshot(built, job)[3] == ["req_exp"], "the retired id is kept server-side"
    text = (await _get(app, job)).text
    for hidden in ("req_exp", "requirements_retired_item_ids", '"structure_review"', '"classification_review"',
                   '"scoring_confirmation"', '"structural_review"', "fixture-model", "extraction_notes", "basis_hash"):
        assert hidden not in text, hidden


@pytest.mark.parametrize("body", [
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "document": {}},
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "review": {}},
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "readiness": {"state": "ready"}},
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "scoring_confirmation": {}},
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "structural_review": {}},
    {"client_revision": 0, "operations": [SAVE_TEXT["operations"][0]], "requirements_retired_item_ids": ["req_x"]},
    {"client_revision": True, "operations": [SAVE_TEXT["operations"][0]]},
    {"client_revision": 0.0, "operations": [SAVE_TEXT["operations"][0]]},
    {"client_revision": 0, "operations": []},
], ids=["document", "review", "readiness", "scoring_confirmation", "structural_review", "retired_ids", "bool_revision",
        "float_revision", "empty_operations"])
async def test_read_only_and_server_owned_request_fields_are_refused_without_a_change(built, engines, seeded, body):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job, body)
    assert response.status_code == 422, response.text
    assert _snapshot(built, job) == before and _audit(built, job) == []


async def test_a_server_owned_field_inside_an_operation_is_refused_with_its_code(built, engines, seeded):
    job = seeded["a"]["job"]
    body = {"client_revision": 0, "operations": [{"op": "set_text", "item_id": "req_sql", "text": "x",
                                                  "structural_review": {"schema_version": 1, "findings": []}}]}
    response = await _post(_app(_user(seeded, "a"), engines(WRITER)), job, body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_operation"
