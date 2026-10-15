"""Requirements v2 (stage 1): integer Equalize and category Normalize. Pure functions, no database or model."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, OverLimitError, apply_category_weights, empty_requirements, equal_category_weights,
    equalize_category, equalize_weights, make_item, normalize_category_weights, validate_final,
)
from services.requirements_v2.weights import (
    NORMALIZE_FALLBACK_REQUIRED, NORMALIZE_NO_ELIGIBLE, NORMALIZE_OK,
)


# ── Equalize ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    (0, []),
    (1, [100]),
    (2, [50, 50]),
    (3, [34, 33, 33]),
    (4, [25, 25, 25, 25]),
    (6, [17, 17, 17, 17, 16, 16]),
    (7, [15, 15, 14, 14, 14, 14, 14]),
    (100, [1] * 100),
])
def test_equalize_weights_integer_division_with_remainder_in_list_order(n, expected):
    assert equalize_weights(n) == expected


@pytest.mark.parametrize("n", range(1, 101))
def test_equalize_weights_always_total_100_and_positive(n):
    w = equalize_weights(n)
    assert sum(w) == 100 and min(w) >= 1 and max(w) - min(w) <= 1
    assert w == sorted(w, reverse=True)           # extra points go to the earliest items


def test_equalize_weights_over_limit_is_explicit_not_silent():
    with pytest.raises(OverLimitError):
        equalize_weights(101)


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, None])
def test_equalize_weights_rejects_non_integers(bad):
    with pytest.raises(ValueError):
        equalize_weights(bad)


def _doc_with_skills(importances):
    doc = empty_requirements()
    doc["categories"]["skills"]["items"] = [
        make_item(f"s{i}", imp, item_id=f"req_s{i}") for i, imp in enumerate(importances)]
    return doc


def test_equalize_category_only_touches_required_items_of_that_category_in_list_order():
    doc = _doc_with_skills(["required", "preferred", "required", "required"])
    doc["categories"]["education"]["items"] = [make_item("BSc", "required", item_id="req_e", weight=7)]
    before = copy.deepcopy(doc)

    out = equalize_category(doc, "skills")

    assert doc == before                                             # input untouched
    items = out["categories"]["skills"]["items"]
    assert [i["weight"] for i in items] == [34, None, 33, 33]        # preferred stays None
    assert out["categories"]["education"] == doc["categories"]["education"]
    assert out["categories"]["skills"]["weight"] == doc["categories"]["skills"]["weight"]


def test_equalize_category_with_no_required_items_changes_nothing():
    doc = _doc_with_skills(["preferred", "preferred"])
    assert equalize_category(doc, "skills") == doc


def test_equalize_category_over_limit_raises_and_keeps_items():
    doc = empty_requirements()
    doc["categories"]["skills"]["items"] = [make_item(f"s{i}", "required") for i in range(101)]
    with pytest.raises(OverLimitError):
        equalize_category(doc, "skills")
    assert len(doc["categories"]["skills"]["items"]) == 101


def test_equalize_category_unknown_category():
    with pytest.raises(ValueError):
        equalize_category(empty_requirements(), "hobbies")


# ── Normalize ─────────────────────────────────────────────────────────────────

def _proposal(**kw):
    return {c: kw.get(c, 0) for c in CATEGORIES}


def test_normalize_already_summing_to_100_is_unchanged():
    r = normalize_category_weights(_proposal(skills=40, experience=30, education=30), ["skills", "experience", "education"])
    assert r.status == NORMALIZE_OK
    assert r.weights == {**{c: 0 for c in CATEGORIES}, "skills": 40, "experience": 30, "education": 30}
    assert r.warnings == ()


def test_normalize_scales_proportionally_and_totals_100():
    r = normalize_category_weights(_proposal(skills=35, experience=25, education=15, certifications=10),
                                   ["skills", "experience", "education", "certifications"])
    assert sum(r.weights.values()) == 100
    assert r.weights["skills"] > r.weights["experience"] > r.weights["education"] > r.weights["certifications"]
    assert r.weights["soft_skills"] == 0


def test_normalize_ineligible_categories_are_forced_to_zero_with_warning():
    r = normalize_category_weights(_proposal(skills=60, certifications=40), ["skills"])
    assert r.weights["skills"] == 100 and r.weights["certifications"] == 0
    assert [w.code for w in r.warnings] == ["proposal_for_ineligible_category"]
    assert r.warnings[0].category == "certifications"


def test_normalize_largest_remainder_ties_follow_category_order():
    # three equal shares of 33.33..: the single leftover point goes to the first category in CATEGORIES order
    r = normalize_category_weights(_proposal(skills=1, experience=1, education=1), ["education", "skills", "experience"])
    assert (r.weights["skills"], r.weights["experience"], r.weights["education"]) == (34, 33, 33)


def test_normalize_is_deterministic_regardless_of_eligible_order_and_input_type():
    p = _proposal(skills=17, experience=29.5, education=11, soft_skills=3)
    a = normalize_category_weights(p, ["skills", "experience", "education", "soft_skills"])
    b = normalize_category_weights(p, ["soft_skills", "education", "experience", "skills"])
    assert a.weights == b.weights and sum(a.weights.values()) == 100


def test_normalize_keeps_eligible_categories_positive():
    r = normalize_category_weights(_proposal(skills=99, experience=1, education=0), ["skills", "experience", "education"])
    assert all(r.weights[c] >= 1 for c in ("skills", "experience", "education"))
    assert sum(r.weights.values()) == 100
    codes = [w.code for w in r.warnings]
    assert "category_raised_to_minimum" in codes and "proposal_value_unusable" not in codes


def test_normalize_tiny_proportions_still_positive_and_total_100():
    r = normalize_category_weights(_proposal(skills=1000, experience=1, education=1), ["skills", "experience", "education"])
    assert r.weights["experience"] >= 1 and r.weights["education"] >= 1 and sum(r.weights.values()) == 100


def test_normalize_all_seven_categories_equal_totals_100_with_remainder_in_category_order():
    r = normalize_category_weights({c: 10 for c in CATEGORIES}, CATEGORIES)
    assert sum(r.weights.values()) == 100
    assert [r.weights[c] for c in CATEGORIES] == [15, 15, 14, 14, 14, 14, 14]


def test_normalize_no_eligible_categories_gives_all_zero():
    r = normalize_category_weights(_proposal(skills=100), [])
    assert r.status == NORMALIZE_NO_ELIGIBLE and set(r.weights.values()) == {0}


@pytest.mark.parametrize("proposal", [
    None, {}, "nonsense", _proposal(), _proposal(skills=0, experience=0),
    {"skills": "40", "experience": None}, {"skills": -5, "experience": float("nan")}, {"skills": True},
])
def test_normalize_unusable_proposal_returns_explicit_fallback_not_a_silent_rule(proposal):
    r = normalize_category_weights(proposal, ["skills", "experience"])
    assert r.status == NORMALIZE_FALLBACK_REQUIRED
    assert r.weights is None                                         # nothing applied
    assert "proposal_unusable" in [w.code for w in r.warnings]
    assert r.suggested_fallback == equal_category_weights(["skills", "experience"])
    assert r.suggested_fallback["skills"] == 50 and r.suggested_fallback["experience"] == 50


def test_normalize_partly_unusable_proposal_warns_about_each_value():
    r = normalize_category_weights({"skills": 60, "experience": "x"}, ["skills", "experience"])
    assert r.status == NORMALIZE_OK
    assert [w.code for w in r.warnings if w.category == "experience"][0] == "proposal_value_unusable"
    assert r.weights["skills"] == 99 and r.weights["experience"] == 1


def test_equal_category_weights_remainder_in_category_order():
    w = equal_category_weights(["education", "skills", "experience"])
    assert (w["skills"], w["experience"], w["education"]) == (34, 33, 33)
    assert w["certifications"] == 0


def test_apply_category_weights_returns_new_document_and_validates_when_items_balanced():
    doc = empty_requirements()
    doc["categories"]["skills"]["items"] = [make_item("a", "required", item_id="req_a", weight=100)]
    doc["categories"]["education"]["items"] = [make_item("b", "required", item_id="req_b", weight=100)]
    result = normalize_category_weights({"skills": 3, "education": 1}, ["skills", "education"])
    out = apply_category_weights(doc, result.weights)
    assert doc["categories"]["skills"]["weight"] == 0
    assert (out["categories"]["skills"]["weight"], out["categories"]["education"]["weight"]) == (75, 25)
    assert validate_final(out).ok
