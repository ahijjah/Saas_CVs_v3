"""Structural review in real extraction: a split wording creates an open finding that blocks readiness; correcting the structure and
resolving the finding in one save unblocks it. English and Arabic. Pure: no database, no model call."""
from __future__ import annotations

import json

import pytest

from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.structural_review import STRUCTURAL_KEY
from workers import requirements_v2_worker as worker

USER = "11111111-1111-4111-8111-111111111111"
NOW = "2026-10-18T10:00:00+00:00"

CASES = {
    "en": {
        "jd": "Senior developer.\nPython or Java is required.\nFive years of experience is preferred.",
        "source": "Python or Java is required",
        "alternatives": ["Python", "Java"],
        "texts": ["Python", "Java"],
        "keep": "Python",
    },
    "ar": {
        "jd": "مطور أول.\nمطلوب بايثون أو جافا.\nخبرة خمس سنوات مفضلة.",
        "source": "مطلوب بايثون أو جافا",
        "alternatives": ["بايثون", "جافا"],
        "texts": ["بايثون", "جافا"],
        "keep": "بايثون",
    },
}


def _split_document(case):
    items = [{"text": t, "importance": "required", "source_text": case["source"], "alternatives": case["alternatives"],
              "origin": "stated"} for t in case["texts"]]
    raw = {"categories": {"skills": items, **{c: [] for c in C.CATEGORIES if c != "skills"}},
           "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    # merging is off: the fixture is a split that reached storage unmerged, which is what the detector reviews
    parsed = parse_response(json.dumps(raw, ensure_ascii=False), case["jd"], "stop", id_factory=worker.stable_id_factory(case["jd"]),
                            merge_alternatives=False)
    doc, expectations = worker.finalise_document(worker.checked_document(parsed), case["jd"])
    return doc, parsed.original, expectations


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_split_wording_creates_an_open_finding_that_blocks_readiness(lang):
    case = CASES[lang]
    doc, original, expectations = _split_document(case)
    findings = doc[STRUCTURAL_KEY]["findings"]
    assert [f["status"] for f in findings] == ["open"]
    assert findings[0]["code"] == "alternatives_split_across_items"
    assert findings[0]["source_text"] == case["source"]              # the source wording is the description's own characters
    assert case["jd"][findings[0]["source_start"]:findings[0]["source_end"]] == case["source"]
    assert compute_readiness(doc, original=original, jd_text=case["jd"]).state == "needs_structural_review"
    assert expectations["expectations"][0]["tokens"] == case["alternatives"]         # the approved baseline is stored with the result


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_correcting_the_structure_and_resolving_the_finding_in_one_save_unblocks_readiness(lang):
    case = CASES[lang]
    doc, original, _ = _split_document(case)
    keep = next(i for i in doc["categories"]["skills"]["items"] if i["text"] == case["keep"])
    drop = next(i for i in doc["categories"]["skills"]["items"] if i["text"] != case["keep"])
    ops = E.parse_operations([
        {"op": "remove_item", "item_id": drop["id"]},
        {"op": "set_item_weight", "item_id": keep["id"], "weight": 100},      # the surviving required item carries the category
        {"op": "resolve_structural_finding", "finding_id": doc[STRUCTURAL_KEY]["findings"][0]["id"], "decision": "fixed",
         "note": None, "evidence_item_ids": [keep["id"]]},
    ])
    planned = E.plan_operations(doc, original, [], ops, user_id=USER, now=NOW, jd_text=case["jd"])
    assert compute_readiness(planned.document, original=original, jd_text=case["jd"]).can_proceed
    resolution = planned.document[STRUCTURAL_KEY]["findings"][0]["resolution"]
    assert resolution["reviewer_user_id"] == USER and resolution["evidence_item_ids"] == [keep["id"]]


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_split_alone_cannot_be_saved_without_resolving_the_finding_that_names_a_removed_item(lang):
    case = CASES[lang]
    doc, original, _ = _split_document(case)
    drop = next(i for i in doc["categories"]["skills"]["items"] if i["text"] != case["keep"])
    with pytest.raises(E.InvalidRequirements):
        E.plan_operations(doc, original, [], E.parse_operations([{"op": "remove_item", "item_id": drop["id"]}]),
                          user_id=USER, now=NOW, jd_text=case["jd"])


# ---- fail closed: malformed or ambiguous evidence never creates an expectation ----

def _expectations_for(items, jd):
    raw = {"categories": {"skills": items, **{c: [] for c in C.CATEGORIES if c != "skills"}},
           "category_weights": {"skills": 100}, "scoreability": {"status": "scoreable"}}
    parsed = parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop", id_factory=worker.stable_id_factory(jd),
                            merge_alternatives=False)
    return worker.expectations_from_extraction(parsed.requirements, jd)["expectations"]


def test_an_alternative_that_is_not_a_word_of_the_wording_creates_no_expectation():
    jd = "Python or Java is required."
    items = [{"text": "Python", "importance": "required", "source_text": "Python or Java is required",
              "alternatives": ["Python", "Rust"], "origin": "stated"},
             {"text": "Java", "importance": "required", "source_text": "Python or Java is required",
              "alternatives": ["Python", "Rust"], "origin": "stated"}]
    assert _expectations_for(items, jd) == []                 # only one declared alternative is in the wording: not two tokens


def test_a_wording_that_appears_twice_in_the_description_is_ambiguous_and_creates_no_expectation():
    jd = "Python or Java is required.\nPython or Java is required."
    items = [{"text": "Python", "importance": "required", "source_text": "Python or Java is required",
              "alternatives": ["Python", "Java"], "origin": "stated"},
             {"text": "Java", "importance": "required", "source_text": "Python or Java is required",
              "alternatives": ["Python", "Java"], "origin": "stated"}]
    assert _expectations_for(items, jd) == []


def test_items_sharing_no_wording_produce_no_expectation_even_with_alternatives():
    jd = "Python is required. Java is preferred."
    items = [{"text": "Python", "importance": "required", "source_text": "Python is required",
              "alternatives": ["Python", "Java"], "origin": "stated"}]
    assert _expectations_for(items, jd) == []
