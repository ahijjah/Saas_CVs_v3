"""Requirements V2 worker: the pure parts (stable ids, the gate before a write). No database and no model call."""
from __future__ import annotations

import dataclasses
import json

import pytest

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from workers import requirements_v2_worker as worker

JD = "Senior Python developer.\nPython and SQL are required.\nFive years of backend experience is preferred."
OUTPUT = {
    "categories": {"skills": [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
        {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}],
        **{c: [] for c in C.CATEGORIES if c != "skills"}},
    "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"},
}


def _ids(factory, count, used=()):
    out, taken = [], set(used)
    for _ in range(count):
        i = factory(taken)
        taken.add(i)
        out.append(i)
    return out


def test_the_same_description_gives_the_same_id_sequence_on_every_run():
    assert _ids(worker.stable_id_factory(JD), 5) == _ids(worker.stable_id_factory(JD), 5)


def test_a_different_description_gives_different_ids():
    assert _ids(worker.stable_id_factory(JD), 3) != _ids(worker.stable_id_factory(JD + " More."), 3)


def test_ids_are_unique_and_match_the_contract_format():
    ids = _ids(worker.stable_id_factory(JD), 200)
    assert len(set(ids)) == len(ids)
    assert all(C.ITEM_ID_RE.match(i) for i in ids)


def test_a_reserved_retired_id_is_skipped():
    first = _ids(worker.stable_id_factory(JD), 1)[0]
    assert first not in _ids(worker.stable_id_factory(JD, reserved=[first]), 3)


def test_an_id_already_in_the_document_is_skipped():
    first = _ids(worker.stable_id_factory(JD), 1)[0]
    assert _ids(worker.stable_id_factory(JD), 2, used=[first])[0] != first


def test_a_valid_draft_passes_the_gate_and_its_ids_are_the_stable_ones():
    result = parse_response(json.dumps(OUTPUT), JD, "stop", id_factory=worker.stable_id_factory(JD))
    doc = worker.checked_document(result)
    assert doc["schema_version"] == C.SCHEMA_VERSION
    assert [i["id"] for i in doc["categories"]["skills"]["items"]] == _ids(worker.stable_id_factory(JD), 2)


def test_a_model_failure_is_refused_with_its_code():
    result = parse_response("{not json", JD, "stop")
    with pytest.raises(worker.InvalidModelOutput, match="invalid_json"):
        worker.checked_document(result)


def test_a_result_that_reuses_a_retired_id_is_refused():
    result = parse_response(json.dumps(OUTPUT), JD, "stop", id_factory=worker.stable_id_factory(JD))
    retired = [result.requirements["categories"]["skills"]["items"][0]["id"]]
    with pytest.raises(worker.InvalidModelOutput, match="retired_id_reused"):
        worker.checked_document(result, retired=retired)


def test_a_document_with_a_wrong_schema_version_is_refused():
    result = parse_response(json.dumps(OUTPUT), JD, "stop")
    bad = dataclasses.replace(result, requirements={**result.requirements, "schema_version": 3})
    with pytest.raises(worker.InvalidModelOutput, match="schema_version"):
        worker.checked_document(bad)


def test_category_weights_that_do_not_total_zero_or_one_hundred_are_refused():
    result = parse_response(json.dumps(OUTPUT), JD, "stop")
    cats = {**result.requirements["categories"], "skills": {**result.requirements["categories"]["skills"], "weight": 50}}
    bad = dataclasses.replace(result, requirements={**result.requirements, "categories": cats})
    with pytest.raises(worker.InvalidModelOutput, match="weights_total"):
        worker.checked_document(bad)


# ---- server-owned blocks and structural expectations ----

def test_the_stored_document_always_carries_the_classification_and_structure_review_blocks():
    result = parse_response(json.dumps(OUTPUT), JD, "stop", id_factory=worker.stable_id_factory(JD))
    doc = worker.finalise_document(worker.checked_document(result), JD)
    assert doc["classification_review"] == {"warnings": [], "acknowledgments": []}
    assert doc["structure_review"] == {"records": []}
    assert "structural_review" not in doc        # present only when the detector has open findings


def test_expectations_are_derived_only_from_items_whose_alternatives_are_words_of_their_own_wording():
    jd = "Python or Java is required. Five years is preferred."
    output = {"categories": {"skills": [
        {"text": "Python or Java", "importance": "required", "source_text": "Python or Java is required",
         "alternatives": ["Python", "Java"], "origin": "stated"},
        {"text": "Five years", "importance": "required", "source_text": "Five years is preferred",
         "alternatives": ["Five", "Years"], "origin": "stated"}],
        **{c: [] for c in C.CATEGORIES if c != "skills"}},
        "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(output), jd, "stop", id_factory=worker.stable_id_factory(jd))
    block = worker.derive_expectations(result.requirements, jd)
    assert [e["kind"] for e in block["expectations"]] == ["alternatives_together", "alternatives_together"]
    from services.requirements_v2.structural_expectations import validate_expectations
    assert len(validate_expectations(block, jd)) == 2                # the canonical validator accepts what is derived
    skills = result.requirements["categories"]["skills"]["items"]
    assert skills[0]["alternatives"] == ["Python", "Java"]
