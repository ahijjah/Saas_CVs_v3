"""AI gate suggestions through the real extraction parser: explicit wording yields a suggestion on a Required item and nothing else changes;
a suggestion is never a gate, never appears on a Preferred item, and the suggestion block keeps only the job description's own words."""
from __future__ import annotations

import json

import pytest

from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.validation import validate_structure
from services.requirements_v2 import gates as R
from workers import requirements_v2_worker as worker


def _parse(jd, quoted, *, importance="required", category="skills", cue=None):
    item = {"text": quoted, "importance": importance, "source_text": quoted, "origin": "stated"}
    if importance == "preferred":
        item["importance_cue"] = cue or "preferred"
    cats = {c: [] for c in C.CATEGORIES}
    cats[category] = [item]
    raw = {"categories": cats, "category_weights": {category: 100} if importance == "required" else {"skills": 100},
           "scoreability": {"status": "scoreable"}}
    result = parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop", id_factory=worker.stable_id_factory(jd))
    assert result.ok, result.errors
    return result.requirements["categories"][category]["items"][0], result.requirements


@pytest.mark.parametrize("jd,quoted,cue", [
    ("Candidates must hold a valid driving licence.", "valid driving licence", "must"),
    ("A bachelor degree is mandatory.", "bachelor degree", "mandatory"),
    ("Applicants need a valid driving licence.", "valid driving licence", "valid driving licence"),
    ("Minimum 5 years of Java experience.", "Java experience", "Minimum"),
    ("A bachelor degree is required.", "bachelor degree", "required"),
])
def test_explicit_wording_on_a_required_item_is_suggested_with_the_job_descriptions_own_cue(jd, quoted, cue):
    item, document = _parse(jd, quoted)
    assert item["gate_suggestion"]["cue"] == cue and item["gate_suggestion"]["suggested"] is True
    assert "gate" not in item                                      # a suggestion is not a gate
    assert R.confirmed_gates(document) == []                       # and enforces nothing
    assert not validate_structure(document)


@pytest.mark.parametrize("jd,quoted", [
    ("Candidates should know Python well.", "Python"),
    ("We offer a friendly team and Python tooling.", "Python tooling"),
])
def test_ordinary_wording_gets_no_suggestion(jd, quoted):
    item, _ = _parse(jd, quoted)
    assert "gate_suggestion" not in item


def test_a_preferred_item_never_gets_a_suggestion_even_beside_explicit_wording():
    item, _ = _parse("A degree is a plus.", "A degree is a plus", importance="preferred")
    assert item["importance"] == "preferred" and "gate_suggestion" not in item and "gate" not in item
    item, _ = _parse("Python is required; Docker is preferred.", "Docker", importance="preferred")
    assert "gate_suggestion" not in item


def test_the_suggestion_does_not_change_the_item_or_the_document_otherwise():
    with_cue, _ = _parse("Candidates must hold a valid driving licence.", "valid driving licence")
    plain, _ = _parse("Candidates hold a valid driving licence.", "valid driving licence")
    with_cue = {k: v for k, v in with_cue.items() if k not in ("gate_suggestion", "id", "source_text")}
    plain = {k: v for k, v in plain.items() if k not in ("gate_suggestion", "id", "source_text")}
    assert with_cue == plain
