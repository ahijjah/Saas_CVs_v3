"""PR A foundations on a real PostgreSQL 16 through the async adapter: the read path (load_for_edit), the typed revision conflict,
the strict audit writer inside the caller's transaction, rollback of an edit when its audit fails, and fail-closed row-level
security. Login roles are used wherever row-level security must apply; superuser connections bypass it."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import requirements_edit as E
from services.requirements_v2 import contract as C

pytestmark = pytest.mark.asyncio

NOW = "2026-10-17T12:00:00+00:00"
JD = "We need Python and SQL. Five years of backend work is preferred."
CANARY = "CANARY"
READ_ROLE = "cv_edit_reader"
WRITER_ROLE = "cv_audit_writer"
NO_AUDIT_ROLE = "cv_audit_nopriv"


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


class _NoDatabase:
    async def execute(self, *args, **kwargs):
        raise AssertionError("SQL ran before the role check")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        cur = db.conn.cursor()
        cur.execute(f"CREATE ROLE {READ_ROLE} LOGIN")
        cur.execute(f"CREATE ROLE {WRITER_ROLE} LOGIN")
        cur.execute(f"CREATE ROLE {NO_AUDIT_ROLE} LOGIN")
        yield db


@pytest_asyncio.fixture()
async def engines(built):
    made = []

    def make(role: str = "postgres"):
        engine = create_async_engine(
            f"postgresql+asyncpg://{role}@127.0.0.1:{built.cluster.port}/{built.name}", poolclass=NullPool,
            connect_args={"server_settings": {"search_path": "cv_analyzer"}})
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
        for role in ("admin", "recruiter", "viewer"):
            cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                        "VALUES (%s, %s, 'x', 'u', %s) RETURNING user_id",
                        (tenant, f"{label}-{role}-{tag}@example.test", role))
            users[role] = str(cur.fetchone()[0])
        tags[label] = {"tenant": tenant, "users": users}
        tags[label]["job"] = _job(built, tags[label])
    return tags


def _job(built, owner, *, marker=2, analysis=None, with_criteria=True, revision=0, description=JD) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (owner["tenant"], owner["users"]["admin"], description))
    job = str(cur.fetchone()[0])
    if with_criteria:
        if analysis is None:
            analysis = {"requirements": _doc(), "extraction_notes": {"model": "fixture-model"}}
        cur.execute(
            "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version,"
            " requirements_revision, weight_skills, weight_experience, weight_education, weight_certifications,"
            " weight_soft_skills, weight_domain_knowledge, weight_other)"
            " VALUES (%s, %s, %s, %s, %s, 100, 0, 0, 0, 0, 0, 0)",
            (job, json.dumps(analysis), json.dumps({"requirements": copy.deepcopy(_doc())}), marker, revision))
    return job


def _row(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json, requirements_schema_version, requirements_revision, requirements_retired_item_ids,"
                " weight_skills, last_edited_by FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _audit_rows(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT action, resource_type, resource_id, tenant_id, user_id, user_email, details"
                " FROM audit_logs WHERE resource_id = %s", (job,))
    return cur.fetchall()


def _user(seeded, label, role="admin") -> E.EditingUser:
    return E.EditingUser(user_id=seeded[label]["users"][role], tenant_id=seeded[label]["tenant"], role=role,
                         email=f"{label}-{role}@example.test")


# ---- load_for_edit: the read path ----

async def test_an_admin_and_a_recruiter_can_load_the_document(built, engines, seeded):
    job = seeded["a"]["job"]
    for role in ("admin", "recruiter"):
        async with AsyncSession(engines()) as session:
            view = await E.load_for_edit(session, _user(seeded, "a", role), job)
        assert view.requirements_revision == 0 and view.schema_version == 2 and view.job_id == job
        assert set(view.document) == {"schema_version", "categories"}


async def test_a_viewer_is_refused_before_any_sql(built, engines, seeded):
    with pytest.raises(E.PermissionDenied):
        await E.load_for_edit(_NoDatabase(), _user(seeded, "a", "viewer"), seeded["a"]["job"])


async def test_a_missing_job_and_another_tenants_job_are_indistinguishable(built, engines, seeded):
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.JobNotFound) as missing:
            await E.load_for_edit(session, _user(seeded, "a"), str(uuid.uuid4()))
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.JobNotFound) as other_tenant:
            await E.load_for_edit(session, _user(seeded, "b"), seeded["a"]["job"])
    async with AsyncSession(engines(READ_ROLE)) as session:
        with pytest.raises(E.JobNotFound) as under_rls:
            await E.load_for_edit(session, _user(seeded, "b"), seeded["a"]["job"])
    assert str(missing.value) == str(other_tenant.value) == str(under_rls.value) == "Job not found."
    assert missing.value.code == other_tenant.value.code == under_rls.value.code == "job_not_found"


async def test_the_owning_tenant_can_load_under_row_level_security(built, engines, seeded):
    async with AsyncSession(engines(READ_ROLE)) as session:
        view = await E.load_for_edit(session, _user(seeded, "a"), seeded["a"]["job"])
    assert view.requirements_revision == 0


async def test_a_legacy_null_schema_version_is_refused(built, engines, seeded):
    job = _job(built, seeded["a"], marker=None)
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.NotRequirementsV2):
            await E.load_for_edit(session, _user(seeded, "a"), job)


async def test_a_job_without_a_criteria_row_has_no_document(built, engines, seeded):
    job = _job(built, seeded["a"], with_criteria=False)
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.NoRequirementsDocument):
            await E.load_for_edit(session, _user(seeded, "a"), job)


async def test_a_criteria_row_without_a_requirements_document_has_no_document(built, engines, seeded):
    job = _job(built, seeded["a"], analysis={"extraction_notes": {}})
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.NoRequirementsDocument):
            await E.load_for_edit(session, _user(seeded, "a"), job)


async def test_a_malformed_stored_document_is_refused(built, engines, seeded):
    broken = _doc()
    del broken["categories"]["skills"]
    job = _job(built, seeded["a"], analysis={"requirements": broken})
    async with AsyncSession(engines()) as session:
        with pytest.raises(E.StoredDocumentInvalid):
            await E.load_for_edit(session, _user(seeded, "a"), job)


async def test_the_load_takes_no_row_lock(built, engines, seeded):
    """Another session holds the criteria row lock. The read must still return promptly."""
    job = seeded["a"]["job"]
    locker = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name,
                              options="-csearch_path=cv_analyzer")
    try:
        cur = locker.cursor()
        cur.execute("SELECT 1 FROM job_criteria WHERE job_id = %s FOR UPDATE", (job,))
        async with AsyncSession(engines()) as session:
            view = await asyncio.wait_for(E.load_for_edit(session, _user(seeded, "a"), job), timeout=5)
        assert view.requirements_revision == 0
    finally:
        locker.rollback()
        locker.close()


async def test_the_view_never_contains_retired_ids_or_unrelated_analysis_keys(built, engines, seeded):
    job = _job(built, seeded["a"], analysis={"requirements": _doc(), "extraction_notes": {"model": "CANARY-MODEL"},
                                             "requirements_pipeline": {"prompt": "CANARY-PIPELINE"}})
    cur = built.conn.cursor()
    cur.execute("UPDATE job_criteria SET requirements_retired_item_ids = '[\"req_gone_canary\"]'::jsonb WHERE job_id = %s", (job,))
    async with AsyncSession(engines()) as session:
        view = await E.load_for_edit(session, _user(seeded, "a"), job)
    text = repr(view)
    for leak in ("CANARY-MODEL", "CANARY-PIPELINE", "req_gone_canary", "requirements_retired_item_ids", "extraction_notes"):
        assert leak not in text, leak


# ---- the typed revision conflict ----

async def test_a_stale_save_carries_the_current_revision(built, engines, seeded):
    job = _job(built, seeded["b"], revision=3)
    with pytest.raises(E.RevisionConflict) as caught:
        async with AsyncSession(engines()) as session:
            async with session.begin():
                await E.save_operations(session, _user(seeded, "b"), job, client_revision=1,
                                        ops=E.parse_operations([{"op": "set_text", "item_id": "req_sql", "text": "x"}]),
                                        now=NOW)
    assert caught.value.current_revision == 3 and caught.value.code == "revision_conflict"


# ---- the strict audit writer ----

async def test_a_save_and_its_audit_commit_together_with_the_full_metadata(built, engines, seeded):
    job = seeded["a"]["job"]
    body = [{"op": "set_text", "item_id": "req_sql", "text": f"{CANARY}-REQUIREMENT-TEXT"},
            {"op": "remove_item", "item_id": "req_exp"},
            {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]},
            {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
             "alternatives": ["Docker", "Containers"], "experience": None}]
    async with AsyncSession(engines(WRITER_ROLE)) as session:
        async with session.begin():
            result = await E.save_operations(session, _user(seeded, "a"), job, client_revision=0,
                                             ops=E.parse_operations(body), now=NOW)
            await E.write_audit_event(session, _user(seeded, "a"), job, action=E.AUDIT_ACTION_SAVE, details=result.audit)
    (row,) = _audit_rows(built, job)
    action, resource_type, resource_id, tenant_id, user_id, email, details = row
    assert (action, resource_type, resource_id) == ("requirements_v2_saved", "job", job)
    assert (str(tenant_id), str(user_id), email) == (seeded["a"]["tenant"], seeded["a"]["users"]["admin"], "a-admin@example.test")
    assert details["revision_before"] == 0 and details["revision_after"] == 1
    assert details["newly_retired_item_ids"] == ["req_exp"]
    assert details["touched_item_ids"] == ["req_py", "req_sql"]
    assert len(details["generated_item_ids"]) == 1
    assert {"item_id": "req_py", "kind": "corrected"} in details["structure_edits"]
    assert CANARY not in json.dumps(details)
    assert _row(built, job)[2] == 1


async def test_nothing_is_visible_until_the_caller_commits(built, engines, seeded):
    job = _job(built, seeded["b"])
    async with AsyncSession(engines(WRITER_ROLE)) as session:
        await E.load_for_edit(session, _user(seeded, "b"), job)          # the caller sets the tenant context in its transaction
        await E.write_audit_event(session, _user(seeded, "b"), job, action=E.AUDIT_ACTION_SAVE,
                                  details=E.SaveAudit({"set_text": 0}, (), (), (), (), "x", "x"))
        assert session.in_transaction()
        assert _audit_rows(built, job) == [], "another connection must not see an uncommitted audit row"
        await session.rollback()
    assert _audit_rows(built, job) == []


async def test_the_service_never_commits_on_its_callers_behalf(built, engines, seeded):
    job = _job(built, seeded["a"])

    async def refuse_commit(*args, **kwargs):
        raise AssertionError("the service must not commit")

    async with AsyncSession(engines(WRITER_ROLE)) as session:
        session.commit = refuse_commit
        await E.load_for_edit(session, _user(seeded, "a"), job)
        audit = E.SaveAudit({"set_text": 0}, (), (), (), (), "x", "x")
        await E.write_audit_event(session, _user(seeded, "a"), job, action=E.AUDIT_ACTION_SAVE, details=audit)
        await session.rollback()


async def test_a_missing_audit_privilege_raises_audit_write_error_from_the_database_error(built, engines, seeded):
    job = _job(built, seeded["a"])
    audit = E.SaveAudit({"set_text": 0}, (), (), (), (), "x", "x")
    async with AsyncSession(engines(NO_AUDIT_ROLE)) as session:
        with pytest.raises(E.AuditWriteError) as caught:
            await E.write_audit_event(session, _user(seeded, "a"), job, action=E.AUDIT_ACTION_SAVE, details=audit)
        await session.rollback()
    assert caught.value.__cause__ is not None
    assert _audit_rows(built, job) == []


async def test_a_missing_row_level_security_context_fails_closed(built, engines, seeded):
    """The writer role may insert, but without the tenant context the policy denies the row."""
    job = _job(built, seeded["a"])
    audit = E.SaveAudit({"set_text": 0}, (), (), (), (), "x", "x")
    async with AsyncSession(engines(WRITER_ROLE)) as session:
        with pytest.raises(E.AuditWriteError):
            await E.write_audit_event(session, _user(seeded, "a"), job, action=E.AUDIT_ACTION_SAVE, details=audit)
        await session.rollback()
    assert _audit_rows(built, job) == []


async def test_a_failed_audit_rolls_the_edit_back_with_it(built, engines, seeded):
    """The edit and its audit share one transaction. The audit fails for a role without the INSERT privilege, the caller rolls
    back, and neither the document nor the revision changes."""
    job = _job(built, seeded["a"])
    before = _row(built, job)
    body = [{"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"}]
    async with AsyncSession(engines(NO_AUDIT_ROLE)) as session:
        try:
            async with session.begin():
                result = await E.save_operations(session, _user(seeded, "a"), job, client_revision=0,
                                                 ops=E.parse_operations(body), now=NOW)
                await E.write_audit_event(session, _user(seeded, "a"), job, action=E.AUDIT_ACTION_SAVE, details=result.audit)
        except E.AuditWriteError:
            pass
        else:
            raise AssertionError("the audit failure must surface")
    assert _row(built, job) == before
    assert _audit_rows(built, job) == []
