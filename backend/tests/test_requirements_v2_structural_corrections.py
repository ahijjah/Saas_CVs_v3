"""Requirements-v2 structural corrections (offline, deterministic). Replays hand-written AI responses on the B02 and B12 fixtures.
No model call, no database, no network. The pinned prompt is not touched, and no prompt candidate is activated."""
from __future__ import annotations

import copy
import json
import pathlib

from services.requirements_v2 import contract as C
from services.requirements_v2.acknowledgment import REVIEW_KEY
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.parser import NEEDS_REVIEW
from services.requirements_v2.extraction.prompt import PROMPT_SHA256

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "requirements_v2_foundations"
B02 = json.loads((FIXTURES / "B02_en_data_analyst.json").read_text(encoding="utf-8"))["jd"]
B12 = json.loads((FIXTURES / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]

PYTHON_OR_R = "Proficiency in Python or R"                 # B02 line 6: one requirement, two alternatives
CSS_HTML_REQUIRED = "معرفة بـ CSS وHTML"                    # B12 line 6: required
CSS_HTML_NOTE = "معرفة CSS وHTML اختيارية"                  # B12 line 12: the recruiter note, cue اختيارية


def _response(items: list[dict]) -> str:
    return json.dumps({"categories": {"skills": items}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                      ensure_ascii=False)


def _warnings(result) -> list[dict]:
    return list(result.requirements.get(REVIEW_KEY, {}).get("warnings", []))


def _review_codes(result) -> set[str]:
    return {i.code for i in result.review}


def _all_importances(doc: dict) -> list[str]:
    return [i["importance"] for block in doc["categories"].values() for i in block["items"]]


# ---- 1. Python or R: one requirement with both alternatives; separate Python and R items are routed, never merged ----

def test_python_or_r_as_one_item_keeps_both_alternatives_and_raises_no_split_review():
    raw = _response([{"text": "Python or R proficiency", "importance": "required", "source_text": PYTHON_OR_R,
                      "alternatives": ["Python", "R"]}])
    res = parse_response(raw, B02)
    items = res.requirements["categories"]["skills"]["items"]
    assert len(items) == 1
    assert items[0]["alternatives"] == ["Python", "R"]
    assert "alternatives_split_across_items" not in _review_codes(res)


def test_python_and_r_split_into_two_items_is_routed_for_review_and_not_merged():
    raw = _response([
        {"text": "Python", "importance": "required", "source_text": PYTHON_OR_R, "alternatives": ["Python", "R"]},
        {"text": "R", "importance": "required", "source_text": PYTHON_OR_R, "alternatives": ["Python", "R"]},
    ])
    res = parse_response(raw, B02)
    items = res.requirements["categories"]["skills"]["items"]
    assert len(items) == 2, "the split must not be merged silently"
    assert "alternatives_split_across_items" in _review_codes(res)


def test_alternatives_that_do_not_appear_in_the_source_wording_are_routed():
    raw = _response([{"text": "Python or Julia", "importance": "required", "source_text": PYTHON_OR_R,
                      "alternatives": ["Python", "Julia"]}])
    res = parse_response(raw, B02)
    assert "alternatives_not_in_source" in _review_codes(res)
    assert res.requirements["categories"]["skills"]["items"][0]["alternatives"] == ["Python", "Julia"]


# ---- 2. CSS and HTML: two independent items, both linked to the same source line, never merged ----

def test_css_and_html_as_two_independent_items_stay_two_items_on_the_same_source_line():
    raw = _response([
        {"text": "CSS", "importance": "required", "source_text": CSS_HTML_REQUIRED},
        {"text": "HTML", "importance": "required", "source_text": CSS_HTML_REQUIRED},
    ])
    res = parse_response(raw, B12)
    items = res.requirements["categories"]["skills"]["items"]
    assert [i["text"] for i in items] == ["CSS", "HTML"]
    assert items[0]["source_text"] == items[1]["source_text"] == CSS_HTML_REQUIRED
    assert all(i["alternatives"] is None for i in items), "independent items carry no OR structure"


# ---- 3. Arabic اختيارية maps to preferred; the value optional is never produced ----

def test_arabic_optional_cue_maps_to_preferred_and_optional_is_never_output():
    raw = _response([{"text": "CSS and HTML", "importance": "optional", "importance_cue": "اختيارية",
                      "source_text": CSS_HTML_NOTE}])
    res = parse_response(raw, B12)
    doc = res.requirements
    assert _all_importances(doc) == ["preferred"]
    assert "optional" not in json.dumps(doc, ensure_ascii=False)
    assert all(i in C.IMPORTANCES for i in _all_importances(doc))


def test_optional_without_a_cue_is_not_guessed_and_defaults_to_required_with_a_report():
    raw = _response([{"text": "CSS", "importance": "optional", "source_text": CSS_HTML_NOTE}])
    res = parse_response(raw, B12)
    assert _all_importances(res.requirements) == ["required"]
    assert "importance_invalid_defaulted_required" in _review_codes(res)


# ---- 4. Conflicting JD statements: evidence is kept apart from importance and routed, never silently resolved ----

def test_required_css_item_that_conflicts_with_the_optional_note_is_routed_and_keeps_required():
    raw = _response([{"text": "CSS", "importance": "required", "source_text": CSS_HTML_REQUIRED}])
    res = parse_response(raw, B12)
    assert _all_importances(res.requirements) == ["required"], "importance is not changed by the conflict"
    routed = [i for i in res.review if i.code == "importance_conflict_unresolved"]
    assert len(routed) == 1 and routed[0].item_id is not None
    assert routed[0].message.startswith(NEEDS_REVIEW) and "confirm" in routed[0].message
    assert "importance_conflict_unresolved" not in [w["code"] for w in _warnings(res)], "a required item is not a Preferred warning"


def test_a_conflict_on_an_unrelated_item_is_not_routed():
    raw = _response([{"text": "Python or R", "importance": "required", "source_text": PYTHON_OR_R,
                      "alternatives": ["Python", "R"]}])
    res = parse_response(raw, B12)                       # B02 wording against the B12 document: no optional cue applies
    assert not [w for w in _warnings(res) if w["code"] == "importance_conflict_unresolved"]


def test_the_conflict_review_does_not_change_readiness_or_scoring_inputs():
    """A routed conflict is a review issue. It does not gate readiness (a policy change, not made here) and it does not change weights."""
    raw = _response([{"text": "CSS", "importance": "required", "source_text": CSS_HTML_REQUIRED}])
    res = parse_response(raw, B12)
    assert res.readiness.state == C.READY
    assert res.requirements["categories"]["skills"]["items"][0]["weight"] == 100


# ---- 5. Frozen inputs: the prompt identity is unchanged and no candidate is activated ----

def test_the_pinned_prompt_identity_is_still_v2_1():
    assert PROMPT_SHA256 == "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
    res = parse_response(_response([]), B02)
    assert res.prompt_version == "criteria_extraction_v2-1"
