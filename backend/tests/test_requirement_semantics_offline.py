"""Requirement semantics, offline: how a requirement's wording combines (any one of / all of / examples / accepted only after review /
flagged ambiguity), how merged statements keep every obligation, how a candidate's evidence is assessed per option and combined by code, and
that nothing is silently changed for a document that has none of it.

The scenarios are deliberately unrelated to each other (construction, nursing, accounting, hospitality, logistics, teaching, legal, a
warehouse) and run in English and Arabic. The AI's interpretation is scripted: these tests check what the CODE does with an interpretation,
and that two statements with similar wording but different intended meaning end up with different, correct behavior.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import re
from decimal import Decimal

import pytest

from services import eligibility_service as G
from services import requirements_edit as E
from services.item_scoring import calc, combine
from services.item_scoring import evaluator as V
from services.item_scoring.combine import LeafResult
from services.requirements_v2 import contract as C
from services.requirements_v2 import logic as L
from services.requirements_v2 import structure as ST
from services.requirements_v2.comparison import edited_categories
from services.requirements_v2.extraction import build_request
from services.requirements_v2.extraction.parser import process_ai_output
from services.requirements_v2.readiness import basis_hash as readiness_hash, compute_readiness
from services.requirements_v2.validation import validate_structure

USER = "11111111-1111-4111-8111-111111111111"
NOW = "2026-11-25T12:00:00+00:00"
NONE_EXP = {"subject": None, "min_years": None}


def _item(text, importance="required", source=None, *, cue=None, logic=None, also=None, experience=None, alternatives=None, origin="stated"):
    out = {"text": text, "importance": importance, "importance_cue": cue, "source_text": source or text, "origin": origin,
           "logic": logic, "experience": experience if experience is not None else NONE_EXP}
    if alternatives is not None:
        out["alternatives"] = alternatives
    if also is not None:
        out["also_stated_in"] = also
    return out


def _answer(**categories):
    cats = {c: [] for c in C.CATEGORIES}
    cats.update(categories)
    return {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
            "category_weights": {c: 10 for c in C.CATEGORIES}, "non_scoreable_requirements": [], "post_hiring_conditions": [],
            "informational_items": [], "warnings": []}


def _opt(text, acceptance="direct", all_of=None):
    return {"text": text, "acceptance": acceptance, "all_of": all_of}


def _process(answer, jd):
    result = process_ai_output(answer, jd)
    assert result.status == "draft", result
    return result


def _find(result, text_part):
    for c in C.CATEGORIES:
        for item in result.requirements["categories"][c]["items"]:
            if text_part in item["text"]:
                return item
    raise AssertionError(text_part)


def _issues(result, code):
    return [i for i in result.review if i.code == code]


# ── scenarios in unrelated fields ───────────────────────────────────────────────────────────────────────────────────

CONSTRUCTION = ("Site Supervisor\nRequirements:\nA diploma in civil engineering, or a bachelor's degree in any engineering field together with two years on "
                "construction sites. Experience with scheduling software such as spreadsheets or a project planner. Fluent English. Strong negotiation skills. "
                "Relevant site experience is considered where the candidate lacks a diploma.")
NURSING = ("Ward Nurse\nRequirements: registration with the national nursing council. Minimum 3 years in acute care. Candidates need either the advanced life "
           "support course or the paediatric course. Three or more years of hospital experience is required.")
ACCOUNTING_AR = ("محاسب\nالمطلوب: درجة البكالوريوس في المحاسبة أو الإدارة المالية. إجادة التعامل مع برامج المحاسبة مثل برنامج كذا أو برنامج كذا. "
                 "يُنظر في الخبرة العملية الطويلة بديلاً عن الدرجة بعد التقييم.")
HOTEL = "Front Desk Agent\nRequirements: fluent French. A friendly, patient manner with guests. Experience with a reservations system is a plus."
WAREHOUSE = ("Warehouse Operative\nRequirements: a forklift licence. Two years in a warehouse. The forklift licence is mandatory. "
             "Warehouse experience of 5 years is required for the lead role.")
LEGAL = "Paralegal\nRequirements: a law degree and 3 years of experience. A law degree or equivalent professional qualification."
LOGISTICS = "Dispatcher\nRequirements: at least 3 years of dispatch experience. Dispatch experience of at least 3 years is required."


def construction_answer():
    return _answer(
        education=[_item("Diploma in civil engineering, or a bachelor's degree in engineering with two years on construction sites",
                         source="A diploma in civil engineering, or a bachelor's degree in any engineering field together with two years on construction sites",
                         logic={"options": [_opt("Diploma in civil engineering"),
                                            _opt("Bachelor's degree in an engineering field with two years on construction sites",
                                                 all_of=["Bachelor's degree in an engineering field", "two years on construction sites"]),
                                            _opt("Relevant site experience where the candidate lacks a diploma", "review")],
                                "examples": None, "ambiguity": None})],
        skills=[_item("Experience with scheduling software", source="Experience with scheduling software such as spreadsheets or a project planner",
                      logic={"options": None, "examples": ["spreadsheets", "a project planner"], "ambiguity": None}),
                _item("Fluent English", source="Fluent English")],
        soft_skills=[_item("Strong negotiation skills", source="Strong negotiation skills")])


# ── 1. all of / any one of ──────────────────────────────────────────────────────────────────────────────────────────

def test_any_one_option_is_one_criterion_with_one_weight_and_the_parts_of_a_route_stay_inside_it():
    result = _process(construction_answer(), CONSTRUCTION)
    education = _find(result, "Diploma")
    assert [o["text"] for o in education["logic"]["options"]][0] == "Diploma in civil engineering"
    assert education["alternatives"] == [o["text"] for o in education["logic"]["options"]]          # the older field mirrors the options
    assert education["logic"]["options"][1]["all_of"] and len(education["logic"]["options"][1]["all_of"]) == 2
    assert [o["acceptance"] for o in education["logic"]["options"]] == ["direct", "direct", "review"]
    assert [o["id"] for o in education["logic"]["options"]] == ["o1", "o2", "o3"]
    assert len(result.requirements["categories"]["education"]["items"]) == 1                        # one criterion, however many routes
    assert education["weight"] == 100                                                               # one weight for the whole criterion


def test_independent_obligations_stay_separate_items_each_with_its_own_weight():
    answer = _answer(education=[_item("A law degree", source="a law degree"), _item("3 years of experience", source="3 years of experience")])
    result = _process(answer, LEGAL)
    items = result.requirements["categories"]["education"]["items"]
    assert len(items) == 2 and [i["weight"] for i in items] == [50, 50]
    assert all(i.get("logic") is None for i in items)


def test_similar_wording_different_meaning_all_versus_any():
    """'A and B' (two obligations) versus 'A or B' (one criterion with two options)."""
    both = _process(_answer(skills=[_item("Welding", source="Welding"), _item("Rigging", source="Rigging")]), "Welding and rigging are required.")
    either = _process(_answer(skills=[_item("Welding or rigging", source="Welding or rigging",
                                            logic={"options": [_opt("Welding"), _opt("Rigging")], "examples": None, "ambiguity": None})]),
                      "Welding or rigging is required.")
    assert len(both.requirements["categories"]["skills"]["items"]) == 2
    assert len(either.requirements["categories"]["skills"]["items"]) == 1
    assert either.requirements["categories"]["skills"]["items"][0]["alternatives"] == ["Welding", "Rigging"]


# ── 2. examples are illustrations ───────────────────────────────────────────────────────────────────────────────────

def test_examples_are_kept_as_examples_never_as_options_or_separate_items():
    result = _process(construction_answer(), CONSTRUCTION)
    item = _find(result, "scheduling software")
    assert item["logic"]["examples"] == ["spreadsheets", "a project planner"] and item["logic"]["options"] is None
    assert not item.get("alternatives")
    assert not any("spreadsheet" in i["text"].lower() for c in C.CATEGORIES for i in result.requirements["categories"][c]["items"])
    assert len(result.requirements["categories"]["skills"]["items"]) == 2


def test_examples_are_given_to_the_evaluator_but_are_not_assessable_leaves():
    item = _find(_process(construction_answer(), CONSTRUCTION), "scheduling software")
    spec = V.ItemSpec(item["id"], "skills", "required", item["text"], 50, item["logic"])
    entry = V._requirement_entry(spec)
    assert entry["examples"] == ["spreadsheets", "a project planner"] and "options" not in entry and "leaf_ids" not in entry
    assert L.leaves(item["logic"]) == []


def test_similar_wording_different_meaning_examples_versus_a_closed_list():
    """'such as X or Y' illustrates; 'one of: X, Y' offers options. Same words, different structure, different evaluation."""
    illustrated = _process(_answer(skills=[_item("Reporting tools", source="Reporting tools such as dashboards or exports",
                                                 logic={"options": None, "examples": ["dashboards", "exports"], "ambiguity": None})]),
                           "Reporting tools such as dashboards or exports.")
    closed = _process(_answer(skills=[_item("Dashboards or exports", source="One of: dashboards or exports",
                                            logic={"options": [_opt("dashboards"), _opt("exports")], "examples": None, "ambiguity": None})]),
                      "One of: dashboards or exports.")
    a = illustrated.requirements["categories"]["skills"]["items"][0]
    b = closed.requirements["categories"]["skills"]["items"][0]
    assert not a.get("alternatives") and a["logic"]["options"] is None
    assert b["alternatives"] == ["dashboards", "exports"] and b["logic"]["examples"] is None


# ── 3. alternatives accepted only after human review ────────────────────────────────────────────────────────────────

def test_a_review_only_alternative_is_kept_distinct_from_a_direct_one():
    item = _find(_process(construction_answer(), CONSTRUCTION), "Diploma")
    review = [o for o in item["logic"]["options"] if o["acceptance"] == "review"]
    assert [o["text"] for o in review] == ["Relevant site experience where the candidate lacks a diploma"]


def test_arabic_options_examples_and_a_review_only_route():
    answer = _answer(
        education=[_item("درجة البكالوريوس في المحاسبة أو الإدارة المالية", source="درجة البكالوريوس في المحاسبة أو الإدارة المالية",
                         logic={"options": [_opt("البكالوريوس في المحاسبة"), _opt("البكالوريوس في الإدارة المالية"),
                                            _opt("خبرة عملية طويلة بديلاً عن الدرجة", "review")], "examples": None, "ambiguity": None})],
        skills=[_item("إجادة التعامل مع برامج المحاسبة", source="إجادة التعامل مع برامج المحاسبة مثل برنامج كذا أو برنامج كذا",
                      logic={"options": None, "examples": ["برنامج كذا"], "ambiguity": None})])
    result = _process(answer, ACCOUNTING_AR)
    education = result.requirements["categories"]["education"]["items"][0]
    assert [o["acceptance"] for o in education["logic"]["options"]] == ["direct", "direct", "review"]
    assert education["alternatives"][0] == "البكالوريوس في المحاسبة"
    assert result.requirements["categories"]["skills"]["items"][0]["logic"]["examples"] == ["برنامج كذا"]
    assert not _issues(result, "logic_option_not_in_job_description")                                # Arabic wording is checked in Arabic


def test_an_option_that_is_not_worded_in_the_job_description_can_never_satisfy_the_criterion_by_itself():
    answer = _answer(skills=[_item("Boat handling or pilot licence", source="Boat handling or a pilot licence",
                                   logic={"options": [_opt("Boat handling"), _opt("Pilot licence"), _opt("Submarine navigation certificate")],
                                          "examples": None, "ambiguity": None})])
    result = _process(answer, "Harbour Assistant\nRequirements: boat handling or a pilot licence.")
    options = result.requirements["categories"]["skills"]["items"][0]["logic"]["options"]
    assert [o["acceptance"] for o in options] == ["direct", "direct", "review"]                      # the invented one is demoted, not trusted
    flagged = _issues(result, "logic_option_not_in_job_description")
    assert len(flagged) == 1 and "Submarine" in flagged[0].message


def test_an_unreadable_acceptance_never_becomes_automatic():
    logic, notes = L.normalize({"options": [{"text": "A", "acceptance": "sure"}, {"text": "B", "acceptance": "direct"}]})
    assert [o["acceptance"] for o in logic["options"]] == ["review", "direct"] and notes


def test_options_that_are_all_review_are_reset_to_direct_for_the_recruiter_to_review_not_left_unsatisfiable():
    logic, notes = L.normalize({"options": [{"text": "A", "acceptance": "review"}, {"text": "B", "acceptance": "review"}]})
    assert [o["acceptance"] for o in logic["options"]] == ["direct", "direct"] and any("no option was direct" in n for n in notes)


# ── 4. merging equivalent statements without losing a distinct obligation ───────────────────────────────────────────

def test_the_same_obligation_stated_twice_is_one_criterion_with_both_sources_and_one_weight():
    answer = _answer(experience=[_item("At least 3 years of dispatch experience", source="at least 3 years of dispatch experience",
                                       also=["Dispatch experience of at least 3 years is required."],
                                       experience={"subject": "dispatch", "min_years": 3})])
    result = _process(answer, LOGISTICS)
    items = result.requirements["categories"]["experience"]["items"]
    assert len(items) == 1 and items[0]["weight"] == 100
    assert items[0]["also_source_texts"] == ["Dispatch experience of at least 3 years is required."]
    assert items[0].get("logic") is None and not _issues(result, "merged_statements_differ")           # nothing differs: settled by the AI's own reading


def test_a_merge_whose_statements_differ_in_a_number_is_flagged_and_keeps_every_statement():
    answer = _answer(experience=[_item("Warehouse experience", source="Two years in a warehouse", also=["Warehouse experience of 5 years is required for the lead role."],
                                       experience={"subject": "warehouse", "min_years": 2})])
    result = _process(answer, WAREHOUSE)
    item = result.requirements["categories"]["experience"]["items"][0]
    assert item["also_source_texts"] == ["Warehouse experience of 5 years is required for the lead role."]
    assert "different number" in item["logic"]["ambiguity"]["note"]
    assert _issues(result, "merged_statements_differ")
    assert item["id"] in ST.structure_status(result.requirements, result.original).needs_review        # a person must look at it


def test_a_merge_that_drops_an_exception_is_flagged():
    answer = _answer(certifications=[_item("A forklift licence", source="a forklift licence", also=["The forklift licence is mandatory unless the role is office-based."])])
    jd = "Operative\nRequirements: a forklift licence. The forklift licence is mandatory unless the role is office-based."
    result = _process(answer, jd)
    assert "exception or condition" in result.requirements["categories"]["certifications"]["items"][0]["logic"]["ambiguity"]["note"]


def test_distinct_obligations_that_the_ai_kept_apart_are_never_merged_by_code():
    answer = _answer(experience=[_item("3 years in acute care", source="Minimum 3 years in acute care", experience={"subject": "acute care", "min_years": 3}),
                                 _item("3 years of hospital experience", source="Three or more years of hospital experience is required", experience={"subject": "hospital", "min_years": 3})])
    result = _process(answer, NURSING)
    items = result.requirements["categories"]["experience"]["items"]
    assert len(items) == 2 and [i["weight"] for i in items] == [50, 50] and all("also_source_texts" not in i for i in items)


def test_a_merged_statement_that_is_not_in_the_job_description_is_dropped_and_reported():
    answer = _answer(experience=[_item("3 years of dispatch", source="at least 3 years of dispatch experience", also=["a sentence the description never made"],
                                       experience={"subject": "dispatch", "min_years": 3})])
    result = _process(answer, LOGISTICS)
    assert "also_source_texts" not in result.requirements["categories"]["experience"]["items"][0]
    assert _issues(result, "also_source_not_found")


# ── 5. language and soft skills stay separate; ambiguity is flagged ──────────────────────────────────────────────────

def test_language_and_soft_skills_stay_separate_items_in_their_own_categories():
    answer = _answer(skills=[_item("Fluent French", source="fluent French")],
                     soft_skills=[_item("A friendly, patient manner with guests", source="A friendly, patient manner with guests")])
    result = _process(answer, HOTEL)
    assert [i["text"] for i in result.requirements["categories"]["skills"]["items"]] == ["Fluent French"]
    assert [i["text"] for i in result.requirements["categories"]["soft_skills"]["items"]] == ["A friendly, patient manner with guests"]


def test_the_pinned_prompt_teaches_the_structure_without_naming_a_tool_or_qualification_of_the_feedback():
    system = build_request("Any job description.")["messages"][0]["content"]
    for phrase in ("logic.options", "all_of", "logic.examples", 'acceptance "review"', "logic.ambiguity", "also_stated_in", "Do not guess",
                   "never folded into a technical skill"):
        assert phrase in system
    for tool in ("Tableau", "Power BI", "PowerShell", "ITIL", "CIPD", "SHRM", "Excel", "LinkedIn", "Azure", "AWS"):
        assert tool not in system


def test_a_flagged_ambiguity_is_reported_for_the_recruiter_and_blocks_readiness_until_a_person_decides():
    answer = _answer(experience=[_item("Experience in a ward or clinical setting", source="experience in a ward or clinical setting",
                                       logic={"options": None, "examples": None, "ambiguity": "It is unclear whether both settings are needed or either one."})])
    result = _process(answer, "Nurse\nRequirements: experience in a ward or clinical setting.")
    item = result.requirements["categories"]["experience"]["items"][0]
    assert item["logic"]["ambiguity"]["note"].startswith("It is unclear")
    assert _issues(result, "requirement_ambiguous")
    assert item["id"] in ST.structure_status(result.requirements, result.original).needs_review       # the AI's own reading does not settle it
    readiness = compute_readiness(result.requirements, original=result.original)
    assert not readiness.can_proceed


def test_a_recruiter_confirmation_settles_an_ambiguity_and_clearing_it_is_a_recorded_correction():
    answer = _answer(skills=[_item("Customer handling", source="customer handling",
                                   logic={"options": None, "examples": None, "ambiguity": "Unclear whether this means in person or by phone."})])
    result = _process(answer, "Agent\nRequirements: customer handling.")
    doc, original = result.requirements, result.original
    item_id = doc["categories"]["skills"]["items"][0]["id"]
    confirmed = ST.confirm_structure(doc, item_id, user_id=USER, confirmed_at=NOW, original=original)
    assert item_id in ST.structure_status(confirmed, original).confirmed
    # clearing the ambiguity through the edit operation is a "corrected" record, and the item is then settled
    plan = E.plan_operations(doc, original, [], E.parse_operations({"operations": [{"op": "set_logic", "item_id": item_id, "logic": None}]}) if False else
                             E.parse_operations([{"op": "set_logic", "item_id": item_id, "logic": None}]), user_id=USER, now=NOW, jd_text="Agent\nRequirements: customer handling.")
    assert "logic" not in plan.document["categories"]["skills"]["items"][0]
    assert ST.structure_status(plan.document, original).needs_review == ()


# ── the combination rules (code), independent of any wording ────────────────────────────────────────────────────────

def L_(result, *quotes, reason=None):
    return LeafResult(result, tuple(quotes) or (("a quotation",) if result in ("met", "partially_met", "not_met") else ()), "why", Decimal("0.9"), reason)


ROUTES = {"options": [_opt("Route A"), {"id": "x", "text": "Route B with C", "acceptance": "direct", "all_of": ["B", "C"]}, _opt("Route D", "review")],
          "examples": None, "ambiguity": None}
ROUTES = L.normalize(ROUTES)[0]


def outcome(leaves, logic=ROUTES):
    return combine.combine(logic, leaves)


def test_any_direct_option_met_satisfies_the_criterion_and_names_the_matched_option():
    out = outcome({"o1": L_("not_met"), "o2.1": L_("met", "q1"), "o2.2": L_("met", "q2"), "o3": L_("unclear")})
    assert (out.result, out.matched_option, out.review_reason) == ("met", "o2", None) and out.evidence == ("q1", "q2")
    assert [o["result"] for o in out.option_results] == ["not_met", "met", "unclear"]


def test_the_parts_of_one_option_must_all_be_met_the_and_rule():
    assert outcome({"o1": L_("not_met"), "o2.1": L_("met"), "o2.2": L_("not_met"), "o3": L_("not_met")}).result == "not_met"
    assert outcome({"o1": L_("not_met"), "o2.1": L_("met"), "o2.2": L_("unclear"), "o3": L_("not_met")}).result == "unclear"
    assert outcome({"o1": L_("not_met"), "o2.1": L_("met"), "o2.2": L_("partially_met"), "o3": L_("not_met")}).result == "partially_met"


def test_a_review_only_alternative_that_is_met_routes_to_a_person_and_is_neither_a_pass_nor_a_fail():
    out = outcome({"o1": L_("not_met"), "o2.1": L_("not_met"), "o2.2": L_("met"), "o3": L_("met")})
    assert (out.result, out.matched_option, out.review_reason) == ("unclear", "o3", "conditional_alternative")


def test_a_direct_match_wins_over_a_review_match_and_no_extra_credit_exists():
    out = outcome({"o1": L_("met"), "o2.1": L_("met"), "o2.2": L_("met"), "o3": L_("met")})
    assert (out.result, out.matched_option) == ("met", "o1")
    weights = [calc.ItemInput("crit", "skills", 100, out.result)]
    assert calc.compute({"skills": 100}, weights).final_score == 100                                   # four options met, one weight, 100 not 400


def test_missing_or_inconclusive_evidence_is_unclear_never_not_met():
    assert outcome({}).result == "unclear"                                                             # nothing assessed at all
    out = outcome({"o1": L_("not_met"), "o2.1": L_("not_met"), "o2.2": L_("not_met"), "o3": L_("unclear", reason="no_verified_evidence")})
    assert (out.result, out.review_reason) == ("unclear", "no_verified_evidence")


def test_every_option_not_met_is_not_met_only_when_each_is_established():
    assert outcome({"o1": L_("not_met"), "o2.1": L_("not_met"), "o2.2": L_("not_met"), "o3": L_("not_met")}).result == "not_met"


def test_the_best_partial_is_partially_met_and_names_that_option():
    out = outcome({"o1": L_("partially_met"), "o2.1": L_("not_met"), "o2.2": L_("not_met"), "o3": L_("not_met")})
    assert (out.result, out.matched_option) == ("partially_met", "o1")


def test_an_ambiguous_criterion_can_only_be_met_by_evidence_and_never_fails_by_itself():
    ambiguous = L.normalize({"options": [_opt("Route A"), _opt("Route B")], "ambiguity": "Unclear whether both are needed."})[0]
    failing = combine.combine(ambiguous, {"o1": L_("not_met"), "o2": L_("not_met")})
    assert (failing.result, failing.review_reason) == ("unclear", "ambiguous_requirement")
    partial = combine.combine(ambiguous, {"o1": L_("partially_met"), "o2": L_("not_met")})
    assert (partial.result, partial.review_reason) == ("unclear", "ambiguous_requirement")
    assert combine.combine(ambiguous, {"o1": L_("met"), "o2": L_("not_met")}).result == "met"


# ── the evaluator: per-option assessment, evidence verified, code combines ──────────────────────────────────────────

CV = "Ana Silva. Holds a diploma in civil engineering. Has two years on construction sites after a bachelor's degree in mechanical engineering."


def _spec(logic, importance="required"):
    return V.ItemSpec("req_edu", "education", importance, "Education", 100 if importance == "required" else None, logic)


def _entry(results, **extra):
    return {"item_id": "req_edu", **extra, "option_results": [
        {"leaf_id": k, "result": r, "evidence_quotes": q, "explanation": "x", "confidence": 0.9} for k, (r, q) in results.items()]}


def _validate(entry, logic=ROUTES, importance="required"):
    return V.validate_output({"evaluations": [entry]}, [_spec(logic, importance)], CV)[0]


def test_the_model_assesses_each_leaf_and_the_code_combines_them():
    item = _validate(_entry({"o1": ("not_met", ["Holds a diploma in civil engineering"]), "o2.1": ("met", ["bachelor's degree in mechanical engineering"]),
                             "o2.2": ("met", ["two years on construction sites"]), "o3": ("unclear", [])}, result="not_met"))
    assert item.result == "met" and item.matched_option == "Route B with C"                              # the model's own combined result is ignored
    assert [o["result"] for o in item.option_results] == ["not_met", "met", "unclear"]


def test_a_leaf_without_a_verified_quotation_is_unclear_so_the_option_is_not_credited():
    item = _validate(_entry({"o1": ("met", ["Holds a doctorate in marine biology"]), "o2.1": ("not_met", ["bachelor's degree in mechanical engineering"]),
                             "o2.2": ("not_met", ["two years on construction sites"]), "o3": ("not_met", ["diploma"])}))
    assert item.result == "unclear" and item.review_reason == "no_verified_evidence"


def test_a_skipped_leaf_counts_as_unclear_and_a_made_up_leaf_is_a_processing_error():
    skipped = _validate(_entry({"o1": ("not_met", ["Holds a diploma in civil engineering"])}))
    assert skipped.result == "unclear"
    with pytest.raises(V.EvaluationProcessingError):
        _validate(_entry({"o9": ("met", ["diploma"])}))
    with pytest.raises(V.EvaluationProcessingError):
        _validate({"item_id": "req_edu", "result": "met"})                                           # no option_results at all
    duplicate = _entry({"o1": ("met", ["diploma"])})
    duplicate["option_results"] = duplicate["option_results"] * 2
    with pytest.raises(V.EvaluationProcessingError):
        _validate(duplicate)


def test_a_review_only_alternative_met_in_the_cv_is_recorded_for_a_person():
    item = _validate(_entry({"o1": ("not_met", ["Holds a diploma in civil engineering"]), "o2.1": ("not_met", ["bachelor's degree in mechanical engineering"]),
                             "o2.2": ("not_met", ["two years on construction sites"]), "o3": ("met", ["two years on construction sites"])}))
    assert (item.result, item.review_reason, item.matched_option) == ("unclear", "conditional_alternative", "Route D")


def test_a_preferred_criterion_with_options_uses_yes_no_unclear_and_never_partial():
    prefer = lambda r: _validate(_entry({"o1": (r, ["Holds a diploma in civil engineering"]), "o2.1": ("not_met", ["bachelor's degree in mechanical engineering"]),
                                          "o2.2": ("not_met", ["two years on construction sites"]), "o3": ("not_met", ["diploma"])}), importance="preferred").result
    assert (prefer("met"), prefer("not_met"), prefer("unclear"), prefer("partially_met")) == ("yes", "no", "not_clear", "not_clear")


def test_the_options_and_examples_reach_the_model_as_structure_and_the_prompt_says_the_application_combines():
    entry = V._requirement_entry(_spec(L.normalize({"options": [_opt("A"), _opt("B", all_of=["b1", "b2"])], "examples": ["e.g. one"]})[0]))
    assert entry["leaf_ids"] == ["o1", "o2.1", "o2.2"] and entry["examples"] == ["e.g. one"]
    assert entry["options"][1]["parts"] == ["b1", "b2"]
    assert "the application does that" in V.ITEM_EVALUATION_SYSTEM_PROMPT and "Never mark a leaf not_met" in V.ITEM_EVALUATION_SYSTEM_PROMPT


def test_an_ambiguous_plain_criterion_cannot_fail_on_the_models_say_so():
    logic = L.normalize({"examples": None, "ambiguity": "Unclear scope."})[0]
    item = V.validate_output({"evaluations": [{"item_id": "req_edu", "result": "not_met", "evidence_quotes": ["Holds a diploma in civil engineering"],
                                               "explanation": "x", "confidence": 0.9}]}, [_spec(logic)], CV)[0]
    assert (item.result, item.review_reason) == ("unclear", "ambiguous_requirement")


# ── gates: ambiguity, a conditional alternative and missing evidence never fail a gate ─────────────────────────────

@pytest.mark.parametrize("item_result,gate,status", [("met", G.PASSED, G.ELIGIBLE), ("unclear", G.NEEDS_VERIFICATION, G.REVIEW_REQUIRED)])
def test_the_combined_result_drives_the_gate_the_same_way_as_any_other_item(item_result, gate, status):
    assert G._ITEM_TO_GATE[item_result] == gate and G.decide([G.GateState(gate)]) == status


def test_conditional_ambiguous_and_missing_evidence_all_resolve_to_unclear_so_none_fails_a_gate():
    cases = [outcome({"o1": L_("not_met"), "o2.1": L_("not_met"), "o2.2": L_("not_met"), "o3": L_("met")}),
             combine.combine(L.normalize({"options": [_opt("A"), _opt("B")], "ambiguity": "x"})[0], {"o1": L_("not_met"), "o2": L_("not_met")}),
             outcome({})]
    for case in cases:
        assert case.result == "unclear" and G._ITEM_TO_GATE[case.result] == G.NEEDS_VERIFICATION
        assert G.decide([G.GateState(G._ITEM_TO_GATE[case.result])]) == G.REVIEW_REQUIRED


# ── compatibility: a document without logic is exactly what it was ──────────────────────────────────────────────────

def _legacy_doc():
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        C.make_item("Welding or rigging", "required", item_id="req_a", weight=100, source_text="Welding or rigging", alternatives=["Welding", "Rigging"])]}
    return doc


def test_an_item_without_logic_has_no_new_keys_and_validates_exactly_as_before():
    item = _legacy_doc()["categories"]["skills"]["items"][0]
    assert "logic" not in item and "also_source_texts" not in item
    assert validate_structure(_legacy_doc()) == ()


def test_digests_and_structure_basis_of_an_existing_document_are_unchanged():
    doc = _legacy_doc()
    rows = [["skills", "req_a", "required", "Welding or rigging", ["Welding", "Rigging"], None]]
    assert readiness_hash(doc) == hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    basis = ST.basis_of(doc["categories"]["skills"]["items"][0])
    assert set(basis) == {"text", "alternatives", "experience"}
    old = {"text": "Welding or rigging", "alternatives": ["Welding", "Rigging"], "experience": None}
    assert ST.basis_hash(basis) == hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_a_stored_structure_record_without_logic_is_still_valid_and_the_comparison_ignores_absent_logic():
    doc = _legacy_doc()
    item = doc["categories"]["skills"]["items"][0]
    record = {"item_id": "req_a", "kind": "confirmed", "user_id": USER, "recorded_at": NOW, "basis": ST.basis_of(item), "basis_hash": ST.basis_hash(ST.basis_of(item))}
    assert ST.block_issues({"records": [record]}) == []
    assert not any(edited_categories(doc, copy.deepcopy(doc)).values())


def test_a_legacy_item_with_alternatives_but_no_logic_keeps_the_single_judgement_path():
    spec = V.item_specs(_legacy_doc())[0]
    assert spec.logic is None and "Welding / Rigging" in spec.text
    item = V.validate_output({"evaluations": [{"item_id": "req_a", "result": "met", "evidence_quotes": ["Welding"], "explanation": "x", "confidence": 0.9}]},
                             [spec], "Certified Welding inspector.")[0]
    assert item.result == "met" and item.matched_option is None and item.option_results is None


def test_a_document_with_logic_validates_and_the_alternatives_must_mirror_the_options():
    doc = _legacy_doc()
    item = doc["categories"]["skills"]["items"][0]
    doc["categories"]["skills"]["items"][0] = L.apply(item, L.normalize({"options": [_opt("Welding"), _opt("Rigging", "review")]})[0])
    assert validate_structure(doc) == ()
    broken = copy.deepcopy(doc)
    broken["categories"]["skills"]["items"][0]["alternatives"] = ["Something else", "Entirely"]
    assert "logic_alternatives_mismatch" in [i.code for i in validate_structure(broken)]


# ── the recruiter's correction ──────────────────────────────────────────────────────────────────────────────────────

def test_a_recruiter_can_correct_the_interpretation_and_it_is_recorded_as_a_correction():
    result = _process(construction_answer(), CONSTRUCTION)
    doc, original = result.requirements, result.original
    education = _find(result, "Diploma")
    corrected = {"options": [{"text": "Diploma in civil engineering", "acceptance": "direct", "all_of": None},
                             {"text": "Bachelor's degree in an engineering field with two years on construction sites", "acceptance": "direct",
                              "all_of": ["Bachelor's degree in an engineering field", "two years on construction sites"]}],
                 "examples": None, "ambiguity": None}
    plan = E.plan_operations(doc, original, [], E.parse_operations([{"op": "set_logic", "item_id": education["id"], "logic": corrected}]),
                             user_id=USER, now=NOW, jd_text=CONSTRUCTION)
    after = next(i for i in plan.document["categories"]["education"]["items"] if i["id"] == education["id"])
    assert [o["id"] for o in after["logic"]["options"]] == ["o1", "o2"] and after["alternatives"] == [o["text"] for o in after["logic"]["options"]]
    assert after["weight"] == education["weight"] and after["source_text"] == education["source_text"]    # weight and source wording are untouched
    assert (education["id"], "corrected") in plan.audit.structure_edits
    assert ST.structure_status(plan.document, original).needs_review == ()


@pytest.mark.parametrize("bad", [
    {"options": [{"text": "only one", "acceptance": "direct"}]},
    {"options": [{"text": "A", "acceptance": "maybe"}, {"text": "B", "acceptance": "direct"}]},
    {"options": [{"text": "A", "acceptance": "review"}, {"text": "B", "acceptance": "review"}]},
    {"options": [{"text": "A", "all_of": ["x"]}, {"text": "B"}]},
    {"examples": []}, {"unknown": 1}, "text", {"options": None, "examples": None, "ambiguity": None}])
def test_an_invalid_correction_is_refused_not_repaired(bad):
    result = _process(construction_answer(), CONSTRUCTION)
    item = _find(result, "Fluent English")
    with pytest.raises(E.InvalidOperation):
        E.plan_operations(result.requirements, result.original, [], E.parse_operations([{"op": "set_logic", "item_id": item["id"], "logic": bad}]),
                          user_id=USER, now=NOW, jd_text=CONSTRUCTION)


def test_the_edit_operation_accepts_no_extra_fields():
    with pytest.raises(E.InvalidOperation):
        E.parse_operations([{"op": "set_logic", "item_id": "req_a", "logic": None, "weight": 5}])


# ── nothing in the interpretation code names a tool, a qualification, an industry or a language ─────────────────────

DOMAIN_WORDS = ("python", "sql", "excel", "powershell", "tableau", "itil", "cipd", "shrm", "aws", "azure", "linux", "java", "forklift", "nurse",
                "engineer", "accountant", "welding", "degree", "diploma", "bachelor", "master", "licence", "license")


def test_the_interpretation_modules_hard_code_no_domain_vocabulary():
    root = pathlib.Path(__file__).resolve().parent.parent / "services"
    for rel in ("requirements_v2/logic.py", "item_scoring/combine.py", "requirements_v2/extraction/logic_reading.py"):
        code = "\n".join(line for line in (root / rel).read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(("#", '"""')))
        body = re.sub(r'""".*?"""', "", code, flags=re.S)
        for word in DOMAIN_WORDS:
            assert not re.search(rf"\b{word}\b", body, re.I), (rel, word)


# ── what the API shows ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_read_response_shows_logic_only_for_an_item_that_has_it_and_the_shape_of_every_other_item_is_unchanged():
    from routers import job_requirements as R
    result = _process(construction_answer(), CONSTRUCTION)
    response = R._edit_response(E.build_edit_view(result.requirements, job_id="job-1", revision=3, schema_version=2, original=result.original, jd_text=CONSTRUCTION)).model_dump()
    items = {i["text"]: i for block in response["document"]["categories"].values() for i in block["items"]}
    with_logic, plain = items["Experience with scheduling software"], items["Fluent English"]
    assert with_logic["logic"]["examples"] == ["spreadsheets", "a project planner"] and "also_source_texts" not in with_logic
    assert set(plain) == {"id", "text", "importance", "weight", "origin", "source_text", "alternatives", "experience", "gate", "gate_suggestion"}
    assert [o["acceptance"] for o in next(i for t, i in items.items() if t.startswith("Diploma"))["logic"]["options"]] == ["direct", "direct", "review"]
