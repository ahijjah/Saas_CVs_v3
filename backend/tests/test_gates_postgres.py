"""Gates / knockout criteria on a real PostgreSQL 16: migration 102 (defaults, constraints, row-level security, upgrade and rollback), the
eligibility service end to end (answers, CV outcomes, recorded results, overrides and their history), and tenant isolation. Login roles are
used wherever row-level security must apply; the superuser connection bypasses it and is used only to seed and to read back."""
from __future__ import annotations

import copy
import json
import pathlib
import uuid

import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from runtime_engines import async_engine
from services import eligibility_service as G
from services.requirements_v2 import contract as C
from services.requirements_v2 import gates as R

pytestmark = pytest.mark.asyncio

BACKEND = pathlib.Path(__file__).resolve().parent.parent
UP = (BACKEND / "db/migrations/102_gates_knockout_criteria.sql").read_text(encoding="utf-8")
DOWN = (BACKEND / "db/rollback/102_gates_knockout_criteria.down.sql").read_text(encoding="utf-8")
NOW = "2026-10-17T12:00:00+00:00"
SECRET_CV_TEXT = "CANARY-CV-CONTENT"


def _item(item_id, text_, importance, weight=None):
    return C.make_item(text_, importance, item_id=item_id, weight=weight, origin=C.ORIGIN_STATED, source_text=text_)


def _doc(*gates) -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        _item("req_py", "Python", C.IMPORTANCE_REQUIRED, 60),
        _item("req_sql", "SQL", C.IMPORTANCE_REQUIRED, 40),
        _item("req_go", "Go", C.IMPORTANCE_PREFERRED),
    ]}
    for item_id, kw in gates:
        args = dict(verification_method="cv_evidence", failure_action="not_eligible", linked_question_id=None,
                    user_id="11111111-1111-4111-8111-111111111111", confirmed_at=NOW)
        args.update(kw)
        doc = R.set_gate(doc, item_id, **args)
    return doc


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
def world(built):
    """Two tenants, each with an admin, an HR manager and a viewer; tenant A also has a job."""
    cur = built.conn.cursor()
    out = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t{label}{tag}", f"{label}{tag}.example"))
        tenant = str(cur.fetchone()[0])
        users = {}
        for role in ("admin", "hr_manager", "recruiter", "viewer"):
            cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', %s, %s) RETURNING user_id",
                        (tenant, f"{label}-{role}-{tag}@example.test", f"{role} {label}", role))
            users[role] = str(cur.fetchone()[0])
        out[label] = {"tenant": tenant, "users": users}
    return out


def _job(built, owner, doc=None, *, decision_text="t") -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, %s, 'JD') RETURNING job_id",
                (owner["tenant"], owner["users"]["admin"], decision_text))
    job = str(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version, requirements_revision,"
        " weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other)"
        " VALUES (%s, %s, %s, 2, 3, 100, 0, 0, 0, 0, 0, 0)",
        (job, json.dumps({"requirements": doc or _doc()}), json.dumps({"requirements": _doc()})))
    return job


def _question(built, owner, job, *, gate=False, action="not_eligible", qtype="yes_no", criteria=None, text_="Valid licence?", options=None) -> str:
    cur = built.conn.cursor()
    criteria = criteria if criteria is not None else {"passing_answers": ["yes"]}
    cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, question_type, passing_criteria, options,"
                " is_eligibility_gate, gate_failure_action) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING question_id",
                (job, owner["tenant"], text_, qtype, json.dumps(criteria), json.dumps(options) if options else None, gate, action))
    return str(cur.fetchone()[0])


def _application(built, owner, job, *, decision="qualified") -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                " processing_status, decision) VALUES (%s, %s, 'Jane', %s, '+100', 'public_apply', 'scored', %s)"
                " RETURNING application_id", (job, owner["tenant"], f"{uuid.uuid4().hex[:8]}@example.test", decision))
    return str(cur.fetchone()[0])


def _answer(built, app, question, value):
    built.conn.cursor().execute("INSERT INTO application_knockout_answers (application_id, tenant_id, question_id, answer_value)"
                                " SELECT application_id, tenant_id, %s, %s FROM applications WHERE application_id = %s", (question, value, app))


def _app_row(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT eligibility_status, decision FROM applications WHERE application_id = %s", (app,))
    return cur.fetchone()


async def _in_tenant(engine, tenant, work):
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant})
        result = await work(session)
        await session.commit()
        return result


# ── migration 102 ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_an_existing_style_question_is_advisory_by_default(built, world):
    job = _job(built, world["a"])
    cur = built.conn.cursor()
    cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text) VALUES (%s, %s, 'Old question') "
                "RETURNING is_eligibility_gate, gate_failure_action", (job, world["a"]["tenant"]))
    assert cur.fetchone() == (False, "not_eligible")


async def test_an_existing_application_is_not_applicable_and_its_score_and_decision_are_untouched(built, world):
    job = _job(built, world["a"])
    app = _application(built, world["a"], job)
    assert _app_row(built, app) == ("not_applicable", "qualified")


@pytest.mark.parametrize("sql", [
    "UPDATE job_knockout_questions SET gate_failure_action = 'reject' WHERE false OR job_id = %s",
    "UPDATE applications SET eligibility_status = 'maybe' WHERE job_id = %s",
])
async def test_the_database_refuses_unknown_enumerations(built, world, sql):
    job = _job(built, world["a"])
    _question(built, world["a"], job)
    _application(built, world["a"], job)
    cur = built.conn.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(sql, (job,))
    built.conn.rollback() if not built.conn.autocommit else None


async def test_an_override_event_without_a_reason_or_user_is_refused_by_the_database(built, world):
    job = _job(built, world["a"], _doc(("req_py", {"verification_method": "manual"})))
    app = _application(built, world["a"], job)
    cur = built.conn.cursor()
    cur.execute("INSERT INTO application_gate_results (application_id, tenant_id, job_id, gate_key, gate_source, gate_label, verification_method,"
                " failure_action, computed_result, result) VALUES (%s, %s, %s, 'req:req_py', 'requirement', 'Python', 'manual',"
                " 'not_eligible', 'failed', 'failed') RETURNING gate_result_id", (app, world["a"]["tenant"], job))
    gid = cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO application_gate_result_events (gate_result_id, application_id, tenant_id, event_type, original_result,"
                    " new_result) VALUES (%s, %s, %s, 'overridden', 'failed', 'overridden')", (gid, app, world["a"]["tenant"]))


async def test_a_result_row_cannot_claim_overridden_without_a_reason(built, world):
    job = _job(built, world["a"])
    app = _application(built, world["a"], job)
    cur = built.conn.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO application_gate_results (application_id, tenant_id, job_id, gate_key, gate_source, gate_label,"
                    " verification_method, failure_action, computed_result, result) VALUES (%s, %s, %s, 'req:x', 'requirement', 'x',"
                    " 'manual', 'not_eligible', 'failed', 'overridden')", (app, world["a"]["tenant"], job))


async def test_both_new_tables_have_row_level_security_enforced_with_tenant_and_worker_policies(built):
    cur = built.conn.cursor()
    cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('application_gate_results', 'application_gate_result_events')")
    assert dict(cur.fetchall()) == {"application_gate_results": True, "application_gate_result_events": True}
    cur.execute("SELECT tablename, policyname FROM pg_policies WHERE tablename IN ('application_gate_results', 'application_gate_result_events')")
    names = {p for _, p in cur.fetchall()}
    assert {"tenant_scope_application_gate_results", "tenant_scope_application_gate_result_events",
            "cv_worker_access_application_gate_results", "cv_worker_access_application_gate_result_events"} <= names


async def test_the_migration_is_idempotent(built):
    cur = built.conn.cursor()
    cur.execute(UP)
    cur.execute(UP)
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE column_name IN ('is_eligibility_gate', 'eligibility_status')")
    assert cur.fetchone()[0] == 2


async def test_rollback_removes_exactly_what_102_added_and_loses_no_application_data():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        cur = db.conn.cursor()
        tag = uuid.uuid4().hex[:8]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"rb{tag}", f"rb{tag}.example"))
        tenant = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                    (tenant, f"rb{tag}@example.test"))
        user = str(cur.fetchone()[0])
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd') RETURNING job_id", (tenant, user))
        job = str(cur.fetchone()[0])
        cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, is_eligibility_gate) VALUES (%s, %s, 'q', true) "
                    "RETURNING question_id", (job, tenant))
        question = str(cur.fetchone()[0])
        cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                    " processing_status, decision) VALUES (%s, %s, 'J', 'j@example.test', '+1', 'public_apply', 'scored',"
                    " 'qualified') RETURNING application_id", (job, tenant))
        app = str(cur.fetchone()[0])
        cur.execute(DOWN)
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE column_name IN ('is_eligibility_gate', 'gate_failure_action',"
                    " 'eligibility_status', 'eligibility_evaluated_at')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'application_gate_result%'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT decision FROM applications WHERE application_id = %s", (app,))
        assert cur.fetchone() == ("qualified",)
        cur.execute("SELECT question_text FROM job_knockout_questions WHERE question_id = %s", (question,))
        assert cur.fetchone() == ("q",)
        cur.execute(DOWN)                                   # a second run changes nothing and does not fail
        cur.execute(UP)                                     # and the upgrade can be applied again afterwards
        cur.execute("SELECT is_eligibility_gate FROM job_knockout_questions WHERE question_id = %s", (question,))
        assert cur.fetchone() == (False,)                   # re-applying never turns a question into a gate
        cur.execute("SELECT eligibility_status FROM applications WHERE application_id = %s", (app,))
        assert cur.fetchone() == ("not_applicable",)


# ── the evaluation ──────────────────────────────────────────────────────────────────────────────────────────────────

async def _refresh(engines, built, owner, app, **kw):
    return await _in_tenant(engines("cv_app"), owner["tenant"], lambda s: G.refresh_application(s, app, **kw))


async def test_an_explicit_failing_answer_to_a_gate_question_makes_the_candidate_not_eligible_whatever_the_score(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=True, action="not_eligible")
    app = _application(built, world["a"], job, decision="qualified")
    _answer(built, app, q, "no")
    assert await _refresh(engines, built, world["a"], app) == G.NOT_ELIGIBLE
    assert _app_row(built, app) == ("not_eligible", "qualified")           # the score and its decision are untouched


async def test_a_passing_answer_is_eligible_and_normal_progression_is_allowed(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=True)
    app = _application(built, world["a"], job)
    _answer(built, app, q, "yes")
    assert await _refresh(engines, built, world["a"], app) == G.ELIGIBLE


async def test_a_missing_answer_is_needs_verification_and_review_required_never_a_rejection(built, engines, world):
    job = _job(built, world["a"])
    _question(built, world["a"], job, gate=True)
    app = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], app) == G.REVIEW_REQUIRED


async def test_a_failed_hold_for_review_gate_gives_review_required(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=True, action="hold_for_review")
    app = _application(built, world["a"], job)
    _answer(built, app, q, "no")
    assert await _refresh(engines, built, world["a"], app) == G.REVIEW_REQUIRED


async def test_an_advisory_question_never_affects_eligibility_even_with_a_failing_answer(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=False)
    app = _application(built, world["a"], job)
    _answer(built, app, q, "no")
    assert await _refresh(engines, built, world["a"], app) == G.NOT_APPLICABLE


async def test_a_job_with_no_gates_is_not_applicable(built, engines, world):
    job = _job(built, world["a"])
    app = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], app) == G.NOT_APPLICABLE


async def test_a_requirement_gate_on_a_question_is_judged_from_the_linked_question_once(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=True, action="hold_for_review")
    cur = built.conn.cursor()
    doc = _doc(("req_py", {"verification_method": "candidate_question", "linked_question_id": q, "failure_action": "not_eligible"}))
    cur.execute("UPDATE job_criteria SET analysis_json = %s WHERE job_id = %s", (json.dumps({"requirements": doc}), job))
    app = _application(built, world["a"], job)
    _answer(built, app, q, "no")
    assert await _refresh(engines, built, world["a"], app) == G.NOT_ELIGIBLE        # the requirement's action governs, not the question's
    cur.execute("SELECT gate_key FROM application_gate_results WHERE application_id = %s", (app,))
    assert [r[0] for r in cur.fetchall()] == ["req:req_py"]


async def test_a_cv_evidence_gate_with_no_check_yet_is_not_assessed_and_the_candidate_needs_review(built, engines, world):
    job = _job(built, world["a"], _doc(("req_py", {})))
    app = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], app) == G.REVIEW_REQUIRED


async def test_cv_outcomes_drive_the_requirement_gate_and_uncertain_evidence_never_rejects(built, engines, world):
    job = _job(built, world["a"], _doc(("req_py", {})))
    uncertain = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], uncertain,
                          cv_outcomes={"req:req_py": {"result": G.NEEDS_VERIFICATION, "confidence": 0.4, "summary": "unclear"}}) == G.REVIEW_REQUIRED
    contradicted = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], contradicted,
                          cv_outcomes={"req:req_py": {"result": G.FAILED, "confidence": 0.95, "summary": "states the opposite"}}) == G.NOT_ELIGIBLE
    met = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], met,
                          cv_outcomes={"req:req_py": {"result": G.PASSED, "confidence": 0.9, "summary": "evidence"}}) == G.ELIGIBLE


async def test_refreshing_again_keeps_a_cv_result_and_is_idempotent(built, engines, world):
    job = _job(built, world["a"], _doc(("req_py", {})))
    app = _application(built, world["a"], job)
    await _refresh(engines, built, world["a"], app, cv_outcomes={"req:req_py": {"result": G.PASSED, "confidence": 0.9, "summary": "ok"}})
    assert await _refresh(engines, built, world["a"], app) == G.ELIGIBLE
    assert await _refresh(engines, built, world["a"], app) == G.ELIGIBLE


async def test_a_suggestion_alone_enforces_nothing(built, engines, world):
    doc = _doc()
    doc["categories"]["skills"]["items"][0]["gate_suggestion"] = {"suggested": True, "cue": "must", "source_text": None}
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    assert await _refresh(engines, built, world["a"], app) == G.NOT_APPLICABLE


# ── recorded results (assessment, manual) and overrides ─────────────────────────────────────────────────────────────

async def _manual_world(built, engines, world, **gate):
    job = _job(built, world["a"], _doc(("req_py", {"verification_method": "manual", **gate})))
    app = _application(built, world["a"], job)
    await _refresh(engines, built, world["a"], app)
    return job, app


async def test_a_manual_gate_waits_for_a_recorded_result_then_follows_it(built, engines, world):
    _, app = await _manual_world(built, engines, world)
    assert _app_row(built, app)[0] == "review_required"
    admin = world["a"]["users"]["admin"]
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
        s, application_id=app, gate_key="req:req_py", result="failed", summary="Checked the original licence", user_id=admin))
    assert status == G.NOT_ELIGIBLE
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
        s, application_id=app, gate_key="req:req_py", result="passed", summary="Licence confirmed", user_id=admin))
    assert status == G.ELIGIBLE


async def test_an_assessment_gate_can_have_its_result_recorded_later(built, engines, world):
    job = _job(built, world["a"], _doc(("req_py", {"verification_method": "assessment"})))
    app = _application(built, world["a"], job)
    await _refresh(engines, built, world["a"], app)
    assert _app_row(built, app)[0] == "review_required"
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
        s, application_id=app, gate_key="req:req_py", result="passed", summary=None, user_id=world["a"]["users"]["admin"]))
    assert status == G.ELIGIBLE


async def test_a_result_cannot_be_recorded_for_a_cv_or_question_gate_or_with_an_invalid_value(built, engines, world):
    job = _job(built, world["a"], _doc(("req_py", {})))
    app = _application(built, world["a"], job)
    await _refresh(engines, built, world["a"], app)
    admin = world["a"]["users"]["admin"]
    with pytest.raises(G.EligibilityError) as caught:
        await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
            s, application_id=app, gate_key="req:req_py", result="passed", summary=None, user_id=admin))
    assert caught.value.code == "not_recordable"
    _, manual_app = await _manual_world(built, engines, world)
    with pytest.raises(G.EligibilityError) as caught:
        await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
            s, application_id=manual_app, gate_key="req:req_py", result="overridden", summary=None, user_id=admin))
    assert caught.value.code == "invalid_result"


async def test_an_override_needs_a_reason_and_stores_original_new_user_and_time(built, engines, world):
    _, app = await _manual_world(built, engines, world)
    admin = world["a"]["users"]["admin"]
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.record_result(
        s, application_id=app, gate_key="req:req_py", result="failed", summary="no licence", user_id=admin))
    for blank in ("", "   "):
        with pytest.raises(G.EligibilityError) as caught:
            await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.override_gate(
                s, application_id=app, gate_key="req:req_py", reason=blank, user_id=admin))
        assert caught.value.code == "override_reason_required"
    assert _app_row(built, app)[0] == "not_eligible"
    hr = world["a"]["users"]["hr_manager"]
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.override_gate(
        s, application_id=app, gate_key="req:req_py", reason="Licence verified in person", user_id=hr))
    assert status == G.ELIGIBLE
    cur = built.conn.cursor()
    cur.execute("SELECT event_type, original_result, new_result, user_id::text, reason, created_at FROM application_gate_result_events "
                "WHERE application_id = %s AND event_type = 'overridden'", (app,))
    event_type, original, new, user, reason, at = cur.fetchone()
    assert (event_type, original, new, user, reason) == ("overridden", "failed", "overridden", hr, "Licence verified in person") and at
    cur.execute("SELECT computed_result, result, overridden_by::text FROM application_gate_results WHERE application_id = %s", (app,))
    assert cur.fetchone() == ("failed", "overridden", hr)                            # the original result is preserved


async def test_an_override_cannot_be_applied_twice_and_can_be_withdrawn_with_history(built, engines, world):
    _, app = await _manual_world(built, engines, world)
    admin = world["a"]["users"]["admin"]
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.override_gate(
        s, application_id=app, gate_key="req:req_py", reason="Waived by the hiring manager", user_id=admin))
    with pytest.raises(G.EligibilityError) as caught:
        await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.override_gate(
            s, application_id=app, gate_key="req:req_py", reason="again", user_id=admin))
    assert caught.value.code == "already_overridden"
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.clear_override(
        s, application_id=app, gate_key="req:req_py", user_id=admin))
    assert status == G.REVIEW_REQUIRED
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, app))
    [gate] = view["gates"]
    assert [e["event_type"] for e in gate["history"]][-2:] == ["overridden", "override_cleared"]
    assert gate["override_reason"] is None


async def test_the_gate_view_has_the_status_the_gates_and_the_effective_decision_but_no_candidate_text(built, engines, world):
    job = _job(built, world["a"])
    q = _question(built, world["a"], job, gate=True)
    app = _application(built, world["a"], job, decision="qualified")
    _answer(built, app, q, "no")
    await _refresh(engines, built, world["a"], app)
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, app))
    assert view["eligibility_status"] == "not_eligible" and view["effective_decision"] == "not_eligible"
    assert view["gates"][0]["result"] == "failed" and view["gates"][0]["evidence_source"] == "candidate_answer"
    assert "final_score" not in view and SECRET_CV_TEXT not in json.dumps(view)


# ── tenant isolation ────────────────────────────────────────────────────────────────────────────────────────────────

async def test_another_tenant_cannot_see_or_change_gate_results(built, engines, world):
    job, app = await _manual_world(built, engines, world)
    other = world["b"]
    rows = await _in_tenant(engines(), other["tenant"], lambda s: s.execute(text("SELECT count(*) FROM application_gate_results")))
    assert rows.scalar() == 0
    events = await _in_tenant(engines(), other["tenant"], lambda s: s.execute(text("SELECT count(*) FROM application_gate_result_events")))
    assert events.scalar() == 0
    with pytest.raises(G.EligibilityError) as caught:
        await _in_tenant(engines(), other["tenant"], lambda s: G.override_gate(
            s, application_id=app, gate_key="req:req_py", reason="not mine", user_id=other["users"]["admin"]))
    assert caught.value.status == 404
    with pytest.raises(G.EligibilityError):
        await _in_tenant(engines(), other["tenant"], lambda s: G.refresh_application(s, app))
    assert await _in_tenant(engines(), other["tenant"], lambda s: G.gate_view(s, app)) is None


async def test_without_a_tenant_context_nothing_is_visible(built, engines, world):
    await _manual_world(built, engines, world)
    async with AsyncSession(engines()) as session:
        assert (await session.execute(text("SELECT count(*) FROM application_gate_results"))).scalar() == 0


async def test_upgrading_a_database_with_existing_required_questions_converts_none_of_them_into_gates():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        cur = db.conn.cursor()
        cur.execute(DOWN)                                   # the state before 102, with the data a production job already has
        tag = uuid.uuid4().hex[:8]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"up{tag}", f"up{tag}.example"))
        tenant = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                    (tenant, f"up{tag}@example.test"))
        user = str(cur.fetchone()[0])
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd') RETURNING job_id", (tenant, user))
        job = str(cur.fetchone()[0])
        for required in (True, False):
            cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text, is_required, passing_criteria)"
                        " VALUES (%s, %s, 'q', %s, '{\"passing_answers\": [\"yes\"]}')", (job, tenant, required))
        cur.execute(UP)
        cur.execute("SELECT is_required, is_eligibility_gate, gate_failure_action FROM job_knockout_questions WHERE job_id = %s ORDER BY is_required", (job,))
        assert cur.fetchall() == [(False, False, "not_eligible"), (True, False, "not_eligible")]
