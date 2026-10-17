"""Copy-to-job (candidate_reuse), the submission_source default removal, score-pending, and the four intake methods, on a real
PostgreSQL 16 built from the full migration chain.

Routes and the intake pipeline run as the runtime role cv_app (subject to row-level security). The cluster's superuser sets up and
inspects rows only. Source CV files are real files on a temporary storage root, so the copy is tested on disk as well as in the
database. The scoring task's broker call is replaced by a recorder, so nothing is enqueued for real.

Grant note: the chain gives cv_app no privilege on tenant_usage_monthly (a separately recorded defect). The one module fixture below
grants it SELECT, INSERT and UPDATE so the intake control path can reach its quota check. It is test setup, not production evidence.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import psycopg2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from auth.dependencies import CurrentUser, get_current_user
from database import get_db, set_rls_context
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import applications as applications_router
from routers import candidate_tags as candidate_router
from services import application_reuse_service as reuse
from services.application_intake_service import process_cv_intake
from workers import cv_score

RUNTIME_ROLE = "cv_app"
JD = ("Senior Python developer to build REST APIs, design PostgreSQL schemas and mentor two engineers. "
      "Five years of Python required.")
PDF = b"%PDF-1.4 minimal candidate cv for the copy and intake tests"


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest.fixture(scope="module", autouse=True)
def chain_quota_grant(built):
    """TEST SETUP ONLY, not production evidence: see the module docstring."""
    cur = built.conn.cursor()
    cur.execute(f"GRANT SELECT, INSERT, UPDATE ON cv_analyzer.tenant_usage_monthly TO {RUNTIME_ROLE}")


def _cur(built):
    return built.conn.cursor()


def _tenant(built):
    cur = _cur(built)
    tag = uuid.uuid4().hex[:10]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                (f"tenant {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id", (tenant, f"{tag}@example.test"))
    return {"tenant": tenant, "user": str(cur.fetchone()[0])}


def _job(built, tenant, *, status="active", criteria="completed") -> str:
    cur = _cur(built)
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 't', %s, %s)"
                " RETURNING job_id", (tenant["tenant"], tenant["user"], JD, status))
    job = str(cur.fetchone()[0])
    if criteria is not None:
        cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s, %s)", (job, criteria))
    return job


def _application(built, tenant, job, *, source="manual_upload", processing="pending", email=None, files_root=None,
                 files=(("cv.pdf", PDF, "extracted text"),)) -> str:
    """An application with one application_files row per entry in `files` (name, bytes, extracted text). The bytes are written to
    the storage root when one is given, under the intake convention path."""
    cur = _cur(built)
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                " processing_status) VALUES (%s, %s, 'Candidate', %s, '+100', %s, %s) RETURNING application_id",
                (job, tenant["tenant"], email or f"{uuid.uuid4().hex[:8]}@example.test", source, processing))
    app = str(cur.fetchone()[0])
    for index, (name, content, extracted) in enumerate(files):
        relative = f"tenants/{tenant['tenant']}/jobs/{job}/{app}" + ("" if index == 0 else f"-{index}") + ".pdf"
        if files_root is not None:
            (files_root / relative).parent.mkdir(parents=True, exist_ok=True)
            (files_root / relative).write_bytes(content)
        cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path,"
                    " file_size_bytes, extracted_text, extraction_status)"
                    " VALUES (%s, %s, %s, 'application/pdf', %s, %s, %s, 'done')",
                    (app, tenant["tenant"], name, relative, len(content), extracted))
    return app


@pytest.fixture()
def files_root(tmp_path, monkeypatch):
    """The storage root the copy writes to. Settings are pointed at it for the duration of the test."""
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(candidate_router, "get_settings", lambda: SimpleNamespace(files_base_path=str(root)))
    return root


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://{RUNTIME_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}",
                              poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


def _app(user, engine):
    app = FastAPI()
    app.include_router(candidate_router.app_router)
    app.include_router(applications_router.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def _user(tenant, role="admin"):
    return CurrentUser(user_id=tenant["user"], tenant_id=tenant["tenant"], email="u@example.test", role=role, full_name="u")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _count_sql(engine):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    return statements


@pytest.fixture()
def scored(monkeypatch):
    calls = []
    monkeypatch.setattr(cv_score.score_cv_task, "delay", lambda **kwargs: calls.append(kwargs["application_id"]))
    return calls


def _files_of(built, application):
    cur = _cur(built)
    cur.execute("SELECT original_name, file_path, file_size_bytes, extracted_text, extraction_status, mime_type"
                " FROM application_files WHERE application_id = %s ORDER BY original_name, file_path", (application,))
    return cur.fetchall()


def _applications_on(built, job) -> int:
    cur = _cur(built)
    cur.execute("SELECT count(*) FROM applications WHERE job_id = %s", (job,))
    return cur.fetchone()[0]


# ---- the migration: default removed, candidate_reuse allowed ----

def test_the_submission_source_default_is_removed_and_candidate_reuse_is_allowed(built):
    cur = _cur(built)
    cur.execute("SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d JOIN pg_attribute a"
                " ON a.attrelid = d.adrelid AND a.attnum = d.adnum"
                " WHERE d.adrelid = 'cv_analyzer.applications'::regclass AND a.attname = 'submission_source'")
    assert cur.fetchall() == []
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'applications_submission_source_check'")
    definition = cur.fetchone()[0]
    assert "candidate_reuse" in definition and "'email'" not in definition


def test_an_insert_that_omits_submission_source_is_refused_and_email_is_refused(built):
    tenant = _tenant(built)
    job = _job(built, tenant)
    conn = built.conn
    conn.autocommit = False
    try:
        cur = conn.cursor()
        for sql, params, code in (
            ("INSERT INTO applications (job_id, tenant_id, candidate_name) VALUES (%s, %s, 'x')", (job, tenant["tenant"]), "23502"),
            ("INSERT INTO applications (job_id, tenant_id, candidate_name, submission_source) VALUES (%s, %s, 'x', 'email')",
             (job, tenant["tenant"]), "23514"),
        ):
            cur.execute("SAVEPOINT p")
            with pytest.raises(psycopg2.Error) as excinfo:
                cur.execute(sql, params)
            assert excinfo.value.pgcode == code
            cur.execute("ROLLBACK TO SAVEPOINT p")
    finally:
        conn.rollback()
        conn.autocommit = True


# ---- copy-to-job: a real CV reuse, with its own copies on storage ----

@pytest.mark.asyncio
async def test_a_one_file_reuse_creates_an_application_with_its_own_copy_and_scores_nothing(built, engine, files_root, scored):
    tenant = _tenant(built)
    source_job, target = _job(built, tenant), _job(built, tenant)
    source = _application(built, tenant, source_job, email="copy@example.test", files_root=files_root,
                          files=(("cv.pdf", PDF, "the extracted text"),))
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 200, response.text
    new_id = response.json()["new_application_id"]

    cur = _cur(built)
    cur.execute("SELECT job_id, tenant_id, submission_source, workflow_status, processing_status FROM applications"
                " WHERE application_id = %s", (new_id,))
    row = cur.fetchone()
    assert (str(row[0]), str(row[1]), row[2], row[3], row[4]) == (
        target, tenant["tenant"], "candidate_reuse", "awaiting_review", "pending")

    [copy] = _files_of(built, new_id)
    source_file = _files_of(built, source)[0]
    assert copy[1] != source_file[1]                                  # its own physical path
    assert (files_root / copy[1]).read_bytes() == PDF                  # the bytes are copied
    assert (files_root / source_file[1]).read_bytes() == PDF          # the source is untouched
    assert copy[0] == "cv.pdf" and copy[2] == len(PDF) and copy[3] == "the extracted text" and copy[4] == "done"
    assert copy[5] == "application/pdf"
    assert scored == []


@pytest.mark.asyncio
async def test_a_multi_file_reuse_copies_every_file_to_its_own_path(built, engine, files_root, scored):
    tenant = _tenant(built)
    source_job, target = _job(built, tenant), _job(built, tenant)
    files = (("cv.pdf", PDF, "first"), ("portfolio.pdf", PDF + b"-2", "second"))
    source = _application(built, tenant, source_job, email="multi@example.test", files_root=files_root, files=files)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 200, response.text
    copies = _files_of(built, response.json()["new_application_id"])
    assert len(copies) == 2
    assert len({c[1] for c in copies}) == 2                            # no two copies share a path
    assert sorted((files_root / c[1]).read_bytes() for c in copies) == sorted(c for _, c, _ in files)
    assert scored == []


@pytest.mark.asyncio
async def test_a_source_without_a_cv_file_is_refused_and_nothing_is_created(built, engine, files_root, scored):
    tenant = _tenant(built)
    source_job, target = _job(built, tenant), _job(built, tenant)
    source = _application(built, tenant, source_job, email="nofile@example.test", files_root=files_root, files=())
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409
    assert _applications_on(built, target) == 0
    assert scored == []


@pytest.mark.asyncio
async def test_a_missing_source_file_is_refused_and_nothing_is_created(built, engine, files_root, scored):
    tenant = _tenant(built)
    source_job, target = _job(built, tenant), _job(built, tenant)
    source = _application(built, tenant, source_job, email="missing@example.test", files_root=files_root)
    (files_root / _files_of(built, source)[0][1]).unlink()
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409
    assert _applications_on(built, target) == 0
    target_dir = files_root / "tenants" / tenant["tenant"] / "jobs" / target
    assert not target_dir.exists() or list(target_dir.iterdir()) == []   # nothing was copied
    assert scored == []


@pytest.mark.asyncio
async def test_a_failed_file_row_rolls_back_the_application_and_removes_its_copies(built, engine, files_root, scored, monkeypatch):
    tenant = _tenant(built)
    source_job, target = _job(built, tenant), _job(built, tenant)
    source = _application(built, tenant, source_job, email="rollback@example.test", files_root=files_root,
                          files=(("cv.pdf", PDF, "a"), ("second.pdf", PDF, "b")))

    original_insert_file_row = reuse._insert_file_row

    async def fail_on_second_row(db, *, new_application_id, tenant_id, row, file_path):
        if file_path.endswith("-1.pdf"):
            raise OSError("simulated storage failure while recording the second file")
        await original_insert_file_row(db, new_application_id=new_application_id, tenant_id=tenant_id, row=row, file_path=file_path)

    monkeypatch.setattr(reuse, "_insert_file_row", fail_on_second_row)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 500
    assert _applications_on(built, target) == 0
    target_dir = files_root / "tenants" / tenant["tenant"] / "jobs" / target
    assert not target_dir.exists() or list(target_dir.iterdir()) == []   # every copy written so far was removed
    assert (files_root / _files_of(built, source)[0][1]).read_bytes() == PDF   # the source is untouched
    assert scored == []


@pytest.mark.asyncio
async def test_a_copy_from_another_tenants_application_is_not_found(built, engine, files_root, scored):
    tenant = _tenant(built)
    other = _tenant(built)
    foreign_source = _application(built, other, _job(built, other), files_root=files_root)
    target = _job(built, tenant)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{foreign_source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 404
    assert _applications_on(built, target) == 0


@pytest.mark.asyncio
async def test_a_copy_to_another_tenants_job_is_not_found(built, engine, files_root, scored):
    tenant = _tenant(built)
    other = _tenant(built)
    source = _application(built, tenant, _job(built, tenant), files_root=files_root)
    foreign = _job(built, other)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": foreign})
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status, criteria", [
    ("closed", "completed"),
    ("active", "failed"),
    ("active", "pending"),
    ("active", "processing"),
])
async def test_a_copy_is_refused_for_an_inactive_target_or_incomplete_criteria(built, engine, files_root, scored, target_status, criteria):
    tenant = _tenant(built)
    source = _application(built, tenant, _job(built, tenant), email=f"refused{uuid.uuid4().hex[:6]}@example.test",
                          files_root=files_root)
    target = _job(built, tenant, status=target_status, criteria=criteria)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409
    assert _applications_on(built, target) == 0
    assert scored == []


@pytest.mark.asyncio
async def test_a_duplicate_candidate_on_the_target_is_still_refused(built, engine, files_root, scored):
    tenant = _tenant(built)
    email = f"dup{uuid.uuid4().hex[:6]}@example.test"
    source = _application(built, tenant, _job(built, tenant), email=email, files_root=files_root)
    target = _job(built, tenant)
    _application(built, tenant, target, email=email, files_root=files_root)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409


# ---- authorization on copy-to-job: admin and recruiter only, before any SQL ----

@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "recruiter"])
async def test_the_copy_route_allows_admin_and_recruiter(built, engine, files_root, scored, role):
    tenant = _tenant(built)
    source = _application(built, tenant, _job(built, tenant), email=f"ok{uuid.uuid4().hex[:6]}@example.test",
                          files_root=files_root)
    target = _job(built, tenant)
    async with _client(_app(_user(tenant, role), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "", "owner", "Admin", "ADMIN", "Recruiter", "viewer", "super_admin", "hr_manager"])
async def test_the_copy_route_refuses_every_other_role_before_any_sql(built, engine, files_root, scored, role):
    tenant = _tenant(built)
    source = _application(built, tenant, _job(built, tenant), email=f"no{uuid.uuid4().hex[:6]}@example.test",
                          files_root=files_root)
    target = _job(built, tenant)
    statements = _count_sql(engine)
    async with _client(_app(_user(tenant, role), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 403
    assert statements == []
    assert _applications_on(built, target) == 0 and scored == []


# ---- score-pending never selects a copy ----

@pytest.mark.asyncio
async def test_score_pending_enqueues_the_manual_upload_and_never_the_copy(built, engine, files_root, scored):
    tenant = _tenant(built)
    job = _job(built, tenant)
    manual = _application(built, tenant, job, source="manual_upload", files_root=files_root)
    copied = _application(built, tenant, job, source="candidate_reuse", files_root=files_root)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post("/applications/score-pending", json={"job_id": job})
    assert response.status_code == 202, response.text
    assert scored == [manual]
    assert copied not in scored


# ---- the four intake methods refuse failed criteria ----

INTAKE_METHODS = [
    ("manual_upload", "manual_upload"),
    ("public_apply", "public_apply"),
    ("email_forwarding", "email_forwarding"),
    ("platform_email", "platform_email"),
]


async def _intake(engine, tenant, job, method, source, tmp_path):
    async with AsyncSession(engine) as db:
        await set_rls_context(db, tenant["tenant"], "admin")
        return await process_cv_intake(
            db, intake_method=method, job_id=job, tenant_id=tenant["tenant"], candidate_name="Candidate",
            candidate_email=f"{uuid.uuid4().hex[:8]}@example.test", content_type="application/pdf", content=PDF,
            original_filename="cv.pdf", submission_source=source, auto_score=False,
            files_base_path=str(tmp_path), max_file_size_mb=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("method, source", INTAKE_METHODS)
@pytest.mark.parametrize("criteria", ["failed", "pending", "processing"])
async def test_each_intake_method_refuses_a_job_whose_criteria_are_not_complete(built, engine, tmp_path, method, source, criteria):
    tenant = _tenant(built)
    job = _job(built, tenant, criteria=criteria)
    result = await _intake(engine, tenant, job, method, source, tmp_path)
    assert result.status == "INTAKE_BLOCKED", result.status
    assert _applications_on(built, job) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method, source", INTAKE_METHODS)
async def test_each_intake_method_accepts_a_job_with_completed_criteria(built, engine, tmp_path, method, source):
    """The control: the same call proceeds past the criteria gate when the criteria are complete."""
    tenant = _tenant(built)
    job = _job(built, tenant, criteria="completed")
    result = await _intake(engine, tenant, job, method, source, tmp_path)
    assert result.status != "INTAKE_BLOCKED", result.status
