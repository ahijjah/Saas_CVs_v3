"""The same behavior across unrelated fields and two languages, end to end through the offline pipeline:
the AI's interpretation (scripted) -> the extracted, validated document -> the per-option assessment of a CV (scripted model, verified quotes)
-> the combination by code -> the deterministic score. Nothing in the code under test knows any of these domains; each scenario is a different
job description with different wording, and the expectations are about structure (any one of, all of, examples, review-only, ambiguity), not subject matter."""
from __future__ import annotations

import pytest

from services.item_scoring import calc
from services.item_scoring import evaluator as V
from services.requirements_v2 import logic as L
from services.requirements_v2.extraction.parser import process_ai_output
from test_requirement_semantics_offline import _answer, _item, _opt


def _assess(doc, cv, answers):
    """Run the evaluation pipeline for one CV. answers: {item_text: (result, quote) | {leaf_id: (result, quote)}} for the criteria the CV speaks to."""
    specs = V.item_specs(doc)
    evaluations = []
    for spec in specs:
        given = next((a for k, a in answers.items() if k in spec.text), None)
        leaves = L.leaves(spec.logic) if (spec.logic or {}).get("options") else None
        if leaves:
            given = given or {}
            evaluations.append({"item_id": spec.item_id, "option_results": [
                {"leaf_id": leaf["leaf_id"], "result": given.get(leaf["leaf_id"], ("unclear", None))[0],
                 "evidence_quotes": [q] if (q := given.get(leaf["leaf_id"], ("unclear", None))[1]) else [], "explanation": "x", "confidence": 0.9} for leaf in leaves]})
        else:
            result, quote = given or ("unclear", None)
            evaluations.append({"item_id": spec.item_id, "result": result, "evidence_quotes": [quote] if quote else [], "explanation": "x", "confidence": 0.9})
    return {spec.item_id: item for spec, item in zip(specs, V.validate_output({"evaluations": evaluations}, specs, cv))}


def _score(doc, items):
    weights = {c: doc["categories"][c]["weight"] for c in doc["categories"]}
    inputs = [calc.ItemInput(i["id"], c, i["weight"], items[i["id"]].result) for c in doc["categories"] for i in doc["categories"][c]["items"] if i["importance"] == "required"]
    return calc.compute(weights, inputs)


def _doc(answer, jd):
    result = process_ai_output(answer, jd)
    assert result.status == "draft"
    return result.requirements


def _cat(answer, **weights):
    answer["category_weights"] = {c: weights.get(c, 0) for c in answer["category_weights"]}
    return answer


# (id, jd, answer, cv, answers by criterion, expected {criterion fragment: (result, matched option or None)}, expected status, expected score)
SCENARIOS = [
    ("hospitality-en", "Front Desk Agent\nRequirements: fluent French. Experience with a reservations system such as the hotel's own or a general booking tool. A patient manner with guests.",
     _cat(_answer(skills=[_item("Fluent French", source="fluent French"),
                          _item("Experience with a reservations system", source="Experience with a reservations system such as the hotel's own or a general booking tool",
                                logic={"options": None, "examples": ["the hotel's own", "a general booking tool"], "ambiguity": None})],
                  soft_skills=[_item("A patient manner with guests", source="A patient manner with guests")]), skills=60, soft_skills=40),
     "Speaks fluent French. Has used a regional booking platform for years. Known for a patient manner with guests.",
     {"Fluent French": ("met", "Speaks fluent French"), "reservations system": ("met", "used a regional booking platform"), "patient manner": ("met", "patient manner with guests")},
     {"Fluent French": ("met", None), "reservations system": ("met", None)}, "scored", 100),
    ("teaching-en", "Teacher\nRequirements: a teaching licence, or a degree in education together with two years of supervised practice. A licence from another region is accepted after review.",
     _answer(certifications=[_item("A teaching licence, or a degree in education with two years of supervised practice", source="a teaching licence, or a degree in education together with two years of supervised practice",
                                   logic={"options": [_opt("A teaching licence"), _opt("A degree in education with two years of supervised practice", all_of=["A degree in education", "Two years of supervised practice"]),
                                                      _opt("A licence from another region", "review")], "examples": None, "ambiguity": None})]),
     "Holds a teaching licence issued in another region. Has a degree in education.",
     {"teaching licence": {"o1": ("not_met", "Holds a teaching licence issued in another region"), "o2.1": ("met", "Has a degree in education"), "o2.2": ("unclear", None), "o3": ("met", "licence issued in another region")}},
     {"teaching licence": ("unclear", "A licence from another region")}, "review_required", None),
    ("legal-en", "Paralegal\nRequirements: a law degree and 3 years of experience.",
     _answer(education=[_item("A law degree", source="a law degree")], experience=[_item("3 years of experience", source="3 years of experience", experience={"subject": None, "min_years": 3})]),
     "Holds a law degree. Two years of paralegal work.",
     {"law degree": ("met", "Holds a law degree"), "3 years": ("not_met", "Two years of paralegal work")},
     {"law degree": ("met", None), "3 years": ("not_met", None)}, "scored", 50),
    ("logistics-en", "Dispatcher\nRequirements: at least 3 years of dispatch experience. Dispatch experience of at least 3 years is required.",
     _answer(experience=[_item("At least 3 years of dispatch experience", source="at least 3 years of dispatch experience", also=["Dispatch experience of at least 3 years is required."],
                               experience={"subject": "dispatch", "min_years": 3})]),
     "Four years of dispatch experience.", {"dispatch": ("met", "Four years of dispatch experience")}, {"dispatch": ("met", None)}, "scored", 100),
    ("agriculture-en", "Farm Manager\nRequirements: experience in crop or livestock management.",
     _answer(experience=[_item("Experience in crop or livestock management", source="experience in crop or livestock management",
                               logic={"options": None, "examples": None, "ambiguity": "It is unclear whether one of the two areas is enough or both are needed."})]),
     "Managed a dairy herd for six years.", {"crop or livestock": ("not_met", "Managed a dairy herd for six years")},
     {"crop or livestock": ("unclear", None)}, "review_required", None),
    ("retail-ar", "مدير متجر\nالمطلوب: خبرة في إدارة المتاجر أو في الإشراف على الفروع. يُنظر في الخبرة في قطاع آخر بعد التقييم.",
     _answer(experience=[_item("خبرة في إدارة المتاجر أو في الإشراف على الفروع", source="خبرة في إدارة المتاجر أو في الإشراف على الفروع",
                               logic={"options": [_opt("خبرة في إدارة المتاجر"), _opt("خبرة في الإشراف على الفروع"), _opt("الخبرة في قطاع آخر", "review")], "examples": None, "ambiguity": None})]),
     "عملت خمس سنوات في الإشراف على الفروع.",
     {"إدارة المتاجر": {"o1": ("not_met", "عملت خمس سنوات في الإشراف على الفروع"), "o2": ("met", "الإشراف على الفروع"), "o3": ("unclear", None)}},
     {"إدارة المتاجر": ("met", "خبرة في الإشراف على الفروع")}, "scored", 100),
    ("finance-ar", "محلل مالي\nالمطلوب: إجادة برامج التحليل مثل برنامج كذا. مهارات عرض قوية.",
     _cat(_answer(skills=[_item("إجادة برامج التحليل", source="إجادة برامج التحليل مثل برنامج كذا", logic={"options": None, "examples": ["برنامج كذا"], "ambiguity": None})],
                  soft_skills=[_item("مهارات عرض قوية", source="مهارات عرض قوية")]), skills=70, soft_skills=30),
     "يستخدم أدوات تحليل مختلفة منذ سنوات. مهارات عرض قوية.",
     {"إجادة برامج التحليل": ("met", "يستخدم أدوات تحليل مختلفة"), "مهارات عرض": ("met", "مهارات عرض قوية")},
     {"إجادة برامج التحليل": ("met", None)}, "scored", 100),
]


@pytest.mark.parametrize("name,jd,answer,cv,answers,expected,status,score", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_each_field_and_language_gets_the_same_structural_behavior(name, jd, answer, cv, answers, expected, status, score):
    doc = _doc(answer, jd)
    items = _assess(doc, cv, answers)
    by_text = {i["text"]: items[i["id"]] for c in doc["categories"] for i in doc["categories"][c]["items"]}
    for fragment, (result, matched) in expected.items():
        text = next(t for t in by_text if fragment in t)
        assert by_text[text].result == result, (name, fragment, by_text[text])
        if matched:
            assert matched in (by_text[text].matched_option or by_text[text].evidence[0]), (name, fragment)
    breakdown = _score(doc, items)
    assert breakdown.status == status and breakdown.final_score == score, (name, breakdown.status, breakdown.final_score)


def test_the_two_scenarios_with_similar_wording_end_differently_because_their_structure_differs():
    """'A law degree and 3 years' is two obligations, each with its own weight; 'a licence, or a degree with 2 years' is one criterion with one
    weight, whose alternative route can stand in for the other."""
    both = next(s for s in SCENARIOS if s[0] == "legal-en")
    either = next(s for s in SCENARIOS if s[0] == "teaching-en")
    doc_both, doc_either = _doc(both[2], both[1]), _doc(either[2], either[1])
    assert sum(len(doc_both["categories"][c]["items"]) for c in doc_both["categories"]) == 2
    assert sum(len(doc_either["categories"][c]["items"]) for c in doc_either["categories"]) == 1
    assert doc_either["categories"]["certifications"]["items"][0]["weight"] == 100


@pytest.mark.parametrize("name", [s[0] for s in SCENARIOS])
def test_every_scenario_document_is_valid_and_keeps_one_weight_per_criterion(name):
    scenario = next(s for s in SCENARIOS if s[0] == name)
    doc = _doc(scenario[2], scenario[1])
    for category, block in doc["categories"].items():
        required = [i for i in block["items"] if i["importance"] == "required"]
        assert not required or sum(i["weight"] for i in required) == 100
