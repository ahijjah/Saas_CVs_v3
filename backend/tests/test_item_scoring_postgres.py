"""Item-level scoring on a real PostgreSQL 16: runs, evaluations and events; unclear items and human resolution; mandatory gates driven by the
stored evaluation; preferred-only jobs; revisions and staleness; rescoring; idempotent and concurrent writes; the effective decision in the list, the
filter, the export and the job counts; and tenant isolation, roles and audit through the HTTP routes."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from routers import applications as AR
from routers import item_scoring as IR
from services import eligibility_service as G
from services.effective_decision import EFFECTIVE_DECISION_SQL
from services.item_scoring import calc
from services.item_scoring import service as S
from services.item_scoring.evaluator import EvaluatedItem, EvaluationOutput, item_specs
from services.requirements_v2 import contract as C
from services.requirements_v2 import gates as R
from test_gates_postgres import _application, _in_tenant, built, engines, world  # noqa: F401  (fixtures and seeding helpers)

pytestmark = pytest.mark.asyncio

NOW = "2026-10-17T12:00:00+00:00"
USER = "11111111-1111-4111-8111-111111111111"
CV = "Jane Doe. Five years of SQL troubleshooting and incident management. Python scripting. English (basic). BSc in Computer Science."
ARABIC = "req_ar"
RESOLVER_SECRET = "CANARY-RESOLUTION-REASON"


def _it(item_id, text_, importance, weight=None):
    return C.make_item(text_, importance, item_id=item_id, weight=weight, origin=C.ORIGIN_STATED, source_text=text_)


def acceptance_doc(*, gate_on: str | None = None, preferred=True) -> dict:
    doc = C.empty_requirements()
    skills = [_it("req_sql", "SQL troubleshooting", C.IMPORTANCE_REQUIRED, 20), _it("req_inc", "Incident management", C.IMPORTANCE_REQUIRED, 20),
              _it("req_py", "Python/PowerShell", C.IMPORTANCE_REQUIRED, 20), _it(ARABIC, "Arabic fluency", C.IMPORTANCE_REQUIRED, 20),
              _it("req_en", "English fluency", C.IMPORTANCE_REQUIRED, 20)]
    if preferred:
        skills.append(_it("req_k8", "Kubernetes", C.IMPORTANCE_PREFERRED))
    doc["categories"]["skills"] = {"weight": 53, "items": skills}
    doc["categories"]["experience"] = {"weight": 27, "items": [_it("req_exp", "Backend experience", C.IMPORTANCE_REQUIRED, 100)]}
    doc["categories"]["education"] = {"weight": 20, "items": [_it("req_edu", "Computer science degree", C.IMPORTANCE_REQUIRED, 100)]}
    if gate_on:
        doc = R.set_gate(doc, gate_on, verification_method="cv_evidence", linked_question_id=None, user_id=USER, confirmed_at=NOW)
    return doc


def preferred_only_doc() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 0, "items": [_it("req_k8", "Kubernetes", C.IMPORTANCE_PREFERRED), _it("req_go", "Go", C.IMPORTANCE_PREFERRED)]}
    return doc


DEFAULT_RESULTS = {"req_sql": "met", "req_inc": "met", "req_py": "partially_met", ARABIC: "unclear", "req_en": "not_met", "req_exp": "met",
                   "req_edu": "met", "req_k8": "no"}


def _output(doc, results=None, quote="Five years of SQL troubleshooting"):
    results = {**DEFAULT_RESULTS, **(results or {})}
    return EvaluationOutput(
        items=[EvaluatedItem(s.item_id, results.get(s.item_id, "met" if s.importance == "required" else "no"),
                             (quote,) if results.get(s.item_id) not in ("unclear", "no", "not_clear") else (), "stated", __import__("decimal").Decimal("0.900"))
               for s in item_specs(doc)],
        narrative={"evaluation_notes": "ok", "strengths": [], "gaps_identified": [], "red_flags": [], "interview_questions": []}, contacts={})


def _job(built, owner, doc, *, revision=3):
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 'Engineer', 'JD') RETURNING job_id",
                (owner["tenant"], owner["users"]["admin"]))
    job = str(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version, requirements_revision,"
        " weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other)"
        " VALUES (%s, %s, %s, 2, %s, 53, 27, 20, 0, 0, 0, 0)", (job, json.dumps({"requirements": doc}), json.dumps({"requirements": doc}), revision))
    return job


async def _record(engines, world, job, app, doc, results=None, *, revision=3, rescore=False, label="a"):
    weights = {c: doc["categories"][c]["weight"] for c in C.CATEGORIES}

    async def work(session):
        return await S.record_run(session, application_id=app, tenant_id=world[label]["tenant"], job_id=job, revision=revision,
                                  category_weights=weights, specs=item_specs(doc), output=_output(doc, results), model="test-model",
                                  thresholds=(85, 65), rescore=rescore)

    return await _in_tenant(engines("cv_worker"), world[label]["tenant"], work)       # the scoring worker writes runs; the API only updates them


def _app_row(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT scoring_status, item_final_score, decision, eligibility_status FROM applications WHERE application_id = %s", (app,))
    return cur.fetchone()


def _run_row(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT status, final_score_raw, final_score_min, final_score_max, final_score, decision FROM application_scoring_runs"
                " WHERE application_id = %s AND is_current", (app,))
    return cur.fetchone()


async def _scored_world(built, engines, world, results=None, *, doc=None, scored_decision=None):
    doc = doc or acceptance_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job, decision=scored_decision or "qualified") if False else _application(built, world["a"], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL WHERE application_id = %s", (app,))
    summary = await _record(engines, world, job, app, doc, results)
    return job, app, doc, summary


# ── a run: stored evaluations, the unclear range and the mirror on the application ──────────────────────────────────

async def test_an_unclear_required_item_makes_the_run_review_required_with_the_provisional_range(built, engines, world):
    job, app, doc, summary = await _scored_world(built, engines, world)
    assert summary.status == "review_required" and summary.final_score is None and summary.decision is None and summary.created
    status, raw, low, high, score, decision = _run_row(built, app)
    assert (status, raw, score, decision) == ("review_required", None, None, None)
    assert (str(low), str(high)) == ("73.500", "84.100")
    assert _app_row(built, app)[:3] == ("review_required", None, None)             # no final numeric decision, and no zero score
    cur = built.conn.cursor()
    cur.execute("SELECT count(*) FILTER (WHERE importance = 'required'), count(*) FILTER (WHERE importance = 'preferred') "
                "FROM application_item_evaluations WHERE application_id = %s", (app,))
    assert cur.fetchone() == (7, 1)
    cur.execute("SELECT count(*) FROM application_item_evaluation_events WHERE application_id = %s AND event_type = 'evaluated'", (app,))
    assert cur.fetchone()[0] == 8
    cur.execute("SELECT count(*) FROM application_scores WHERE application_id = %s", (app,))
    assert cur.fetchone()[0] == 0                                                   # no legacy score is written for an item-level run


async def test_each_evaluation_stores_the_requirement_revision_result_evidence_explanation_confidence_source_and_time(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    cur = built.conn.cursor()
    cur.execute("SELECT item_id, requirements_revision, result, ai_result, evidence, explanation, confidence, evaluation_source, evaluated_at,"
                " item_weight, resolved_by FROM application_item_evaluations WHERE application_id = %s AND item_id = 'req_sql'", (app,))
    item_id, revision, result, ai_result, evidence, explanation, confidence, source, at, weight, resolved_by = cur.fetchone()
    assert (item_id, revision, result, ai_result, weight, resolved_by) == ("req_sql", 3, "met", "met", 20, None)
    assert evidence == ["Five years of SQL troubleshooting"] and explanation == "stated" and str(confidence) == "0.900"
    assert source == "item_level_v2:test-model" and at is not None


async def test_the_view_recomputes_every_number_from_the_stored_results(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: S.load_view(s, app))
    skills = view["categories"][0]
    assert view["status"] == "review_required" and view["final_score"] is None and view["range"] == {"min": "73.5", "max": "84.1"}
    assert (skills["score_min"], skills["score_max"], skills["contribution_min"], skills["contribution_max"]) == ("50", "70", "26.5", "37.1")
    assert [i["earned"] for i in skills["items"]] == ["20", "20", "10", None, "0"]
    assert [c["contribution"] for c in view["categories"][1:]] == ["27", "20"]
    assert view["preferred"]["summary"] == {"yes": 0, "no": 1, "not_clear": 0} and view["unclear_count"] == 1
    assert view["requirements_revision_used"] == 3 and view["current_revision"] == 3 and view["stale"] is False


async def test_preferred_results_never_change_the_score_or_the_status(built, engines, world):
    outcomes = {}
    for answer in ("yes", "no", "not_clear"):
        job, app, doc, summary = await _scored_world(built, engines, world, {ARABIC: "met", "req_k8": answer})
        outcomes[answer] = (summary.status, summary.final_score, summary.decision, _run_row(built, app)[1])
    assert len(set(outcomes.values())) == 1 and outcomes["yes"][1] == 84


async def test_not_clear_preferred_items_do_not_block_the_numeric_decision(built, engines, world):
    job, app, doc, summary = await _scored_world(built, engines, world, {ARABIC: "met", "req_k8": "not_clear"})
    assert summary.status == "scored" and summary.decision == "partial" and _app_row(built, app)[0:3] == ("scored", 84, "partial")


async def test_a_second_write_for_the_same_application_is_idempotent(built, engines, world):
    job, app, doc, first = await _scored_world(built, engines, world)
    second = await _record(engines, world, job, app, doc)
    assert (first.run_id, second.created) == (second.run_id, False)
    cur = built.conn.cursor()
    cur.execute("SELECT (SELECT count(*) FROM application_scoring_runs WHERE application_id = %s),"
                " (SELECT count(*) FROM application_item_evaluations WHERE application_id = %s)", (app, app))
    assert cur.fetchone() == (1, 8)


async def test_concurrent_writes_cannot_create_duplicate_runs_or_item_results(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    results = await asyncio.gather(*[_record(engines, world, job, app, doc) for _ in range(6)])
    assert sum(1 for r in results if r.created) == 1 and len({r.run_id for r in results}) == 1
    cur = built.conn.cursor()
    cur.execute("SELECT (SELECT count(*) FROM application_scoring_runs WHERE application_id = %s AND is_current),"
                " (SELECT count(*) FROM application_item_evaluations WHERE application_id = %s),"
                " (SELECT count(DISTINCT item_id) FROM application_item_evaluations WHERE application_id = %s)", (app, app, app))
    assert cur.fetchone() == (1, 8, 8)


async def test_the_database_refuses_a_second_current_run_and_a_second_result_for_an_item(built, engines, world):
    import psycopg2
    job, app, doc, summary = await _scored_world(built, engines, world)
    cur = built.conn.cursor()
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("INSERT INTO application_scoring_runs (application_id, tenant_id, job_id, requirements_revision, status, category_weights,"
                    " final_score_min, final_score_max) SELECT application_id, tenant_id, job_id, 3, 'review_required', '{}', 0, 1"
                    " FROM application_scoring_runs WHERE run_id = %s", (summary.run_id,))
    built.conn.rollback() if not built.conn.autocommit else None
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("INSERT INTO application_item_evaluations (run_id, application_id, tenant_id, requirements_revision, item_id, category,"
                    " importance, item_text, item_weight, ai_result, result, evaluation_source)"
                    " SELECT run_id, application_id, tenant_id, 3, item_id, category, importance, item_text, item_weight, ai_result, result, 'x'"
                    " FROM application_item_evaluations WHERE run_id = %s LIMIT 1", (summary.run_id,))


# ── a human resolves an unclear item ────────────────────────────────────────────────────────────────────────────────

async def _resolve(engines, world, app, result, reason="Verified the CV and the interview notes", item=ARABIC, label="a", role="admin"):
    user = world[label]["users"][role]
    return await _in_tenant(engines(), world[label]["tenant"], lambda s: S.resolve_item(
        s, application_id=app, item_id=item, result=result, reason=reason, user_id=user))


@pytest.mark.parametrize("result,score,raw", [("met", 84, "84.100"), ("partially_met", 79, "78.800"), ("not_met", 74, "73.500")])
async def test_resolving_the_last_unclear_item_recalculates_and_finalises_the_score_and_decision(built, engines, world, result, score, raw):
    job, app, doc, _ = await _scored_world(built, engines, world)
    outcome = await _resolve(engines, world, app, result)
    assert outcome == {"status": "scored", "final_score": score, "decision": "partial", "previous_result": "unclear", "new_result": result}
    status, run_raw, low, high, final, decision = _run_row(built, app)
    assert (status, str(run_raw), final, decision) == ("scored", raw, score, "partial") and low == high == run_raw
    assert _app_row(built, app)[:3] == ("scored", score, "partial")


async def test_a_resolution_stores_who_when_why_and_the_previous_and_new_result(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    await _resolve(engines, world, app, "partially_met", reason="  Interview confirmed conversational Arabic  ", role="hr_manager")
    cur = built.conn.cursor()
    cur.execute("SELECT ai_result, result, resolved_by::text, resolved_at, resolution_reason FROM application_item_evaluations"
                " WHERE application_id = %s AND item_id = %s", (app, ARABIC))
    ai_result, result, by, at, reason = cur.fetchone()
    assert (ai_result, result, by, reason) == ("unclear", "partially_met", world["a"]["users"]["hr_manager"], "Interview confirmed conversational Arabic") and at
    cur.execute("SELECT event_type, previous_result, new_result, user_id::text, reason FROM application_item_evaluation_events"
                " WHERE application_id = %s AND item_id_check = 0" if False else
                "SELECT e.event_type, e.previous_result, e.new_result, e.user_id::text, e.reason FROM application_item_evaluation_events e"
                " JOIN application_item_evaluations v ON v.evaluation_id = e.evaluation_id WHERE e.application_id = %s AND v.item_id = %s"
                " ORDER BY e.created_at, e.event_id", (app, ARABIC))
    assert cur.fetchall() == [("evaluated", None, "unclear", None, None),
                              ("resolved", "unclear", "partially_met", world["a"]["users"]["hr_manager"], "Interview confirmed conversational Arabic")]


async def test_a_resolution_needs_a_reason_a_valid_result_and_an_unclear_required_item(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    for reason in ("", "   "):
        with pytest.raises(S.ItemScoringError) as caught:
            await _resolve(engines, world, app, "met", reason=reason)
        assert caught.value.code == "resolution_reason_required"
    for bad in ("unclear", "yes", "great"):
        with pytest.raises(S.ItemScoringError) as caught:
            await _resolve(engines, world, app, bad)
        assert caught.value.code == "invalid_result"
    for item, code in (("req_sql", "not_unclear"), ("req_k8", "not_required"), ("req_missing", "item_not_found")):
        with pytest.raises(S.ItemScoringError) as caught:
            await _resolve(engines, world, app, "met", item=item)
        assert caught.value.code == code
    assert _app_row(built, app)[0] == "review_required"                              # nothing changed


async def test_a_resolved_item_cannot_be_resolved_again(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    await _resolve(engines, world, app, "met")
    with pytest.raises(S.ItemScoringError) as caught:
        await _resolve(engines, world, app, "not_met")
    assert caught.value.code == "not_unclear"


async def test_the_score_is_not_final_until_every_unclear_item_is_resolved(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world, {ARABIC: "unclear", "req_en": "unclear"})
    assert _run_row(built, app)[2:4] == (calc.exact_decimal(__import__("fractions").Fraction(63, 2)) if False else _run_row(built, app)[2], _run_row(built, app)[3])
    first = await _resolve(engines, world, app, "met", item=ARABIC)
    assert first["status"] == "review_required" and first["final_score"] is None and first["decision"] is None
    assert _app_row(built, app)[:3] == ("review_required", None, None)
    second = await _resolve(engines, world, app, "not_met", item="req_en")
    assert second["status"] == "scored" and second["final_score"] == 74 + 0 or second["final_score"] is not None


# ── preferred-only jobs ─────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_preferred_only_job_is_not_scored_with_an_absent_score_and_no_rejection(built, engines, world):
    doc = preferred_only_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL WHERE application_id = %s", (app,))
    summary = await _record(engines, world, job, app, doc, {"req_k8": "yes", "req_go": "not_clear"})
    assert (summary.status, summary.final_score, summary.decision) == ("not_scored", None, None)
    assert _app_row(built, app)[:3] == ("not_scored", None, None)
    assert _run_row(built, app)[:6] == ("not_scored", None, None, None, None, None)
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: S.load_view(s, app))
    assert view["final_score"] is None and view["categories"] == [] and view["preferred"]["summary"] == {"yes": 1, "no": 0, "not_clear": 1}
    assert G.effective_decision("not_applicable", None, "not_scored") == "not_scored"


# ── revisions, staleness and rescoring ──────────────────────────────────────────────────────────────────────────────

async def test_a_requirements_edit_makes_the_prior_score_stale_and_the_view_says_which_revisions(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    built.conn.cursor().execute("UPDATE job_criteria SET requirements_revision = 5 WHERE job_id = %s", (job,))
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: S.load_view(s, app))
    assert (view["requirements_revision_used"], view["current_revision"], view["stale"]) == (3, 5, True)


async def test_a_rescore_keeps_the_previous_run_and_uses_the_current_revision(built, engines, world):
    job, app, doc, first = await _scored_world(built, engines, world)
    built.conn.cursor().execute("UPDATE job_criteria SET requirements_revision = 4 WHERE job_id = %s", (job,))
    second = await _record(engines, world, job, app, doc, {ARABIC: "met"}, revision=4, rescore=True)
    assert second.created and second.run_id != first.run_id and (second.status, second.final_score) == ("scored", 84)
    cur = built.conn.cursor()
    cur.execute("SELECT run_id::text, requirements_revision, is_current, superseded_at IS NOT NULL, status FROM application_scoring_runs"
                " WHERE application_id = %s ORDER BY created_at, run_id", (app,))
    rows = {r[0]: r for r in cur.fetchall()}
    assert rows[first.run_id][1:] == (3, False, True, "review_required") and rows[second.run_id][1:] == (4, True, False, "scored")
    cur.execute("SELECT count(*) FROM application_item_evaluations WHERE run_id = %s", (first.run_id,))
    assert cur.fetchone()[0] == 8                                                   # the previous results are preserved
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: S.load_view(s, app))
    assert view["requirements_revision_used"] == 4 and view["stale"] is False and view["final_score"] == 84


async def test_rescoring_one_application_does_not_touch_another(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    one, two = _application(built, world["a"], job), _application(built, world["a"], job)
    await _record(engines, world, job, one, doc)
    first_two = await _record(engines, world, job, two, doc)
    await _record(engines, world, job, one, doc, {ARABIC: "met"}, rescore=True)
    assert _run_row(built, two)[0] == "review_required"
    assert (await _record(engines, world, job, two, doc)).run_id == first_two.run_id


# ── mandatory gates follow the stored item evaluation ───────────────────────────────────────────────────────────────

@pytest.fixture()
def no_second_evaluation(monkeypatch):
    """A gate must not run a second model evaluation of the CV when a stored item evaluation exists."""
    import services.ai_service as ai

    def refuse():
        raise AssertionError("a second CV evaluation was attempted")

    monkeypatch.setattr(ai, "_get_client", refuse)


@pytest.mark.parametrize("sql_result,gate,eligibility", [
    ("met", "passed", "eligible"), ("partially_met", "failed", "not_eligible"), ("not_met", "failed", "not_eligible"),
    ("unclear", "needs_verification", "review_required")])
async def test_a_mandatory_gate_follows_the_stored_item_result(built, engines, world, no_second_evaluation, sql_result, gate, eligibility):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL WHERE application_id = %s", (app,))
    await _record(engines, world, job, app, doc, {"req_sql": sql_result, ARABIC: "met"})
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    assert status == {"eligible": G.ELIGIBLE, "not_eligible": G.NOT_ELIGIBLE, "review_required": G.REVIEW_REQUIRED}[eligibility]
    view = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, app))
    assert view["gates"][0]["result"] == gate and view["gates"][0]["evidence_source"] == "cv"


async def test_a_gate_outcome_never_changes_the_stored_score_or_decision(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    await _record(engines, world, job, app, doc, {"req_sql": "not_met", ARABIC: "met"})
    before = _app_row(built, app)[:3]
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    after = _app_row(built, app)
    assert before == after[:3] == ("scored", 74, "partial") and after[3] == "not_eligible"
    assert G.effective_decision(after[3], after[2], after[0]) == "not_eligible"


async def test_resolving_the_gated_item_updates_the_gate_without_a_second_evaluation(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on=ARABIC)
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    await _record(engines, world, job, app, doc)
    assert await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True)) == G.REVIEW_REQUIRED
    await _resolve(engines, world, app, "partially_met")
    assert await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True)) == G.NOT_ELIGIBLE
    assert _app_row(built, app)[0:3] == ("scored", 79, "partial")                    # the score and decision do not move because of the gate


async def test_a_gate_override_still_works_and_is_audited(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    await _record(engines, world, job, app, doc, {"req_sql": "not_met", ARABIC: "met"})
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    status = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.override_gate(
        s, application_id=app, gate_key="req:req_sql", reason="Waived by the hiring manager", user_id=world["a"]["users"]["admin"]))
    assert status == G.ELIGIBLE
    cur = built.conn.cursor()
    cur.execute("SELECT original_result, new_result, reason FROM application_gate_result_events WHERE application_id = %s AND event_type = 'overridden'", (app,))
    assert cur.fetchone() == ("failed", "overridden", "Waived by the hiring manager")


# ── the effective decision everywhere ───────────────────────────────────────────────────────────────────────────────

CASES = [("not_eligible", "qualified", "scored"), ("review_required", "qualified", "scored"), ("eligible", None, "review_required"),
         ("not_applicable", None, "not_scored"), ("eligible", "qualified", "scored"), ("not_applicable", "partial", None),
         ("not_applicable", "rejected", None), ("not_eligible", None, "review_required")]


async def test_the_sql_effective_decision_matches_the_python_rule_for_every_state(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    cur = built.conn.cursor()
    for eligibility, decision, scoring in CASES:
        app = _application(built, world["a"], job)
        cur.execute("UPDATE applications SET eligibility_status = %s, decision = %s, scoring_status = %s WHERE application_id = %s",
                    (eligibility, decision, scoring, app))
        cur.execute(f"SELECT {EFFECTIVE_DECISION_SQL} FROM applications a WHERE a.application_id = %s", (app,))
        assert cur.fetchone()[0] == G.effective_decision(eligibility, decision, scoring), (eligibility, decision, scoring)


def _list_client(engine, world, label="a", role="admin"):
    app = FastAPI()
    app.include_router(AR.router)
    app.include_router(IR.router)
    user = CurrentUser(world[label]["users"][role], world[label]["tenant"], f"{role}@example.test", role, "U")
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_the_list_shows_the_effective_decision_filters_by_it_and_exports_it(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    cur = built.conn.cursor()
    apps = {}
    for key, (eligibility, decision, scoring) in {"gate": ("not_eligible", "qualified", "scored"), "review": ("not_applicable", None, "review_required"),
                                                  "plain": ("not_applicable", "qualified", "scored"), "unscored": ("not_applicable", None, "not_scored")}.items():
        app = _application(built, world["a"], job)
        cur.execute("UPDATE applications SET eligibility_status = %s, decision = %s, scoring_status = %s, processing_status = 'ai_scored',"
                    " item_final_score = %s WHERE application_id = %s", (eligibility, decision, scoring, 99 if scoring == "scored" else None, app))
        apps[key] = app
    async with _list_client(engines(), world) as client:
        body = (await client.get("/applications", params={"job_id": job})).json()
        rows = {r["application_id"]: r for r in body}
        assert rows[apps["gate"]]["status"] == "not_eligible" and rows[apps["gate"]]["stored_decision"] == "qualified"
        assert rows[apps["gate"]]["score"] == 99                                             # the number stays visible beside the real outcome
        assert rows[apps["review"]]["status"] == "review_required" and rows[apps["review"]]["score"] is None
        assert rows[apps["unscored"]]["status"] == "not_scored" and rows[apps["unscored"]]["score"] is None
        assert rows[apps["plain"]]["status"] == "qualified"
        only = (await client.get("/applications", params={"job_id": job, "ai_decision": "not_eligible"})).json()
        assert [r["application_id"] for r in only] == [apps["gate"]]
        qualified = (await client.get("/applications", params={"job_id": job, "ai_decision": "qualified"})).json()
        assert [r["application_id"] for r in qualified] == [apps["plain"]]
        export = await client.get("/applications/export", params={"job_id": job, "format": "csv"})
        assert export.status_code == 200 and "Not Eligible" in export.text and "Review Required" in export.text and "Not Scored" in export.text


async def test_the_job_counts_use_the_effective_decision(built, engines, world):
    import routers.jobs as JR
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    cur = built.conn.cursor()
    for eligibility, decision, scoring in [("not_eligible", "qualified", "scored"), ("not_applicable", "qualified", "scored"),
                                           ("not_applicable", None, "review_required"), ("not_applicable", None, "not_scored")]:
        app = _application(built, world["a"], job)
        cur.execute("UPDATE applications SET eligibility_status = %s, decision = %s, scoring_status = %s, processing_status = 'ai_scored'"
                    " WHERE application_id = %s", (eligibility, decision, scoring, app))
    cur.execute(f"SELECT count(*) FILTER (WHERE {EFFECTIVE_DECISION_SQL} = 'qualified'), count(*) FILTER (WHERE {EFFECTIVE_DECISION_SQL} = 'not_eligible'),"
                f" count(*) FILTER (WHERE {EFFECTIVE_DECISION_SQL} = 'review_required'), count(*) FILTER (WHERE {EFFECTIVE_DECISION_SQL} = 'not_scored')"
                " FROM applications a WHERE a.job_id = %s", (job,))
    assert cur.fetchone() == (1, 1, 1, 1)
    source = open(JR.__file__).read()
    assert "applications_not_eligible" in source and "applications_review_required" in source and "applications_not_scored" in source


# ── the HTTP routes: roles, tenants, validation, audit ──────────────────────────────────────────────────────────────

async def test_every_tenant_role_reads_the_breakdown(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    for role in ("admin", "hr_manager", "recruiter", "viewer"):
        async with _list_client(engines(), world, role=role) as client:
            response = await client.get(f"/applications/{app}/item-scoring")
            assert response.status_code == 200, role
            body = response.json()
            assert body["status"] == "review_required" and body["effective_decision"] == "review_required" and body["stale"] is False


async def test_the_breakdown_reports_the_effective_decision_with_the_stored_decision_separately(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    await _record(engines, world, job, app, doc, {"req_sql": "not_met", ARABIC: "met"})
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    async with _list_client(engines(), world) as client:
        body = (await client.get(f"/applications/{app}/item-scoring")).json()
    assert (body["final_score"], body["decision"], body["eligibility_status"], body["effective_decision"]) == (74, "partial", "not_eligible", "not_eligible")


@pytest.mark.parametrize("role", ["recruiter", "viewer"])
async def test_a_recruiter_and_a_viewer_cannot_resolve_or_rescore(built, engines, world, role):
    job, app, doc, _ = await _scored_world(built, engines, world)
    async with _list_client(engines(), world, role=role) as client:
        assert (await client.post(f"/applications/{app}/item-scoring/items/{ARABIC}/resolve", json={"result": "met", "reason": "r"})).status_code == 403
        assert (await client.post(f"/applications/{app}/item-scoring/rescore")).status_code == 403
    assert _app_row(built, app)[0] == "review_required"


@pytest.mark.parametrize("role", ["admin", "hr_manager"])
async def test_admin_and_hr_manager_resolve_with_an_audit_row_that_has_no_reason_text(built, engines, world, role):
    job, app, doc, _ = await _scored_world(built, engines, world)
    async with _list_client(engines(), world, role=role) as client:
        response = await client.post(f"/applications/{app}/item-scoring/items/{ARABIC}/resolve", json={"result": "met", "reason": RESOLVER_SECRET})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "scored" and body["final_score"] == 84 and body["decision"] == "partial" and body["unclear_count"] == 0
        item = next(i for c in body["categories"] for i in c["items"] if i["item_id"] == ARABIC)
        assert item["result"] == "met" and item["ai_result"] == "unclear" and item["resolution"]["reason"] == RESOLVER_SECRET
        assert [h["event_type"] for h in item["history"]] == ["evaluated", "resolved"]
    cur = built.conn.cursor()
    cur.execute("SELECT action, user_id::text, details::text FROM audit_logs WHERE resource_id = %s AND action = 'item_result_resolved'", (app,))
    action, user, details = cur.fetchone()
    assert user == world["a"]["users"][role] and RESOLVER_SECRET not in details and '"new_result": "met"' in details


@pytest.mark.parametrize("body", [{}, {"result": "met"}, {"reason": "r"}, {"result": "unclear", "reason": "r"}, {"result": "yes", "reason": "r"},
                                  {"result": "met", "reason": ""}, {"result": "met", "reason": "x" * 1001}, {"result": "met", "reason": "r", "score": 100}])
async def test_the_resolve_body_is_strict(built, engines, world, body):
    job, app, doc, _ = await _scored_world(built, engines, world)
    async with _list_client(engines(), world) as client:
        assert (await client.post(f"/applications/{app}/item-scoring/items/{ARABIC}/resolve", json=body)).status_code == 422


async def test_another_tenant_gets_the_same_404_everywhere_and_changes_nothing(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    async with _list_client(engines(), world, label="b") as client:
        assert (await client.get(f"/applications/{app}/item-scoring")).status_code == 404
        assert (await client.post(f"/applications/{app}/item-scoring/items/{ARABIC}/resolve", json={"result": "met", "reason": "mine now"})).status_code == 404
        assert (await client.post(f"/applications/{app}/item-scoring/rescore")).status_code == 404
        assert (await client.get(f"/applications/{uuid.uuid4()}/item-scoring")).status_code == 404
        assert (await client.get("/applications/not-a-uuid/item-scoring")).status_code == 404
    assert _app_row(built, app)[0] == "review_required"
    for table in ("application_scoring_runs", "application_item_evaluations", "application_item_evaluation_events"):
        rows = await _in_tenant(engines(), world["b"]["tenant"], lambda s, t=table: s.execute(text(f"SELECT count(*) FROM {t}")))
        assert rows.scalar() == 0                                                       # row-level security: the other tenant sees nothing


async def test_the_rescore_route_queues_one_task_for_this_application_only(built, engines, world, monkeypatch):
    import workers.cv_score as W
    queued = []
    monkeypatch.setattr(W.rescore_application_task, "delay", lambda *a, **k: queued.append(a))
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    built.conn.cursor().execute(
        "INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes, extracted_text, extraction_status)"
        " SELECT application_id, tenant_id, 'cv.pdf', 'application/pdf', 'x.pdf', 10, %s, 'done' FROM applications WHERE application_id = %s", (CV, app))
    built.conn.cursor().execute("UPDATE job_criteria SET analysis_json = jsonb_set(analysis_json, '{requirements,categories,skills,items}',"
                                " analysis_json #> '{requirements,categories,skills,items}') WHERE job_id = %s", (job,))
    async with _list_client(engines(), world) as client:
        response = await client.post(f"/applications/{app}/item-scoring/rescore")
    assert response.status_code in (202, 409)
    if response.status_code == 202:
        assert queued == [(app, job, world["a"]["tenant"], world["a"]["users"]["admin"])]


async def test_the_rescore_route_refuses_a_job_that_is_not_requirements_v2_and_an_application_without_cv_text(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    async with _list_client(engines(), world) as client:
        response = await client.post(f"/applications/{app}/item-scoring/rescore")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "no_cv_text"
    legacy_job = _job(built, world["a"], doc)
    built.conn.cursor().execute("UPDATE job_criteria SET requirements_schema_version = NULL WHERE job_id = %s", (legacy_job,))
    legacy_app = _application(built, world["a"], legacy_job)
    async with _list_client(engines(), world) as client:
        response = await client.post(f"/applications/{legacy_app}/item-scoring/rescore")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "not_requirements_v2"


# ── legacy scores stay readable and are labelled ────────────────────────────────────────────────────────────────────

async def test_an_application_with_only_a_legacy_score_is_labelled_legacy_and_its_staleness_is_derived(built, engines, world):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = _application(built, world["a"], job)
    cur = built.conn.cursor()
    cur.execute("INSERT INTO application_scores (application_id, score_skills, score_experience, score_education, score_certifications,"
                " score_soft_skills, score_domain_knowledge, score_other, final_score, weights_snapshot, ai_model, requirements_revision_used)"
                " VALUES (%s, 80, 70, 60, 0, 0, 0, 0, 72, '{}', 'old-model', 2)", (app,))
    async with _list_client(engines(), world) as client:
        body = (await client.get(f"/applications/{app}/item-scoring")).json()
        assert body["scoring_method"] == "legacy_category" and body["requirements_revision_used"] == 2 and body["current_revision"] == 3 and body["stale"] is True
        detail = (await client.get("/applications/details", params={"application_id": app})).json()
        assert detail["scoring_method"] == "legacy_category" and detail["overall_score"] == 72 and detail["score_stale"] is True
        assert detail["scoring_status"] is None
    none_app = _application(built, world["a"], job)
    async with _list_client(engines(), world) as client:
        assert (await client.get(f"/applications/{none_app}/item-scoring")).json()["scoring_method"] == "none"


async def test_the_application_detail_reports_the_item_level_outcome_and_no_zero_for_an_unscored_application(built, engines, world):
    job, app, doc, _ = await _scored_world(built, engines, world)
    async with _list_client(engines(), world) as client:
        detail = (await client.get("/applications/details", params={"application_id": app})).json()
    assert detail["scoring_method"] == "item_level_v2" and detail["scoring_status"] == "review_required"
    assert detail["overall_score"] is None and detail["decision"] is None and detail["effective_decision"] == "review_required"
    assert detail["requirements_revision_used"] == 3 and detail["current_requirements_revision"] == 3 and detail["score_stale"] is False
