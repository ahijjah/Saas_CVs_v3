"""Staging UAT repairs for Job Analysis (Requirements V2): explicit OR alternatives, warnings that match the source, and the
validity of what the extraction produces. Offline: the AI output is given directly, nothing calls a model."""
from __future__ import annotations

import json

import pytest

from services.requirements_v2.acknowledgment import build_warning, classification_status
from services.requirements_v2.contract import CATEGORIES
from services.requirements_v2.extraction.parser import merge_explicit_alternatives, parse_response
from services.requirements_v2.validation import validate_draft

JD = (
    "Senior IT Support Engineer\n"
    "Requirements:\n"
    "- The candidate must know Python or PowerShell scripting.\n"
    "- Three years of technical-support experience.\n"
    "- Bachelor's degree in Computer Science or IT.\n"
    "- The candidate must be fluent in Arabic and English.\n"
    "Preferred:\n"
    "- ITIL certification is preferred.\n"
    "- Azure or AWS experience is preferred.\n"
)


def _item(text, importance, source, *, cue=None, **extra):
    item = {"text": text, "importance": importance, "importance_cue": cue, "source_text": source, "origin": "stated"}
    item.update(extra)
    return item


def _ai(**categories):
    cats = {c: [] for c in CATEGORIES}
    cats.update(categories)
    weights = {c: 0 for c in CATEGORIES}
    for c, items in categories.items():
        if any(i["importance"] == "required" for i in items):
            weights[c] = 100 // max(1, sum(1 for k, v in categories.items() if any(i["importance"] == "required" for i in v)))
    return {"categories": cats, "category_weights": weights, "scoring_confirmation": None}


def _parse(ai, jd=JD, **kw):
    res = parse_response(json.dumps(ai), jd, **kw)
    assert res.status == "draft", res.errors
    return res


def _items(res, category):
    return res.requirements["categories"][category]["items"]


def _codes(res):
    return [w.code for w in res.review]


# ── explicit OR alternatives ───────────────────────────────────────────────────────────────────────────────────────

def test_python_or_powershell_is_one_required_alternatives_group():
    res = _parse(_ai(skills=[
        _item("Python scripting", "required", "The candidate must know Python or PowerShell scripting."),
        _item("PowerShell scripting", "required", "The candidate must know Python or PowerShell scripting."),
    ]))
    skills = _items(res, "skills")
    assert len(skills) == 1
    assert skills[0]["importance"] == "required"
    assert skills[0]["alternatives"] == ["Python scripting", "PowerShell scripting"]
    assert skills[0]["text"] == "Python scripting or PowerShell scripting"
    assert skills[0]["source_text"] == "The candidate must know Python or PowerShell scripting."


def test_azure_or_aws_is_one_preferred_alternatives_group_with_its_cue():
    res = _parse(_ai(skills=[
        _item("Azure experience", "preferred", "Azure or AWS experience is preferred.", cue="is preferred"),
        _item("AWS experience", "preferred", "Azure or AWS experience is preferred.", cue="is preferred"),
    ]))
    skills = _items(res, "skills")
    assert len(skills) == 1
    assert skills[0]["importance"] == "preferred"
    assert skills[0]["weight"] is None
    assert skills[0]["alternatives"] == ["Azure experience", "AWS experience"]
    assert "preferred_cue_missing" not in _codes(res)


def test_and_stays_two_separate_required_requirements():
    """Arabic and English are two requirements, not alternatives: the connector is 'and', never 'or'."""
    res = _parse(_ai(skills=[
        _item("Arabic", "required", "The candidate must be fluent in Arabic and English."),
        _item("English", "required", "The candidate must be fluent in Arabic and English."),
    ]))
    skills = _items(res, "skills")
    assert [i["text"] for i in skills] == ["Arabic", "English"]
    assert all(i.get("alternatives") is None for i in skills)
    assert all(i["importance"] == "required" for i in skills)


def test_items_from_different_sentences_are_never_merged():
    items = [{"text": "Python", "importance": "required", "source_text": "Know Python or R."},
             {"text": "R", "importance": "required", "source_text": "Know SQL."},
             {"text": "SQL", "importance": "required", "source_text": "Know SQL."}]
    merged = merge_explicit_alternatives(items)
    assert [m["text"] for m in merged] == ["Python", "R", "SQL"]
    assert all(m.get("alternatives") is None for m in merged)


def test_identical_wording_is_not_an_alternatives_group():
    """Duplicates are not alternatives, and the parser does no deduplication: both items stay as the model gave them."""
    items = [{"text": "Python", "importance": "required", "source_text": "Know Python or R."},
             {"text": "Python", "importance": "required", "source_text": "Know Python or R."}]
    merged = merge_explicit_alternatives(items)
    assert len(merged) == 2 and all(m.get("alternatives") is None for m in merged)


def test_different_importance_is_not_merged():
    items = [{"text": "Azure", "importance": "required", "source_text": "Azure or AWS is preferred."},
             {"text": "AWS", "importance": "preferred", "source_text": "Azure or AWS is preferred."}]
    assert len(merge_explicit_alternatives(items)) == 2


def test_an_item_that_already_holds_its_alternatives_is_left_alone_and_structured_experience_is_never_merged():
    own = [{"text": "A or B", "importance": "required", "source_text": "A or B.", "alternatives": ["A", "B"]}]
    assert merge_explicit_alternatives(own, "A or B.") == own
    years = [{"text": "Python", "importance": "required", "source_text": "Python or R for 3 years.",
              "experience": {"subject": "Python", "min_years": 3}},
             {"text": "R", "importance": "required", "source_text": "Python or R for 3 years.",
              "experience": {"subject": "R", "min_years": 3}}]
    assert len(merge_explicit_alternatives(years, "Python or R for 3 years.")) == 2


def test_a_split_item_that_declares_the_alternatives_is_merged_with_its_other_half():
    items = [{"text": "A", "importance": "required", "source_text": "A or B.", "alternatives": ["A", "B"]},
             {"text": "B", "importance": "required", "source_text": "A or B."}]
    merged = merge_explicit_alternatives(items, "A or B.")
    assert len(merged) == 1 and merged[0]["alternatives"] == ["A", "B"]


def test_merged_output_passes_the_contract_validator():
    res = _parse(_ai(skills=[
        _item("Python scripting", "required", "The candidate must know Python or PowerShell scripting."),
        _item("PowerShell scripting", "required", "The candidate must know Python or PowerShell scripting."),
    ]))
    assert not validate_draft(res.requirements).errors


def test_the_or_connector_is_matched_as_a_word_not_inside_a_word():
    items = [{"text": "Oracle", "importance": "required", "source_text": "Oracle and Forward planning."},
             {"text": "Forward", "importance": "required", "source_text": "Oracle and Forward planning."}]
    assert len(merge_explicit_alternatives(items)) == 2


# ── classification warnings match the source ──────────────────────────────────────────────────────────────────────

def test_explicitly_preferred_itil_has_no_warning():
    res = _parse(_ai(certifications=[_item("ITIL certification", "preferred", "ITIL certification is preferred.", cue="is preferred")]))
    assert not [c for c in _codes(res) if c.startswith("preferred_cue")]
    assert classification_status(res.requirements).open == ()


def _doc_with_warning(source_text, cue, code="preferred_cue_not_linked_to_item"):
    cats = {c: {"weight": 0, "items": []} for c in CATEGORIES}
    cats["certifications"]["items"] = [{"id": "req_itil1", "text": "ITIL certification", "importance": "preferred", "weight": None,
                                       "origin": "stated", "source_text": source_text, "alternatives": None}]
    doc = {"schema_version": 2, "categories": cats, "scoring_confirmation": None}
    doc["classification_review"] = {"warnings": [build_warning(code=code, item_id="req_itil1", category="certifications",
                                                              cue=cue, source_text=None, message="m")],
                                    "acknowledgments": []}
    return doc


def test_a_reopened_warning_clears_when_the_items_own_source_shows_the_cue():
    doc = _doc_with_warning("ITIL certification is preferred.", "is preferred")
    status = classification_status(doc)
    assert status.open == ()
    assert status.resolved == ("req_itil1:preferred_cue_not_linked_to_item",)


def test_a_warning_stays_open_when_the_source_does_not_show_the_cue():
    doc = _doc_with_warning("ITIL certification.", "is preferred")
    assert classification_status(doc).open == ("req_itil1:preferred_cue_not_linked_to_item",)


def test_a_genuinely_unverified_cue_still_produces_a_warning_at_extraction():
    res = _parse(_ai(certifications=[_item("ITIL certification", "preferred", "ITIL certification.", cue=None)]))
    assert "preferred_cue_missing" in _codes(res)


# ── contextual facts stay on their scored items ───────────────────────────────────────────────────────────────────

def test_three_years_of_technical_support_experience_is_preserved():
    res = _parse(_ai(experience=[_item("Technical-support experience", "required", "Three years of technical-support experience.",
                                       experience={"subject": "technical support", "min_years": 3})]))
    item = _items(res, "experience")[0]
    assert item["experience"] == {"subject": "technical support", "min_years": 3}


def test_bachelor_degree_with_computer_science_or_it_is_preserved_as_stated():
    res = _parse(_ai(education=[_item("Bachelor's degree in Computer Science or IT", "required",
                                      "Bachelor's degree in Computer Science or IT.")]))
    assert _items(res, "education")[0]["text"] == "Bachelor's degree in Computer Science or IT"
