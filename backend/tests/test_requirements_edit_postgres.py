"""The requirements-v2 editing service on a real PostgreSQL 16, built from the full current migration chain.

Covers: the role gate before any SQL, the schema-version gate, the optimistic revision check, the increment on every accepted
save, the locked read, serialisation of two saves on one row, rollback, tenant isolation (explicit filter and row-level
security under a non-owner role), protected columns, preserved analysis_json keys, append-only retired ids, the structured
edit record, and the structural review checked against the stored job description."""
from __future__ import annotations

import copy
import json
import threading
import uuid

import psycopg2
import pytest

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.structural_review import CODE_ALTERNATIVES_SPLIT, READINESS_INVALID

PROBE_ROLE = "cv_edit_probe"
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
    """Stands in for a connection. Any attempt to run SQL through it fails the test."""

    def cursor(self):
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
        cur.execute(f"CREATE ROLE {PROBE_ROLE} NOLOGIN")
        cur.execute(f"GRANT USAGE ON SCHEMA cv_analyzer TO {PROBE_ROLE}")
        cur.execute(f"GRANT SELECT, UPDATE ON cv_analyzer.jobs, cv_analyzer.job_criteria TO {PROBE_ROLE}")
        yield db


@pytest.fixture()
def connect(built):
    """Connection factory. A role, when given, is set for the session so row-level security applies to it. Every
    connection is closed at the end of the test so the database can be dropped cleanly."""
    made = []

    def make(role: str | None = None):
        conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="postgres", dbname=built.name,
                                options="-csearch_path=cv_analyzer")
        if role:
            conn.autocommit = True
            conn.cursor().execute(f"SET ROLE {role}")
            conn.autocommit = False
        made.append(conn)
        return conn

    yield make
    for conn in made:
        conn.close()


@pytest.fixture()
def seeded(built):
    """Two tenants, each with an admin user and a job. The job holds the base document, the original analysis is the
    same document, and one unrelated analysis key must survive every save."""
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


def _save(conn, user, job, body, revision):
    """One save in its own transaction: committed on success, rolled back on any refusal."""
    try:
        result = E.save_operations(conn, user, job, client_revision=revision, ops=E.parse_operations(body), now=NOW)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def _set_text(item_id, text_):
    return [{"op": "set_text", "item_id": item_id, "text": text_}]


# ---- gates before any write ----

def test_a_viewer_is_refused_before_any_sql_runs(built, seeded):
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.PermissionDenied):
        E.save_operations(_NoDatabase(), _user(seeded, "a", role="viewer"), seeded["a"]["job"], client_revision=0,
                          ops=E.parse_operations(_set_text("req_sql", "x")), now=NOW)
    assert _row(built, seeded["a"]["job"]) == before


def test_a_legacy_row_is_refused_and_nothing_is_written(built, connect, seeded):
    job = _job(built, seeded["b"], _doc(), marker=None)
    before = _row(built, job)
    with pytest.raises(E.NotRequirementsV2):
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)
    assert _row(built, job) == before


def test_a_v2_row_without_a_document_is_refused(built, connect, seeded):
    job = _job(built, seeded["b"], _doc(), analysis={"extraction_notes": {}})
    with pytest.raises(E.NoRequirementsDocument):
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)


def test_another_tenants_job_is_not_found_under_row_level_security(built, connect, seeded):
    """A non-owner role (row-level security applies) cannot see or change another tenant's job."""
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.JobNotFound):
        _save(connect(PROBE_ROLE), _user(seeded, "b"), seeded["a"]["job"], _set_text("req_sql", "x"), 0)
    assert _row(built, seeded["a"]["job"]) == before


def test_the_explicit_tenant_filter_holds_even_when_row_level_security_is_bypassed(built, connect, seeded):
    """A superuser connection bypasses row-level security, so the tenant filter in the service is the only guard here."""
    before = _row(built, seeded["a"]["job"])
    with pytest.raises(E.JobNotFound):
        _save(connect(), _user(seeded, "b"), seeded["a"]["job"], _set_text("req_sql", "x"), 0)
    assert _row(built, seeded["a"]["job"]) == before


def test_the_owning_tenant_can_save_under_row_level_security(built, connect, seeded):
    """Positive control for the tests above: the same non-owner role saves its own job."""
    result = _save(connect(PROBE_ROLE), _user(seeded, "a"), seeded["a"]["job"], _set_text("req_sql", "SQL (PostgreSQL)"), 0)
    assert result.revision == 1


# ---- revision and writes ----

def test_a_valid_save_writes_the_document_and_keeps_everything_else(built, connect, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    _save(connect(), _user(seeded, "a"), job, _set_text("req_sql", "SQL (PostgreSQL)"), 0)
    analysis, original, marker, revision, retired, weight_skills, last_edited_by = _row(built, job)
    assert analysis["requirements"]["categories"]["skills"]["items"][1]["text"] == "SQL (PostgreSQL)"
    assert analysis["extraction_notes"] == {"model": "fixture-model"}
    assert original == before[1]
    assert (marker, revision, retired, weight_skills) == (2, 1, [], 100)
    assert str(last_edited_by) == seeded["a"]["user"]


def test_every_accepted_save_increments_the_revision_by_exactly_one(built, connect, seeded):
    job = seeded["a"]["job"]
    _save(connect(), _user(seeded, "a"), job, _set_text("req_sql", "SQL"), 0)
    result = _save(connect(), _user(seeded, "a"), job, _set_text("req_sql", "SQL, again"), 1)
    assert result.revision == 2 and _row(built, job)[3] == 2


def test_a_refused_save_writes_nothing_and_does_not_increment(built, connect, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    with pytest.raises(E.InvalidRequirements):
        _save(connect(), _user(seeded, "a"), job, [{"op": "set_item_weight", "item_id": "req_py", "weight": 70}], 0)
    assert _row(built, job) == before


def test_a_stale_revision_is_a_conflict_and_writes_nothing(built, connect, seeded):
    job = _job(built, seeded["b"], _doc(), revision=3)
    before = _row(built, job)
    with pytest.raises(E.RevisionConflict):
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 2)
    assert _row(built, job) == before


def test_a_corrupt_stored_document_is_refused_and_nothing_is_written(built, connect, seeded):
    broken = _doc()
    del broken["categories"]["skills"]
    job = _job(built, seeded["b"], broken)
    before = _row(built, job)
    with pytest.raises(E.StoredDocumentInvalid):
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "x"), 0)
    assert _row(built, job) == before


def test_two_saves_on_one_job_are_serialised_by_the_row_lock(built, connect, seeded):
    """Save A holds the criteria row lock without committing. Save B, with the same client revision, must wait; once A
    commits, B reads revision 1 and is refused. Exactly one save takes effect."""
    job = _job(built, seeded["b"], _doc())
    user = _user(seeded, "b")
    results: list = []

    def second_save():
        try:
            results.append(_save(connect(), user, job, _set_text("req_sql", "B"), 0))
        except E.RevisionConflict as exc:
            results.append(exc)

    conn_a = connect()
    E.save_operations(conn_a, user, job, client_revision=0, ops=E.parse_operations(_set_text("req_sql", "A")), now=NOW)
    worker = threading.Thread(target=second_save)
    worker.start()
    worker.join(timeout=0.5)
    assert worker.is_alive(), "the second save must wait on the row lock"
    conn_a.commit()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], E.RevisionConflict)
    analysis, _, _, revision, _, _, _ = _row(built, job)
    assert revision == 1
    assert analysis["requirements"]["categories"]["skills"]["items"][1]["text"] == "A"


def test_a_failure_after_the_save_rolls_everything_back(built, connect, seeded):
    job = _job(built, seeded["b"], _doc())
    before = _row(built, job)
    conn = connect()
    with pytest.raises(RuntimeError, match="caller failed after the save"):
        try:
            E.save_operations(conn, _user(seeded, "b"), job, client_revision=0,
                              ops=E.parse_operations(_set_text("req_sql", "gone")), now=NOW)
            raise RuntimeError("caller failed after the save")
        finally:
            conn.rollback()
    assert _row(built, job) == before


# ---- protected and server-owned state ----

def test_add_and_edit_keep_existing_provenance_and_protected_columns(built, connect, seeded):
    job = seeded["a"]["job"]
    before = _row(built, job)
    body = [{"op": "set_text", "item_id": "req_py", "text": "Python 3"},
            {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
             "alternatives": None, "experience": None}]
    _save(connect(), _user(seeded, "a"), job, body, 0)
    analysis, original, marker, _, retired, _, _ = _row(built, job)
    items = {i["id"]: i for i in analysis["requirements"]["categories"]["skills"]["items"]}
    assert (items["req_py"]["origin"], items["req_py"]["source_text"]) == ("stated", "Python")
    added = items[next(i for i in items if i not in {"req_py", "req_sql"})]
    assert (added["text"], added["origin"], added["source_text"]) == ("Docker", "recruiter_added", None)
    assert original == before[1] and marker == 2 and retired == []


def test_a_removed_item_id_is_retired_and_kept_by_later_saves(built, connect, seeded):
    job = seeded["a"]["job"]
    _save(connect(), _user(seeded, "a"), job, [{"op": "remove_item", "item_id": "req_sql"},
                                              {"op": "set_item_weight", "item_id": "req_py", "weight": 100}], 0)
    _save(connect(), _user(seeded, "a"), job, _set_text("req_py", "Python 3 only"), 1)
    assert _row(built, job)[4] == ["req_sql"]


def test_a_structured_edit_is_recorded_in_the_stored_document(built, connect, seeded):
    job = seeded["a"]["job"]
    _save(connect(), _user(seeded, "a"), job, [{"op": "set_alternatives", "item_id": "req_py",
                                                "alternatives": ["Python", "CPython"]}], 0)
    records = _row(built, job)[0]["requirements"]["structure_review"]["records"]
    assert [(r["item_id"], r["kind"], r["user_id"]) for r in records] == [("req_py", "corrected", seeded["a"]["user"])]


def test_an_open_structural_finding_is_kept_and_does_not_block_the_save(built, connect, seeded):
    doc = _doc()
    doc["structural_review"] = _block(["req_py", "req_sql"])
    job = _job(built, seeded["b"], doc)
    result = _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert result.open_structural_finding_ids == ("sf_1",)
    assert _row(built, job)[0]["requirements"]["structural_review"] == doc["structural_review"]


def test_an_invalid_structural_review_is_refused_and_nothing_is_written(built, connect, seeded):
    doc = _doc()
    block = _block(["req_py"])
    block["findings"][0]["source_end"] += 2
    doc["structural_review"] = block
    job = _job(built, seeded["b"], doc)
    before = _row(built, job)
    with pytest.raises(E.InvalidRequirements) as caught:
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert [i.code for i in caught.value.issues] == [READINESS_INVALID]
    assert _row(built, job) == before


def test_the_structural_review_is_checked_against_the_stored_job_description(built, connect, seeded):
    """A finding that matches the description is refused once the stored description no longer contains its text."""
    doc = _doc()
    doc["structural_review"] = _block(["req_py"])
    job = _job(built, seeded["b"], doc)
    cur = built.conn.cursor()
    cur.execute("UPDATE jobs SET description = %s WHERE job_id = %s", ("A completely different description.", job))
    with pytest.raises(E.InvalidRequirements) as caught:
        _save(connect(), _user(seeded, "b"), job, _set_text("req_sql", "SQL"), 0)
    assert [i.code for i in caught.value.issues] == [READINESS_INVALID]


def test_a_client_cannot_supply_a_structural_review_block(built, connect, seeded):
    """Operations are the only input. A block smuggled in as a field is refused before any write."""
    doc = _doc()
    doc["structural_review"] = _block(["req_py", "req_sql"])
    job = _job(built, seeded["b"], doc)
    before = _row(built, job)
    with pytest.raises(E.InvalidOperation):
        _save(connect(), _user(seeded, "b"), job, [{"op": "set_text", "item_id": "req_sql", "text": "SQL",
                                                    "structural_review": {"schema_version": 1, "findings": []}}], 0)
    assert _row(built, job) == before
