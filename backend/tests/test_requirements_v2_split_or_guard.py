"""Candidate split-OR guard (requirements-v2-split-or-guard-1), offline: detection of one OR requirement split into one item per option
(both observed forms, English and Arabic), controls that must not be flagged, blocking under both classification-policy settings, no
acknowledgment bypass, resolution through the existing editing operations, reopening, composition with the injection guard, unchanged weights
and items, and replay of stored responses. No model call, no network, no database. The frozen parser/prompts/benchmark are asserted unchanged."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys

import pytest

from parser_candidates.requirements_v2_injection_guard_1 import NEEDS_INJECTION_REVIEW
from parser_candidates.requirements_v2_injection_guard_1 import inspect_result as inspect_injection
from parser_candidates.requirements_v2_injection_guard_1 import reconcile as reconcile_injection
from parser_candidates.requirements_v2_split_or_guard_1 import (
    NEEDS_SPLIT_OR_REVIEW, SPLIT_OR_VERSION, carry_split_or_review, composed_readiness, detect, inspect_result, open_issues, reconcile, validate_review,
)
from parser_candidates.requirements_v2_split_or_guard_1 import guard as S
from services.requirements_v2.acknowledgment import acknowledge_classification_warning
from services.requirements_v2.editing import add_item, remove_item, set_category_weight, set_structure, set_text
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.similarity import find_similar_items
from services.requirements_v2.weights import equalize_category

BACKEND = pathlib.Path(__file__).resolve().parent.parent
POLICIES = (True, False)
CATS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
EN_JD = "Backend Developer\n\nRequirements:\n- Python or Java.\n- Three years of backend development.\n- PostgreSQL.\n"
AR_JD = "مطور واجهات\n\nالمتطلبات:\n- إجادة اللغة العربية أو الإنجليزية.\n- خبرة لا تقل عن 3 سنوات في الدعم.\n- معرفة بنظام CRM.\n"


def _item(text, src=None, imp="required", alts=None, exp=None, origin="stated", cue=None):
    return {"text": text, "importance": imp, "importance_cue": cue, "source_text": src or text, "origin": origin, "alternatives": alts, "experience": exp}


def _raw(cats, weights=None):
    full = {c: [] for c in CATS}
    full.update(cats)
    return {"scoreability": {"status": "scoreable", "reason": ""}, "categories": full, "category_weights": {c: 0 for c in CATS} | (weights or {}),
            "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}


def _parse(jd, raw):
    res = parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop")
    assert res.ok
    return res


def _en(form):
    a, b = (["Java"], ["Python"]) if form == "single" else (["Python", "Java"], ["Python", "Java"])
    return EN_JD, _raw({"skills": [_item("Python", "Python or Java", alts=a), _item("Java", "Python or Java", alts=b), _item("PostgreSQL")],
                        "experience": [_item("Three years of backend development", exp={"subject": "backend development", "min_years": 3})]}, {"skills": 60, "experience": 40})


def _ar(form):
    a, b = (["اللغة الإنجليزية"], ["اللغة العربية"]) if form == "single" else (["العربية", "الإنجليزية"], ["العربية", "الإنجليزية"])
    return AR_JD, _raw({"skills": [_item("اللغة العربية", "إجادة اللغة العربية أو الإنجليزية", alts=a), _item("اللغة الإنجليزية", "إجادة اللغة العربية أو الإنجليزية", alts=b),
                                   _item("معرفة بنظام CRM")],
                        "experience": [_item("خبرة لا تقل عن 3 سنوات في الدعم", exp={"subject": "الدعم", "min_years": 3})]}, {"skills": 60, "experience": 40})


def _both(doc, review):
    return {p: composed_readiness(doc, None, review, require_classification_acknowledgment=p).state for p in POLICIES}


# ══ detection: both observed forms, English and Arabic ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("make,form,expected", [(_en, "single", "single_entry_mutual"), (_en, "complete", "complete_alternatives_repeated"),
                                                 (_ar, "single", "single_entry_mutual"), (_ar, "complete", "complete_alternatives_repeated")],
                         ids=["en_v21_form", "en_v22_form", "ar_v21_form", "ar_v22_form"])
def test_both_forms_are_detected_and_block_under_both_policies(make, form, expected):
    jd, raw = make(form)
    res = _parse(jd, raw)
    review = inspect_result(res)
    assert validate_review(review) == [] and review["guard_version"] == SPLIT_OR_VERSION
    [issue] = review["issues"]
    assert issue["form"] == expected and issue["category"] == "skills" and len(issue["item_ids"]) == 2 and len(issue["options"]) == 2
    assert issue["status"] == "open" and issue["reason"] and issue["shared_evidence"] in jd
    if form == "single":                                   # the frozen parser dropped these lists; the guard still saw them
        assert any(i.code == "alternatives_invalid_dropped" for i in res.review)
        assert all(i["alternatives"] is None for i in res.requirements["categories"]["skills"]["items"][:2])
        assert issue["raw_alternatives"] and all(v for v in issue["raw_alternatives"].values())
    assert _both(res.requirements, review) == {True: NEEDS_SPLIT_OR_REVIEW, False: NEEDS_SPLIT_OR_REVIEW}
    for p in POLICIES:                                     # even where the frozen parser says ready
        r = composed_readiness(res.requirements, None, review, require_classification_acknowledgment=p)
        assert r.can_proceed is False and r.scoring_mode is None and any(x.code == "split_or_requirement" for x in r.reasons)


def test_three_way_split_and_mixed_partial_list_are_detected():
    jd = "Requirements:\n- Python or Java or Go.\n"
    three = _parse(jd, _raw({"skills": [_item("Python", "Python or Java or Go", alts=["Java", "Go"]), _item("Java", "Python or Java or Go", alts=["Python", "Go"]),
                                        _item("Go", "Python or Java or Go", alts=["Python", "Java"])]}, {"skills": 100}))
    assert [len(i["item_ids"]) for i in detect(three.raw_ai_output, three.requirements)["issues"]] == [3]
    partial = _parse("Requirements:\n- Python or Java.\n", _raw({"skills": [_item("Python", "Python or Java", alts=["Python", "Java"]), _item("Java", "Python or Java")]}, {"skills": 100}))
    [iss] = detect(partial.raw_ai_output, partial.requirements)["issues"]
    assert iss["form"] == "mixed"                                                               # one item names its sibling, the sibling has no list


# ══ false-positive controls ════════════════════════════════════════════════════════════════════════════════════════════
CONTROLS = {
    "en_and_two_independent_items": ("Requirements:\n- Excel and Power BI.\n", {"skills": [_item("Excel", "Excel and Power BI"), _item("Power BI", "Excel and Power BI")]}),
    "ar_and_two_independent_items": ("المتطلبات:\n- إجادة Excel وPower BI.\n", {"skills": [_item("إجادة Excel", "إجادة Excel وPower BI"), _item("إجادة Power BI", "إجادة Excel وPower BI")]}),
    "en_and_items_wrongly_carrying_alternatives_but_no_or_in_the_sentence": (
        "Requirements:\n- Excel and Power BI.\n", {"skills": [_item("Excel", "Excel and Power BI", alts=["Excel", "Power BI"]), _item("Power BI", "Excel and Power BI", alts=["Excel", "Power BI"])]}),
    "en_or_sentence_but_no_alternatives_at_all": ("Requirements:\n- Python or Java.\n", {"skills": [_item("Python", "Python or Java"), _item("Java", "Python or Java")]}),
    "ar_or_sentence_but_no_alternatives_at_all": ("المتطلبات:\n- إجادة اللغة العربية أو الإنجليزية.\n", {"skills": [_item("اللغة العربية", "إجادة اللغة العربية أو الإنجليزية"), _item("اللغة الإنجليزية", "إجادة اللغة العربية أو الإنجليزية")]}),
    "en_correct_single_or_item": ("Requirements:\n- Python or Java.\n", {"skills": [_item("Python or Java", "Python or Java", alts=["Python", "Java"])]}),
    "ar_correct_single_or_item": ("المتطلبات:\n- إجادة اللغة العربية أو الإنجليزية.\n", {"skills": [_item("إجادة اللغة العربية أو الإنجليزية", alts=["العربية", "الإنجليزية"])]}),
    "ordinary_repeated_requirement_different_evidence": ("Requirements:\n- Good communication.\nNote: we want good communication with everyone.\n",
                                                          {"soft_skills": [_item("Good communication", "Good communication."), _item("Good communication", "we want good communication with everyone")]}),
    "same_text_same_evidence_without_links": ("Requirements:\n- Python or Java.\n", {"skills": [_item("Python or Java", "Python or Java"), _item("Python or Java", "Python or Java")]}),
    "same_evidence_in_two_categories": ("Requirements:\n- Python or Java.\n", {"skills": [_item("Python", "Python or Java", alts=["Java"])], "domain_knowledge": [_item("Java", "Python or Java", alts=["Python"])]}),
    "unrelated_items": ("Requirements:\n- Python.\n- Java.\n", {"skills": [_item("Python", alts=["Rust"]), _item("Java", alts=["Kotlin"])]}),
}


@pytest.mark.parametrize("name", sorted(CONTROLS))
@pytest.mark.parametrize("policy", POLICIES, ids=["ack_required", "ack_not_required"])
def test_controls_are_not_flagged_and_readiness_equals_the_frozen_result(name, policy):
    jd, cats = CONTROLS[name]
    res = _parse(jd, _raw(cats, {c: 100 // len(cats) for c in cats}))
    review = inspect_result(res)
    assert review["issues"] == [], review
    assert composed_readiness(res.requirements, None, review, require_classification_acknowledgment=policy).state == \
        compute_readiness(res.requirements, require_classification_acknowledgment=policy).state


def test_ordinary_duplicate_warnings_stay_informational():
    jd = "Requirements:\n- Good written communication.\n"
    res = _parse(jd, _raw({"skills": [_item("Good written communication")], "soft_skills": [_item("Good written communication")]}, {"skills": 50, "soft_skills": 50}))   # the B06 v2-2 pattern
    assert find_similar_items(res.requirements)                                                  # the similarity layer still warns ...
    review = inspect_result(res)
    for p in POLICIES:                                                                           # ... and it never blocks
        assert composed_readiness(res.requirements, None, review, require_classification_acknowledgment=p).state == compute_readiness(res.requirements, require_classification_acknowledgment=p).state


# ══ nothing is changed; policy; no bypass ══════════════════════════════════════════════════════════════════════════════
def test_items_weights_raw_output_and_original_are_preserved_and_acknowledgment_cannot_bypass():
    jd, raw = _en("complete")
    raw["categories"]["skills"][2] = _item("PostgreSQL", "PostgreSQL", imp="preferred", cue="Requirements")        # a frozen classification warning exists
    res = _parse(jd, raw)
    before = (copy.deepcopy(res.requirements), copy.deepcopy(res.raw_ai_output), copy.deepcopy(res.original), copy.deepcopy(res.category_weights), copy.deepcopy(res.review))
    review = inspect_result(res)
    composed_readiness(res.requirements, None, review)
    reconcile(review, res.requirements, user_id="u", at="t")
    assert before == (res.requirements, res.raw_ai_output, res.original, res.category_weights, res.review)
    assert len(res.requirements["categories"]["skills"]["items"]) == 3                             # both option items stay visible
    doc = copy.deepcopy(res.requirements)
    for w in (doc.get("classification_review") or {}).get("warnings", []):
        doc = acknowledge_classification_warning(doc, w["id"], user_id="u1", acknowledged_at="2026-10-09T10:00:00Z")
    assert compute_readiness(doc, require_classification_acknowledgment=True).state != "needs_classification_review"
    assert _both(doc, review) == {True: NEEDS_SPLIT_OR_REVIEW, False: NEEDS_SPLIT_OR_REVIEW}


def test_the_stored_review_cannot_be_forged():
    jd, raw = _en("single")
    res = _parse(jd, raw)
    review = inspect_result(res)
    forged = copy.deepcopy(review)
    forged["issues"][0]["status"], forged["issues"][0]["resolution"] = "resolved", {"kind": "x", "by": "attacker", "at": "t"}
    assert carry_split_or_review(review, forged) == review and carry_split_or_review(None, forged) is None
    assert _both(res.requirements, forged) == {True: NEEDS_SPLIT_OR_REVIEW, False: NEEDS_SPLIT_OR_REVIEW}        # openness is derived from the document
    bad = copy.deepcopy(review)
    bad["issues"][0]["status"], bad["issues"][0]["resolution"] = "resolved", None
    assert validate_review(bad) != []


# ══ resolution through the existing editing operations ═══════════════════════════════════════════════════════════════════
def _prep(make, form):
    jd, raw = make(form)
    res = _parse(jd, raw)
    return res, inspect_result(res)


def _ids(review):
    return review["issues"][0]["item_ids"]


@pytest.mark.parametrize("make", [_en, _ar], ids=["en", "ar"])
def test_keep_one_item_with_the_full_alternatives_and_remove_the_redundant_one_v22_form(make):
    res, review = _prep(make, "complete")
    keep, drop = _ids(review)
    doc = remove_item(res.requirements, drop)                                                       # 'Python' already carries ['Python', 'Java']
    rev, events = reconcile(review, doc, user_id="u7", at="2026-10-09T12:00:00Z")
    assert [(e["event"], e["resolution"]) for e in events] == [("resolved", "kept_one_item_with_full_alternatives")]
    assert rev["issues"][0]["resolution"] == {"kind": "kept_one_item_with_full_alternatives", "by": "u7", "at": "2026-10-09T12:00:00Z"}
    assert open_issues(rev, doc) == []
    # the frozen weight validation still applies: the remaining skill weights no longer total 100 until the recruiter rebalances (nothing is redistributed for her)
    for p in POLICIES:
        assert composed_readiness(doc, None, rev, require_classification_acknowledgment=p).state == "needs_review"
    kept_before = {i["id"]: i["weight"] for i in res.requirements["categories"]["skills"]["items"] if i["id"] != drop}
    assert {i["id"]: i["weight"] for i in doc["categories"]["skills"]["items"]} == kept_before and sum(kept_before.values()) < 100       # no redistribution
    fixed = equalize_category(doc, "skills")
    assert composed_readiness(fixed, None, rev, require_classification_acknowledgment=False).state == "ready"
    assert composed_readiness(fixed, None, rev, require_classification_acknowledgment=True).state == compute_readiness(fixed).state


def test_v21_form_needs_the_alternatives_to_be_restored_on_the_kept_item():
    res, review = _prep(_en, "single")
    keep, drop = _ids(review)
    doc = remove_item(res.requirements, drop)                                                       # the option 'Java' would silently disappear
    rev, events = reconcile(review, doc)
    assert events == [] and _both(equalize_category(doc, "skills"), rev) == {True: NEEDS_SPLIT_OR_REVIEW, False: NEEDS_SPLIT_OR_REVIEW}
    doc2 = set_structure(doc, keep, alternatives=["Python", "Java"])
    doc2 = set_text(doc2, keep, "Python or Java")
    rev2, ev2 = reconcile(rev, doc2, user_id="u", at="t")
    assert [e["resolution"] for e in ev2] == ["kept_one_item_with_full_alternatives"] and open_issues(rev2, doc2) == []
    assert _both(equalize_category(doc2, "skills"), rev2) == {True: compute_readiness(equalize_category(doc2, "skills")).state, False: "ready"}
    # one alternative is not "full"
    partial = set_structure(doc, keep, alternatives=["Python"])
    assert open_issues(reconcile(rev, partial)[0], partial) != []


def test_removing_the_whole_group_clears_the_issue_but_normal_validation_still_applies():
    res, review = _prep(_en, "complete")
    doc = res.requirements
    for iid in _ids(review):
        doc = remove_item(doc, iid)
    rev, events = reconcile(review, doc)
    assert [e["resolution"] for e in events] == ["group_removed"] and open_issues(rev, doc) == []
    assert composed_readiness(equalize_category(doc, "skills"), None, rev, require_classification_acknowledgment=False).state == "ready"       # other items remain
    only = _parse("Requirements:\n- Python or Java.\n", _raw({"skills": [_item("Python", "Python or Java", alts=["Python", "Java"]), _item("Java", "Python or Java", alts=["Python", "Java"])]}, {"skills": 100}))
    rv = inspect_result(only)
    empty = only.requirements
    for iid in _ids(rv):
        empty = remove_item(empty, iid)
    rv2, _ = reconcile(rv, empty)
    assert open_issues(rv2, empty) == []
    for p in POLICIES:                                                                               # an empty job is still not ready
        assert composed_readiness(empty, None, rv2, require_classification_acknowledgment=p).state == "needs_items"


def test_a_false_positive_can_be_decoupled_by_clearing_the_links_but_one_remaining_link_is_not_enough():
    res, review = _prep(_en, "complete")
    a, b = _ids(review)
    one_cleared = set_structure(res.requirements, a, alternatives=None)
    assert open_issues(reconcile(review, one_cleared)[0], one_cleared) != []                         # 'Java' still lists both options
    both_cleared = set_structure(one_cleared, b, alternatives=None)
    rev, events = reconcile(review, both_cleared, user_id="u", at="t")
    assert [e["resolution"] for e in events] == ["decoupled_by_recruiter"] and open_issues(rev, both_cleared) == []
    v21res, v21 = _prep(_en, "single")
    x, y = _ids(v21)
    # v2-1 form: the frozen parser already dropped the lists, so "clearing" them is invisible and cannot decouple (documented limit) ...
    cleared = set_structure(set_structure(v21res.requirements, x, alternatives=None), y, alternatives=None)
    assert open_issues(reconcile(v21, cleared)[0], cleared) != []
    untouched = set_text(v21res.requirements, x, "Python")                                           # ... nor does a harmless text edit
    assert open_issues(reconcile(v21, untouched)[0], untouched) != []
    # ... the false-positive path there is: remove both and add the two requirements as the recruiter's own items
    own = remove_item(remove_item(v21res.requirements, x), y)
    own, _ = add_item(own, "skills", "Python", "required")
    own, _ = add_item(own, "skills", "Java", "required")
    rev, ev = reconcile(v21, own, user_id="u", at="t")
    assert [e["resolution"] for e in ev] == ["group_removed"] and detect(v21res.raw_ai_output, own)["issues"] == []


def test_an_unrelated_edit_never_clears_the_issue():
    res, review = _prep(_en, "complete")
    doc = res.requirements
    other = next(i["id"] for i in doc["categories"]["skills"]["items"] if i["id"] not in _ids(review))
    exp_item = doc["categories"]["experience"]["items"][0]["id"]
    for edited in (set_text(doc, other, "PostgreSQL 15"), set_category_weight(doc, "experience", 55), set_text(doc, exp_item, "Four years of backend development"),
                   remove_item(doc, other), add_item(doc, "domain_knowledge", "Kafka", "preferred")[0],
                   set_text(doc, _ids(review)[0], "Python language")):
        rev, events = reconcile(review, edited)
        assert events == [] and open_issues(rev, edited) != [] and rev["issues"][0]["status"] == "open"


def test_restoring_the_split_reopens_the_issue():
    res, review = _prep(_en, "complete")
    keep, drop = _ids(review)
    fixed = remove_item(res.requirements, drop)
    rev, _ = reconcile(review, fixed, user_id="u", at="t1")
    assert rev["issues"][0]["status"] == "resolved"
    rev2, ev2 = reconcile(rev, res.requirements, user_id="u", at="t2")                              # the split items are back (e.g. an older version is saved)
    assert [e["event"] for e in ev2] == ["reopened"] and rev2["issues"][0]["history"][-1]["event"] == "reopened" and open_issues(rev2, res.requirements)
    assert _both(res.requirements, rev2) == {True: NEEDS_SPLIT_OR_REVIEW, False: NEEDS_SPLIT_OR_REVIEW}
    # the kept item losing its full alternatives also reopens it
    lost = set_structure(fixed, keep, alternatives=None)
    rev3, ev3 = reconcile(rev, lost)
    assert [e["event"] for e in ev3] == ["reopened"]
    assert reconcile(rev3, lost)[1] == []                                                            # idempotent


def test_weights_and_item_visibility_are_untouched_by_detection_and_reconciliation():
    res, review = _prep(_en, "complete")
    w0 = {c: res.requirements["categories"][c]["weight"] for c in CATS}
    iw0 = [i["weight"] for i in res.requirements["categories"]["skills"]["items"]]
    reconcile(review, res.requirements)
    composed_readiness(res.requirements, None, review)
    assert {c: res.requirements["categories"][c]["weight"] for c in CATS} == w0 and [i["weight"] for i in res.requirements["categories"]["skills"]["items"]] == iw0
    assert iw0 == [34, 33, 33]                                                                     # the double weight slot is visible, and left for the recruiter


# ══ composition with the injection guard ═════════════════════════════════════════════════════════════════════════════════
FIX = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_injection_guard" / "b06_v22_run1.json").read_text(encoding="utf-8"))


def test_fixing_the_or_issue_cannot_bypass_an_injection_blocker():
    res = parse_response(FIX["raw"], FIX["jd"], FIX["finish_reason"])
    inj, sor = inspect_injection(FIX["jd"], res), inspect_result(res)
    assert [i["kind"] for i in inj["issues"]] == ["requirement", "weights"] and len(sor["issues"]) == 1                # B06 v2-2 run 1 has both kinds
    doc = res.requirements
    for p in POLICIES:
        r = composed_readiness(doc, inj, sor, require_classification_acknowledgment=p)
        assert r.state == NEEDS_INJECTION_REVIEW and {x.code for x in r.reasons} >= {"injection_contamination", "split_or_requirement"}
    keep, drop = sor["issues"][0]["item_ids"]
    fixed_or = equalize_category(remove_item(doc, drop), "skills")
    sor2, _ = reconcile(sor, fixed_or)
    assert open_issues(sor2, fixed_or) == []
    for p in POLICIES:                                                                                                    # OR fixed, injection not: still blocked by injection
        assert composed_readiness(fixed_or, inj, sor2, require_classification_acknowledgment=p).state == NEEDS_INJECTION_REVIEW
    # injection fixed (Rust removed, soft_skills corrected), OR not: the OR blocker shows
    req = next(i for i in inj["issues"] if i["kind"] == "requirement")
    fixed_inj = set_category_weight(set_category_weight(set_category_weight(remove_item(doc, req["item_id"]), "skills", 30), "experience", 50), "soft_skills", 20)
    inj2, _ = reconcile_injection(inj, fixed_inj)
    for p in POLICIES:
        assert composed_readiness(fixed_inj, inj2, sor, require_classification_acknowledgment=p).state == NEEDS_SPLIT_OR_REVIEW
    # both fixed: the frozen result
    both = set_category_weight(set_category_weight(set_category_weight(equalize_category(remove_item(remove_item(doc, req["item_id"]), drop), "skills"),
                                                                       "skills", 30), "experience", 50), "soft_skills", 20)
    inj3, _ = reconcile_injection(inj, both)
    sor3, _ = reconcile(sor, both)
    assert composed_readiness(both, inj3, sor3, require_classification_acknowledgment=False).state == "ready"
    assert composed_readiness(both, inj3, sor3, require_classification_acknowledgment=True).state == compute_readiness(both).state


# ══ replay of stored responses ══════════════════════════════════════════════════════════════════════════════════════════
def _replay_module():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_split_or_replay", BACKEND / "scripts" / "requirements_v2_split_or_guard_replay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(BACKEND / "scripts"))


def test_replay_flags_exactly_the_observed_split_calls_and_leaves_the_official_gates_alone():
    rp = _replay_module()
    cases = rp.ev.load_cases()
    out = {n: rp.replay_run(cases, d) for n, d in rp.RUNS.items()}
    flagged = {(n, r["case"][:3], r["run"], r["split_or"][0]["form"]) for n, rep in out.items() for r in rep["rows"] if r["split_or"]}
    assert flagged == {("v2-1 baseline", "B06", "run1", "single_entry_mutual"), ("v2-1 baseline", "B06", "run2", "single_entry_mutual"),
                       ("v2-1 baseline", "B08", "run1", "single_entry_mutual"), ("v2-1 baseline", "B08", "run2", "single_entry_mutual"),
                       ("v2-1 baseline", "B12", "run1", "single_entry_mutual"), ("v2-1 baseline", "B12", "run2", "single_entry_mutual"),
                       ("v2-1 baseline", "B02", "run2", "single_entry_mutual"),
                       ("v2-2 candidate", "B02", "run1", "complete_alternatives_repeated"), ("v2-2 candidate", "B02", "run2", "complete_alternatives_repeated"),
                       ("v2-2 candidate", "B06", "run1", "complete_alternatives_repeated"), ("v2-2 candidate", "B06", "run2", "complete_alternatives_repeated")}
    for n, rep in out.items():
        assert len(rep["rows"]) == 24
        for r in rep["rows"]:
            if r["split_or"]:
                assert r["composed_ack_required"] in (NEEDS_SPLIT_OR_REVIEW, NEEDS_INJECTION_REVIEW) and r["composed_ack_required"] == r["composed_ack_not_required"]
                if r["injection_issues"]:
                    assert r["composed_ack_required"] == NEEDS_INJECTION_REVIEW                                         # the injection blocker keeps priority
            elif not r["injection_issues"]:
                assert r["composed_ack_required"] == r["frozen_ack_required"] and r["composed_ack_not_required"] == r["frozen_ack_not_required"]
    changed = sum(1 for rep in out.values() for r in rep["rows"] if r["changed_by_split_or"])
    assert changed == 8                                                                                                  # 5 (v2-1) + 3 (v2-2); the rest were already blocked by the injection guard
    for name, d in rp.RUNS.items():
        assert out[name]["official_gates"] == json.loads((d / "results.json").read_text(encoding="utf-8"))["gates"]       # official gates: the saved results, unchanged
    md = rp.render(out)
    assert "NOT a benchmark result" in md and "needs_split_or_review" in md


def test_frozen_artifacts_are_untouched():
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location("req_v2_run_s", BACKEND / "scripts" / "requirements_v2_extraction_run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
    finally:
        sys.path.remove(str(BACKEND / "scripts"))
    import subprocess
    try:
        subprocess.run(["git", "-C", str(run.REPO), "cat-file", "-e", run.FROZEN_COMMIT], check=True, capture_output=True)
    except Exception:
        pytest.skip("frozen commit not in this checkout")
    assert run.verify_frozen() == [] and run.verify_candidate() == []
    from services.requirements_v2.extraction.prompt import PROMPT_SHA256
    assert PROMPT_SHA256 == "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
