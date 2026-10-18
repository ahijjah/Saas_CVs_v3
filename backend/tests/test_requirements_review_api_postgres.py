"""Review actions through the approved HTTP API on a real PostgreSQL 16 chain: one revision per accepted save, the stable error
mapping, the refusal of server-owned fields, the audit contents, and the indistinguishable refusals across tenants and roles."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import config
from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import job_requirements
from services.requirements_v2 import contract as C

JD = ("Senior developer.\nPython or Java is required.\nFive years of experience is preferred.")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://postgres@127.0.0.1:{built.cluster.port}/{built.name}", poolclass=NullPool,
                              connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


def _user(built, role="admin", tenant=None):
    cur = built.conn.cursor()
    if tenant is None:
        tag = uuid.uuid4().hex[:8]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tag}", f"{tag}.example"))
        tenant = str(cur.fetchone()[0])
    tag = uuid.uuid4().hex[:8]
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', %s) RETURNING user_id",
                (tenant, f"{tag}@example.test", role))
    return CurrentUser(user_id=str(cur.fetchone()[0]), tenant_id=tenant, email=f"{tag}@example.test", role=role, full_name="u")


def _job(built, user, document, original, expectations, revision=1):
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 't', %s, 'active') RETURNING job_id",
                (user.tenant_id, user.user_id, JD))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_revision,"
                " analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education, weight_certifications,"
                " weight_soft_skills, weight_domain_knowledge, weight_other)"
                " VALUES (%s, 'completed', 2, %s, CAST(%s AS jsonb), CAST(%s AS jsonb), %s, %s, %s, %s, %s, %s, %s)",
                (job, revision, json.dumps({"requirements": document}, ensure_ascii=False),
                 json.dumps({"requirements": original, "structural_expectations": expectations}, ensure_ascii=False),
                 *[document["categories"][c]["weight"] for c in C.CATEGORIES]))
    return job


def _app(user, engine):
    app = FastAPI()
    app.include_router(job_requirements.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _stored(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json, requirements_revision FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _audit_rows(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT action, details FROM audit_logs WHERE resource_id = %s ORDER BY created_at", (job,))
    return cur.fetchall()


# A two-item skills category with a structural finding and a classification warning: enough review surface for every action.
@pytest.fixture()
def document(built):
    from test_requirements_v2_scoring_pipeline_postgres import _structural_split
    return _structural_split(jd=JD, source="Python or Java is required", alternatives=("Python", "Java"))


@pytest.mark.asyncio
async def test_a_save_with_several_review_actions_increments_the_revision_exactly_once_and_audits_ids_and_kinds_only(built, engine,
                                                                                                                     document):
    doc, original, expectations = document
    user = _user(built)
    job = _job(built, user, doc, original, expectations)
    finding_id = doc["structural_review"]["findings"][0]["id"]
    ids = [i["id"] for i in doc["categories"]["skills"]["items"]]
    body = {"client_revision": 1, "operations": [
        {"op": "resolve_structural_finding", "finding_id": finding_id, "decision": "accepted_as_is",
         "note": "Two tools, both acceptable.", "evidence_item_ids": None},
        {"op": "reorder_items", "category": "skills", "item_ids": list(reversed(ids))}]}
    async with _client(_app(user, engine)) as client:
        response = await client.post(f"/jobs/{job}/requirements/edits", json=body)
    assert response.status_code == 200 and response.json()["requirements_revision"] == 2
    assert _stored(built, job)[1] == 2
    (action, details), = _audit_rows(built, job)
    kinds = [(a["kind"], a["id"]) for a in details["review_actions"]]
    assert ("resolve_structural_finding", finding_id) in kinds and ("reorder_items", "skills") in kinds
    assert "Two tools, both acceptable." not in json.dumps(details, ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_stale_revision_is_a_conflict_that_reports_the_current_revision_and_writes_nothing(built, engine, document):
    doc, original, expectations = document
    user = _user(built)
    job = _job(built, user, doc, original, expectations, revision=4)
    async with _client(_app(user, engine)) as client:
        response = await client.post(f"/jobs/{job}/requirements/edits", json={"client_revision": 3, "operations": [
            {"op": "reorder_items", "category": "skills", "item_ids": [i["id"] for i in doc["categories"]["skills"]["items"]]}]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "revision_conflict" and response.json()["detail"]["current_revision"] == 4
    assert _stored(built, job)[1] == 4 and _audit_rows(built, job) == []


@pytest.mark.asyncio
async def test_an_unknown_warning_is_refused_with_its_code_and_nothing_changes(built, engine, document):
    doc, original, expectations = document
    user = _user(built)
    job = _job(built, user, doc, original, expectations)
    before = _stored(built, job)
    async with _client(_app(user, engine)) as client:
        response = await client.post(f"/jobs/{job}/requirements/edits", json={"client_revision": 1, "operations": [
            {"op": "acknowledge_classification_warning", "warning_id": "not-a-real-warning"}]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "unknown_warning"
    assert _stored(built, job) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"confirmed_at": "2030-01-01T00:00:00+00:00"}, {"basis_hash": "a" * 64},
                                   {"user_id": "11111111-1111-4111-8111-111111111111"}])
async def test_a_client_supplied_confirmation_field_is_refused(built, engine, document, extra):
    doc, original, expectations = document
    user = _user(built)
    job = _job(built, user, doc, original, expectations)
    async with _client(_app(user, engine)) as client:
        response = await client.post(f"/jobs/{job}/requirements/edits", json={"client_revision": 1, "operations": [
            {"op": "confirm_no_numeric_score", **extra}]})
    assert response.status_code == 422
    assert _stored(built, job)[1] == 1


@pytest.mark.asyncio
async def test_another_tenant_and_a_read_only_role_are_refused_without_revealing_the_job(built, engine, document):
    doc, original, expectations = document
    owner = _user(built)
    job = _job(built, owner, doc, original, expectations)
    body = {"client_revision": 1, "operations": [{"op": "confirm_no_numeric_score"}]}
    stranger = _user(built)
    async with _client(_app(stranger, engine)) as client:
        other = await client.post(f"/jobs/{job}/requirements/edits", json=body)
    viewer = _user(built, role="viewer", tenant=owner.tenant_id)
    async with _client(_app(viewer, engine)) as client:
        read_only = await client.post(f"/jobs/{job}/requirements/edits", json=body)
    missing = None
    async with _client(_app(stranger, engine)) as client:
        missing = await client.post(f"/jobs/{uuid.uuid4()}/requirements/edits", json=body)
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()                       # another tenant's job looks like a job that does not exist
    assert read_only.status_code == 403 and read_only.json()["detail"]["code"] == "permission_denied"
    assert _stored(built, job)[1] == 1
