"""Gates on a real PostgreSQL 16: saving a Mandatory Gate through the requirements editor (who may, what the server stamps, which
linked-question rules the database enforces) and the knockout-question editor's id-preserving save and its guards."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services import knockout_questions_service as K
from services import requirements_edit as E
from test_gates_postgres import (  # noqa: F401  (fixtures and seeding helpers)
    _answer, _application, _doc, _in_tenant, _job, _question, built, engines, world)

pytestmark = pytest.mark.asyncio

NOW = "2026-10-17T12:00:00+00:00"


def _user(world, label, role):
    return E.EditingUser(user_id=world[label]["users"][role], tenant_id=world[label]["tenant"], role=role, email=f"{role}@example.test")


def _set_gate(item="req_py", method="candidate_question", question=None, action="not_eligible"):
    return E.parse_operations([{"op": "set_gate", "item_id": item, "verification_method": method, "failure_action": action,
                                "linked_question_id": question}])


async def _save(engines, world, label, role, job, ops, revision=3):
    user = _user(world, label, role)

    async def work(session):
        return await E.save_operations(session, user, job, client_revision=revision, ops=ops, now=NOW)

    return await _in_tenant(engines(), world[label]["tenant"], work)


def _stored_gate(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json, requirements_revision FROM job_criteria WHERE job_id = %s", (job,))
    analysis, revision = cur.fetchone()
    return analysis["requirements"]["categories"]["skills"]["items"][0].get("gate"), revision


# ── who may confirm a gate, and what the server stamps ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "hr_manager"])
async def test_admin_and_hr_manager_confirm_a_gate_and_the_server_stamps_user_and_time(built, engines, world, role):
    job = _job(built, world["a"])
    result = await _save(engines, world, "a", role, job, _set_gate(method="cv_evidence"))
    gate, revision = _stored_gate(built, job)
    assert revision == result.revision == 4
    assert gate["mandatory"] is True and gate["confirmed_by"] == world["a"]["users"][role] and gate["confirmed_at"] == NOW
    assert result.audit.gate_changes == (("req_py", "confirmed"),)


@pytest.mark.parametrize("role", ["recruiter", "viewer"])
async def test_a_recruiter_and_a_viewer_cannot_confirm_a_gate(built, engines, world, role):
    job = _job(built, world["a"])
    with pytest.raises(E.PermissionDenied):
        await _save(engines, world, "a", role, job, _set_gate(method="cv_evidence"))
    assert _stored_gate(built, job) == (None, 3)


async def test_another_tenant_cannot_change_the_gate_of_a_job_it_does_not_own(built, engines, world):
    job = _job(built, world["a"])
    with pytest.raises(E.JobNotFound):
        await _save(engines, world, "b", "admin", job, _set_gate(method="cv_evidence"))
    assert _stored_gate(built, job) == (None, 3)


# ── the linked knockout question ────────────────────────────────────────────────────────────────────────────────────

async def test_a_candidate_question_gate_linked_to_a_valid_gate_question_of_the_job_is_saved(built, engines, world):
    job = _job(built, world["a"])
    question = _question(built, world["a"], job, gate=True)
    await _save(engines, world, "a", "admin", job, _set_gate(question=question))
    gate, _ = _stored_gate(built, job)
    assert gate["linked_question_id"] == question and gate["verification_method"] == "candidate_question"


async def _issue_codes(engines, world, job, ops):
    with pytest.raises(E.InvalidRequirements) as caught:
        await _save(engines, world, "a", "admin", job, ops)
    return {i.code for i in caught.value.issues}


async def test_a_question_that_does_not_exist_cannot_be_linked(built, engines, world):
    job = _job(built, world["a"])
    assert "linked_question_not_found" in await _issue_codes(engines, world, job, _set_gate(question=str(uuid.uuid4())))
    assert _stored_gate(built, job) == (None, 3)


async def test_a_question_of_another_job_cannot_be_linked(built, engines, world):
    job, other = _job(built, world["a"]), _job(built, world["a"])
    foreign = _question(built, world["a"], other, gate=True)
    assert "linked_question_other_job" in await _issue_codes(engines, world, job, _set_gate(question=foreign))


async def test_a_question_of_another_tenant_is_reported_as_not_found(built, engines, world):
    job = _job(built, world["a"])
    foreign_job = _job(built, world["b"])
    foreign = _question(built, world["b"], foreign_job, gate=True)
    assert "linked_question_not_found" in await _issue_codes(engines, world, job, _set_gate(question=foreign))


async def test_a_question_that_is_not_an_eligibility_gate_cannot_be_linked(built, engines, world):
    job = _job(built, world["a"])
    advisory = _question(built, world["a"], job, gate=False)
    assert "linked_question_not_a_gate" in await _issue_codes(engines, world, job, _set_gate(question=advisory))


async def test_a_question_without_valid_passing_criteria_cannot_be_linked(built, engines, world):
    job = _job(built, world["a"])
    broken = _question(built, world["a"], job, gate=True, criteria={"passing_answers": []})
    assert "linked_question_invalid_criteria" in await _issue_codes(engines, world, job, _set_gate(question=broken))


async def test_the_save_contradictions_report_their_stable_codes(built, engines, world):
    job = _job(built, world["a"])
    assert "gate_question_link_missing" in await _issue_codes(engines, world, job, _set_gate(question=None))
    assert "gate_contradictory_configuration" in await _issue_codes(engines, world, job, _set_gate(method="manual", question=str(uuid.uuid4())))
    assert "gate_without_verification_method" in await _issue_codes(engines, world, job, _set_gate(method=None))


async def test_one_question_cannot_verify_two_requirements(built, engines, world):
    job = _job(built, world["a"])
    question = _question(built, world["a"], job, gate=True)
    ops = E.parse_operations([
        {"op": "set_gate", "item_id": "req_py", "verification_method": "candidate_question", "failure_action": "not_eligible", "linked_question_id": question},
        {"op": "set_gate", "item_id": "req_sql", "verification_method": "candidate_question", "failure_action": "not_eligible", "linked_question_id": question}])
    assert "gate_question_linked_twice" in await _issue_codes(engines, world, job, ops)


async def test_a_preferred_item_cannot_be_made_a_gate_through_the_editor(built, engines, world):
    job = _job(built, world["a"])
    with pytest.raises(E.InvalidOperation):
        await _save(engines, world, "a", "admin", job, _set_gate(item="req_go", method="cv_evidence"))


# ── the question editor ─────────────────────────────────────────────────────────────────────────────────────────────

def _payload(**kw):
    base = {"question_text": "Do you hold a valid licence?", "question_type": "yes_no", "is_required": True,
            "passing_criteria": {"passing_answers": ["yes"]}}
    base.update(kw)
    return base


async def _save_questions(engines, world, job, questions, label="a"):
    return await _in_tenant(engines(), world[label]["tenant"],
                            lambda s: K.save_job_knockout_questions(s, job, world[label]["tenant"], questions))


async def _stored(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT question_id::text, question_text, is_required, is_eligibility_gate, gate_failure_action FROM job_knockout_questions"
                " WHERE job_id = %s ORDER BY display_order", (job,))
    return cur.fetchall()


async def test_a_question_saved_the_old_way_is_advisory_and_response_required_keeps_its_meaning(built, engines, world):
    job = _job(built, world["a"])
    await _save_questions(engines, world, job, [_payload(is_required=False), _payload(question_text="Second?")])
    rows = await _stored(built, job)
    assert [(r[2], r[3], r[4]) for r in rows] == [(False, False, "not_eligible"), (True, False, "not_eligible")]


async def test_response_required_and_eligibility_gate_are_independent_settings(built, engines, world):
    job = _job(built, world["a"])
    await _save_questions(engines, world, job, [_payload(is_required=False, is_eligibility_gate=True, gate_failure_action="hold_for_review")])
    [(_, _, required, gate, action)] = await _stored(built, job)
    assert (required, gate, action) == (False, True, "hold_for_review")


async def test_saving_with_ids_keeps_the_question_and_its_answers_and_a_save_without_ids_still_replaces(built, engines, world):
    job = _job(built, world["a"])
    keep = _question(built, world["a"], job, gate=True, text_="Keep me")
    drop = _question(built, world["a"], job, gate=False, text_="Drop me")
    app = _application(built, world["a"], job)
    _answer(built, app, keep, "yes")
    await _save_questions(engines, world, job, [_payload(question_id=keep, question_text="Keep me, reworded", is_eligibility_gate=True)])
    rows = await _stored(built, job)
    assert [(r[0], r[1]) for r in rows] == [(keep, "Keep me, reworded")]
    cur = built.conn.cursor()
    cur.execute("SELECT count(*) FROM application_knockout_answers WHERE question_id = %s", (keep,))
    assert cur.fetchone()[0] == 1                                               # the answer survived the save
    cur.execute("SELECT count(*) FROM job_knockout_questions WHERE question_id = %s", (drop,))
    assert cur.fetchone()[0] == 0
    await _save_questions(engines, world, job, [_payload(question_text="Brand new")])
    assert [r[1] for r in await _stored(built, job)] == ["Brand new"]           # without ids, the old behaviour: replace


async def test_a_question_id_of_another_job_or_tenant_is_refused_and_nothing_changes(built, engines, world):
    job, other = _job(built, world["a"]), _job(built, world["b"])
    foreign = _question(built, world["b"], other, gate=True)
    with pytest.raises(HTTPException) as caught:
        await _save_questions(engines, world, job, [_payload(question_id=foreign)])
    assert caught.value.status_code == 422
    assert len(await _stored(built, other)) == 1


async def test_a_gate_question_without_valid_passing_criteria_is_refused(built, engines, world):
    job = _job(built, world["a"])
    with pytest.raises(HTTPException) as caught:
        await _save_questions(engines, world, job, [_payload(is_eligibility_gate=True, passing_criteria={"passing_answers": []})])
    assert caught.value.status_code == 422
    with pytest.raises(HTTPException):
        await _save_questions(engines, world, job, [_payload(is_eligibility_gate=True, passing_criteria=None)])


@pytest.mark.parametrize("extra", [{"is_eligibility_gate": "yes"}, {"gate_failure_action": "reject"}, {"question_id": "nope"}])
async def test_malformed_gate_controls_are_refused(built, engines, world, extra):
    job = _job(built, world["a"])
    with pytest.raises(HTTPException) as caught:
        await _save_questions(engines, world, job, [_payload(**extra)])
    assert caught.value.status_code == 422


async def _linked_world(built, engines, world):
    job = _job(built, world["a"])
    question = _question(built, world["a"], job, gate=True)
    await _save(engines, world, "a", "admin", job, _set_gate(question=question))
    return job, question


async def test_a_question_linked_to_a_requirement_gate_cannot_be_removed(built, engines, world):
    job, question = await _linked_world(built, engines, world)
    with pytest.raises(HTTPException) as caught:
        await _save_questions(engines, world, job, [_payload(question_text="Something else")])
    assert caught.value.status_code == 409 and caught.value.detail["code"] == "question_linked_to_gate"
    assert [r[0] for r in await _stored(built, job)] == [question]


async def test_a_question_linked_to_a_requirement_gate_cannot_stop_being_a_gate(built, engines, world):
    job, question = await _linked_world(built, engines, world)
    with pytest.raises(HTTPException) as caught:
        await _save_questions(engines, world, job, [_payload(question_id=question, is_eligibility_gate=False)])
    assert caught.value.status_code == 409
    await _save_questions(engines, world, job, [_payload(question_id=question, is_eligibility_gate=True, question_text="Reworded")])


async def test_the_read_model_shows_the_gate_settings_and_the_linked_requirement(built, engines, world):
    job, question = await _linked_world(built, engines, world)
    other = _question(built, world["a"], job, gate=False, text_="Advisory one")
    rows = await _in_tenant(engines(), world["a"]["tenant"], lambda s: K.get_job_knockout_questions(s, job))
    by_id = {r["question_id"]: r for r in rows}
    assert by_id[question]["is_eligibility_gate"] is True and by_id[question]["linked_requirement"]["item_id"] == "req_py"
    assert by_id[other]["is_eligibility_gate"] is False and by_id[other]["linked_requirement"] is None


async def test_the_public_candidate_view_never_shows_gate_settings_or_passing_criteria(built, engines, world):
    job = _job(built, world["a"])
    _question(built, world["a"], job, gate=True)
    rows = await _in_tenant(engines(), world["a"]["tenant"], lambda s: K.get_public_job_knockout_questions(s, job))
    assert rows and not ({"passing_criteria", "is_eligibility_gate", "gate_failure_action", "linked_requirement"} & set(rows[0]))
