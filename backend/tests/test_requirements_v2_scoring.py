"""Requirements v2 scoring inputs: the adapter, the readiness gate (offline) and the revision guard on a real PostgreSQL 16 chain."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from database import set_rls_context
from runtime_engines import async_engine, async_url
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services.requirements_v2 import contract as C
from services.requirements_v2.scoring import (
    PREFERRED_KEY, RequirementsRevisionChanged, ScoringNotReady, criteria_for_scoring, lock_revision, scoring_inputs,
    weights_for_scoring,
)
from services.requirements_v2.extraction import parse_response
from workers import requirements_v2_worker as worker

JD = "Senior Python developer.\nPython and SQL are required.\nFive years of backend experience is preferred."
# A Preferred item the description does not mark as optional ("valued" is not optional wording): the classification-warning fixture.
JD_UNCLEAR = JD.replace("is preferred", "is valued")


def _document(*, cue="preferred", extra_required=(), jd=JD):
    """A finalised document from the real parser and finaliser, for the job description above."""
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
        {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
        *extra_required,
    ]
    cats["experience"] = [{"text": "Backend experience", "importance": "preferred", "importance_cue": cue, "origin": "stated",
                           "source_text": "Five years of backend experience " + ("is preferred" if jd == JD else "is valued"),
                           "experience": {"subject": "backend experience", "min_years": 5}}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(raw), jd, "stop", id_factory=worker.stable_id_factory(jd))
    doc, _ = worker.finalise_document(worker.checked_document(result), jd)
    return doc, result.original


def _ready_document():
    return _document(cue="preferred")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        cur = db.conn.cursor()
        yield db


# ---- the adapter ----

def test_required_items_become_the_legacy_dimension_arrays_with_their_wording_and_structure():
    doc, _ = _ready_document()
    criteria = criteria_for_scoring(doc)
    assert criteria["skills"] == ["Python", "SQL"]
    assert criteria["experience"] == []                       # the preferred experience item is not a required criterion
    assert criteria[PREFERRED_KEY] == {"experience": ["Backend experience"]}


def test_alternatives_and_experience_are_part_of_the_required_criterion_wording():
    doc = copy.deepcopy(_ready_document()[0])
    doc["categories"]["skills"]["items"][0]["alternatives"] = ["Python", "Python 3"]
    doc["categories"]["experience"]["items"][0]["importance"] = "required"
    doc["categories"]["experience"]["items"][0]["weight"] = 100
    doc["categories"]["experience"]["items"][0]["alternatives"] = None
    criteria = criteria_for_scoring(doc)
    assert criteria["skills"][0] == "Python (one of: Python / Python 3)"
    assert criteria["experience"] == ["Backend experience (minimum 5 years of backend experience)"]


def test_weights_map_to_the_seven_weight_columns():
    doc, _ = _ready_document()
    weights = weights_for_scoring(doc)
    assert weights == {"weight_skills": 100, "weight_experience": 0, "weight_education": 0, "weight_certifications": 0,
                       "weight_soft_skills": 0, "weight_domain_knowledge": 0, "weight_other": 0}


def test_the_same_document_gives_the_same_inputs_every_time():
    doc, original = _ready_document()
    a = scoring_inputs(_row_json(doc), _row_json(original), JD, 4)
    b = scoring_inputs(_row_json(doc), _row_json(original), JD, 4)
    assert a == b and a.revision == 4


def _row_json(doc):
    return {"requirements": doc}


# ---- the readiness gate ----

def test_a_ready_document_is_scored():
    doc, original = _ready_document()
    inputs = scoring_inputs(_row_json(doc), _row_json(original), JD, 1)
    assert inputs.criteria["skills"] == ["Python", "SQL"]


def test_a_document_with_an_unacknowledged_classification_warning_is_refused():
    doc, original = _document(cue="nice to have", jd=JD_UNCLEAR)
    with pytest.raises(ScoringNotReady) as refused:
        scoring_inputs(_row_json(doc), _row_json(original), JD_UNCLEAR, 1)
    assert refused.value.state == "needs_classification_review"


def test_a_missing_or_invalid_document_is_refused():
    doc, original = _ready_document()
    with pytest.raises(ScoringNotReady) as missing:
        scoring_inputs({"other": 1}, _row_json(original), JD, 1)
    assert missing.value.state == "no_document"
    broken = copy.deepcopy(doc)
    broken["categories"]["skills"]["items"][0]["id"] = "bad id"
    with pytest.raises(ScoringNotReady) as invalid:
        scoring_inputs(_row_json(broken), _row_json(original), JD, 1)
    assert invalid.value.state == "invalid_document"


def test_an_open_structural_finding_refuses_scoring():
    doc, original = _ready_document()
    start = JD.index("Python and SQL are required")
    finding = {"id": "sf_1", "code": "alternatives_split_across_items",
               "item_ids": [doc["categories"]["skills"]["items"][0]["id"], doc["categories"]["skills"]["items"][1]["id"]],
               "source_text": "Python and SQL are required", "source_start": start, "source_end": start + len("Python and SQL are required"),
               "message": "Check these together.", "status": "open", "resolution": None}
    doc = {**doc, "structural_review": {"schema_version": 1, "findings": [finding]}}
    with pytest.raises(ScoringNotReady) as refused:
        scoring_inputs(_row_json(doc), _row_json(original), JD, 1)
    assert refused.value.state == "needs_structural_review"


# ---- the revision guard, on the real database ----

@pytest_asyncio.fixture()
async def engine(built):
    eng = async_engine(built, "cv_worker")
    yield eng
    await eng.dispose()


def _job_at_revision(built, revision) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                (f"t {uuid.uuid4().hex[:8]}", f"{uuid.uuid4().hex[:8]}.example"))
    tenant = cur.fetchone()[0]
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                (tenant, f"{uuid.uuid4().hex}@example.test"))
    user = cur.fetchone()[0]
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (tenant, user, JD))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, requirements_schema_version, requirements_revision, criteria_extraction_status)"
                " VALUES (%s, 2, %s, 'completed')", (job, revision))
    return job


@pytest.mark.asyncio
async def test_the_revision_guard_passes_for_the_current_revision_and_refuses_a_stale_one(built, engine):
    job = _job_at_revision(built, 3)
    async with AsyncSession(engine) as db:
        async with db.begin():
            await set_rls_context(db, "", "super_admin")
            await lock_revision(db, job, 3)
    async with AsyncSession(engine) as db:
        async with db.begin():
            await set_rls_context(db, "", "super_admin")
            with pytest.raises(RequirementsRevisionChanged):
                await lock_revision(db, job, 2)


@pytest.mark.asyncio
async def test_an_edit_waits_while_a_score_holds_the_revision_and_then_the_score_is_refused(built, engine):
    job = _job_at_revision(built, 5)
    scoring = AsyncSession(engine)
    await scoring.begin()
    await set_rls_context(scoring, "", "super_admin")
    await lock_revision(scoring, job, 5)            # the score is being written under revision 5

    async def edit():
        async with AsyncSession(engine) as db:
            async with db.begin():
                await set_rls_context(db, "", "super_admin")
                await db.execute(text("UPDATE job_criteria SET requirements_revision = requirements_revision + 1 WHERE job_id = :j"),
                                 {"j": job})

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(edit(), timeout=0.5)  # blocked by the score's share lock
    await scoring.commit()
    await scoring.close()
    await edit()                                    # now the edit proceeds
    async with AsyncSession(engine) as db:
        async with db.begin():
            await set_rls_context(db, "", "super_admin")
            with pytest.raises(RequirementsRevisionChanged):
                await lock_revision(db, job, 5)     # a later score at the old revision is refused
