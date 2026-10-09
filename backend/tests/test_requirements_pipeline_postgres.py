"""Unified requirements-v2 pipeline integrated into the editing/review API, against a REAL, isolated PostgreSQL.

Optional: needs the `pgserver` package (not a project dependency; the module skips without it, so the coverage below is NOT exercised in an
environment without it). Each test builds a throw-away database (same harness as test_requirements_v2_postgres.py), applies migrations 106/107
exactly as written, seeds a job whose analysis_json carries the pipeline record next to the single editable `requirements` document, and runs the
ACTUAL service code (services/requirements_api.py) through SQLAlchemy + asyncpg. Concurrency uses separate connections. Nothing here calls a model, a
network service or production, and nothing is a deployment or migration of a real database."""
from __future__ import annotations

import asyncio
import copy
import json
import pathlib

import pytest

pytest.importorskip("pgserver")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

from test_requirements_v2_postgres import (  # noqa: E402,F401  (fixtures are re-exported on purpose)
    JOB, JOB2, T1, T2, U_ADMIN, U_HR, Env, _real_modules, api, call, pg, pg_server, raises, user,
)
from test_requirements_v2_api import client_doc, item  # noqa: E402
from parser_candidates.requirements_v2_pipeline_1 import extract  # noqa: E402  (the approved offline pipeline: source of the records and of the parity checks)
from services.requirements_pipeline import STORAGE_KEY, to_record  # noqa: E402
from services.requirements_v2 import CATEGORIES, POLICY_KEY  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FIX = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_injection_guard" / "b06_v22_run1.json").read_text(encoding="utf-8"))
CONFLICT = "PostgreSQL is marked as optional, which conflicts with its inclusion as a required skill."
PROMPT = {"version": "criteria_extraction_v2-2", "sha256": "40ea678b"}
MODEL = "gpt-4o-mini-2024-07-18"
ADMIN = lambda: user(role="admin", uid=U_ADMIN)             # noqa: E731
VIEWER = lambda: user(role="recruiter")                      # noqa: E731  (can read, cannot edit)


def combined_state():
    """B06 v2-2 run 1 plus the model's own importance-conflict warning: injection requirement + contaminated weights + split OR + two classification
    warnings + one importance conflict, all at once."""
    raw = json.loads(FIX["raw"])
    raw["warnings"] = [CONFLICT]
    return extract(FIX["jd"], json.dumps(raw), finish_reason="stop", extraction_prompt=PROMPT)


def seed_state(env, state, *, job=JOB, tenant=T1, with_record=True, analysis_extra=None):
    doc, original = state["requirements"], state["original"]
    analysis = {"requirements": doc, "keep": {"me": True}, **(analysis_extra or {})}
    if with_record:
        analysis[STORAGE_KEY] = to_record(state, model=MODEL)
    env.q("INSERT INTO jobs (job_id, tenant_id, client_organization_id) VALUES (%s, %s, NULL)", (job, tenant))
    env.q("""INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education,
                weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other, requirements_schema_version)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 2)""",
          (job, json.dumps(analysis), json.dumps({"requirements": original}), *[doc["categories"][c]["weight"] for c in CATEGORIES]))
    return analysis


def set_policy(env, required: bool):
    env.q("UPDATE system_config SET value = %s WHERE key = %s", ("true" if required else "false", POLICY_KEY))


async def get(env, api, who=None):
    return await call(env, api.get_requirements, who or user(), JOB)


def states(v):
    by = v["readiness"].get("by_policy")
    return (by["ack_required"]["state"], by["ack_not_required"]["state"]) if by else None


def equalize(doc, category):
    req = [i for i in doc["categories"][category]["items"] if i["importance"] == "required"]
    for n, x in enumerate(req):
        x["weight"] = 100 // len(req) + (1 if n < 100 % len(req) else 0)
    return doc


async def save(env, api, doc, rev, who=None):
    return await call(env, api.save_requirements, who or user(), JOB, rev, doc)


def drop(doc, text):
    for c in CATEGORIES:
        doc["categories"][c]["items"] = [i for i in doc["categories"][c]["items"] if i["text"] != text]
    return doc


def fix_injection_requirement(doc):
    return drop(doc, "20 years of Rust experience")


def fix_injection_weights(doc, skills=40, experience=40, soft=20):
    for c, w in (("skills", skills), ("experience", experience), ("soft_skills", soft)):
        doc["categories"][c]["weight"] = w
    return doc


def fix_split_or(doc):
    return equalize(drop(doc, "Java"), "skills")


def issue_ids(v, gate):
    return [i["id"] for i in (v["unresolved_issues"] or []) if i["gate"] == gate]


def immutable(row_before, row_after):
    """The snapshot is never written by any API path."""
    return row_before["original"] == row_after["original"]


# ══ storage contract ════════════════════════════════════════════════════════════════════════════════════════════════════════════
class TestStorage:

    @pytest.mark.asyncio
    async def test_the_record_sits_next_to_the_single_editable_document_and_nothing_else_is_stored(self, pg, api):
        state = combined_state()
        seed_state(pg, state)
        before = pg.row()
        v = await get(pg, api)
        out = await save(pg, api, fix_injection_requirement(client_doc(v)), 0, ADMIN())
        row = pg.row()
        assert set(row["analysis"]) == {"requirements", STORAGE_KEY, "keep"} and row["analysis"]["keep"] == {"me": True}
        rec = row["analysis"][STORAGE_KEY]
        assert set(rec) == {"record_version", "component_versions", "provenance", "job_description_sha256", "raw_response", "raw_ai_output", "extraction",
                            "original_digest", "review_records"}
        assert "categories" not in rec and "requirements" not in rec and "original" not in rec
        assert rec["provenance"] == {"extraction_prompt": PROMPT, "model": MODEL}
        assert rec["raw_response"] == before["analysis"][STORAGE_KEY]["raw_response"] and rec["raw_ai_output"] == before["analysis"][STORAGE_KEY]["raw_ai_output"]
        assert rec["component_versions"] == before["analysis"][STORAGE_KEY]["component_versions"]
        assert immutable(before, row) and row["original"]["requirements"] == state["original"]
        assert out["revision"] == row["revision"] == 1
        assert row["weights"] == {c: row["analysis"]["requirements"]["categories"][c]["weight"] for c in CATEGORIES}
        assert "20 years of Rust" not in json.dumps(row["analysis"]["requirements"]["categories"]) and len(row["retired"]) == 1

    @pytest.mark.asyncio
    async def test_the_guard_events_are_audited_in_the_same_transaction(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        await save(pg, api, fix_injection_requirement(client_doc(v)), 0)
        actions = [a["action"] for a in pg.audit()]
        assert "requirements_saved" in actions and "requirements_injection_guard_resolved" in actions
        resolved = pg.audit("requirements_injection_guard_resolved")[0]["details"]
        assert resolved["kind"] == "requirement" and resolved["resolution"] == "item_removed" and resolved["revision"] == 1
        assert pg.q("SELECT count(DISTINCT xmin::text) FROM audit_logs") == [(1,)]
        assert pg.q("SELECT (SELECT xmin::text FROM job_criteria WHERE job_id = %s) = (SELECT xmin::text FROM audit_logs LIMIT 1)", (JOB,)) == [(True,)]


# ══ GET: derived, never trusted ═════════════════════════════════════════════════════════════════════════════════════════════════
class TestGet:

    @pytest.mark.asyncio
    async def test_guarded_readiness_issues_provenance_and_original_access(self, pg, api):
        state = combined_state()
        seed_state(pg, state)
        v = await get(pg, api)
        assert v["readiness"]["guarded"] is True and v["readiness"]["basis"] == "pipeline"
        assert v["readiness"]["state"] == "needs_injection_review" and v["readiness"]["can_proceed"] is False
        assert states(v) == ("needs_injection_review",) * 2
        assert [i["gate"] for i in v["unresolved_issues"]] == ["injection", "injection", "split_or", "classification", "classification", "conflict"]
        assert set(v["gates"]) == {"injection", "split_or", "classification", "conflict"}
        assert [w["kind"] for w in v["normalized_warnings"]] == ["importance_conflict"]
        p = v["pipeline"]
        assert p["status"] == "ok" and p["available"] and p["provenance"] == {"extraction_prompt": PROMPT, "model": MODEL}
        assert p["raw_response"]["text"] == FIX["raw"].replace(FIX["raw"], json.loads(json.dumps(state["raw_response"]["text"])))
        assert p["raw_ai_output"] == state["raw_ai_output"] and p["component_versions"]["pipeline"] == "requirements-v2-pipeline-1"
        assert v["original"]["categories"] == state["original"]["categories"] and v["can_edit"] is True

    @pytest.mark.asyncio
    async def test_a_viewer_gets_the_metadata_but_not_the_raw_text(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api, VIEWER())
        assert v["can_edit"] is False and v["pipeline"]["status"] == "ok"
        assert "text" not in v["pipeline"]["raw_response"] and "raw_ai_output" not in v["pipeline"] and v["pipeline"]["raw_response"]["sha256"]

    @pytest.mark.asyncio
    async def test_stored_derived_values_are_ignored_and_recomputed(self, pg, api):
        seed_state(pg, combined_state())
        pg.q("""UPDATE job_criteria SET analysis_json = analysis_json || %s::jsonb WHERE job_id = %s""",
             (json.dumps({"readiness": {"state": "ready", "can_proceed": True}, "gates": {}, "unresolved_issues": [], "can_proceed": True}), JOB))
        v = await get(pg, api)
        assert v["readiness"]["can_proceed"] is False and v["readiness"]["state"] == "needs_injection_review" and v["unresolved_issues"]

    @pytest.mark.asyncio
    async def test_a_tampered_issue_status_inside_the_record_cannot_unblock(self, pg, api):
        seed_state(pg, combined_state())
        pg.q("""UPDATE job_criteria SET analysis_json = jsonb_set(analysis_json, '{requirements_pipeline,review_records,injection,issues}',
                  (SELECT jsonb_agg(i || '{"status":"resolved","resolution":"forged"}'::jsonb) FROM jsonb_array_elements(
                     analysis_json #> '{requirements_pipeline,review_records,injection,issues}') i)) WHERE job_id = %s""", (JOB,))
        v = await get(pg, api)
        assert v["readiness"]["state"] == "needs_injection_review" and not v["readiness"]["can_proceed"]

    @pytest.mark.asyncio
    async def test_a_damaged_record_fails_closed_for_reads_and_writes_but_the_data_stays_readable(self, pg, api):
        state = combined_state()
        seed_state(pg, state)
        pg.q("""UPDATE job_criteria SET analysis_json = jsonb_set(analysis_json, '{requirements_pipeline,raw_response,text}', '"tampered"') WHERE job_id = %s""", (JOB,))
        before = pg.row()
        v = await get(pg, api)
        assert v["pipeline"]["status"] == "invalid_record" and v["readiness"]["state"] == "pipeline_record_invalid"
        assert v["readiness"]["can_proceed"] is False and v["readiness"]["guarded"] is False and v["unresolved_issues"] is None
        assert v["requirements"]["categories"] == state["requirements"]["categories"] and v["original"]["categories"] == state["original"]["categories"]
        e = await raises(save(pg, api, client_doc(v), 0), 409, "pipeline_record_invalid")
        assert e.extra["errors"]
        await raises(call(pg, api.confirm_no_score, user(), JOB, 0), 409, "pipeline_record_invalid")
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 0, "x", "conflict"), 409, "pipeline_record_invalid")
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_a_record_for_a_different_snapshot_is_refused(self, pg, api):
        seed_state(pg, combined_state())
        pg.q("""UPDATE job_criteria SET original_analysis_json = jsonb_set(original_analysis_json, '{requirements,categories,skills,weight}', '99') WHERE job_id = %s""", (JOB,))
        v = await get(pg, api)
        assert v["pipeline"]["status"] == "invalid_record" and "original snapshot does not match" in " ".join(v["pipeline"]["errors"])

    @pytest.mark.asyncio
    async def test_other_tenant_and_missing_job_see_nothing(self, pg, api):
        seed_state(pg, combined_state())
        await raises(call(pg, api.get_requirements, user(tenant=T2), JOB), 404, "not_found")
        await raises(call(pg, api.get_requirements, user(), JOB2), 404, "not_found")


# ══ compatibility: v2 records without pipeline data ═══════════════════════════════════════════════════════════════════════════════
class TestCompatibility:

    @pytest.mark.asyncio
    async def test_a_record_without_pipeline_data_is_labelled_not_guarded_and_keeps_its_original(self, pg, api):
        state = combined_state()
        seed_state(pg, state, with_record=False)
        v = await get(pg, api)
        assert v["pipeline"]["status"] == "unavailable" and v["pipeline"]["available"] is False and "No provenance is claimed" in v["pipeline"]["message"]
        assert "provenance" not in v["pipeline"] and "raw_response" not in v["pipeline"] and "component_versions" not in v["pipeline"]
        assert v["readiness"]["guarded"] is False and v["readiness"]["basis"] == "frozen_only"
        assert v["unresolved_issues"] is None and v["gates"] is None and v["normalized_warnings"] is None and v["informational"] is None
        assert "by_policy" not in v["readiness"]
        assert v["original"]["categories"] == state["original"]["categories"]
        assert v["readiness"]["state"] in ("needs_classification_review", "ready")                  # the frozen answer, labelled as such

    @pytest.mark.asyncio
    async def test_saves_still_work_and_never_invent_a_record(self, pg, api):
        seed_state(pg, combined_state(), with_record=False)
        v = await get(pg, api)
        doc = client_doc(v)
        doc["categories"]["soft_skills"]["items"][0]["text"] = "Written communication"
        out = await save(pg, api, doc, 0)
        assert out["revision"] == 1 and STORAGE_KEY not in pg.row()["analysis"] and out["pipeline"]["status"] == "unavailable"
        assert not [a for a in pg.audit() if "guard" in a["action"] or "adapter" in a["action"]]

    @pytest.mark.asyncio
    async def test_conflict_acknowledgment_needs_a_record_and_injection_never_has_one(self, pg, api):
        seed_state(pg, combined_state(), with_record=False)
        before = pg.row()
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 0, "x", "conflict"), 409, "pipeline_data_missing")
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 0, "x", "injection"), 409, "gate_not_acknowledgeable")
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_preferred_only_confirmation_keeps_the_frozen_behaviour_without_a_record(self, pg, api):
        from test_requirements_v2_api import preferred_only_job
        r = preferred_only_job()
        pg.q("INSERT INTO jobs (job_id, tenant_id, client_organization_id) VALUES (%s, %s, NULL)", (JOB, T1))
        pg.q("""INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version) VALUES (%s, %s, %s, 2)""",
             (JOB, json.dumps({"requirements": r.requirements}), json.dumps({"requirements": r.original})))
        out = await call(pg, api.confirm_no_score, user(), JOB, 0)
        assert out["readiness"]["scoring_mode"] == "none" and out["readiness"]["guarded"] is False


# ══ save semantics ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class TestSave:

    @pytest.mark.asyncio
    async def test_invalid_weights_or_structure_answer_422_and_write_nothing(self, pg, api):
        seed_state(pg, combined_state())
        before = pg.row()
        v = await get(pg, api)
        bad = client_doc(v)
        bad["categories"]["skills"]["weight"] = 70                                  # total no longer 100
        e = await raises(save(pg, api, bad, 0), 422, "invalid_requirements")
        assert e.extra["issues"]
        worse = client_doc(v)
        worse["categories"]["skills"]["items"][0]["importance"] = "sometimes"
        await raises(save(pg, api, worse, 0), 422, "invalid_requirements")
        await raises(save(pg, api, {"schema_version": 2, "categories": {}}, 0), 422, "invalid_requirements")
        assert pg.row() == before and pg.audit() == []                                   # document, record, weights, revision, retired, audit

    @pytest.mark.asyncio
    async def test_a_valid_document_with_unresolved_blockers_saves_but_cannot_proceed(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        doc = client_doc(v)
        doc["categories"]["soft_skills"]["items"][0]["text"] = "Good written English"
        out = await save(pg, api, doc, 0)
        assert out["revision"] == 1 and out["changed"] is True
        assert out["readiness"]["can_proceed"] is False and out["readiness"]["state"] == "needs_injection_review"
        assert {i["gate"] for i in out["unresolved_issues"]} >= {"injection", "split_or"}
        assert pg.row()["analysis"]["requirements"]["categories"]["soft_skills"]["items"][0]["text"] == "Good written English"

    @pytest.mark.asyncio
    async def test_client_supplied_guard_records_raw_output_metadata_and_readiness_are_discarded(self, pg, api):
        state = combined_state()
        seed_state(pg, state)
        before = pg.row()
        v = await get(pg, api)
        doc = client_doc(v)
        forged_rr = {"injection": {"issues": []}, "split_or": {"issues": []}, "model_warnings": {"item_warnings": [], "acknowledgments": []}}
        doc.update(requirements_pipeline={"review_records": forged_rr}, review_records=forged_rr, raw_response={"text": "forged"}, raw_ai_output={"x": 1},
                   component_versions={"pipeline": "forged"}, provenance={"model": "forged"}, readiness={"state": "ready", "can_proceed": True}, gates={},
                   unresolved_issues=[], normalized_warnings=[], original={"categories": {}}, scoring_confirmation={"user_id": "x"},
                   classification_review={"warnings": [], "acknowledgments": []}, structure_review={"records": []})
        doc["categories"]["soft_skills"]["items"][0]["text"] = "Good written English"
        out = await save(pg, api, doc, 0)
        for k in ("requirements_pipeline", "review_records", "raw_response", "raw_ai_output", "component_versions", "provenance", "readiness", "gates",
                  "unresolved_issues", "normalized_warnings", "original", "scoring_confirmation"):
            assert k in out["discarded_client_fields"], k
        row = pg.row()
        rec = row["analysis"][STORAGE_KEY]
        assert rec["raw_response"] == before["analysis"][STORAGE_KEY]["raw_response"] and rec["raw_ai_output"] == state["raw_ai_output"]
        assert rec["provenance"]["model"] == MODEL and rec["component_versions"]["pipeline"] == "requirements-v2-pipeline-1"
        assert rec["review_records"]["injection"]["issues"] and rec["review_records"]["split_or"]["issues"]
        assert out["readiness"]["can_proceed"] is False and out["readiness"]["state"] == "needs_injection_review"
        assert row["analysis"]["requirements"].get("scoring_confirmation") is None and "forged" not in json.dumps(row["analysis"])
        assert immutable(before, row)

    @pytest.mark.asyncio
    async def test_body_level_echo_of_server_state_is_discarded_and_listed(self, pg, api):
        from routers.job_requirements import AcknowledgeWarningRequest, SaveRequirementsRequest
        body = SaveRequirementsRequest(expected_revision=0, requirements={"x": 1}, readiness={"can_proceed": True}, review_records={"a": 1}, raw_response="x")
        assert sorted(body.discarded()) == ["raw_response", "readiness", "review_records"]
        with pytest.raises(Exception):
            SaveRequirementsRequest(expected_revision=0, requirements={}, surprise=1)
        ack = AcknowledgeWarningRequest(expected_revision=0, warning_id="w", provenance={"model": "x"})
        assert ack.gate == "classification" and ack.discarded() == ["provenance"]
        seed_state(pg, combined_state())
        v = await get(pg, api)
        doc = fix_injection_requirement(client_doc(v))
        out = await call(pg, api.save_requirements, user(), JOB, 0, doc, ["readiness", "review_records"])
        assert out["discarded_client_fields"] == ["body.readiness", "body.review_records"]

    @pytest.mark.asyncio
    async def test_roles_tenants_and_revisions_are_unchanged(self, pg, api):
        seed_state(pg, combined_state())
        before = pg.row()
        doc = client_doc(await get(pg, api))
        await raises(save(pg, api, doc, 0, VIEWER()), 403, "forbidden")
        await raises(save(pg, api, doc, 0, user(tenant=T2)), 404, "not_found")
        stale = await raises(save(pg, api, doc, 5), 409, "requirements_revision_conflict")
        assert stale.extra["current"]["pipeline"]["status"] == "ok" and stale.extra["current"]["readiness"]["guarded"] is True
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_retired_ids_and_weight_columns_follow_every_pipeline_save(self, pg, api):
        state = combined_state()
        seed_state(pg, state)
        v = await get(pg, api)
        doc = fix_injection_requirement(client_doc(v))
        rust = item(client_doc(v), "20 years of Rust experience")[1]["id"]
        await save(pg, api, doc, 0)
        row = pg.row()
        assert row["retired"] == [rust] and row["weights"] == {c: row["analysis"]["requirements"]["categories"][c]["weight"] for c in CATEGORIES}
        back = client_doc(await get(pg, api))
        back["categories"]["other_requirements"]["items"].append({"id": rust, "text": "20 years of Rust experience", "importance": "preferred", "weight": None})
        await raises(save(pg, api, back, 1), 422, "invalid_requirements")                # a retired id cannot come back
        assert pg.row()["revision"] == 1


# ══ acknowledgment ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════
class TestAcknowledgment:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("gate", ["injection", "split_or", "structure", "confirmation", "validation", "no_items", "extraction"])
    async def test_gates_without_an_acknowledgment_refuse_it(self, pg, api, gate):
        seed_state(pg, combined_state())
        before = pg.row()
        v = await get(pg, api)
        wid = (issue_ids(v, gate) or ["anything"])[0]
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 0, wid, gate), 409, "gate_not_acknowledgeable")
        assert pg.row() == before and pg.audit() == []

    @pytest.mark.asyncio
    async def test_an_unknown_gate_is_a_422_and_a_non_editor_a_403(self, pg, api):
        seed_state(pg, combined_state())
        await raises(call(pg, api.acknowledge_warning, user(), JOB, 0, "x", "mystery"), 422, "unknown_gate")
        await raises(call(pg, api.acknowledge_warning, VIEWER(), JOB, 0, "x", "injection"), 403, "forbidden")

    @pytest.mark.asyncio
    async def test_acknowledging_classification_and_conflict_never_clears_injection_or_split_or(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        rev = 0
        for gate in ("classification", "conflict"):
            for wid in issue_ids(v, gate):
                out = await call(pg, api.acknowledge_warning, user(), JOB, rev, wid, gate)
                rev = out["revision"]
                v = out
        assert {i["gate"] for i in v["unresolved_issues"]} == {"injection", "split_or"}
        assert states(v) == ("needs_injection_review",) * 2 and v["readiness"]["can_proceed"] is False
        assert pg.audit("requirements_conflict_acknowledged") and pg.audit("requirements_classification_acknowledged")
        # an acknowledgment that is already given, or for a warning that does not exist
        await raises(call(pg, api.acknowledge_warning, user(), JOB, rev, "nope", "conflict"), 404, "classification_warning_not_found")

    @pytest.mark.asyncio
    async def test_the_conflict_acknowledgment_is_a_server_owned_record_with_the_users_identity(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        wid = issue_ids(v, "conflict")[0]
        await call(pg, api.acknowledge_warning, ADMIN(), JOB, 0, wid, "conflict")
        ack = pg.row()["analysis"][STORAGE_KEY]["review_records"]["model_warnings"]["acknowledgments"]
        assert len(ack) == 1 and ack[0]["user_id"] == U_ADMIN and ack[0]["warning_id"] == wid
        (audit,) = pg.audit("requirements_conflict_acknowledged")
        assert audit["user_id"] == U_ADMIN and audit["details"]["warning_id"] == wid and audit["details"]["revision"] == 1


# ══ combined lifecycles through the API ═════════════════════════════════════════════════════════════════════════════════════════════
class TestCombinedLifecycle:

    async def walk(self, pg, api, policy):
        set_policy(pg, policy)
        seed_state(pg, combined_state())
        v = await get(pg, api)
        assert states(v) == ("needs_injection_review",) * 2
        rev = 0
        v = await save(pg, api, fix_injection_requirement(client_doc(v)), rev); rev += 1
        assert states(v) == ("needs_injection_review",) * 2                                   # the contaminated weights keep it open
        assert [i["kind"] for i in v["unresolved_issues"] if i["gate"] == "injection"] == ["injection_weights"]
        v = await save(pg, api, fix_injection_weights(client_doc(v)), rev); rev += 1
        assert states(v) == ("needs_split_or_review",) * 2
        v = await save(pg, api, fix_split_or(client_doc(v)), rev); rev += 1
        assert states(v) == ("needs_classification_review", "ready")
        return v, rev

    @pytest.mark.asyncio
    @pytest.mark.parametrize("policy", [True, False])
    async def test_simultaneous_issues_are_cleared_one_by_one_in_precedence_order(self, pg, api, policy):
        v, rev = await self.walk(pg, api, policy)
        assert v["readiness"]["state"] == ("needs_classification_review" if policy else "ready")
        assert v["readiness"]["can_proceed"] is (not policy)
        for wid in issue_ids(v, "classification"):
            v = await call(pg, api.acknowledge_warning, user(), JOB, rev, wid, "classification"); rev += 1
        assert states(v) == ("needs_conflict_review", "ready")
        for wid in issue_ids(v, "conflict"):
            v = await call(pg, api.acknowledge_warning, user(), JOB, rev, wid, "conflict"); rev += 1
        assert states(v) == ("ready", "ready") and v["unresolved_issues"] == [] and v["readiness"]["can_proceed"] is True
        assert pg.row()["revision"] == rev

    @pytest.mark.asyncio
    async def test_a_policy_change_takes_effect_without_any_write(self, pg, api):
        v, rev = await self.walk(pg, api, True)
        before = pg.row()
        assert v["readiness"]["state"] == "needs_classification_review"
        set_policy(pg, False)
        v2 = await get(pg, api)
        assert v2["readiness"]["state"] == "ready" and v2["readiness"]["can_proceed"] and v2["readiness"]["by_policy"]["ack_required"]["state"] == "needs_classification_review"
        assert {i["gate"] for i in v2["unresolved_issues"]} == {"classification", "conflict"}                 # still visible
        set_policy(pg, True)
        assert (await get(pg, api))["readiness"]["state"] == "needs_classification_review"
        assert pg.row() == before
        # an unparseable setting fails closed to Yes
        pg.q("UPDATE system_config SET value = 'maybe' WHERE key = %s", (POLICY_KEY,))
        assert (await get(pg, api))["readiness"]["state"] == "needs_classification_review"

    @pytest.mark.asyncio
    async def test_reversal_reopens_only_the_gate_that_was_reversed(self, pg, api):
        v, rev = await self.walk(pg, api, True)
        for wid in issue_ids(v, "classification"):
            v = await call(pg, api.acknowledge_warning, user(), JOB, rev, wid, "classification"); rev += 1
        for wid in issue_ids(v, "conflict"):
            v = await call(pg, api.acknowledge_warning, user(), JOB, rev, wid, "conflict"); rev += 1
        assert states(v) == ("ready", "ready")
        # reverse the weights correction: soft_skills back to the contaminated applied value
        v = await save(pg, api, fix_injection_weights(client_doc(v), skills=20, experience=30, soft=50), rev); rev += 1
        assert states(v) == ("needs_injection_review",) * 2 and [i["kind"] for i in v["unresolved_issues"]] == ["injection_weights"]
        assert pg.audit("requirements_injection_guard_reopened")
        v = await save(pg, api, fix_injection_weights(client_doc(v)), rev); rev += 1
        assert states(v) == ("ready", "ready")                                                         # earlier acknowledgments are still valid
        # reverse the split-OR correction: the removed option comes back as an item quoting the same sentence is not possible from a client
        # (source wording is server-owned), so a plain re-added "Java" is a new recruiter item and does not reopen it
        doc = client_doc(v)
        doc["categories"]["skills"]["items"].append({"text": "Java", "importance": "required", "weight": None})
        v = await save(pg, api, equalize(doc, "skills"), rev); rev += 1
        assert v["readiness"]["can_proceed"] is True and not any(i["gate"] == "split_or" for i in v["unresolved_issues"])
        # reversing a classification: PostgreSQL Preferred -> Required -> Preferred reopens its classification and conflict warnings
        doc = client_doc(v)
        pg_item = item(doc, "PostgreSQL")[1]
        pg_item["importance"], pg_item["weight"] = "required", None
        v = await save(pg, api, equalize(doc, "skills"), rev); rev += 1
        assert states(v) == ("ready", "ready")
        doc = client_doc(v)
        item(doc, "PostgreSQL")[1].update(importance="preferred", weight=None)
        v = await save(pg, api, equalize(doc, "skills"), rev); rev += 1
        assert states(v) == ("needs_classification_review", "ready") and {"classification", "conflict"} <= {i["gate"] for i in v["unresolved_issues"]}

    @pytest.mark.asyncio
    async def test_item_removal_resolves_what_depended_on_the_item(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        doc = drop(client_doc(v), "PostgreSQL")
        v = await save(pg, api, equalize(doc, "skills"), 0)
        assert {i["gate"] for i in v["unresolved_issues"]} == {"injection", "split_or", "classification"}        # the conflict left with the item
        assert pg.audit("requirements_warning_adapter_resolved")
        v = await save(pg, api, fix_injection_requirement(client_doc(v)), 1)
        doc = fix_split_or(fix_injection_weights(client_doc(v)))
        v = await save(pg, api, doc, 2)
        assert v["unresolved_issues"] == [] and states(v) == ("ready", "ready")                       # nothing was left that depended on the removed items
        # remove every item: needs_items
        empty = client_doc(v)
        for c in CATEGORIES:
            empty["categories"][c].update(items=[], weight=0)
        v = await save(pg, api, empty, 3)
        assert states(v) == ("needs_items",) * 2 and v["readiness"]["can_proceed"] is False

    @pytest.mark.asyncio
    async def test_structure_review_follows_the_guards_and_its_confirmation_is_guarded_by_precedence(self, pg, api):
        v, rev = await self.walk(pg, api, False)
        assert v["readiness"]["state"] == "ready"
        doc = client_doc(v)
        item(doc, "Python")[1]["text"] = "Python programming"
        v = await save(pg, api, doc, rev); rev += 1
        assert v["readiness"]["state"] == "needs_structure_review" and v["gates"]["structure"]["open"] == 1
        await raises(call(pg, api.acknowledge_warning, user(), JOB, rev, issue_ids(v, "structure")[0], "structure"), 409, "gate_not_acknowledgeable")
        iid = item(v["requirements"], "Python programming")[1]["id"]
        v = await call(pg, api.confirm_structure_review, user(), JOB, rev, iid); rev += 1
        assert v["readiness"]["state"] == "ready" and v["readiness"]["can_proceed"]
        # an injection issue reopened later outranks the confirmed structure
        v = await save(pg, api, fix_injection_weights(client_doc(v), skills=20, experience=30, soft=50), rev)
        assert v["readiness"]["state"] == "needs_injection_review"

    @pytest.mark.asyncio
    async def test_preferred_only_confirmation_is_refused_while_any_guard_is_open(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        doc = client_doc(v)
        for c in CATEGORIES:
            doc["categories"][c]["weight"] = 0
            for i in doc["categories"][c]["items"]:
                i["importance"], i["weight"] = "preferred", None
        v = await save(pg, api, doc, 0)
        assert v["readiness"]["can_proceed"] is False
        e = await raises(call(pg, api.confirm_no_score, user(), JOB, 1), 409, "nothing_to_confirm")
        assert e.extra["readiness_state"] == "needs_injection_review"
        before = pg.row()
        await raises(call(pg, api.confirm_no_score, user(), JOB, 1), 409, "nothing_to_confirm")
        assert pg.row() == before
        # guards settled (the contaminated weight is gone with the weights; the injected requirement and the split are removed)
        v = await save(pg, api, fix_split_or_pref(fix_injection_requirement(client_doc(v))), 1)
        assert v["readiness"]["state"] in ("needs_classification_review", "needs_confirmation")
        set_policy(pg, False)
        v = await call(pg, api.confirm_no_score, user(), JOB, 2)
        assert v["readiness"]["state"] == "ready" and v["readiness"]["scoring_mode"] == "none" and v["readiness"]["can_proceed"]
        assert pg.audit("requirements_preferred_only_confirmed")


def fix_split_or_pref(doc):
    return drop(doc, "Java")


# ══ concurrency on real connections ═══════════════════════════════════════════════════════════════════════════════════════════════
class TestConcurrency:

    @pytest.mark.asyncio
    async def test_one_of_many_simultaneous_saves_wins_and_the_record_matches_the_winning_document(self, pg, api):
        seed_state(pg, combined_state())
        base = client_doc(await get(pg, api))
        docs = []
        for n in range(8):
            d = fix_injection_requirement(copy.deepcopy(base)) if n % 2 else copy.deepcopy(base)
            d["categories"]["soft_skills"]["items"][0]["text"] = f"edit {n}"
            docs.append(d)
        results = await asyncio.gather(*[save(pg, api, d, 0) for d in docs], return_exceptions=True)
        wins = [r for r in results if isinstance(r, dict)]
        assert len(wins) == 1 and all(isinstance(r, api.ApiError) and r.code == "requirements_revision_conflict" for r in results if not isinstance(r, dict))
        row = pg.row()
        assert row["revision"] == 1
        removed_rust = "20 years of Rust" not in json.dumps(row["analysis"]["requirements"]["categories"])
        statuses = {i["status"] for i in row["analysis"][STORAGE_KEY]["review_records"]["injection"]["issues"] if i["kind"] == "requirement"}
        assert statuses == ({"resolved"} if removed_rust else {"open"})                                     # record and document never diverge
        assert len(pg.audit("requirements_saved")) == 1

    @pytest.mark.asyncio
    async def test_a_save_and_two_acknowledgments_racing_at_one_revision(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        doc = fix_injection_requirement(client_doc(v))
        cwid, kwid = issue_ids(v, "classification")[0], issue_ids(v, "conflict")[0]
        results = await asyncio.gather(save(pg, api, doc, 0), call(pg, api.acknowledge_warning, user(), JOB, 0, cwid, "classification"),
                                       call(pg, api.acknowledge_warning, user(), JOB, 0, kwid, "conflict"), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in results) == 1 and pg.row()["revision"] == 1
        losers = [x for x in results if not isinstance(x, dict)]
        assert len(losers) == 2 and all(getattr(x, "code", "") == "requirements_revision_conflict" for x in losers)

    @pytest.mark.asyncio
    async def test_sequential_writers_each_see_the_previous_writers_record(self, pg, api):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        v = await save(pg, api, fix_injection_requirement(client_doc(v)), 0)
        await raises(save(pg, api, fix_injection_weights(client_doc(v)), 0), 409, "requirements_revision_conflict")      # stale writer
        v = await save(pg, api, fix_injection_weights(client_doc(v)), 1)
        assert not any(i["gate"] == "injection" for i in v["unresolved_issues"]) and pg.row()["revision"] == 2
        recs = pg.row()["analysis"][STORAGE_KEY]["review_records"]["injection"]["issues"]
        assert {i["kind"]: i["status"] for i in recs} == {"requirement": "resolved", "weights": "resolved"}


# ══ rollback ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class TestRollback:

    def break_audit(self, pg):
        pg.q("CREATE OR REPLACE FUNCTION fail_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit down'; END $$")
        pg.q("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION fail_audit()")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["save", "classification", "conflict"])
    async def test_a_failed_audit_rolls_back_document_record_weights_and_revision(self, pg, api, operation):
        seed_state(pg, combined_state())
        v = await get(pg, api)
        before = pg.row()
        self.break_audit(pg)
        if operation == "save":
            coro = save(pg, api, fix_injection_weights(fix_injection_requirement(client_doc(v))), 0)
        elif operation == "classification":
            coro = call(pg, api.acknowledge_warning, user(), JOB, 0, issue_ids(v, "classification")[0], "classification")
        else:
            coro = call(pg, api.acknowledge_warning, user(), JOB, 0, issue_ids(v, "conflict")[0], "conflict")
        with pytest.raises(Exception, match="audit down"):
            await coro
        assert pg.row() == before and pg.q("SELECT count(*) FROM audit_logs") == [(0,)]
        assert pg.q("SELECT job_id FROM job_criteria WHERE job_id = %s FOR UPDATE NOWAIT", (JOB,))            # the lock was released
        pg.q("DROP TRIGGER fail_audit ON audit_logs")
        assert (await get(pg, api))["revision"] == 0

    @pytest.mark.asyncio
    async def test_a_422_mid_transaction_leaves_everything_unlocked_and_unchanged(self, pg, api):
        seed_state(pg, combined_state())
        before = pg.row()
        bad = client_doc(await get(pg, api))
        bad["categories"]["skills"]["weight"] = 1
        await raises(save(pg, api, bad, 0), 422, "invalid_requirements")
        assert pg.row() == before and pg.q("SELECT job_id FROM job_criteria WHERE job_id = %s FOR UPDATE NOWAIT", (JOB,))
