"""Requirements v2 (stage 1): draft vs final validation, readiness and the preferred-only confirmation."""
from __future__ import annotations

import copy

import pytest

from services.requirements_v2 import (
    CATEGORIES, ConfirmationError, add_item, basis_hash, carry_confirmation, compute_readiness,
    confirm_no_numeric_score, empty_requirements, equalize_category, make_item, remove_item, set_importance,
    set_text, validate_draft, validate_final,
)


def _req(text, weight, iid, **kw):
    return make_item(text, "required", item_id=iid, weight=weight, **kw)


def _pref(text, iid, **kw):
    return make_item(text, "preferred", item_id=iid, **kw)


def scored_doc():
    """Valid weighted job: skills 60% (two required 50/50 plus one preferred), education 40%."""
    d = empty_requirements()
    d["categories"]["skills"] = {"weight": 60, "items": [_req("ATS", 50, "req_s1"), _req("LinkedIn", 50, "req_s2"),
                                                          _pref("Workday", "req_s3")]}
    d["categories"]["education"] = {"weight": 40, "items": [_req("BSc in HR or Business", 100, "req_e1",
                                                                  alternatives=["HR", "Business"])]}
    return d


def preferred_only_doc():
    d = empty_requirements()
    d["categories"]["certifications"]["items"] = [_pref("CIPD", "req_c1")]
    d["categories"]["skills"]["items"] = [_pref("Workday", "req_s1")]
    return d


def codes(result):
    return sorted(set(result.codes()))


# ── final validation: valid and invalid weights ───────────────────────────────

def test_valid_weighted_document():
    assert validate_final(scored_doc()).ok
    assert compute_readiness(scored_doc()).state == "ready"
    assert compute_readiness(scored_doc()).scoring_mode == "weighted"


def test_valid_document_is_not_mutated_by_validation():
    d = scored_doc()
    before = copy.deepcopy(d)
    validate_final(d), validate_draft(d), compute_readiness(d)
    assert d == before


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=51), "required_weights_total"),
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=None), "required_weight_invalid"),
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=0), "required_weight_invalid"),
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=101), "required_weight_invalid"),
    (lambda d: d["categories"]["skills"]["items"][2].update(weight=5), "preferred_item_has_weight"),
    (lambda d: d["categories"]["skills"].update(weight=0), "category_weight_not_positive"),
    (lambda d: d["categories"]["skills"].update(weight=59), "category_weights_total"),
    (lambda d: d["categories"]["certifications"].update(weight=5), "category_weight_without_required_items"),
    (lambda d: d["categories"]["skills"].update(weight=-1), "bad_category_weight"),
    (lambda d: d["categories"]["skills"].update(weight=60.0), "bad_category_weight"),
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=50.0), "bad_weight_type"),
    (lambda d: d["categories"]["skills"]["items"][0].update(weight=True), "bad_weight_type"),
])
def test_final_rejects_invalid_weights(mutate, expected):
    d = scored_doc()
    mutate(d)
    r = validate_final(d)
    assert not r.ok and expected in codes(r)
    assert compute_readiness(d).state == "needs_review"


def test_final_requires_category_weights_to_total_100_when_any_required_exists():
    d = scored_doc()
    d["categories"]["education"]["weight"] = 41
    assert "category_weights_total" in codes(validate_final(d))


# ── final validation: structure ───────────────────────────────────────────────

@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d.update(schema_version=1), "bad_schema_version"),
    (lambda d: d["categories"].pop("skills"), "missing_category"),
    (lambda d: d["categories"].update(hobbies={"weight": 0, "items": []}), "unknown_category"),
    (lambda d: d["categories"]["skills"]["items"][0].update(importance="mandatory"), "bad_importance"),
    (lambda d: d["categories"]["skills"]["items"][0].update(text="   "), "empty_text"),
    (lambda d: d["categories"]["skills"]["items"][0].update(id="req_s2"), "duplicate_item_id"),
    (lambda d: d["categories"]["skills"]["items"][0].update(id="abc"), "bad_item_id"),
    (lambda d: d["categories"]["skills"]["items"][0].update(origin="ai"), "bad_origin"),
    (lambda d: d["categories"]["skills"]["items"][0].update(mandatory=True), "item_unknown_fields"),
    (lambda d: d["categories"]["skills"]["items"][0].pop("weight"), "item_missing_fields"),
    (lambda d: d["categories"]["skills"]["items"][0].update(alternatives=["only one"]), "bad_alternatives"),
    (lambda d: d["categories"]["skills"]["items"][0].update(experience={"subject": "x", "min_years": 1}),
     "experience_outside_experience_category"),
])
def test_final_rejects_malformed_documents(mutate, expected):
    d = scored_doc()
    mutate(d)
    assert expected in codes(validate_final(d))


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_non_object_input(bad):
    assert codes(validate_final(bad)) == ["not_an_object"]
    assert codes(validate_draft(bad)) == ["not_an_object"]


def test_experience_structure_rules():
    d = scored_doc()
    exp_item = _req("5 years of recruitment experience", 100, "req_x1",
                    experience={"subject": "recruitment", "min_years": 5})
    d["categories"]["experience"] = {"weight": 10, "items": [exp_item]}
    d["categories"]["skills"]["weight"] = 50
    assert validate_final(d).ok
    for bad in ({"subject": None, "min_years": None}, {"subject": "", "min_years": 5},
                {"subject": "hr", "min_years": -1}, {"subject": "hr", "min_years": 2.5}, {"subject": "hr"}):
        d["categories"]["experience"]["items"][0]["experience"] = bad
        assert "bad_experience" in codes(validate_final(d)), bad
    d["categories"]["experience"]["items"][0]["experience"] = {"subject": None, "min_years": 3}
    assert validate_final(d).ok                                      # duration without subject is allowed


# ── duplicates are allowed ────────────────────────────────────────────────────

def test_duplicate_items_are_allowed_within_and_across_categories():
    d = empty_requirements()
    d["categories"]["skills"] = {"weight": 50, "items": [_req("Excel", 50, "req_a"), _req("Excel", 50, "req_b")]}
    d["categories"]["domain_knowledge"] = {"weight": 50, "items": [_req("excel", 100, "req_c"), _pref("Excel", "req_d")]}
    r = validate_final(d)
    assert r.ok and r.review == ()
    assert compute_readiness(d).state == "ready"


# ── limits, over-limit retention ──────────────────────────────────────────────

def _over_limit_doc(n=101):
    d = empty_requirements()
    d["categories"]["skills"] = {"weight": 100, "items": [
        make_item(f"skill {i}", "required", item_id=f"req_k{i}") for i in range(n)]}
    return d


def test_exactly_100_required_items_is_valid_with_equalize():
    d = _over_limit_doc(100)
    d = equalize_category(d, "skills")
    assert validate_final(d).ok
    assert {i["weight"] for i in d["categories"]["skills"]["items"]} == {1}


def test_over_limit_final_is_rejected_but_nothing_is_dropped():
    d = _over_limit_doc(101)
    r = validate_final(d)
    assert "too_many_required_items" in codes(r)
    assert len(d["categories"]["skills"]["items"]) == 101
    assert compute_readiness(d).state == "needs_review"


def test_over_limit_draft_is_retained_for_review_not_an_error():
    d = _over_limit_doc(130)
    r = validate_draft(d)
    assert r.ok and r.errors == ()
    assert [i.code for i in r.review if i.category == "skills"] == ["too_many_required_items"]
    assert len(d["categories"]["skills"]["items"]) == 130            # input untouched, still all there


def test_over_limit_can_be_resolved_by_reclassifying_without_losing_items():
    d = _over_limit_doc(101)
    d = set_importance(d, "req_k100", "preferred")
    d = equalize_category(d, "skills")
    assert validate_final(d).ok and len(d["categories"]["skills"]["items"]) == 101


# ── draft validation: review issues vs errors ─────────────────────────────────

def test_draft_with_unweighted_items_is_reviewable_not_an_error():
    d = empty_requirements()
    d["categories"]["skills"]["items"] = [make_item("ATS", "required", item_id="req_1"),
                                          make_item("CRM", "required", item_id="req_2")]
    r = validate_draft(d)
    assert r.ok
    assert {"required_weight_invalid", "category_weight_not_positive", "category_weights_total"} <= set(r.codes())


def test_draft_malformed_input_is_an_error():
    d = scored_doc()
    d["categories"]["skills"]["items"][0]["importance"] = "maybe"
    r = validate_draft(d)
    assert not r.ok and "bad_importance" in codes(r)


def test_draft_empty_job_reports_no_items_for_review():
    r = validate_draft(empty_requirements())
    assert r.ok and r.codes() == ["no_items"]


def test_draft_must_not_carry_a_confirmation():
    d = preferred_only_doc()
    d["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "u", "confirmed_at": "t", "basis_hash": "h"}
    assert codes(validate_draft(d)) == ["confirmation_not_allowed_in_draft"]


def test_valid_final_document_has_no_review_issues():
    assert validate_final(scored_doc()).review == ()
    assert validate_draft(scored_doc()).codes() == []


# ── readiness: empty and preferred-only ───────────────────────────────────────

def test_empty_job_is_valid_but_cannot_proceed():
    d = empty_requirements()
    assert validate_final(d).ok
    ready = compute_readiness(d)
    assert ready.state == "needs_items" and not ready.can_proceed and ready.scoring_mode is None
    assert [i.code for i in ready.reasons] == ["no_items"]


def test_preferred_only_job_needs_confirmation_and_all_weights_are_zero():
    d = preferred_only_doc()
    assert validate_final(d).ok
    assert all(d["categories"][c]["weight"] == 0 for c in CATEGORIES)
    ready = compute_readiness(d)
    assert ready.state == "needs_confirmation" and not ready.can_proceed


def test_preferred_only_with_a_nonzero_category_weight_is_invalid():
    d = preferred_only_doc()
    d["categories"]["skills"]["weight"] = 100
    assert "category_weight_without_required_items" in codes(validate_final(d))
    assert compute_readiness(d).state == "needs_review"


def test_confirming_preferred_only_allows_proceeding_without_a_score():
    d = confirm_no_numeric_score(preferred_only_doc(), user_id="u1", confirmed_at="2026-01-01T00:00:00Z")
    conf = d["scoring_confirmation"]
    assert conf["kind"] == "no_numeric_score" and conf["user_id"] == "u1" and conf["basis_hash"] == basis_hash(d)
    ready = compute_readiness(d)
    assert ready.state == "ready" and ready.scoring_mode == "none" and ready.can_proceed
    assert validate_final(d).ok


def test_confirmation_is_refused_unless_the_job_is_preferred_only_and_unconfirmed():
    for doc in (empty_requirements(), scored_doc()):
        with pytest.raises(ConfirmationError):
            confirm_no_numeric_score(doc, user_id="u", confirmed_at="t")
    confirmed = confirm_no_numeric_score(preferred_only_doc(), user_id="u", confirmed_at="t")
    with pytest.raises(ConfirmationError):
        confirm_no_numeric_score(confirmed, user_id="u", confirmed_at="t")
    with pytest.raises(ConfirmationError):
        confirm_no_numeric_score(preferred_only_doc(), user_id="", confirmed_at="t")


def test_confirm_does_not_mutate_its_input():
    d = preferred_only_doc()
    before = copy.deepcopy(d)
    confirm_no_numeric_score(d, user_id="u", confirmed_at="t")
    assert d == before


# ── confirmation invalidation ─────────────────────────────────────────────────

def _confirmed():
    return confirm_no_numeric_score(preferred_only_doc(), user_id="u1", confirmed_at="t0")


def _saved(stored, edited):
    """What a save would persist: the edited document with the server's confirmation carried over if still valid."""
    return carry_confirmation(stored, edited)


def test_unchanged_content_keeps_the_confirmation():
    stored = _confirmed()
    assert _saved(stored, stored)["scoring_confirmation"] == stored["scoring_confirmation"]


@pytest.mark.parametrize("edit", [
    lambda d: set_text(d, "req_c1", "CIPD level 5"),                          # wording
    lambda d: add_item(d, "skills", "SQL", "preferred")[0],                   # addition
    lambda d: remove_item(d, "req_c1"),                                        # removal
    lambda d: add_item(d, "skills", "SQL", "required")[0],                     # no longer preferred-only
    lambda d: set_importance(d, "req_c1", "required"),                         # classification
])
def test_wording_addition_removal_classification_invalidate_confirmation(edit):
    stored = _confirmed()
    saved = _saved(stored, edit(stored))
    assert saved["scoring_confirmation"] is None
    assert not compute_readiness(saved).can_proceed


def test_structured_requirement_changes_invalidate_confirmation():
    stored = _confirmed()
    alt = copy.deepcopy(stored)
    alt["categories"]["certifications"]["items"][0]["alternatives"] = ["CIPD", "SHRM"]
    assert _saved(stored, alt)["scoring_confirmation"] is None

    exp_stored = empty_requirements()
    exp_stored["categories"]["experience"]["items"] = [
        _pref("Recruitment experience", "req_x1", experience={"subject": "recruitment", "min_years": 3})]
    exp_stored = confirm_no_numeric_score(exp_stored, user_id="u", confirmed_at="t")
    for new_exp in ({"subject": "recruitment", "min_years": 5}, {"subject": "sourcing", "min_years": 3}, None):
        e = copy.deepcopy(exp_stored)
        e["categories"]["experience"]["items"][0]["experience"] = new_exp
        assert _saved(exp_stored, e)["scoring_confirmation"] is None, new_exp


def test_reordering_and_provenance_do_not_invalidate_the_confirmation():
    stored = _confirmed()
    reordered = copy.deepcopy(stored)
    reordered["categories"]["skills"]["items"].reverse()
    reordered["categories"]["certifications"]["items"][0]["source_text"] = "different quote"
    assert _saved(stored, reordered)["scoring_confirmation"] is not None


def test_invalidated_confirmation_stays_gone_after_restoring_content_with_a_new_save():
    stored = _confirmed()
    edited = _saved(stored, set_text(stored, "req_c1", "CIPD level 5"))      # confirmation dropped
    restored = _saved(edited, set_text(edited, "req_c1", "CIPD"))            # content back to what was confirmed
    assert restored["scoring_confirmation"] is None                           # must be re-confirmed explicitly
    assert compute_readiness(restored).state == "needs_confirmation"


def test_client_supplied_confirmation_is_never_trusted():
    stored = preferred_only_doc()                                            # no stored confirmation
    forged = copy.deepcopy(stored)
    forged["scoring_confirmation"] = {"kind": "no_numeric_score", "user_id": "evil", "confirmed_at": "t",
                                      "basis_hash": basis_hash(stored)}
    assert carry_confirmation(stored, forged)["scoring_confirmation"] is None
    assert carry_confirmation(None, forged)["scoring_confirmation"] is None


def test_client_cannot_alter_the_stored_confirmation_metadata():
    stored = _confirmed()
    tampered = copy.deepcopy(stored)
    tampered["scoring_confirmation"]["user_id"] = "someone-else"
    assert carry_confirmation(stored, tampered)["scoring_confirmation"]["user_id"] == "u1"


def test_stale_or_inapplicable_confirmation_fails_final_validation():
    stored = _confirmed()
    stale = set_text(stored, "req_c1", "CIPD level 5")                       # confirmation still attached
    assert "confirmation_stale" in codes(validate_final(stale))
    now_scored = copy.deepcopy(scored_doc())
    now_scored["scoring_confirmation"] = stored["scoring_confirmation"]
    assert "confirmation_not_applicable" in codes(validate_final(now_scored))


def test_malformed_confirmation_is_rejected():
    d = preferred_only_doc()
    d["scoring_confirmation"] = {"kind": "other", "user_id": "u", "confirmed_at": "t", "basis_hash": "h"}
    assert "bad_confirmation" in codes(validate_final(d))


def test_carry_confirmation_survives_malformed_incoming():
    stored = _confirmed()
    assert carry_confirmation(stored, {"categories": "nope"})["scoring_confirmation"] is None


def test_scored_job_never_uses_a_confirmation():
    d = equalize_category(add_item(preferred_only_doc(), "skills", "ATS", "required")[0], "skills")
    d = carry_confirmation(_confirmed(), d)
    assert d["scoring_confirmation"] is None


def test_weight_findings_carry_the_totals_the_editor_shows():
    # 60 + 50 = 110 for the two Required skills: the finding names the actual total, the expected 100 and the difference
    d = scored_doc()
    d["categories"]["skills"]["items"][0].update(weight=60)
    d["categories"]["skills"]["items"][1].update(weight=50)
    finding = next(i for i in validate_final(d).errors if i.code == "required_weights_total")
    assert finding.category == "skills"
    assert finding.params == {"total": 110, "expected": 100, "difference": 10}


def test_category_total_finding_carries_actual_expected_and_difference():
    d = scored_doc()
    d["categories"]["education"]["weight"] = 41
    finding = next(i for i in validate_final(d).errors if i.code == "category_weights_total")
    assert finding.params == {"total": 101, "expected": 100, "difference": 1}


def test_required_weight_finding_names_the_value_it_rejected():
    d = scored_doc()
    d["categories"]["skills"]["items"][0].update(weight=None)
    finding = next(i for i in validate_final(d).errors if i.code == "required_weight_invalid")
    assert finding.item_id == d["categories"]["skills"]["items"][0]["id"] and finding.params == {"weight": None}
