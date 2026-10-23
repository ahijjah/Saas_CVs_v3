"""Requirements V2 item-level scoring, offline: the exact arithmetic, the acceptance example, the evaluator's validation of what the model
returns, evidence verification, preferred items, and the gate mapping. No database and no model call."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from services import eligibility_service as G
from services.effective_decision import effective_decision_sql
from services.item_scoring import calc as C
from services.item_scoring import evaluator as V
from services.item_scoring.calc import (
    MET, NOT_MET, PARTIALLY_MET, UNCLEAR, ItemInput, ScoringInputError, compute, exact_decimal, format_number, preferred_summary, round_final)


def items(*rows):
    return [ItemInput(i, c, w, r) for i, c, w, r in rows]


# ── the factors and one category ────────────────────────────────────────────────────────────────────────────────────

def _single(result, weight=40, other=60):
    rows = [("a", "skills", weight, result), ("b", "skills", 100 - weight, MET)]
    return compute({"skills": 100}, items(*rows)), other


def test_met_earns_the_full_item_weight():
    breakdown, _ = _single(MET)
    assert breakdown.categories[0].items[0].earned == 40


def test_partially_met_earns_exactly_half_the_item_weight():
    breakdown, _ = _single(PARTIALLY_MET)
    assert breakdown.categories[0].items[0].earned == 20 and C.PARTIAL_FACTOR == Fraction(1, 2)


def test_not_met_earns_zero():
    breakdown, _ = _single(NOT_MET)
    assert breakdown.categories[0].items[0].earned == 0


def test_unclear_receives_no_automatic_score():
    breakdown, _ = _single(UNCLEAR)
    first = breakdown.categories[0].items[0]
    assert first.earned is None and breakdown.categories[0].score is None and breakdown.final_raw is None and breakdown.final_score is None


def test_the_partial_factor_is_not_configurable():
    import ast
    import inspect
    imported = {n.module or a.name for n in ast.walk(ast.parse(inspect.getsource(C))) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in getattr(n, "names", [])} | {a.name for n in ast.walk(ast.parse(inspect.getsource(C))) if isinstance(n, ast.Import) for a in n.names}
    assert not ({"os", "config", "services.runtime_config", "services.prompt_config"} & imported)       # nothing configurable is read
    assert C.PARTIAL_FACTOR == Fraction(1, 2) and C.FACTORS["partially_met"] == Fraction(1, 2)
    assert "factor" not in inspect.signature(compute).parameters


def test_several_item_results_give_the_exact_category_score():
    rows = items(("a", "skills", 30, MET), ("b", "skills", 30, PARTIALLY_MET), ("c", "skills", 20, NOT_MET), ("d", "skills", 20, MET))
    breakdown = compute({"skills": 100}, rows)
    assert breakdown.categories[0].score == Fraction(65) and breakdown.final_raw == 65 and breakdown.final_score == 65


def test_category_weights_give_the_exact_final_score():
    rows = items(("a", "skills", 100, MET), ("b", "experience", 50, PARTIALLY_MET), ("c", "experience", 50, NOT_MET))
    breakdown = compute({"skills": 70, "experience": 30}, rows)
    assert breakdown.final_raw == Fraction(70) + Fraction(25) * Fraction(30, 100)       # 70 + 7.5
    assert format_number(breakdown.final_raw) == "77.5"


# ── arithmetic is exact and rounded once ────────────────────────────────────────────────────────────────────────────

def test_arithmetic_uses_fractions_never_binary_floats():
    rows = items(("a", "skills", 33, PARTIALLY_MET), ("b", "skills", 67, MET))
    breakdown = compute({"skills": 100}, rows)
    assert all(isinstance(v, Fraction) for v in (breakdown.final_raw, breakdown.categories[0].score))
    assert breakdown.final_raw == Fraction(16.5 if False else 33, 2) + 67      # exact, with no float anywhere
    assert not any(isinstance(getattr(breakdown, f), float) for f in ("final_raw", "final_min", "final_max"))


@pytest.mark.parametrize("value,expected", [(Fraction(735, 10), 74), (Fraction(841, 10), 84), (Fraction(788, 10), 79), (Fraction(1, 2), 1),
                                            (Fraction(499, 1000), 0), (Fraction(995, 10), 100), (Fraction(0), 0), (Fraction(100), 100)])
def test_the_single_rounding_rule_is_half_up_on_the_final_score(value, expected):
    assert round_final(value) == expected


def test_rounding_happens_once_on_the_final_score_not_per_item_or_category():
    # three categories whose contributions are 0.5 each: per-category rounding would give 3, rounding the sum gives 2 (1.5 rounds half up)
    rows = items(("a", "skills", 100, PARTIALLY_MET), ("b", "experience", 100, PARTIALLY_MET), ("c", "education", 100, PARTIALLY_MET))
    breakdown = compute({"skills": 34, "experience": 33, "education": 33}, rows)
    assert breakdown.final_raw == Fraction(50)
    rows = items(("a", "skills", 1, MET), ("b", "skills", 99, NOT_MET), ("c", "experience", 1, MET), ("d", "experience", 99, NOT_MET),
                 ("e", "education", 1, MET), ("f", "education", 99, NOT_MET))
    breakdown = compute({"skills": 50, "experience": 50, "education": 0}, rows[:4])
    assert breakdown.final_raw == 1 and breakdown.final_score == 1


def test_exact_decimal_refuses_to_round():
    assert exact_decimal(Fraction(841, 10)) == Decimal("84.100")
    with pytest.raises(ArithmeticError):
        exact_decimal(Fraction(1, 3))


# ── the worked acceptance example ───────────────────────────────────────────────────────────────────────────────────

WEIGHTS = {"skills": 53, "experience": 27, "education": 20}


def acceptance(arabic):
    return compute(WEIGHTS, items(
        ("sql", "skills", 20, MET), ("incident", "skills", 20, MET), ("python", "skills", 20, PARTIALLY_MET), ("arabic", "skills", 20, arabic),
        ("english", "skills", 20, NOT_MET), ("exp", "experience", 100, MET), ("edu", "education", 100, MET)))


def test_acceptance_example_before_the_arabic_review():
    breakdown = acceptance(UNCLEAR)
    skills = breakdown.categories[0]
    assert breakdown.status == C.REVIEW_REQUIRED and breakdown.final_raw is None and breakdown.final_score is None
    assert (skills.score_min, skills.score_max) == (50, 70)
    assert (format_number(skills.contribution_min), format_number(skills.contribution_max)) == ("26.5", "37.1")
    assert [c.contribution for c in breakdown.categories[1:]] == [27, 20] and [c.score for c in breakdown.categories[1:]] == [100, 100]
    assert (format_number(breakdown.final_min), format_number(breakdown.final_max)) == ("73.5", "84.1")
    assert breakdown.unclear_item_ids == ("arabic",)


def test_acceptance_example_arabic_met():
    breakdown = acceptance(MET)
    assert breakdown.status == C.SCORED and breakdown.categories[0].score == 70
    assert format_number(breakdown.final_raw) == "84.1" and breakdown.final_score == 84


def test_acceptance_example_arabic_partial():
    breakdown = acceptance(PARTIALLY_MET)
    assert breakdown.categories[0].score == 60 and format_number(breakdown.final_raw) == "78.8" and breakdown.final_score == 79


def test_acceptance_example_arabic_not_met():
    breakdown = acceptance(NOT_MET)
    assert breakdown.categories[0].score == 50 and format_number(breakdown.final_raw) == "73.5" and breakdown.final_score == 74


@pytest.mark.parametrize("arabic,score,decision", [(MET, 84, "partial"), (PARTIALLY_MET, 79, "partial"), (NOT_MET, 74, "partial")])
def test_acceptance_example_decisions_with_the_default_thresholds(arabic, score, decision):
    from services.ai_service import determine_decision
    assert acceptance(arabic).final_score == score and determine_decision(score, 85, 65) == decision


def test_acceptance_example_with_a_lower_qualified_threshold():
    from services.ai_service import determine_decision
    assert determine_decision(acceptance(MET).final_score, 80, 60) == "qualified"


# ── unclear items: review required, never zero / half / full ────────────────────────────────────────────────────────

def test_unclear_required_items_make_the_status_review_required_with_a_range():
    breakdown = compute({"skills": 100}, items(("a", "skills", 50, UNCLEAR), ("b", "skills", 50, MET)))
    assert breakdown.status == C.REVIEW_REQUIRED
    assert (breakdown.final_min, breakdown.final_max) == (50, 100) and breakdown.final_score is None


def test_a_category_with_an_unclear_item_has_no_score_or_contribution_only_a_range():
    breakdown = compute({"skills": 53, "experience": 47}, items(
        ("a", "skills", 50, UNCLEAR), ("b", "skills", 50, MET), ("c", "experience", 100, MET)))
    skills, experience = breakdown.categories
    assert skills.score is None and skills.contribution is None and (skills.score_min, skills.score_max) == (50, 100)
    assert experience.score == 100 and experience.contribution == 47
    assert all(item.earned is None for item in skills.items if item.result == UNCLEAR)


def test_every_unclear_item_counts_zero_in_the_minimum_and_its_full_weight_in_the_maximum():
    breakdown = compute({"skills": 60, "experience": 40}, items(
        ("a", "skills", 50, UNCLEAR), ("b", "skills", 50, UNCLEAR), ("c", "experience", 100, UNCLEAR)))
    assert (breakdown.final_min, breakdown.final_max) == (0, 100) and len(breakdown.unclear_item_ids) == 3


def test_the_status_is_scored_only_when_every_required_item_has_a_result():
    assert compute({"skills": 100}, items(("a", "skills", 100, MET))).status == C.SCORED


# ── preferred-only jobs ─────────────────────────────────────────────────────────────────────────────────────────────

def test_a_job_without_required_items_is_not_scored_and_its_score_is_absent_not_zero():
    breakdown = compute({c: 0 for c in C.CATEGORIES}, [])
    assert breakdown.status == C.NOT_SCORED and breakdown.final_raw is None and breakdown.final_score is None
    assert breakdown.final_min is None and breakdown.final_max is None


# ── preferred results never enter a number ──────────────────────────────────────────────────────────────────────────

def test_preferred_summary_counts_yes_no_and_not_clear():
    assert preferred_summary(["yes", "yes", "yes", "no", "not_clear", "not_clear"]) == {"yes": 3, "no": 1, "not_clear": 2}


def test_preferred_results_cannot_be_given_to_the_calculation():
    for result in ("yes", "no", "not_clear"):
        with pytest.raises(ScoringInputError):
            compute({"skills": 100}, items(("a", "skills", 100, result)))
    with pytest.raises(ScoringInputError):
        preferred_summary(["partially_met"])


def test_the_score_is_identical_whatever_the_preferred_answers_are():
    required = items(("a", "skills", 60, MET), ("b", "skills", 40, PARTIALLY_MET))
    results = {tuple(preferred_summary(p).items()): compute({"skills": 100}, required).final_raw
               for p in (["yes", "yes"], ["no", "no"], ["not_clear", "not_clear"], [])}
    assert len(set(results.values())) == 1


# ── invalid inputs are refused ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("weights,rows", [
    ({"skills": 100}, [("a", "skills", 50, MET)]),                                          # item weights total 50
    ({"skills": 50, "experience": 40}, [("a", "skills", 100, MET), ("b", "experience", 100, MET)]),   # categories total 90
    ({"skills": 100}, [("a", "skills", 100, MET), ("a", "skills", 0, MET)]),               # duplicate id / zero weight
    ({"skills": 100}, [("a", "skills", 100, "great")]),
    ({"skills": 100.0}, [("a", "skills", 100, MET)]),
    ({"skills": 100}, [("a", "skills", True, MET)]),
    ({"skills": 100}, [("a", "luck", 100, MET)]),
])
def test_unscorable_inputs_are_refused(weights, rows):
    with pytest.raises(ScoringInputError):
        compute(weights, items(*rows))


# ── the evaluator: what the model may and may not decide ────────────────────────────────────────────────────────────

CV = "Jane Doe. Senior support engineer. Five years of SQL troubleshooting at Acme. Languages: Arabic (native), English (basic)."


def specs():
    return [V.ItemSpec("req_sql", "skills", "required", "SQL troubleshooting", 50), V.ItemSpec("req_en", "skills", "required", "English fluency", 50),
            V.ItemSpec("req_k8", "skills", "preferred", "Kubernetes", None)]


def raw(**entries):
    base = {"req_sql": {"item_id": "req_sql", "result": "met", "evidence_quotes": ["Five years of SQL troubleshooting"], "explanation": "stated",
                        "confidence": 0.95},
            "req_en": {"item_id": "req_en", "result": "not_met", "evidence_quotes": ["English (basic)"], "explanation": "basic only", "confidence": 0.9},
            "req_k8": {"item_id": "req_k8", "result": "no", "evidence_quotes": [], "explanation": "not mentioned", "confidence": 0.9}}
    base.update(entries)
    return {"evaluations": list(base.values())}


def test_a_well_formed_output_is_accepted_item_by_item():
    out = V.validate_output(raw(), specs(), CV)
    assert [(e.item_id, e.result) for e in out] == [("req_sql", "met"), ("req_en", "not_met"), ("req_k8", "no")]
    assert out[0].evidence == ("Five years of SQL troubleshooting",) and out[0].confidence == Decimal("0.950")


def test_ai_provided_numeric_totals_are_ignored():
    payload = {**raw(), "final_score": 100, "category_scores": {"skills": 100}, "score_skills": 100, "total": 999,
               "evaluation_notes": "Strong", "strengths": ["x"]}
    out = V.validate_output(payload, specs(), CV)
    narrative, contacts = V.narrative_and_contacts(payload)
    assert [e.result for e in out] == ["met", "not_met", "no"]
    assert set(narrative) == {"evaluation_notes", "strengths", "gaps_identified", "red_flags", "interview_questions"}
    assert "final_score" not in json.dumps(narrative) and set(contacts) == {"name", "email", "phone"}
    breakdown = compute({"skills": 100}, items(("req_sql", "skills", 50, out[0].result), ("req_en", "skills", 50, out[1].result)))
    assert breakdown.final_raw == 50                          # computed from the results, not from the 100 the model offered


def test_a_numeric_result_in_place_of_a_classification_is_not_accepted():
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(raw(req_sql={"item_id": "req_sql", "result": 85, "evidence_quotes": [], "confidence": 1}), specs(), CV)


def test_invented_evidence_is_not_accepted_and_the_result_is_downgraded():
    out = V.validate_output(raw(req_sql={"item_id": "req_sql", "result": "met", "evidence_quotes": ["Led a team of fifty engineers"],
                                         "explanation": "x", "confidence": 0.99}), specs(), CV)
    assert out[0].result == "unclear" and out[0].evidence == () and "no quotation from the CV could be verified" in out[0].explanation
    assert "the model said met" in out[0].explanation


def test_only_verified_quotes_are_kept_and_matching_ignores_case_space_and_arabic_diacritics():
    cv = "SQL   troubleshooting  at ACME.\nيتحدث اللغة العَرَبية بطلاقة"
    quotes = V.verified_quotes(["sql troubleshooting at acme", "invented", "اللغة العربية بطلاقة", "ab", 5], cv)
    assert quotes == ("sql troubleshooting at acme", "اللغة العربية بطلاقة")


def test_the_cv_text_can_be_the_raw_extraction_or_the_cleaned_text():
    assert V.verified_quotes(["only in raw"], "cleaned text", "raw text with only in raw inside") == ("only in raw",)
    assert V.verified_quotes(["only in raw"], "cleaned text", "other") == ()


@pytest.mark.parametrize("result", ["met", "partially_met", "not_met"])
def test_a_decisive_required_result_needs_a_verified_quote(result):
    out = V.validate_output(raw(req_sql={"item_id": "req_sql", "result": result, "evidence_quotes": [], "explanation": "x", "confidence": 0.99}),
                            specs(), CV)
    assert out[0].result == "unclear"


@pytest.mark.parametrize("confidence", [0.59, 0, None, "high", -1, 2, float("nan"), True])
def test_a_decisive_result_with_low_or_unreadable_confidence_is_unclear(confidence):
    out = V.validate_output(raw(req_sql={"item_id": "req_sql", "result": "met", "evidence_quotes": ["SQL troubleshooting"], "explanation": "x",
                                         "confidence": confidence}), specs(), CV)
    assert out[0].result == "unclear"


def test_missing_evidence_is_unclear_never_not_met():
    out = V.validate_output(raw(req_sql={"item_id": "req_sql", "result": "unclear", "evidence_quotes": [], "explanation": "not mentioned"}), specs(), CV)
    assert out[0].result == "unclear"


def test_a_not_met_without_a_verified_quote_is_unclear_not_zero_credit():
    out = V.validate_output(raw(req_en={"item_id": "req_en", "result": "not_met", "evidence_quotes": ["No English at all"], "explanation": "x",
                                        "confidence": 0.99}), specs(), CV)
    assert out[1].result == "unclear"


def test_preferred_yes_needs_a_verified_quote_else_it_is_not_clear():
    ok = V.validate_output(raw(req_k8={"item_id": "req_k8", "result": "yes", "evidence_quotes": ["Senior support engineer"], "explanation": "x",
                                       "confidence": 0.9}), specs(), CV)
    bad = V.validate_output(raw(req_k8={"item_id": "req_k8", "result": "yes", "evidence_quotes": ["Kubernetes admin"], "explanation": "x",
                                        "confidence": 0.9}), specs(), CV)
    assert ok[2].result == "yes" and bad[2].result == "not_clear"


def test_preferred_items_never_use_partially_met_and_required_items_never_use_yes():
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(raw(req_k8={"item_id": "req_k8", "result": "partially_met", "evidence_quotes": [], "confidence": 1}), specs(), CV)
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(raw(req_sql={"item_id": "req_sql", "result": "yes", "evidence_quotes": [], "confidence": 1}), specs(), CV)


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"evaluations": "x"}, {"evaluations": []}])
def test_an_unusable_output_is_a_processing_error(payload):
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(payload, specs(), CV)


def test_an_output_that_skips_or_repeats_an_item_is_a_processing_error():
    short = raw()
    short["evaluations"] = short["evaluations"][:2]
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(short, specs(), CV)
    twice = raw()
    twice["evaluations"].append(twice["evaluations"][0])
    with pytest.raises(V.EvaluationProcessingError):
        V.validate_output(twice, specs(), CV)


# ── processing errors are not evidence ──────────────────────────────────────────────────────────────────────────────

class _Completions:
    def __init__(self, reply=None, error=None, finish="stop"):
        self.reply, self.error, self.finish, self.calls = reply, error, finish, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=self.finish)],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def _client(**kw):
    completions = _Completions(**kw)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _evaluate(client):
    return asyncio.run(V.evaluate(client, "m", specs(), CV, job_title="Engineer", cv_language="en"))


@pytest.mark.parametrize("kw", [{"error": RuntimeError("upstream down")}, {"reply": "not json"}, {"reply": {"evaluations": "x"}},
                                {"reply": raw(), "finish": "length"}])
def test_a_model_or_parsing_failure_is_a_processing_error_not_an_unclear_item(kw):
    client, _ = _client(**kw)
    with pytest.raises(V.EvaluationProcessingError):
        _evaluate(client)


def test_a_successful_call_is_deterministic_and_sends_no_item_weights():
    client, completions = _client(reply=raw())
    out = _evaluate(client)
    call = completions.calls[0]
    sent = call["messages"][1]["content"]
    assert call["temperature"] == 0.0 and "weight" not in sent.lower() and '"req_sql"' in sent
    assert [e.result for e in out.items] == ["met", "not_met", "no"]


def test_the_prompt_forbids_scores_and_defines_the_results():
    prompt = V.ITEM_EVALUATION_SYSTEM_PROMPT
    for word in ("partially_met", "unclear", "not_clear", "NEVER produce scores", "copied EXACTLY", "SECURITY RULES"):
        assert word in prompt


# ── a mandatory gate follows the stored item evaluation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("item_result,gate_result,status", [
    ("met", G.PASSED, G.ELIGIBLE), ("partially_met", G.FAILED, G.NOT_ELIGIBLE), ("not_met", G.FAILED, G.NOT_ELIGIBLE),
    ("unclear", G.NEEDS_VERIFICATION, G.REVIEW_REQUIRED)])
def test_the_gate_outcome_for_each_item_result(item_result, gate_result, status):
    assert G._ITEM_TO_GATE[item_result] == gate_result
    assert G.decide([G.GateState(gate_result)]) == status


def test_every_required_result_maps_to_a_gate_result():
    assert set(G._ITEM_TO_GATE) == set(C.REQUIRED_RESULTS)


# ── the effective decision ──────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("eligibility,decision,scoring,expected", [
    ("not_eligible", "qualified", "scored", "not_eligible"), ("not_eligible", None, "review_required", "not_eligible"),
    ("review_required", "qualified", "scored", "review_required"), ("eligible", None, "review_required", "review_required"),
    ("not_applicable", None, "not_scored", "not_scored"), ("eligible", "qualified", "scored", "qualified"),
    ("not_applicable", "partial", None, "partial"), ("not_applicable", "rejected", None, "rejected"), (None, None, None, None)])
def test_the_effective_decision_rule(eligibility, decision, scoring, expected):
    assert G.effective_decision(eligibility, decision, scoring) == expected


def test_the_sql_rule_has_the_same_branches_as_the_python_rule():
    sql = effective_decision_sql("x")
    assert sql.index("not_eligible") < sql.index("review_required") < sql.index("not_scored")
    assert "x.eligibility_status" in sql and "x.scoring_status" in sql and "ELSE x.decision END" in sql
