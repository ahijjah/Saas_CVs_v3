"""The requirements-v2 editing service on a real PostgreSQL 16, through the async adapter the application uses (SQLAlchemy
AsyncSession on asyncpg). The chain is built from the full current migration chain on a disposable cluster.

Covers: the role gate before any SQL, the schema-version gate, the optimistic revision check, the increment on every accepted
save, the locked read, serialisation of two saves on one row, a concurrent revision conflict, rollback, the service leaving the
commit to its caller, tenant isolation (explicit filter, and row-level security under a non-owner login role), protected
columns, preserved analysis_json keys, append-only retired ids, the structured edit record, and the structural review checked
against the stored job description."""
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

from runtime_engines import async_engine
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.structural_review import CODE_ALTERNATIVES_SPLIT, READINESS_INVALID

pytestmark = pytest.mark.asyncio

PROBE_ROLE = "cv_app"
NOW = "2026-10-17T12:00:00+00:00"
JD = "We need Python and SQL. Five years of backend work is preferred."


def _item(item_id, text_, importance, weight=None, **kw):
    return C.make_item(text_, importance, item_id=item_id, weight=weight, origin=kw.pop("origin", C.ORIGIN_STATED),
                       source_text=kw.pop("source_text", text_), **kw)


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


def _block(item_ids, *, start=None, text_="Python and SQL", jd=JD) -> dict:
    start = jd.index(text_) if start is None else start
    return {"schema_version": 1, "findings": [{
        "id": "sf_1", "code": CODE_ALTERNATIVES_SPLIT, "item_ids": list(item_ids), "source_text": text_,
        "source_start": start, "source_end": start + len(text_), "message": "Check these together.",
        "status": "open", "resolution": None}]}


class _NoDatabase:
    """Stands in for a session. Any attempt to run SQL through it fails the test."""

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
        # A login role, not SET ROLE: a rolled-back save cannot silently fall back to a superuser connection.
        yield db


@pytest_asyncio.fixture()
async def engines(built):
    """Async engine factory, one engine per login role. Each engine is disposed at the end of the test so the database
    can be dropped cleanly."""
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
    """Two tenants, each with an admin user and a job. The job holds the base document, the original analysis is the same
    document, and one unrelated analysis key must survive every save."""
    cur = built.conn.cursor()
    tags = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                    (f"tenant {label} {tag}", f"{label}-{tag}.example"))
        tenant = cur.fetchone()[0]
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                    "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id", (tenant, f"{label}-{tag}@example.test"))
        user = cur.fetchone()[0]
        tags[label] = {"tenant": str(tenant), "user": str(user)}
    tags["a"]["job"] = _job(built, tags["a"], _doc())
    tags["b"]["job"] = _job(built, tags["b"], _doc())
    return tags


def _job(built, owner, doc, *, marker=2, revision=0, description=JD, analysis=None, original=None) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (owner["tenant"], owner["user"], description))
    job = str(cur.fetchone()[0])
    if analysis is None:
        analysis = {"requirements": doc, "extraction_notes": {"model": "fixture-model"}}
    original_doc = copy.deepcopy(doc) if original is None else original
    cur.execute(
        "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version,"
        " requirements_revision, weight_skills, weight_experience, weight_education, weight_certifications,"
        " weight_soft_skills, weight_domain_knowledge, weight_other)"
        " VALUES (%s, %s, %s, %s, %s, 100, 0, 0, 0, 0, 0, 0)",
        (job, json.dumps(analysis), json.dumps({"requirements": original_doc}), marker, revision))
    return job


def _row(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json, original_analysis_json, requirements_schema_version, requirements_revision,"
                " requirements_retired_item_ids, weight_skills, last_edited_by FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _user(tags, label, role="admin"):
    return E.EditingUser(user_id=tags[label]["user"], tenant_id=tags[label]["tenant"], role=role)


async def _save(engine, user, job, body, revision):
    """One save through the production caller pattern: the caller opens the transaction, the service never commits."""
    async with AsyncSession(engine) as session:
        async with session.begin():
            return await E.save_operations(session, user, job, client_revision=revision,
                                           ops=E.parse_operations(body), now=NOW)


def _set_text(item_id, text_):
    return [{"op": "set_text", "item_id": item_id, "text": text_}]


# ---- gates before any write ----

async def test_a_viewer_is_refused_before_any_sql_runs(built, seeded):
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.PermissionDenied):
        await E.save_operations(_NoDatabase(), _user(seeded, "a", role="viewer"), seeded["a"]["job"], client_revision=0,
                                ops=E.parse_operations(_set_text("req_sql", "x")), now=NOW)
    assert _row(built, seeded["a"]["job"]) == before


async def test_a_legacy_row_is_refused_and_nothing_is_written(built, engines, seeded):
    job = _job(built, seeded["b"], _doc(), marker=None)
    before = _row(built, job)
    with pytest.raises(E.NotRequirementsV2):
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)
    assert _row(built, job) == before


async def test_a_v2_row_without_a_document_is_refused(built, engines, seeded):
    job = _job(built, seeded["b"], _doc(), analysis={"extraction_notes": {}})
    with pytest.raises(E.NoRequirementsDocument):
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)


async def test_another_tenants_job_is_not_found_under_row_level_security(built, engines, seeded):
    """A non-owner login role, so row-level security applies: it cannot see or change another tenant's job."""
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.JobNotFound):
        await _save(engines(PROBE_ROLE), _user(seeded, "b"), seeded["a"]["job"], _set_text("req_sql", "x"), 0)
    assert _row(built, seeded["a"]["job"]) == before


async def test_the_explicit_tenant_filter_holds_even_when_row_level_security_is_bypassed(built, engines, seeded):
    """A superuser connection bypasses row-level security, so the service's tenant filter is the only guard here."""
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.JobNotFound):
        await _save(engines(), _user(seeded, "b"), seeded["a"]["job"], _set_text("req_sql", "x"), 0)
    assert _row(built, seeded["a"]["job"]) == before


async def test_the_owning_tenant_can_save_under_row_level_security(built, engines, seeded):
    """Positive control for the tests above: the same non-owner login role saves its own job."""
    result = await _save(engines(PROBE_ROLE), _user(seeded, "a"), seeded["a"]["job"], _set_text("req_sql", "SQL (PostgreSQL)"), 0)
    assert result.revision == 1


# ---- revision and writes ----

async def test_a_valid_save_writes_the_document_and_keeps_everything_else(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    await _save(engines(), _user(seeded, "a"), job, _set_text("req_sql", "SQL (PostgreSQL)"), 0)
    analysis, original, marker, revision, retired, weight_skills, last_edited_by = _row(built, job)
    assert analysis["requirements"]["categories"]["skills"]["items"][1]["text"] == "SQL (PostgreSQL)"
    assert analysis["extraction_notes"] == {"model": "fixture-model"}
    assert original == before[1]
    assert (marker, revision, retired, weight_skills) == (2, 1, [], 100)
    assert str(last_edited_by) == seeded["a"]["user"]


async def test_every_accepted_save_increments_the_revision_by_exactly_one(built, engines, seeded):
    job = seeded["a"]["job"]
    await _save(engines(), _user(seeded, "a"), job, _set_text("req_sql", "SQL"), 0)
    result = await _save(engines(), _user(seeded, "a"), job, _set_text("req_sql", "SQL, again"), 1)
    assert result.revision == 2 and _row(built, job)[3] == 2


async def test_a_refused_save_writes_nothing_and_does_not_increment(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    with pytest.raises(E.InvalidRequirements):
        await _save(engines(), _user(seeded, "a"), job, [{"op": "set_item_weight", "item_id": "req_py", "weight": 70}], 0)
    assert _row(built, job) == before


async def test_a_stale_revision_is_a_conflict_and_writes_nothing(built, engines, seeded):
    job = _job(built, seeded["b"], _doc(), revision=3)
    before = _row(built, job)
    with pytest.raises(E.RevisionConflict):
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 2)
    assert _row(built, job) == before


async def test_a_corrupt_stored_document_is_refused_and_nothing_is_written(built, engines, seeded):
    broken = _doc()
    del broken["categories"]["skills"]
    job = _job(built, seeded["b"], broken)
    before = _row(built, job)
    with pytest.raises(E.StoredDocumentInvalid):
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)
    assert _row(built, job) == before


async def test_the_service_does_not_commit_on_its_callers_behalf(built, engines, seeded):
    """Until the caller commits, nothing is visible to another connection. The row lock is held by the open transaction."""
    job = _job(built, seeded["b"], _doc())
    async with AsyncSession(engines()) as session:
        await E.save_operations(session, _user(seeded, "b"), job, client_revision=0,
                                ops=E.parse_operations(_set_text("req_sql", "pending")), now=NOW)
        assert session.in_transaction()
        assert _row(built, job)[3] == 0, "another connection must not see an uncommitted save"
        await session.rollback()
    assert _row(built, job)[3] == 0


async def test_two_saves_on_one_job_are_serialised_by_the_row_lock(built, engines, seeded):
    """Save A holds the criteria row lock until its transaction commits. Save B, with the same client revision, must wait; once A
    commits, B reads revision 1 and is refused. Exactly one save takes effect."""
    job = _job(built, seeded["b"], _doc())
    user = _user(seeded, "b")
    engine = engines()
    second = None
    async with AsyncSession(engine) as session_a:
        async with session_a.begin():
            await E.save_operations(session_a, user, job, client_revision=0,
                                    ops=E.parse_operations(_set_text("req_sql", "A")), now=NOW)
            second = asyncio.create_task(_save(engines(), user, job, _set_text("req_sql", "B"), 0))
            await asyncio.sleep(0.5)
            assert not second.done(), "the second save must wait on the row lock"
    with pytest.raises(E.RevisionConflict):
        await asyncio.wait_for(second, timeout=30)
    analysis, _, _, revision, _, _, _ = _row(built, job)
    assert revision == 1
    assert analysis["requirements"]["categories"]["skills"]["items"][1]["text"] == "A"


async def test_a_failure_after_the_save_rolls_everything_back(built, engines, seeded):
    job = _job(built, seeded["b"], _doc())
    before = _row(built, job)
    with pytest.raises(RuntimeError, match="caller failed after the save"):
        async with AsyncSession(engines()) as session:
            async with session.begin():
                await E.save_operations(session, _user(seeded, "b"), job, client_revision=0,
                                        ops=E.parse_operations(_set_text("req_sql", "gone")), now=NOW)
                raise RuntimeError("caller failed after the save")
    assert _row(built, job) == before


# ---- protected and server-owned state ----

async def test_add_and_edit_keep_existing_provenance_and_protected_columns(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    body = [{"op": "set_text", "item_id": "req_py", "text": "Python 3"},
            {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
             "alternatives": None, "experience": None}]
    await _save(engines(), _user(seeded, "a"), job, body, 0)
    analysis, original, marker, _, retired, _, _ = _row(built, job)
    items = {i["id"]: i for i in analysis["requirements"]["categories"]["skills"]["items"]}
    assert (items["req_py"]["origin"], items["req_py"]["source_text"]) == ("stated", "Python")
    added = items[next(i for i in items if i not in {"req_py", "req_sql"})]
    assert (added["text"], added["origin"], added["source_text"]) == ("Docker", "recruiter_added", None)
    assert original == before[1] and marker == 2 and retired == []


async def test_a_removed_item_id_is_retired_and_kept_by_later_saves(built, engines, seeded):
    job = seeded["a"]["job"]
    await _save(engines(), _user(seeded, "a"), job, [{"op": "remove_item", "item_id": "req_sql"},
                                                    {"op": "set_item_weight", "item_id": "req_py", "weight": 100}], 0)
    await _save(engines(), _user(seeded, "a"), job, _set_text("req_py", "Python 3 only"), 1)
    assert _row(built, job)[4] == ["req_sql"]


async def test_a_structured_edit_is_recorded_in_the_stored_document(built, engines, seeded):
    job = seeded["a"]["job"]
    await _save(engines(), _user(seeded, "a"), job, [{"op": "set_alternatives", "item_id": "req_py",
                                                     "alternatives": ["Python", "CPython"]}], 0)
    records = _row(built, job)[0]["requirements"]["structure_review"]["records"]
    assert [(r["item_id"], r["kind"], r["user_id"]) for r in records] == [("req_py", "corrected", seeded["a"]["user"])]


async def test_an_open_structural_finding_is_kept_and_does_not_block_the_save(built, engines, seeded):
    doc = _doc()
    doc["structural_review"] = _block(["req_py", "req_sql"])
    job = _job(built, seeded["b"], doc)
    result = await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert result.open_structural_finding_ids == ("sf_1",)
    assert _row(built, job)[0]["requirements"]["structural_review"] == doc["structural_review"]


async def test_an_invalid_structural_review_is_refused_and_nothing_is_written(built, engines, seeded):
    doc = _doc()
    block = _block(["req_py"])
    block["findings"][0]["source_end"] += 2
    doc["structural_review"] = block
    job = _job(built, seeded["b"], doc)
    before = _row(built, job)
    with pytest.raises(E.InvalidRequirements) as caught:
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert [i.code for i in caught.value.issues] == [READINESS_INVALID]
    assert _row(built, job) == before


async def test_the_structural_review_is_checked_against_the_stored_job_description(built, engines, seeded):
    """A finding that matches the description is refused once the stored description no longer contains its text."""
    doc = _doc()
    doc["structural_review"] = _block(["req_py"])
    job = _job(built, seeded["b"], doc)
    cur = built.conn.cursor()
    cur.execute("UPDATE jobs SET description = %s WHERE job_id = %s", ("A completely different description.", job))
    with pytest.raises(E.InvalidRequirements) as caught:
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert [i.code for i in caught.value.issues] == [READINESS_INVALID]


async def test_a_client_cannot_supply_a_structural_review_block(built, engines, seeded):
    """Operations are the only input. A block smuggled in as a field is refused before any write."""
    doc = _doc()
    doc["structural_review"] = _block(["req_py", "req_sql"])
    job = _job(built, seeded["b"], doc)
    before = _row(built, job)
    with pytest.raises(E.InvalidOperation):
        await _save(engines(), _user(seeded, "b"), job, [{"op": "set_text", "item_id": "req_sql", "text": "SQL",
                                                          "structural_review": {"schema_version": 1, "findings": []}}], 0)
    assert _row(built, job) == before


# ---- stored retired ids: malformed values refuse the save; valid values keep working ----

def _set_retired(built, job, raw_json):
    cur = built.conn.cursor()
    cur.execute("UPDATE job_criteria SET requirements_retired_item_ids = %s::jsonb WHERE job_id = %s", (raw_json, job))


def _audit(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT last_edited_by, last_edited_at, updated_at FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


@pytest.mark.parametrize("raw_json", [
    '[1]',                      # a non-string entry
    '[null]',
    '["bad id"]',               # not the requirement-id format
    '["req_a\\n"]',             # a trailing newline is not part of an id
    '["req_a", "req_a"]',       # a duplicate
    '["req_a", 2]',
])
async def test_malformed_stored_retired_ids_refuse_the_save_and_change_nothing(built, engines, seeded, raw_json):
    job = _job(built, seeded["b"], _doc())
    _set_retired(built, job, raw_json)
    before, audit_before = _row(built, job), _audit(built, job)
    with pytest.raises(E.StoredDocumentInvalid):
        await _save(engines(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)
    assert _row(built, job) == before, "document, revision, weights and retired ids must be unchanged"
    assert _audit(built, job) == audit_before, "the audit columns must be unchanged"


@pytest.mark.parametrize("raw_json", ['"req_1"', '{"req_1": true}', '7', 'null', None])
async def test_the_column_itself_refuses_values_that_are_not_a_list(built, seeded, raw_json):
    """Non-list values cannot be stored at all (NOT NULL and the array CHECK). The service refuses them too, for any other path."""
    job = seeded["a"]["job"]
    cur = built.conn.cursor()
    with pytest.raises(psycopg2.Error):
        cur.execute("UPDATE job_criteria SET requirements_retired_item_ids = %s::jsonb WHERE job_id = %s", (raw_json, job))
    assert _row(built, job)[4] == []


async def test_a_valid_stored_retired_list_keeps_working(built, engines, seeded):
    job = seeded["a"]["job"]
    _set_retired(built, job, '["req_old"]')
    await _save(engines(), _user(seeded, "a"), job, [{"op": "remove_item", "item_id": "req_sql"},
                                                    {"op": "set_item_weight", "item_id": "req_py", "weight": 100}], 0)
    assert _row(built, job)[4] == ["req_old", "req_sql"]
    await _save(engines(), _user(seeded, "a"), job, _set_text("req_py", "Python 3 only"), 1)
    assert _row(built, job)[4] == ["req_old", "req_sql"]


async def test_an_unsorted_valid_stored_list_is_normalised_when_saved(built, engines, seeded):
    job = seeded["a"]["job"]
    _set_retired(built, job, '["req_z", "req_a"]')
    result = await _save(engines(), _user(seeded, "a"), job, _set_text("req_py", "Python 3"), 0)
    assert result.revision == 1
    assert _row(built, job)[4] == ["req_a", "req_z"]
