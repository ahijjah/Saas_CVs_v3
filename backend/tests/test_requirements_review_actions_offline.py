"""Requirements v2 review actions through the real planner (pure, no database): confirmation, classification acknowledgment, structure
confirmation, structural-finding resolution and reordering. Every stored value a review action creates comes from the server."""
from __future__ import annotations

import copy
import json

import pytest

from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.structural_review import STRUCTURAL_KEY
from workers import requirements_v2_worker as worker

USER = "11111111-1111-4111-8111-111111111111"
NOW = "2026-10-18T10:00:00+00:00"
JD = "Senior Python developer.\nPython and SQL are required.\nFive years of backend experience is preferred."


def _doc(*, cue="preferred", skills=None, experience=None):
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"] = skills if skills is not None else [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
        {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}]
    cats["experience"] = experience if experience is not None else [
        {"text": "Backend experience", "importance": "preferred", "importance_cue": cue, "origin": "stated",
         "source_text": "Five years of backend experience is preferred",
         "experience": {"subject": "backend experience", "min_years": 5}}]
    raw = {"categories": cats, "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    result = worker.parse_response if False else None
    from services.requirements_v2.extraction import parse_response
    parsed = parse_response(json.dumps(raw), JD, "stop", id_factory=worker.stable_id_factory(JD))
    doc, _ = worker.finalise_document(worker.checked_document(parsed), JD)
    return doc, parsed.original


def _plan(stored, original, ops, *, retired=()):
    return E.plan_operations(stored, original, list(retired), E.parse_operations(ops), user_id=USER, now=NOW,
                             jd_text=JD)


def _ids(doc, category="skills"):
    return [i["id"] for i in doc["categories"][category]["items"]]


# ---- parsing: the client cannot supply server-owned fields ----

@pytest.mark.parametrize("op", [
    {"op": "confirm_no_numeric_score", "basis_hash": "a" * 64},            # a client-supplied hash
    {"op": "confirm_no_numeric_score", "user_id": USER},                     # a client-supplied identity
    {"op": "confirm_no_numeric_score", "confirmed_at": NOW},                 # a client-supplied time
    {"op": "acknowledge_classification_warning", "warning_id": "w1", "user_id": USER},
    {"op": "resolve_structural_finding", "finding_id": "f1", "decision": "fixed", "note": None,
     "evidence_item_ids": None, "reviewer_user_id": USER},
])
def test_a_review_operation_with_a_server_owned_field_is_refused(op):
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([op])


def test_a_review_operation_with_an_unknown_decision_or_a_malformed_id_is_refused():
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "resolve_structural_finding", "finding_id": "f1", "decision": "approved", "note": None,
                             "evidence_item_ids": None}])
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "acknowledge_classification_warning", "warning_id": "x" * 200}])
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "confirm_structure", "item_id": "not valid"}])


# ---- confirmation: the server computes the hash and stamps the user and time ----

def test_confirming_a_preferred_only_document_stores_the_server_identity_time_and_hash():
    doc, original = _document_preferred_only()
    planned = _plan(doc, original, [{"op": "confirm_no_numeric_score"}])
    confirmation = planned.document["scoring_confirmation"]
    assert confirmation["user_id"] == USER and confirmation["confirmed_at"] == NOW
    assert len(confirmation["basis_hash"]) == 64
    assert compute_readiness(planned.document, original=original, jd_text=JD).can_proceed


def test_a_confirmation_is_refused_when_the_document_needs_nothing_to_confirm():
    doc, original = _ready_weighted()
    with pytest.raises(E.InvalidOperation):
        _plan(doc, original, [{"op": "confirm_no_numeric_score"}])


def _document_preferred_only():
    doc, original = _doc(skills=[], experience=[
        {"text": "Backend experience", "importance": "preferred", "importance_cue": "preferred", "origin": "stated",
         "source_text": "Five years of backend experience is preferred", "experience": None}])
    return doc, original


def _ready_weighted():
    return _doc()


# ---- classification acknowledgment ----

def _classification_document():
    return _doc(cue="nice to have")                       # an unestablished cue: one open classification warning


def test_acknowledging_an_open_warning_records_the_server_identity_and_time():
    doc, original = _classification_document()
    warning_id = doc[worker.REVIEW_KEY]["warnings"][0]["id"]
    planned = _plan(doc, original, [{"op": "acknowledge_classification_warning", "warning_id": warning_id}])
    ack = planned.document[worker.REVIEW_KEY]["acknowledgments"][0]
    assert ack["warning_id"] == warning_id and ack["user_id"] == USER and ack["acknowledged_at"] == NOW
    assert compute_readiness(planned.document, original=original, jd_text=JD).state != "needs_classification_review"


def test_an_unknown_warning_id_is_refused_and_changes_nothing():
    doc, original = _classification_document()
    with pytest.raises(E.InvalidOperation) as refused:
        _plan(doc, original, [{"op": "acknowledge_classification_warning", "warning_id": "nope"}])
    assert refused.value.code == "unknown_warning"


def test_acknowledging_the_same_warning_twice_is_refused():
    doc, original = _classification_document()
    warning_id = doc[worker.REVIEW_KEY]["warnings"][0]["id"]
    acknowledged = _plan(doc, original, [{"op": "acknowledge_classification_warning", "warning_id": warning_id}]).document
    with pytest.raises(E.InvalidOperation) as refused:
        _plan(acknowledged, original, [{"op": "acknowledge_classification_warning", "warning_id": warning_id}])
    assert refused.value.code == "already_acknowledged"


# ---- structure confirmation ----

def test_confirming_a_changed_structure_records_the_server_identity_and_time():
    doc, original = _doc()
    item = doc["categories"]["experience"]["items"][0]
    item["experience"] = {"subject": "backend experience", "min_years": 5}
    edited = _plan(doc, original, [{"op": "set_text", "item_id": item["id"], "text": "Backend engineering experience"}]).document
    assert compute_readiness(edited, original=original, jd_text=JD).state == "needs_structure_review"
    confirmed = _plan(edited, original, [{"op": "confirm_structure", "item_id": item["id"]}]).document
    records = confirmed["structure_review"]["records"]
    assert [r["item_id"] for r in records] == [item["id"]] and records[0]["user_id"] == USER
    assert records[0]["recorded_at"] == NOW


# ---- structural-finding resolution ----

def _structural_document(*, resolved=False):
    doc, original = _doc()
    start = JD.index("Python and SQL are required")
    ids = _ids(doc)
    finding = {"id": "sf_1", "code": "alternatives_split_across_items", "item_ids": ids,
               "source_text": "Python and SQL are required", "source_start": start,
               "source_end": start + len("Python and SQL are required"), "message": "Check these together.",
               "status": "open", "resolution": None}
    return {**doc, STRUCTURAL_KEY: {"schema_version": 1, "findings": [finding]}}, original


def test_a_structural_finding_blocks_readiness_until_it_is_resolved():
    doc, original = _structural_document()
    assert compute_readiness(doc, original=original, jd_text=JD).state == "needs_structural_review"


def test_resolving_a_finding_with_evidence_allows_readiness_and_records_the_server_identity():
    doc, original = _structural_document()
    ids = _ids(doc)
    planned = _plan(doc, original, [{"op": "resolve_structural_finding", "finding_id": "sf_1", "decision": "fixed",
                                     "note": None, "evidence_item_ids": [ids[0]]}])
    resolution = planned.document[STRUCTURAL_KEY]["findings"][0]["resolution"]
    assert resolution["reviewer_user_id"] == USER and resolution["resolved_at"] == NOW
    assert resolution["evidence_item_ids"] == [ids[0]]
    assert compute_readiness(planned.document, original=original, jd_text=JD).state != "needs_structural_review"


def test_dismissing_a_finding_requires_a_note_and_the_note_is_not_returned_in_any_view():
    doc, original = _structural_document()
    with pytest.raises(E.InvalidOperation):
        _plan(doc, original, [{"op": "resolve_structural_finding", "finding_id": "sf_1",
                               "decision": "accepted_as_is", "note": "", "evidence_item_ids": None}])
    planned = _plan(doc, original, [{"op": "resolve_structural_finding", "finding_id": "sf_1",
                                     "decision": "accepted_as_is", "note": "Two different tools.", "evidence_item_ids": None}])
    view = E.build_edit_view(planned.document, job_id="j", revision=1, schema_version=2, original=original, jd_text=JD)
    assert "Two different tools." not in json.dumps(view.__dict__, default=str)


def test_an_unknown_or_already_resolved_finding_is_refused():
    doc, original = _structural_document()
    with pytest.raises(E.InvalidOperation):
        _plan(doc, original, [{"op": "resolve_structural_finding", "finding_id": "nope", "decision": "fixed", "note": None,
                               "evidence_item_ids": [_ids(doc)[0]]}])
    resolved = _plan(doc, original, [{"op": "resolve_structural_finding", "finding_id": "sf_1", "decision": "fixed",
                                      "note": None, "evidence_item_ids": [_ids(doc)[0]]}]).document
    with pytest.raises(E.InvalidOperation) as refused:
        _plan(resolved, original, [{"op": "resolve_structural_finding", "finding_id": "sf_1", "decision": "fixed",
                                    "note": None, "evidence_item_ids": [_ids(doc)[0]]}])
    assert refused.value.code == "already_resolved"


# ---- reordering: exactly the existing ids ----

def test_reordering_with_exactly_the_existing_ids_changes_only_the_order():
    doc, original = _doc()
    a, b = _ids(doc)
    planned = _plan(doc, original, [{"op": "reorder_items", "category": "skills", "item_ids": [b, a]}])
    assert _ids(planned.document) == [b, a]
    assert planned.document["categories"]["skills"]["items"][0]["text"] == "SQL"


@pytest.mark.parametrize("item_ids_of", [
    lambda a, b: [a],                       # an omission
    lambda a, b: [a, b, a],                 # a duplicate
    lambda a, b: [a, b, "req_unknown01"],   # an addition
    lambda a, b: [],                        # empty
])
def test_reordering_with_a_missing_duplicate_or_added_id_is_refused(item_ids_of):
    doc, original = _doc()
    a, b = _ids(doc)
    with pytest.raises(E.InvalidOperation):
        _plan(doc, original, [{"op": "reorder_items", "category": "skills", "item_ids": item_ids_of(a, b)}])


def test_a_review_action_leaves_the_stored_document_untouched():
    doc, original = _doc()
    before = copy.deepcopy(doc)
    _plan(doc, original, [{"op": "reorder_items", "category": "skills", "item_ids": list(reversed(_ids(doc)))}])
    assert doc == before


def test_a_confirmation_goes_stale_when_the_document_changes_and_no_longer_allows_scoring():
    doc, original = _document_preferred_only()
    confirmed = _plan(doc, original, [{"op": "confirm_no_numeric_score"}]).document
    assert compute_readiness(confirmed, original=original, jd_text=JD).can_proceed
    item_id = confirmed["categories"]["experience"]["items"][0]["id"]
    changed = _plan(confirmed, original, [{"op": "set_text", "item_id": item_id, "text": "Backend engineering experience"}]).document
    assert not compute_readiness(changed, original=original, jd_text=JD).can_proceed
    assert compute_readiness(changed, original=original, jd_text=JD).state == "needs_confirmation"
