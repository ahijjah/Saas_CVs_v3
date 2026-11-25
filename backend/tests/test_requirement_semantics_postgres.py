"""Requirement semantics on a real PostgreSQL 16: the model's per-option assessment is combined by code, stored with the matched option and
every option's result, shown by the scoring view, drives the gate without ever failing it on ambiguity or a conditional alternative, and shows
the same criteria and outcomes in the Candidate Matrix. A criterion has one weight, and a document without any of this behaves exactly as before.

The criteria are from unrelated fields (a site supervisor, a ward nurse) so the behavior is shown not to depend on any one domain."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from services import eligibility_service as G
from services.item_scoring import evaluator as V
from services.item_scoring import service as S
from services.item_scoring.evaluator import EvaluationOutput, item_specs
from services.requirements_v2 import contract as C
from services.requirements_v2 import gates as R
from services.requirements_v2 import logic as L
from test_candidate_matrix_postgres import _client, by_name, rows
from test_gates_postgres import _application, _in_tenant, built, engines, world  # noqa: F401
from test_item_scoring_postgres import no_second_evaluation  # noqa: F401

pytestmark = pytest.mark.asyncio

USER = "11111111-1111-4111-8111-111111111111"
NOW = "2026-11-25T12:00:00+00:00"
CV = ("Candidate CV. Holds a diploma in civil engineering. Completed a bachelor's degree in mechanical engineering and then two years on construction sites. "
      "Ten years of site experience, no formal diploma. Uses spreadsheets for scheduling. Registered with the national nursing council. "
      "Completed the paediatric course. Speaks fluent English.")


def _opt(text_, acceptance="direct", all_of=None):
    return {"text": text_, "acceptance": acceptance, "all_of": all_of}


def semantic_doc(*, gate_on_route: bool = True) -> dict:
    doc = C.empty_requirements()
    route = L.apply(C.make_item("Diploma, or a degree with site experience", "required", item_id="req_route", weight=34, origin=C.ORIGIN_STATED,
                                source_text="a diploma, or a degree together with site experience"),
                    L.normalize({"options": [_opt("Diploma in civil engineering"),
                                             _opt("Degree with two years on construction sites", all_of=["Bachelor's degree in an engineering field", "Two years on construction sites"]),
                                             _opt("Long site experience without a diploma", "review")]})[0])
    course = L.apply(C.make_item("Either life support or paediatric course", "required", item_id="req_course", weight=33, origin=C.ORIGIN_STATED,
                                 source_text="either the life support course or the paediatric course"),
                     L.normalize({"options": [_opt("Advanced life support course"), _opt("Paediatric course")]})[0])
    tools = L.apply(C.make_item("Scheduling software", "required", item_id="req_tools", weight=33, origin=C.ORIGIN_STATED,
                                source_text="scheduling software such as spreadsheets or a project planner"),
                    L.normalize({"examples": ["spreadsheets", "a project planner"]})[0])
    ambiguous = L.apply(C.make_item("Clinical setting experience", "required", item_id="req_setting", weight=None, origin=C.ORIGIN_STATED,
                                    source_text="experience in a ward or clinical setting"),
                        L.normalize({"ambiguity": "Unclear whether both settings are needed or either one."})[0])
    doc["categories"]["skills"] = {"weight": 100, "items": [route, course, tools]}
    doc["categories"]["experience"] = {"weight": 0, "items": []}
    if gate_on_route:
        doc = R.set_gate(doc, "req_route", verification_method="cv_evidence", linked_question_id=None, user_id=USER, confirmed_at=NOW)
    return doc


def ambiguous_doc() -> dict:
    doc = semantic_doc(gate_on_route=False)
    ambiguous = L.apply(C.make_item("Clinical setting experience", "required", item_id="req_setting", weight=None, origin=C.ORIGIN_STATED,
                                    source_text="experience in a ward or clinical setting"),
                        L.normalize({"ambiguity": "Unclear whether both settings are needed or either one."})[0])
    doc["categories"]["skills"]["items"] = [doc["categories"]["skills"]["items"][0], ambiguous]
    doc["categories"]["skills"]["items"][0]["weight"], ambiguous["weight"] = 50, 50
    return R.set_gate(doc, "req_setting", verification_method="cv_evidence", linked_question_id=None, user_id=USER, confirmed_at=NOW)


def _job(built, owner, doc):
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 'Supervisor', 'JD') RETURNING job_id", (owner["tenant"], owner["users"]["admin"]))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version, requirements_revision, weight_skills, weight_experience,"
                " weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other)"
                " VALUES (%s, %s, %s, 2, 3, 100, 0, 0, 0, 0, 0, 0)", (job, json.dumps({"requirements": doc}), json.dumps({"requirements": doc})))
    return job


def model_output(doc, answers: dict):
    """What the evaluation model returned for each criterion; `answers[item_id]` is {leaf_id: (result, [quotes])} or (result, [quotes])."""
    evaluations = []
    for spec in item_specs(doc):
        answer = answers.get(spec.item_id)
        if answer is None:
            answer = {leaf["leaf_id"]: ("unclear", []) for leaf in L.leaves(spec.logic)} if (spec.logic or {}).get("options") else ("unclear", [])
        if isinstance(answer, dict):
            evaluations.append({"item_id": spec.item_id, "option_results": [
                {"leaf_id": k, "result": r, "evidence_quotes": q, "explanation": f"assessed {k}", "confidence": 0.9} for k, (r, q) in answer.items()]})
        else:
            evaluations.append({"item_id": spec.item_id, "result": answer[0], "evidence_quotes": answer[1], "explanation": "assessed", "confidence": 0.9})
    return V.validate_output({"evaluations": evaluations}, item_specs(doc), CV)


async def _score(built, engines, world, job, doc, name, answers, *, gate=True):
    app = _application(built, world["a"], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL, candidate_name = %s, processing_status = 'ai_scored' WHERE application_id = %s", (name, app))
    items = model_output(doc, answers)
    weights = {c: doc["categories"][c]["weight"] for c in C.CATEGORIES}
    out = EvaluationOutput(items=items, narrative={}, contacts={})

    async def work(session):
        return await S.record_run(session, application_id=app, tenant_id=world["a"]["tenant"], job_id=job, revision=3, category_weights=weights, specs=item_specs(doc),
                                  output=out, model="m", thresholds=(85, 65))
    await _in_tenant(engines("cv_worker"), world["a"]["tenant"], work)
    if gate:
        await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    return app


DIPLOMA = ("met", ["Holds a diploma in civil engineering"])
NOT_DIPLOMA = ("not_met", ["no formal diploma"])
GOOD_REST = {"req_course": {"o1": ("not_met", ["Completed the paediatric course"]), "o2": ("met", ["Completed the paediatric course"])},
             "req_tools": ("met", ["Uses spreadsheets for scheduling"])}


async def _view(engines, world, app):
    async def work(session):
        await session.execute(text("SELECT 1"))
        return await S.load_view(session, app)
    return await _in_tenant(engines(), world["a"]["tenant"], work)


def _item(view, item_id):
    return next(i for c in view["categories"] for i in c["items"] if i["item_id"] == item_id)


# ── which alternative was met, and why a case needs review ──────────────────────────────────────────────────────────

async def test_the_stored_evaluation_says_which_option_was_met_and_shows_every_options_result(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    route = {"o1": ("not_met", ["no formal diploma"]), "o2.1": ("met", ["bachelor's degree in mechanical engineering"]),
             "o2.2": ("met", ["two years on construction sites"]), "o3": ("unclear", [])}
    app = await _score(built, engines, world, job, doc, "Degree Route", {**GOOD_REST, "req_route": route})
    view = await _view(engines, world, app)
    item = _item(view, "req_route")
    assert item["result"] == "met" and item["matched_option"] == "Degree with two years on construction sites" and item["review_reason"] is None
    assert [o["result"] for o in item["option_results"]] == ["not_met", "met", "unclear"]
    assert item["option_results"][1]["parts"] == [{"text": "Bachelor's degree in an engineering field", "result": "met"}, {"text": "Two years on construction sites", "result": "met"}]
    assert item["evidence"] == ["bachelor's degree in mechanical engineering", "two years on construction sites"]
    assert view["status"] == "scored" and view["final_score"] == 100                                  # one weight per criterion
    assert _item(view, "req_course")["matched_option"] == "Paediatric course"


async def test_meeting_several_options_earns_no_extra_credit(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    one = await _score(built, engines, world, job, doc, "One Route", {**GOOD_REST, "req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA}})
    every = await _score(built, engines, world, job, doc, "Every Route", {**GOOD_REST, "req_route": {
        "o1": DIPLOMA, "o2.1": ("met", ["bachelor's degree in mechanical engineering"]), "o2.2": ("met", ["two years on construction sites"]), "o3": ("met", ["Ten years of site experience"])}})
    a, b = await _view(engines, world, one), await _view(engines, world, every)
    assert a["final_score"] == b["final_score"] == 100
    assert _item(b, "req_route")["matched_option"] == "Diploma in civil engineering"                  # the first direct match, not a sum


async def test_a_review_only_alternative_met_is_recorded_for_a_person_and_never_fails_the_gate(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    route = {"o1": NOT_DIPLOMA, "o2.1": ("not_met", ["no formal diploma"]), "o2.2": ("not_met", ["no formal diploma"]), "o3": ("met", ["Ten years of site experience"])}
    app = await _score(built, engines, world, job, doc, "Conditional", {**GOOD_REST, "req_route": route})
    view = await _view(engines, world, app)
    item = _item(view, "req_route")
    assert (item["result"], item["review_reason"], item["matched_option"]) == ("unclear", "conditional_alternative", "Long site experience without a diploma")
    assert view["status"] == "review_required" and view["final_score"] is None and view["unclear_count"] == 1       # no number, no decision: not zero
    gates = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, app))
    assert gates["eligibility_status"] == "review_required" and gates["gates"][0]["result"] == "needs_verification"
    assert "accepted only after review" in gates["gates"][0]["evidence_summary"]


async def test_a_confirmed_failure_on_every_option_fails_the_gate_but_missing_evidence_does_not(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    failing = await _score(built, engines, world, job, doc, "Fails", {**GOOD_REST, "req_route": {
        "o1": NOT_DIPLOMA, "o2.1": ("not_met", ["no formal diploma"]), "o2.2": ("not_met", ["no formal diploma"]), "o3": ("not_met", ["no formal diploma"])}})
    missing = await _score(built, engines, world, job, doc, "Silent", {**GOOD_REST})                   # the CV says nothing about the route
    fails = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, failing))
    silent = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, missing))
    assert fails["eligibility_status"] == "not_eligible"
    assert silent["eligibility_status"] == "review_required" and silent["gates"][0]["result"] == "needs_verification"
    view = await _view(engines, world, missing)
    assert _item(view, "req_route")["result"] == "unclear" and view["final_score"] is None


async def test_an_ambiguous_criterion_cannot_fail_a_gate_and_the_candidate_goes_to_review(built, engines, world, no_second_evaluation):
    doc = ambiguous_doc()
    job = _job(built, world["a"], doc)
    answers = {"req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA},
               "req_setting": ("not_met", ["Ten years of site experience"])}
    app = await _score(built, engines, world, job, doc, "Ambiguous", answers)
    view = await _view(engines, world, app)
    assert (_item(view, "req_setting")["result"], _item(view, "req_setting")["review_reason"]) == ("unclear", "ambiguous_requirement")
    gates = await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.gate_view(s, app))
    assert gates["eligibility_status"] == "review_required" and "ambiguous" in gates["gates"][0]["evidence_summary"]


async def test_examples_are_never_required(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    app = await _score(built, engines, world, job, doc, "Other Tool", {**GOOD_REST, "req_tools": ("met", ["Uses spreadsheets for scheduling"]),
                                                                       "req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA}})
    view = await _view(engines, world, app)
    assert _item(view, "req_tools")["result"] == "met" and _item(view, "req_tools")["option_results"] is None and view["final_score"] == 100


# ── the same criteria and outcomes in the Candidate Matrix ──────────────────────────────────────────────────────────

async def test_the_matrix_shows_the_same_outcomes_and_flags_conditional_and_ambiguous_cases(built, engines, world, no_second_evaluation):
    doc = semantic_doc()
    job = _job(built, world["a"], doc)
    await _score(built, engines, world, job, doc, "Direct", {**GOOD_REST, "req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA}})
    await _score(built, engines, world, job, doc, "Conditional", {**GOOD_REST, "req_route": {"o1": NOT_DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": ("met", ["Ten years of site experience"])}})
    await _score(built, engines, world, job, doc, "Fails", {**GOOD_REST, "req_route": {"o1": NOT_DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA}})
    async with _client(engines(), world) as client:
        body = by_name(await rows(client, job, columns=["candidate.name", "mandatory:req_route", "gate:req:req_route", "decision.flags"]))
        direct, conditional, fails = body["Direct"], body["Conditional"], body["Fails"]
        assert (direct["eligibility"], direct["score"], direct["cells"]["mandatory:req_route"]["status"]) == ("eligible", 100, "met")
        assert (conditional["eligibility"], conditional["score"], conditional["cells"]["mandatory:req_route"]["status"]) == ("review_required", None, "unclear")
        assert conditional["cells"]["gate:req:req_route"]["status"] == "review_required"             # a conditional alternative never fails the gate
        assert {e["kind"] for e in conditional["flags"]["items"]} >= {"conditional_alternative", "unresolved_review"}
        assert (fails["eligibility"], fails["cells"]["mandatory:req_route"]["status"]) == ("not_eligible", "not_met")
        only = await rows(client, job, filters={"flags": ["conditional_alternative"]})
        assert [r["candidate_name"] for r in only["rows"]] == ["Conditional"]


async def test_the_matrix_flags_an_ambiguous_requirement(built, engines, world, no_second_evaluation):
    doc = ambiguous_doc()
    job = _job(built, world["a"], doc)
    await _score(built, engines, world, job, doc, "Ambiguous", {"req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA},
                                                              "req_setting": ("not_met", ["Ten years of site experience"])})
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job))["Ambiguous"]
    kinds = {e["kind"]: e for e in row["flags"]["items"]}
    assert "ambiguous_requirement" in kinds and kinds["ambiguous_requirement"]["targets"] == [{"type": "item", "id": "req_setting"}]
    assert row["eligibility"] == "review_required"


# ── compatibility ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_criterion_without_options_is_stored_exactly_as_before(built, engines, world, no_second_evaluation):
    doc = semantic_doc(gate_on_route=False)
    job = _job(built, world["a"], doc)
    app = await _score(built, engines, world, job, doc, "Plain", {**GOOD_REST, "req_route": {"o1": DIPLOMA, "o2.1": NOT_DIPLOMA, "o2.2": NOT_DIPLOMA, "o3": NOT_DIPLOMA}}, gate=False)
    cur = built.conn.cursor()
    cur.execute("SELECT item_id, matched_option IS NULL, option_results IS NULL, review_reason FROM application_item_evaluations WHERE application_id = %s ORDER BY position", (app,))
    assert cur.fetchall() == [("req_route", False, False, None), ("req_course", False, False, None), ("req_tools", True, True, None)]
