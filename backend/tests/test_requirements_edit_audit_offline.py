"""Audit metadata and the strict audit writer, offline. The payload is built from typed ids, counts, kinds and states only, and
the writer never commits, never swallows an error, and converts only database failures into AuditWriteError."""
from __future__ import annotations

import asyncio
import copy
import json
import re

import pytest
from sqlalchemy.exc import SQLAlchemyError

from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.readiness import compute_readiness

USER = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"
NOW = "2026-10-17T12:00:00+00:00"
JD = "We need Python and SQL. Five years of backend work is preferred."
CANARY = "CANARY"
ID_RE = re.compile(r"^req_[A-Za-z0-9]{1,32}$")
ENUM_RE = re.compile(r"^[a-z_]+$")


def _item(item_id, text, importance, weight=None, **kw):
    return C.make_item(text, importance, item_id=item_id, weight=weight, origin=kw.pop("origin", C.ORIGIN_STATED),
                       source_text=kw.pop("source_text", text), **kw)


def _base() -> dict:
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


def _plan(body, stored=None):
    stored = _base() if stored is None else stored
    original = copy.deepcopy(_base())
    return E.plan_operations(stored, original, [], E.parse_operations(body), user_id=USER, now=NOW, jd_text=JD)


def _leaves(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield ("key", key)
            yield from _leaves(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _leaves(inner)
    else:
        yield ("value", value)


class _Session:
    """A fake AsyncSession that records every call, so the writer's behaviour can be asserted without a database."""

    def __init__(self, error: BaseException | None = None):
        self.calls: list[str] = []
        self.error = error
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append("execute")
        self.statements.append((str(statement), params or {}))
        if self.error is not None:
            raise self.error

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")


def _user() -> E.EditingUser:
    return E.EditingUser(user_id=USER, tenant_id=TENANT, role="admin", email="a@example.test")


# ---- metadata produced by planning ----

def test_planning_records_counts_touched_generated_retired_and_structure_ids():
    body = [
        {"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]},
        {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
         "alternatives": ["Docker", "Containers"], "experience": None},
        {"op": "remove_item", "item_id": "req_exp"},
    ]
    planned = _plan(body)
    audit = planned.audit
    (generated,) = audit.generated_item_ids
    assert ID_RE.match(generated)
    assert audit.operation_counts == {
        "set_text": 1, "set_importance": 0, "set_item_weight": 0, "set_alternatives": 1, "set_experience": 0,
        "set_category_weight": 0, "add_item": 1, "remove_item": 1, "confirm_no_numeric_score": 0,
        "acknowledge_classification_warning": 0, "confirm_structure": 0, "resolve_structural_finding": 0,
        "reorder_items": 0, "set_gate": 0, "clear_gate": 0}
    assert audit.touched_item_ids == ("req_py", "req_sql")
    assert audit.newly_retired_item_ids == ("req_exp",)
    assert set(audit.structure_edits) == {("req_py", "corrected"), (generated, "entered")}
    assert audit.revision_before is None and audit.revision_after is None


def test_readiness_states_before_and_after_come_from_the_documents():
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"}])
    assert planned.audit.readiness_before == compute_readiness(_base(), original=copy.deepcopy(_base()), jd_text=JD).state
    assert planned.audit.readiness_after == compute_readiness(planned.document, original=copy.deepcopy(_base()), jd_text=JD).state


def test_an_unchanged_item_is_not_reported_as_touched():
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}])
    assert planned.audit.touched_item_ids == ()
    assert planned.audit.generated_item_ids == () and planned.audit.newly_retired_item_ids == ()


def test_a_wording_change_to_an_existing_item_is_touched_and_records_no_structure_edit():
    planned = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"}])
    assert planned.audit.touched_item_ids == ("req_sql",)
    assert planned.audit.structure_edits == ()
    assert planned.audit.operation_counts["set_text"] == 1


# ---- the payload carries ids, counts, kinds and states only ----

def test_the_payload_has_exactly_the_approved_keys():
    planned = _plan([{"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]}])
    payload = E.audit_details(planned.audit)
    assert set(payload) == {"operation_counts", "touched_item_ids", "generated_item_ids", "newly_retired_item_ids",
                            "structure_edits", "revision_before", "revision_after", "readiness", "review_actions", "gate_changes"}
    assert set(payload["readiness"]) == {"before", "after"}
    assert all(set(edit) == {"item_id", "kind"} for edit in payload["structure_edits"])


def test_no_free_text_reaches_the_payload_even_when_the_request_carries_it():
    body = [
        {"op": "set_text", "item_id": "req_sql", "text": f"{CANARY}-REQUIREMENT-TEXT"},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", f"{CANARY}-ALTERNATIVE"]},
        {"op": "add_item", "category": "experience", "text": f"{CANARY}-ADDED", "importance": "preferred", "weight": None,
         "alternatives": [f"{CANARY}-A", f"{CANARY}-B"], "experience": {"subject": f"{CANARY}-SUBJECT", "min_years": 2}},
    ]
    planned = _plan(body)
    text = json.dumps(E.audit_details(planned.audit))
    assert CANARY not in text


def test_every_leaf_of_the_payload_is_an_id_a_number_an_enum_word_or_none():
    body = [
        {"op": "set_text", "item_id": "req_sql", "text": "SQL (PostgreSQL)"},
        {"op": "set_alternatives", "item_id": "req_py", "alternatives": ["Python", "CPython"]},
        {"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
         "alternatives": ["Docker", "Containers"], "experience": None},
        {"op": "remove_item", "item_id": "req_exp"},
    ]
    payload = E.audit_details(_plan(body).audit)
    for kind, value in _leaves(payload):
        if kind == "key":
            assert ENUM_RE.match(value), value
        elif value is None or isinstance(value, int):
            continue
        else:
            assert isinstance(value, str)
            assert ID_RE.match(value) or ENUM_RE.match(value), value


def test_generated_and_touched_ids_appear_in_the_payload():
    planned = _plan([{"op": "add_item", "category": "skills", "text": "Docker", "importance": "preferred", "weight": None,
                      "alternatives": None, "experience": None}])
    payload = E.audit_details(planned.audit)
    assert payload["generated_item_ids"] == list(planned.audit.generated_item_ids) and len(payload["generated_item_ids"]) == 1


def test_retired_ids_appear_only_in_the_audit_metadata_and_never_in_the_document():
    planned = _plan([{"op": "remove_item", "item_id": "req_exp"}])
    assert planned.audit.newly_retired_item_ids == ("req_exp",)
    assert "req_exp" not in json.dumps(planned.document["categories"]["experience"])


# ---- the strict writer ----

def test_an_unknown_audit_action_is_refused_before_any_sql():
    session = _Session()
    with pytest.raises(ValueError):
        asyncio.run(E.write_audit_event(session, _user(), JOB, action="made_up", details=_plan([
            {"op": "set_text", "item_id": "req_sql", "text": "SQL"}]).audit))
    assert session.calls == []


def test_the_writer_refuses_anything_that_is_not_the_typed_audit():
    session = _Session()
    with pytest.raises(TypeError):
        asyncio.run(E.write_audit_event(session, _user(), JOB, action=E.AUDIT_ACTION_SAVE, details={"text": CANARY}))
    assert session.calls == []


def test_a_successful_insert_never_commits_or_rolls_back():
    session = _Session()
    audit = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}]).audit
    asyncio.run(E.write_audit_event(session, _user(), JOB, action=E.AUDIT_ACTION_SAVE, details=audit))
    assert session.calls == ["execute"]
    _, params = session.statements[0]
    assert params["tid"] == TENANT and params["uid"] == USER and params["jid"] == JOB and params["email"] == "a@example.test"
    assert json.loads(params["details"]) == E.audit_details(audit)


def test_a_database_failure_is_raised_as_audit_write_error_from_the_original():
    original = SQLAlchemyError("simulated insufficient privilege")
    session = _Session(error=original)
    audit = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}]).audit
    with pytest.raises(E.AuditWriteError) as caught:
        asyncio.run(E.write_audit_event(session, _user(), JOB, action=E.AUDIT_ACTION_SAVE, details=audit))
    assert caught.value.code == "audit_failed" and caught.value.status == 500
    assert caught.value.__cause__ is original
    assert session.calls == ["execute"], "the writer itself must not roll back or commit"


def test_a_non_database_failure_is_not_converted_or_swallowed():
    error = RuntimeError("unrelated")
    session = _Session(error=error)
    audit = _plan([{"op": "set_text", "item_id": "req_sql", "text": "SQL"}]).audit
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(E.write_audit_event(session, _user(), JOB, action=E.AUDIT_ACTION_SAVE, details=audit))
    assert caught.value is error
