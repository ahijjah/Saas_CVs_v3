"""Final Candidate Matrix on a real PostgreSQL 16: the summaries and eligibility from the stored scoring run and gate results, legacy and
unassessed applications, flags, filtering, stable sorting and paging, the column registry and layouts (precedence, validation, versions),
roles, tenant isolation and row-level security, audit, the CSV export, and a query count that does not grow with the page."""
from __future__ import annotations

import csv
import io
import json
import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from routers import candidate_matrix as MR
from services import eligibility_service as G
from services.item_scoring import service as S
from services.item_scoring.evaluator import EvaluatedItem, EvaluationOutput, item_specs
from services.requirements_v2 import contract as C
from test_gates_postgres import _application, _in_tenant, built, engines, world  # noqa: F401
from test_item_scoring_postgres import ARABIC, DEFAULT_RESULTS, _job, _record, acceptance_doc, no_second_evaluation  # noqa: F401

pytestmark = pytest.mark.asyncio

ALL_MET = {k: ("yes" if k == "req_k8" else "met") for k in DEFAULT_RESULTS}


def _client(engine, world, *, label="a", role="admin"):
    app = FastAPI()
    app.include_router(MR.router)
    user = CurrentUser(world[label]["users"][role], world[label]["tenant"], f"{role}@example.test", role, "U")
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _name(built, app, name, **columns):
    sets = ", ".join(["candidate_name = %s"] + [f"{k} = %s" for k in columns])
    built.conn.cursor().execute(f"UPDATE applications SET {sets} WHERE application_id = %s", (name, *columns.values(), app))


async def _candidate(built, engines, world, job, doc, name, results, *, gate=False, label="a", **columns):
    app = _application(built, world[label], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL, processing_status = 'ai_scored' WHERE application_id = %s", (app,))
    _name(built, app, name, **columns)
    await _record(engines, world, job, app, doc, results, label=label)
    if gate:
        await _in_tenant(engines(), world[label]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    return app


async def rows(client, job, **body):
    response = await client.post(f"/candidate-matrix/jobs/{job}/rows", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def by_name(body):
    return {r["candidate_name"]: r for r in body["rows"]}


@pytest_asyncio.fixture()
async def scenario(built, engines, world, no_second_evaluation):
    """One job whose SQL requirement is a Mandatory Gate, and five applications: a high score with a failed gate, an unclear Required item,
    a clean pass, a legacy score, and one not scored yet."""
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    apps = {
        "failed_gate": await _candidate(built, engines, world, job, doc, "Failed Gate", {**ALL_MET, "req_sql": "not_met"}, gate=True),
        "unclear": await _candidate(built, engines, world, job, doc, "Unclear Item", {**ALL_MET, ARABIC: "unclear"}, gate=True),
        "clean": await _candidate(built, engines, world, job, doc, "Clean Pass", ALL_MET, gate=True),
    }
    legacy = _application(built, world["a"], job)
    _name(built, legacy, "Legacy Score")
    built.conn.cursor().execute(
        "UPDATE applications SET decision = 'qualified', processing_status = 'ai_scored' WHERE application_id = %s", (legacy,))
    built.conn.cursor().execute(
        "INSERT INTO application_scores (application_id, score_skills, score_experience, score_education, score_certifications, score_soft_skills,"
        " score_domain_knowledge, score_other, final_score, weights_snapshot, ai_model) VALUES (%s, 80, 70, 60, 0, 0, 0, 0, 72, '{}', 'old')", (legacy,))
    pending = _application(built, world["a"], job)
    _name(built, pending, "Pending Upload")
    built.conn.cursor().execute("UPDATE applications SET decision = NULL, processing_status = 'pending' WHERE application_id = %s", (pending,))
    apps.update(legacy=legacy, pending=pending)
    return SimpleNamespace_(job=job, doc=doc, apps=apps)


class SimpleNamespace_:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ── summaries, eligibility, gates ───────────────────────────────────────────────────────────────────────────────────

async def test_the_acceptance_example_summaries_and_review_required(built, engines, world, no_second_evaluation):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    await _candidate(built, engines, world, job, doc, "Example", None)
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job))["Example"]
    assert row["mandatory"] == {"assessed": True, "met": 4, "partially_met": 1, "not_met": 1, "unclear": 1, "total": 7}
    assert row["preferred"] == {"assessed": True, "yes": 0, "no": 1, "unclear": 0, "total": 1}
    assert row["eligibility"] == "review_required" and row["score"] is None
    assert row["scoring"]["state"] == "review_required" and row["scoring"]["method"] == "item_level_v2"
    assert row["gates"]["assessed"] is False


async def test_a_failed_gate_makes_a_high_score_candidate_not_eligible_without_touching_the_score(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, scenario.job))["Failed Gate"]
    assert row["score"] == 89 and row["eligibility"] == "not_eligible"
    assert row["gates"]["failed"] == 1 and row["gates"]["passed"] == 0 and row["gates"]["assessed"] is True
    cur = built.conn.cursor()
    cur.execute("SELECT item_final_score, decision, eligibility_status FROM applications WHERE application_id = %s", (scenario.apps["failed_gate"],))
    assert cur.fetchone() == (89, "qualified", "not_eligible")                                # the stored score and decision are the scoring run's own


async def test_unclear_or_unverifiable_evidence_is_review_required_not_not_eligible(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        body = by_name(await rows(client, scenario.job, columns=["candidate.name", f"mandatory:{ARABIC}", "gate:req:req_sql"]))
    unclear = body["Unclear Item"]
    assert unclear["eligibility"] == "review_required" and unclear["score"] is None
    assert unclear["cells"][f"mandatory:{ARABIC}"] == {"status": "unclear", "confidence": 0.9, "resolved": False, "review_reason": None, "flagged": True}
    assert unclear["cells"]["gate:req:req_sql"]["status"] == "passed"


async def test_a_gate_that_needs_verification_is_review_required(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    await _candidate(built, engines, world, job, doc, "Needs Check", {**ALL_MET, "req_sql": "unclear"}, gate=True)
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job, columns=["candidate.name", "gate:req:req_sql"]))["Needs Check"]
    assert row["eligibility"] == "review_required" and row["gates"]["review_required"] == 1 and row["cells"]["gate:req:req_sql"]["status"] == "review_required"
    assert {e["kind"] for e in row["flags"]["items"]} >= {"unclear_criteria", "unresolved_review"}


async def test_a_clean_candidate_is_eligible_and_a_job_without_gates_is_not_assessed(built, engines, world, scenario, no_second_evaluation):
    async with _client(engines(), world) as client:
        clean = by_name(await rows(client, scenario.job))["Clean Pass"]
    assert clean["eligibility"] == "eligible" and clean["score"] == 100 and clean["gates"]["passed"] == 1
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    await _candidate(built, engines, world, job, doc, "No Gates", ALL_MET)
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job))["No Gates"]
    assert row["eligibility"] == "not_assessed" and row["score"] == 100 and row["gates"]["assessed"] is False


async def test_a_human_resolution_updates_the_matrix_and_keeps_the_result_visible(built, engines, world, no_second_evaluation):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = await _candidate(built, engines, world, job, doc, "Resolved", None)

    async def resolve(session):
        await S.resolve_item(session, application_id=app, item_id=ARABIC, result="met", reason="verified", user_id=world["a"]["users"]["admin"])

    await _in_tenant(engines(), world["a"]["tenant"], resolve)
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job, columns=["candidate.name", f"mandatory:{ARABIC}"]))["Resolved"]
    assert row["score"] == 84 and row["eligibility"] == "not_assessed" and row["mandatory"]["unclear"] == 0
    assert row["cells"][f"mandatory:{ARABIC}"]["status"] == "met" and row["cells"][f"mandatory:{ARABIC}"]["resolved"] is True
    assert "unclear_criteria" not in {e["kind"] for e in row["flags"]["items"]}


# ── preferred ───────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_preferred_results_are_yes_no_unclear_and_never_partial(built, engines, world, no_second_evaluation):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    await _candidate(built, engines, world, job, doc, "Not Clear", {**ALL_MET, "req_k8": "not_clear"})
    await _candidate(built, engines, world, job, doc, "Yes", ALL_MET)
    async with _client(engines(), world) as client:
        body = by_name(await rows(client, job, columns=["candidate.name", "preferred:req_k8"]))
    assert body["Not Clear"]["cells"]["preferred:req_k8"]["status"] == "unclear" and body["Not Clear"]["preferred"]["unclear"] == 1
    assert body["Yes"]["cells"]["preferred:req_k8"]["status"] == "yes" and body["Yes"]["preferred"]["yes"] == 1
    assert body["Not Clear"]["score"] == body["Yes"]["score"] == 100                      # a Preferred answer never moves the score
    assert body["Not Clear"]["eligibility"] == "not_assessed"                             # nor eligibility


async def test_the_database_refuses_partial_compliance_for_a_preferred_item(built, engines, world, no_second_evaluation):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    app = await _candidate(built, engines, world, job, doc, "P", ALL_MET)
    cur = built.conn.cursor()
    with pytest.raises(Exception) as caught:
        cur.execute("UPDATE application_item_evaluations SET result = 'partially_met', ai_result = 'partially_met' WHERE application_id = %s AND importance = 'preferred'", (app,))
    assert "result_check" in str(caught.value)


async def test_the_evaluator_cannot_produce_partial_for_a_preferred_item():
    from services.item_scoring import evaluator as V
    spec = [V.ItemSpec("req_k8", "skills", "preferred", "Kubernetes", None)]
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output({"evaluations": [{"item_id": "req_k8", "result": "partially_met", "evidence_quotes": ["x"], "confidence": 0.9}]}, spec, "x cv")


# ── legacy and unassessed ───────────────────────────────────────────────────────────────────────────────────────────

async def test_legacy_and_unscored_applications_do_not_break_the_matrix(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        body = by_name(await rows(client, scenario.job, columns=["candidate.name", "mandatory:req_sql", "preferred:req_k8", "gate:req:req_sql", "decision.scoring"]))
    legacy, pending = body["Legacy Score"], body["Pending Upload"]
    assert legacy["score"] == 72 and legacy["scoring"]["method"] == "legacy_category" and legacy["scoring"]["state"] == "legacy"
    assert legacy["mandatory"]["assessed"] is False and legacy["preferred"]["assessed"] is False and legacy["eligibility"] == "not_assessed"
    assert legacy["cells"]["mandatory:req_sql"]["status"] == "not_assessed" and legacy["cells"]["preferred:req_k8"]["status"] == "not_assessed"
    assert legacy["cells"]["gate:req:req_sql"]["status"] == "not_assessed"
    assert pending["score"] is None and pending["scoring"]["method"] == "none" and pending["flags"]["total"] == 0


# ── flags ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_flags_come_from_the_stored_review_reasons_and_open_the_right_targets(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    weights = {c: doc["categories"][c]["weight"] for c in C.CATEGORIES}
    reasons = {"req_inc": "no_verified_evidence", "req_py": "low_confidence", "req_en": "conflicting_evidence"}
    results = {**ALL_MET, "req_sql": "unclear", **{k: "unclear" for k in reasons}}
    output = EvaluationOutput(items=[EvaluatedItem(s.item_id, results.get(s.item_id, "met" if s.importance == "required" else "yes"),
                                                  () if results.get(s.item_id) == "unclear" else ("Five years",), "x", Decimal("0.900"),
                                                  reasons.get(s.item_id)) for s in item_specs(doc)],
                              narrative={}, contacts={})
    app = _application(built, world["a"], job)
    built.conn.cursor().execute("UPDATE applications SET decision = NULL, processing_status = 'ai_scored' WHERE application_id = %s", (app,))
    _name(built, app, "Flagged")
    await _in_tenant(engines("cv_worker"), world["a"]["tenant"], lambda s: S.record_run(
        s, application_id=app, tenant_id=world["a"]["tenant"], job_id=job, revision=3, category_weights=weights, specs=item_specs(doc),
        output=output, model="m", thresholds=(85, 65)))
    await _in_tenant(engines(), world["a"]["tenant"], lambda s: G.evaluate_application(s, app, run_cv_check=True))
    built.conn.cursor().execute("UPDATE applications SET processing_status = 'failed', stopped_reason = 'processing_error' WHERE application_id = %s", (app,))
    async with _client(engines(), world) as client:
        row = by_name(await rows(client, job))["Flagged"]
    kinds = {e["kind"]: e for e in row["flags"]["items"]}
    assert set(kinds) == {"unclear_criteria", "evidence_not_found", "low_confidence", "conflicting_evidence", "processing_error", "unresolved_review"}
    assert kinds["unclear_criteria"]["severity"] == "high"                                   # the SQL requirement is also a gate
    assert {"type": "item", "id": "req_inc"} in kinds["evidence_not_found"]["targets"]
    assert kinds["processing_error"]["targets"] == [{"type": "application", "id": app}]
    assert {"type": "gate", "id": "req:req_sql"} in kinds["unresolved_review"]["targets"]
    async with _client(engines(), world) as client:
        only = await rows(client, job, filters={"flags": ["low_confidence"]})
        none = await rows(client, job, filters={"flags": ["conflicting_evidence"]}, page_size=10)
    assert [r["candidate_name"] for r in only["rows"]] == ["Flagged"] and [r["candidate_name"] for r in none["rows"]] == ["Flagged"]


# ── filtering, sorting, paging ──────────────────────────────────────────────────────────────────────────────────────

async def test_filters_apply_to_eligibility_scores_criteria_gates_and_search(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        names = lambda body: sorted(r["candidate_name"] for r in body["rows"])                  # noqa: E731
        assert names(await rows(client, scenario.job, filters={"eligibility": ["not_eligible"]})) == ["Failed Gate"]
        assert names(await rows(client, scenario.job, filters={"eligibility": ["review_required"]})) == ["Unclear Item"]
        assert names(await rows(client, scenario.job, filters={"eligibility": ["eligible"]})) == ["Clean Pass"]
        assert names(await rows(client, scenario.job, filters={"score_min": 90})) == ["Clean Pass"]
        assert names(await rows(client, scenario.job, filters={"score_min": 70, "score_max": 80})) == ["Legacy Score"]
        assert names(await rows(client, scenario.job, filters={"mandatory": [{"item_id": "req_sql", "results": ["not_met"]}]})) == ["Failed Gate"]
        assert names(await rows(client, scenario.job, filters={"mandatory": [{"item_id": ARABIC, "results": ["unclear"]}]})) == ["Unclear Item"]
        assert names(await rows(client, scenario.job, filters={"mandatory": [{"item_id": "req_sql", "results": ["not_assessed"]}]})) == ["Legacy Score", "Pending Upload"]
        assert names(await rows(client, scenario.job, filters={"preferred": [{"item_id": "req_k8", "results": ["yes"]}]})) == ["Clean Pass", "Failed Gate", "Unclear Item"]
        assert names(await rows(client, scenario.job, filters={"gates": [{"gate_key": "req:req_sql", "results": ["failed"]}]})) == ["Failed Gate"]
        assert names(await rows(client, scenario.job, filters={"scoring_status": ["legacy"]})) == ["Legacy Score"]
        assert names(await rows(client, scenario.job, filters={"flags": ["unresolved_review"]})) == ["Unclear Item"]
        assert names(await rows(client, scenario.job, filters={"search": "clean"})) == ["Clean Pass"]
        assert names(await rows(client, scenario.job, filters={"search": "%"})) == []                       # a wildcard is searched for literally


async def test_sorting_is_the_recruiters_explicit_choice_and_missing_scores_go_last(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        desc = await rows(client, scenario.job, sort={"column": "decision.score", "direction": "desc"})
        asc = await rows(client, scenario.job, sort={"column": "decision.score", "direction": "asc"})
        elig = await rows(client, scenario.job, sort={"column": "decision.eligibility", "direction": "asc"})
        crit = await rows(client, scenario.job, columns=["candidate.name", "mandatory:req_sql"], sort={"column": "mandatory:req_sql", "direction": "asc"})
    assert [r["score"] for r in desc["rows"]][:3] == [100, 89, 72] and [r["score"] for r in desc["rows"]][3:] == [None, None]
    assert [r["score"] for r in asc["rows"]][:3] == [72, 89, 100] and [r["score"] for r in asc["rows"]][3:] == [None, None]
    assert elig["rows"][0]["candidate_name"] == "Clean Pass" and elig["rows"][-1]["candidate_name"] == "Failed Gate"
    assert crit["rows"][0]["cells"]["mandatory:req_sql"]["status"] == "met" and crit["rows"][-1]["cells"]["mandatory:req_sql"]["status"] == "not_assessed"
    assert [r["cells"]["mandatory:req_sql"]["status"] for r in crit["rows"]][-3:] == ["not_met", "not_assessed", "not_assessed"] or \
        [r["cells"]["mandatory:req_sql"]["status"] for r in crit["rows"]] == ["met", "met", "not_met", "not_assessed", "not_assessed"]


async def test_paging_is_stable_when_every_sort_value_ties(built, engines, world, no_second_evaluation):
    doc = acceptance_doc()
    job = _job(built, world["a"], doc)
    for i in range(23):
        await _candidate(built, engines, world, job, doc, f"Tie {i:02d}", ALL_MET)
    built.conn.cursor().execute("UPDATE applications SET applied_at = '2026-10-01 10:00:00' WHERE job_id = %s", (job,))       # identical dates too
    async with _client(engines(), world) as client:
        seen = []
        for page in (1, 2, 3):
            body = await rows(client, job, page=page, page_size=10, sort={"column": "decision.score", "direction": "desc"})
            assert body["total"] == 23
            seen += [r["application_id"] for r in body["rows"]]
        again = []
        for page in (1, 2, 3):
            again += [r["application_id"] for r in (await rows(client, job, page=page, page_size=10, sort={"column": "decision.score", "direction": "desc"}))["rows"]]
        beyond = await rows(client, job, page=9, page_size=10)
    assert len(seen) == len(set(seen)) == 23 and seen == again and beyond["rows"] == [] and beyond["total"] == 23


async def test_the_query_count_does_not_grow_with_the_page_or_the_columns(built, engines, world, no_second_evaluation):
    doc = acceptance_doc(gate_on="req_sql")
    job = _job(built, world["a"], doc)
    for i in range(14):
        await _candidate(built, engines, world, job, doc, f"N{i:02d}", ALL_MET, gate=True)
    engine = engines()
    counter = {"n": 0}
    event.listen(engine.sync_engine, "before_cursor_execute", lambda *a, **k: counter.__setitem__("n", counter["n"] + 1))
    columns = ["candidate.name", "candidate.tags", "mandatory.summary", "mandatory:req_sql", f"mandatory:{ARABIC}", "preferred:req_k8", "gate:req:req_sql", "decision.flags"]
    async with _client(engine, world) as client:
        counter["n"] = 0
        await rows(client, job, columns=columns[:2], page_size=10)
        small = counter["n"]
        counter["n"] = 0
        await rows(client, job, columns=columns, page_size=100)
        large = counter["n"]
    assert small <= 16 and large <= 16 and abs(large - small) <= 3, (small, large)


# ── columns and layouts ─────────────────────────────────────────────────────────────────────────────────────────────

async def test_the_column_definitions_list_every_requirement_and_gate_by_immutable_id(built, engines, world, scenario):
    async with _client(engines(), world, role="viewer") as client:
        body = (await client.get(f"/candidate-matrix/jobs/{scenario.job}/columns")).json()
    ids = {c["id"]: c for c in body["columns"]}
    assert {"mandatory:req_sql", f"mandatory:{ARABIC}", "preferred:req_k8", "gate:req:req_sql"} <= set(ids)
    assert ids["candidate.location"]["available"] is False and ids["mandatory:req_sql"]["group"] == "mandatory" and ids["preferred:req_k8"]["group"] == "preferred"
    assert body["vocabulary"]["preferred"] == ["yes", "no", "unclear", "not_assessed"]
    assert not any(k.startswith("candidate.age") or "birth" in k for k in ids)


@pytest.mark.parametrize("body", [
    {"columns": ["candidate.name", "mandatory:req_gone"]}, {"columns": ["candidate.name", "candidate.location"]}, {"columns": ["candidate.name", "decision.score", "decision.score"]},
    {"columns": ["candidate.name", "x'; DROP TABLE applications; --"]}, {"sort": {"column": "candidate.source", "direction": "asc"}},
    {"filters": {"mandatory": [{"item_id": "req_gone", "results": ["met"]}]}}, {"unknown": 1}, {"page": 0}, {"page_size": 1000}])
async def test_invalid_requests_are_refused(built, engines, world, scenario, body):
    async with _client(engines(), world) as client:
        response = await client.post(f"/candidate-matrix/jobs/{scenario.job}/rows", json=body)
    assert response.status_code == 422, (body, response.text)


LAYOUT = {"columns": ["candidate.name", "decision.score", "mandatory:req_sql"], "sort": {"column": "decision.score", "direction": "desc"}}


async def test_precedence_personal_over_job_over_tenant_over_system_and_reset(built, engines, world, scenario):
    job = scenario.job
    tenant_layout = {"columns": ["candidate.name", "workflow.status"]}
    job_layout = {"columns": ["candidate.name", "decision.flags"]}
    async with _client(engines(), world) as admin, _client(engines(), world, role="recruiter") as recruiter:
        async def source(client):
            return (await client.get(f"/candidate-matrix/jobs/{job}/config")).json()
        assert (await source(admin))["source"] == "system"
        assert (await admin.put("/candidate-matrix/tenant-config", json={"layout": tenant_layout})).status_code == 200
        assert (await source(recruiter))["source"] == "tenant" and (await source(recruiter))["effective"]["columns"] == ["candidate.name", "workflow.status"]
        assert (await admin.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": job_layout})).status_code == 200
        assert (await source(recruiter))["source"] == "job"
        assert (await recruiter.put(f"/candidate-matrix/jobs/{job}/config/personal", json={"layout": LAYOUT})).status_code == 200
        mine, theirs = await source(recruiter), await source(admin)
        assert mine["source"] == "personal" and mine["effective"]["columns"] == LAYOUT["columns"]
        assert theirs["source"] == "job"                                                   # a personal view never changes the shared one
        assert (await recruiter.delete(f"/candidate-matrix/jobs/{job}/config/personal")).json()["removed"] is True
        assert (await source(recruiter))["source"] == "job"
        assert (await admin.delete(f"/candidate-matrix/jobs/{job}/config/job")).status_code == 200
        assert (await source(recruiter))["source"] == "tenant"
        assert (await admin.delete("/candidate-matrix/tenant-config")).status_code == 200
        assert (await source(recruiter))["source"] == "system"


async def test_the_rows_use_the_layout_in_effect_when_the_request_names_nothing(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        assert (await client.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": {**LAYOUT, "filters": {"eligibility": ["eligible"]}}})).status_code == 200
        body = await rows(client, scenario.job)
    assert body["columns"] == LAYOUT["columns"] and [r["candidate_name"] for r in body["rows"]] == ["Clean Pass"] and body["sort"]["column"] == "decision.score"


async def test_a_removed_requirement_keeps_the_saved_layout_readable_and_is_reported_unavailable(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        layout = {"columns": ["candidate.name", "mandatory:req_edu", "decision.score"]}
        assert (await client.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": layout})).status_code == 200
        cur = built.conn.cursor()
        doc = json.loads(json.dumps(scenario.doc))
        doc["categories"]["education"]["items"] = []
        cur.execute("UPDATE job_criteria SET analysis_json = %s WHERE job_id = %s", (json.dumps({"requirements": doc}), scenario.job))
        config = (await client.get(f"/candidate-matrix/jobs/{scenario.job}/config")).json()
        body = await rows(client, scenario.job)
    assert config["unavailable_columns"] == ["mandatory:req_edu"] and config["effective"]["columns"] == ["candidate.name", "decision.score"]
    assert body["columns"] == ["candidate.name", "decision.score"] and len(body["rows"]) == 5
    cur.execute("SELECT config -> 'columns' FROM candidate_matrix_configs WHERE job_id = %s AND scope = 'job'", (scenario.job,))
    assert cur.fetchone()[0] == layout["columns"]                                            # the stored layout is untouched


async def test_a_stale_save_is_a_conflict_and_versions_count_up(built, engines, world, scenario):
    job = scenario.job
    async with _client(engines(), world) as client:
        first = (await client.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT})).json()
        assert first["version"] == 1
        assert (await client.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT})).status_code == 409           # exists, no expected_version
        second = (await client.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT, "expected_version": 1})).json()
        assert second["version"] == 2
        assert (await client.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT, "expected_version": 1})).status_code == 409


async def test_an_unreadable_stored_layout_falls_through_to_the_next_layer(built, engines, world, scenario):
    built.conn.cursor().execute(
        "INSERT INTO candidate_matrix_configs (tenant_id, scope, job_id, config) VALUES (%s, 'job', %s, '{\"columns\": 5}')", (world["a"]["tenant"], scenario.job))
    async with _client(engines(), world) as client:
        config = (await client.get(f"/candidate-matrix/jobs/{scenario.job}/config")).json()
    assert config["source"] == "system" and config["layers"]["job"]["readable"] is False


async def test_the_tenant_default_accepts_only_standard_columns(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        response = await client.put("/candidate-matrix/tenant-config", json={"layout": {"columns": ["candidate.name", "mandatory:req_sql"]}})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "tenant_dynamic_column"


# ── roles ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_roles_for_reading_saving_and_exporting(built, engines, world, scenario):
    job = scenario.job
    expectations = {"admin": (200, 200, 200), "hr_manager": (200, 200, 200), "recruiter": (200, 403, 200), "viewer": (403, 403, 403)}
    for role in ("admin", "hr_manager", "recruiter", "viewer"):
        async with _client(engines(), world, role=role) as client:
            assert (await client.post(f"/candidate-matrix/jobs/{job}/rows", json={})).status_code == 200, role
            personal, shared, export = expectations[role]
            assert (await client.put(f"/candidate-matrix/jobs/{job}/config/personal", json={"layout": LAYOUT, "expected_version": None})).status_code in ((200, 409) if role != "viewer" else (403,)), role
            await client.delete(f"/candidate-matrix/jobs/{job}/config/job")
            await client.delete("/candidate-matrix/tenant-config")
            assert (await client.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT})).status_code == (200 if role in ("admin", "hr_manager") else 403), role
            assert (await client.put("/candidate-matrix/tenant-config", json={"layout": {"columns": ["candidate.name"]}})).status_code == (200 if role in ("admin", "hr_manager") else 403), role
            assert (await client.post(f"/candidate-matrix/jobs/{job}/export", json={})).status_code == export, role


# ── tenants and row-level security ──────────────────────────────────────────────────────────────────────────────────

async def test_another_tenant_sees_nothing_and_gets_the_same_404_everywhere(built, engines, world, scenario):
    async with _client(engines(), world) as owner:
        assert (await owner.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": LAYOUT})).status_code == 200
    async with _client(engines(), world, label="b") as other:
        missing = str(uuid.uuid4())
        for path, method, body in ((f"/candidate-matrix/jobs/{scenario.job}/columns", "get", None), (f"/candidate-matrix/jobs/{scenario.job}/config", "get", None),
                                   (f"/candidate-matrix/jobs/{scenario.job}/rows", "post", {}), (f"/candidate-matrix/jobs/{scenario.job}/export", "post", {}),
                                   (f"/candidate-matrix/jobs/{scenario.job}/config/job", "put", {"layout": LAYOUT}),
                                   (f"/candidate-matrix/jobs/{scenario.job}/config/personal", "put", {"layout": LAYOUT}),
                                   (f"/candidate-matrix/jobs/{scenario.job}/config/job", "delete", None)):
            response = await getattr(other, method)(path, **({"json": body} if body is not None else {}))
            same = await getattr(other, method)(path.replace(scenario.job, missing), **({"json": body} if body is not None else {}))
            assert response.status_code == 404 and response.json() == same.json(), (method, path)
    cur = built.conn.cursor()
    cur.execute("SELECT count(*) FROM candidate_matrix_configs WHERE job_id = %s", (scenario.job,))
    assert cur.fetchone()[0] == 1                                                              # the other tenant's writes changed nothing


async def test_dynamic_column_ids_of_another_tenants_job_are_not_columns(built, engines, world, scenario):
    other_doc = acceptance_doc()
    other_job = _job(built, world["b"], other_doc)
    async with _client(engines(), world, label="b") as other:
        body = await other.post(f"/candidate-matrix/jobs/{other_job}/rows", json={"columns": ["candidate.name", "gate:req:req_sql"]})
        assert body.status_code == 422                                                         # req_sql is not a gate of this job
        ok = await other.post(f"/candidate-matrix/jobs/{other_job}/rows", json={"columns": ["candidate.name", "mandatory:req_sql"]})
        assert ok.status_code == 200 and ok.json()["rows"] == []                               # the same item id here sees only this tenant's rows


async def test_row_level_security_hides_and_protects_the_layouts(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        assert (await client.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": LAYOUT})).status_code == 200
    engine = engines()
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": world["b"]["tenant"]})
        assert (await session.execute(text("SELECT count(*) FROM candidate_matrix_configs"))).scalar() == 0
        with pytest.raises(DBAPIError):
            await session.execute(text("INSERT INTO candidate_matrix_configs (tenant_id, scope, config) VALUES (CAST(:t AS uuid), 'tenant', '{}')"), {"t": world["a"]["tenant"]})
        await session.rollback()
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": world["a"]["tenant"]})
        assert (await session.execute(text("SELECT count(*) FROM candidate_matrix_configs"))).scalar() == 1


# ── audit ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_shared_changes_are_audited_and_personal_ones_are_not(built, engines, world, scenario):
    job = scenario.job
    async with _client(engines(), world) as admin, _client(engines(), world, role="recruiter") as recruiter:
        await admin.put(f"/candidate-matrix/jobs/{job}/config/job", json={"layout": LAYOUT})
        await admin.put("/candidate-matrix/tenant-config", json={"layout": {"columns": ["candidate.name"]}})
        await recruiter.put(f"/candidate-matrix/jobs/{job}/config/personal", json={"layout": LAYOUT})
        await admin.delete(f"/candidate-matrix/jobs/{job}/config/job")
        await admin.post(f"/candidate-matrix/jobs/{job}/export", json={"columns": ["candidate.name", "decision.score"]})
    cur = built.conn.cursor()
    cur.execute("SELECT action, resource_type, details::text FROM audit_logs WHERE tenant_id = %s AND action LIKE 'matrix_%%' ORDER BY created_at", (world["a"]["tenant"],))
    got = cur.fetchall()
    assert [(a, r) for a, r, _ in got] == [("matrix_config_saved", "job"), ("matrix_config_saved", "tenant"), ("matrix_config_reset", "job"), ("matrix_exported", "job")]
    assert "Failed Gate" not in " ".join(d for *_, d in got)                                   # ids and counts only


# ── export ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _csv(response):
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))), response.content


async def test_the_export_follows_the_visible_columns_filters_sort_and_hides_everything_else(built, engines, world, scenario):
    columns = ["candidate.name", "mandatory:req_sql", "decision.eligibility", "decision.score"]
    async with _client(engines(), world) as client:
        rows_, raw = _csv(await client.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={
            "columns": columns, "filters": {"score_min": 80}, "sort": {"column": "decision.score", "direction": "asc"}, "include_metadata": False}))
    assert rows_[0] == ["Candidate", "SQL troubleshooting", "Eligibility", "Overall score"]
    assert rows_[1:] == [["Failed Gate", "Not Met", "Not Eligible", "89"], ["Clean Pass", "Met", "Eligible", "100"]]
    assert raw.startswith(b"\xef\xbb\xbf")
    text_ = raw.decode("utf-8")
    for hidden in ("Preferred criteria", "Application date", "Workflow status", "Five years of SQL troubleshooting", "Legacy Score"):
        assert hidden not in text_


async def test_the_export_uses_the_layout_in_effect_when_it_names_nothing_and_carries_metadata(built, engines, world, scenario):
    async with _client(engines(), world) as client:
        await client.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": {"columns": ["candidate.name", "decision.score"]}})
        rows_, _ = _csv(await client.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={}))
    meta = {r[0]: r[1] for r in rows_[:8] if len(r) > 1}
    assert meta["Job"].startswith("Engineer") and "Organization" in meta and rows_[9] == ["Candidate", "Overall score"] and len(rows_) == 10 + 5


async def test_the_arabic_export_is_utf8_with_arabic_headers_and_statuses(built, engines, world, scenario):
    built.conn.cursor().execute("UPDATE applications SET candidate_name = 'أحمد المطيري' WHERE application_id = %s", (scenario.apps["clean"],))
    async with _client(engines(), world) as client:
        rows_, raw = _csv(await client.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={
            "columns": ["candidate.name", "decision.eligibility"], "lang": "ar", "include_metadata": False, "filters": {"search": "أحمد"}}))
    assert rows_ == [["المرشح", "الأهلية"], ["أحمد المطيري", "مؤهل"]] and "أحمد المطيري".encode("utf-8") in raw


async def test_the_export_refuses_a_hidden_or_unknown_column_and_a_formula_name_is_neutralised(built, engines, world, scenario):
    built.conn.cursor().execute("UPDATE applications SET candidate_name = '=HYPERLINK(\"http://x\")' WHERE application_id = %s", (scenario.apps["pending"],))
    async with _client(engines(), world) as client:
        assert (await client.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={"columns": ["candidate.name", "evidence.quotes"]})).status_code == 422
        rows_, _ = _csv(await client.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={"columns": ["candidate.name"], "include_metadata": False}))
    assert any(r[0].startswith("'=HYPERLINK") for r in rows_)


# ── agency client scoping ───────────────────────────────────────────────────────────────────────────────────────────

async def test_a_recruiter_without_the_client_assignment_cannot_open_that_clients_matrix(built, engines, world, scenario):
    cur = built.conn.cursor()
    cur.execute("INSERT INTO client_organizations (tenant_id, organization_name, status) VALUES (%s, 'Acme', 'active') RETURNING client_organization_id", (world["a"]["tenant"],))
    client_id = str(cur.fetchone()[0])
    cur.execute("UPDATE jobs SET client_organization_id = %s WHERE job_id = %s", (client_id, scenario.job))
    async with _client(engines(), world, role="recruiter") as recruiter:
        assert (await recruiter.post(f"/candidate-matrix/jobs/{scenario.job}/rows", json={})).status_code == 404
    cur.execute("INSERT INTO agency_user_clients (tenant_id, user_id, client_organization_id) VALUES (%s, %s, %s)", (world["a"]["tenant"], world["a"]["users"]["recruiter"], client_id))
    async with _client(engines(), world, role="recruiter") as recruiter, _client(engines(), world) as admin:
        assert (await recruiter.post(f"/candidate-matrix/jobs/{scenario.job}/rows", json={})).status_code == 200
        rows_, _ = _csv(await admin.post(f"/candidate-matrix/jobs/{scenario.job}/export", json={"columns": ["candidate.name"]}))
    assert ["Client", "Acme"] in rows_[:6]                                                  # the client name comes from the existing client organization record


# ── the explicit tenant predicates, independent of row-level security ───────────────────────────────────────────────

async def test_the_explicit_tenant_predicates_hold_even_when_row_level_security_is_not_the_one_deciding(built, engines, world, scenario):
    """Row-level security is told this is tenant A; the services are asked, in code, for tenant B. Tenant A's job, layouts and rows must still
    not be reachable, so a missing predicate in the SQL cannot hide behind the policy."""
    from routers.applications import _build_candidate_filter_clause
    from services.matrix import config as MC
    from services.matrix import query as MQ
    from services.matrix import registry as MR
    async with _client(engines(), world) as client:
        assert (await client.put(f"/candidate-matrix/jobs/{scenario.job}/config/job", json={"layout": LAYOUT})).status_code == 200
        assert (await client.put("/candidate-matrix/tenant-config", json={"layout": {"columns": ["candidate.name", "workflow.status"]}})).status_code == 200
    user_b = CurrentUser(world["b"]["users"]["admin"], world["b"]["tenant"], "b@example.test", "admin", "B")
    async with AsyncSession(engines()) as db:
        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": world["a"]["tenant"]})
        layers = await MC.load_layers(db, tenant_id=world["b"]["tenant"], job_id=scenario.job, user_id=world["b"]["users"]["admin"])
        assert layers == {"tenant": None, "job": None, "personal": None}
        where, params = _build_candidate_filter_clause(user_b, True, job_id=scenario.job)
        registry = {c.id: c for c in MR.STATIC}
        rows_, total = await MQ.fetch_rows(db, access_where=where, access_params=params, user_id=user_b.user_id, filters=MC.Filters(), sort=None,
                                           registry=registry, visible=["candidate.name"], page=1, page_size=25)
        assert rows_ == [] and total == 0
        assert await MC.delete_layout(db, tenant_id=world["b"]["tenant"], scope="job", job_id=scenario.job, user_id=user_b.user_id) is False
        assert await MC.delete_layout(db, tenant_id=world["b"]["tenant"], scope="tenant", job_id=None, user_id=user_b.user_id) is False
        await db.rollback()
    async with AsyncSession(engines()) as db:                                                 # and with the right tenant the same calls do see them
        await db.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": world["a"]["tenant"]})
        layers = await MC.load_layers(db, tenant_id=world["a"]["tenant"], job_id=scenario.job, user_id=world["a"]["users"]["admin"])
        assert layers["job"] is not None and layers["tenant"] is not None
        await db.rollback()
