"""Requirements-v2 Arabic optional-cue normalization (offline, deterministic). Replays hand-written AI responses on the B12 fixture.
No model call, no database, no network. The pinned prompt is not touched and no prompt candidate is activated.

Scope: only the Arabic cue اختيارية. Python or R, CSS and HTML, and conflicting wording are NOT covered here; they remain
unresolved and need a separate structural-review contract."""
from __future__ import annotations

import json
import pathlib

from services.requirements_v2 import contract as C
from services.requirements_v2.acknowledgment import REVIEW_KEY
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.extraction.prompt import PROMPT_SHA256

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "requirements_v2_foundations"
B12 = json.loads((FIXTURES / "B12_ar_injection.json").read_text(encoding="utf-8"))["jd"]

CSS_HTML_REQUIRED = "معرفة بـ CSS وHTML"                    # B12 line 6: required wording, no cue on this line
CSS_HTML_NOTE = "معرفة CSS وHTML اختيارية"                  # B12 line 12: the note carries the cue اختيارية


def _response(items: list[dict]) -> str:
    return json.dumps({"categories": {"skills": items}, "category_weights": {"skills": 100}, "scoring_confirmation": None},
                      ensure_ascii=False)


def _importances(doc: dict) -> list[str]:
    return [i["importance"] for block in doc["categories"].values() for i in block["items"]]


def _review_codes(res) -> set[str]:
    return {i.code for i in res.review}


def test_an_established_arabic_optional_cue_is_stored_as_preferred_and_optional_is_never_stored():
    raw = _response([{"text": "CSS and HTML", "importance": "optional", "importance_cue": "اختيارية", "source_text": CSS_HTML_NOTE}])
    res = parse_response(raw, B12)
    assert _importances(res.requirements) == ["preferred"]
    assert "optional" not in json.dumps(res.requirements, ensure_ascii=False)
    assert "importance_optional_mapped_to_preferred" in _review_codes(res)


def test_the_cue_word_given_as_the_importance_value_maps_to_preferred_when_established():
    raw = _response([{"text": "CSS and HTML", "importance": "اختيارية", "source_text": CSS_HTML_NOTE}])
    res = parse_response(raw, B12)
    assert _importances(res.requirements) == ["preferred"]


def test_optional_without_a_cue_fails_closed_to_required_with_a_review_report():
    raw = _response([{"text": "CSS", "importance": "optional", "source_text": CSS_HTML_NOTE}])
    res = parse_response(raw, B12)
    assert _importances(res.requirements) == ["required"]
    assert "importance_invalid_defaulted_required" in _review_codes(res)


def test_optional_with_a_cue_that_is_not_linked_to_the_item_fails_closed_to_required():
    """The cue exists in the job description (B12 line 12) but not in this item's own wording or governing heading (line 6)."""
    raw = _response([{"text": "CSS", "importance": "optional", "importance_cue": "اختيارية", "source_text": CSS_HTML_REQUIRED}])
    res = parse_response(raw, B12)
    assert _importances(res.requirements) == ["required"]
    assert "optional_cue_not_established" in _review_codes(res)
    assert REVIEW_KEY not in res.requirements, "fails closed to required: no classification warning is created"


def test_an_unestablished_optional_cue_records_one_issue_and_fails_closed_to_required():
    """The cue exists in the job description (B12 line 12) but is not tied to this item (line 6): one issue, required, no block."""
    raw = _response([{"text": "CSS", "importance": "optional", "importance_cue": "اختيارية", "source_text": CSS_HTML_REQUIRED}])
    res = parse_response(raw, B12)
    assert _importances(res.requirements) == ["required"]
    assert [i.code for i in res.review].count("optional_cue_not_established") == 1
    assert "importance_invalid_defaulted_required" not in _review_codes(res)
    assert REVIEW_KEY not in res.requirements, "no classification-warning block is created"


def test_optional_is_never_stored_for_any_input():
    inputs = [
        {"text": "a", "importance": "optional"},
        {"text": "b", "importance": "optional", "importance_cue": "اختيارية", "source_text": CSS_HTML_NOTE},
        {"text": "c", "importance": "optional", "importance_cue": "اختيارية", "source_text": CSS_HTML_REQUIRED},
        {"text": "d", "importance": "اختيارية", "source_text": CSS_HTML_REQUIRED},
        {"text": "e", "importance": "OPTIONAL", "importance_cue": "optional", "source_text": CSS_HTML_NOTE},
    ]
    for item in inputs:
        doc = parse_response(_response([item]), B12).requirements
        assert all(v in C.IMPORTANCES for v in _importances(doc)), item
        assert "optional" not in json.dumps(doc, ensure_ascii=False), item


def test_the_pinned_prompt_identity_is_unchanged():
    assert PROMPT_SHA256 == "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
