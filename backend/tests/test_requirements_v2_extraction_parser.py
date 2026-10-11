"""Requirements-v2 extraction (offline): the parser / post-processor, driven only by synthetic fixture responses.
No model call, no database. Fixtures are hand-written and labelled SYNTHETIC."""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from services.requirements_guard import is_requirements_v2
from services.requirements_v2 import (
    CATEGORIES, collect_item_ids, compute_readiness, confirm_no_numeric_score, edited_category_names,
    original_digest, set_text, validate_final,
)
from services.requirements_v2.extraction import parse_response, process_ai_output

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "requirements_v2_extraction"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def run_fixture(name: str):
    fx = fixture(name)
    return fx, parse_response(json.dumps(fx["ai_response"], ensure_ascii=False), fx["jd"])


def codes(result) -> list[str]:
    return [i.code for i in result.review]


def items(result, category):
    return result.requirements["categories"][category]["items"]


def ai(categories=None, weights="__default__", **extra):
    """A minimal AI response; every category present."""
    cats = {c: [] for c in CATEGORIES}
    cats.update(categories or {})
    out = {"scoreability": {"status": "scoreable", "reason": ""}, "categories": cats,
           "non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": [], "warnings": []}
    if weights == "__default__":
        weights = {c: 10 for c in CATEGORIES}
    if weights is not None:
        out["category_weights"] = weights
    out.update(extra)
    return out


def it(text, source=None, importance="required", cue=None, origin="stated", alternatives=None, experience=None, **kw):
    return {"text": text, "importance": importance, "importance_cue": cue,
            "source_text": source if source is not None else text, "origin": origin,
            "alternatives": alternatives, "experience": experience, **kw}


def parse(categories=None, jd="", weights="__default__", **extra):
    return process_ai_output(ai(categories, weights, **extra), jd)


# ══ English fixture ═══════════════════════════════════════════════════════════

class TestEnglishFixture:

    @pytest.fixture
    def run(self):
        return run_fixture("en_recruitment_specialist")

    def test_produces_a_valid_ready_draft(self, run):
        fx, result = run
        assert result.ok and result.errors == ()
        assert validate_final(result.requirements).ok
        assert result.readiness.state == "ready" and result.readiness.scoring_mode == "weighted"

    def test_review_contains_only_the_expected_notes(self, run):
        _, result = run
        assert codes(result) == ["proposal_for_ineligible_category"]            # domain proposed 5 but has no required item

    def test_every_category_item_count_and_importance(self, run):
        _, result = run
        counts = {c: len(items(result, c)) for c in CATEGORIES}
        assert counts == {"skills": 5, "experience": 4, "education": 1, "certifications": 1, "soft_skills": 2,
                          "domain_knowledge": 1, "other_requirements": 0}
        assert [i["importance"] for i in items(result, "skills")] == ["required"] * 4 + ["preferred"]
        assert items(result, "domain_knowledge")[0]["importance"] == "preferred"

    def test_required_items_get_equal_integer_weights_and_preferred_none(self, run):
        _, result = run
        assert [i["weight"] for i in items(result, "skills")] == [25, 25, 25, 25, None]
        assert [i["weight"] for i in items(result, "experience")] == [25, 25, 25, 25]
        assert [i["weight"] for i in items(result, "soft_skills")] == [50, 50]
        assert items(result, "education")[0]["weight"] == 100 and items(result, "certifications")[0]["weight"] == 100
        assert items(result, "domain_knowledge")[0]["weight"] is None

    def test_category_weights_are_normalized_proposals(self, run):
        _, result = run
        weights = {c: result.requirements["categories"][c]["weight"] for c in CATEGORIES}
        assert sum(weights.values()) == 100 and weights["domain_knowledge"] == 0 and weights["other_requirements"] == 0
        assert weights["experience"] > weights["skills"] > weights["education"] >= 1
        assert result.category_weights["status"] == "ok" and result.category_weights["applied"] == weights

    def test_or_alternatives_stay_in_one_item(self, run):
        _, result = run
        edu = items(result, "education")
        assert len(edu) == 1 and edu[0]["alternatives"] == ["Human Resources", "Business Administration"]
        assert "Human Resources or Business Administration" in edu[0]["text"]

    def test_independent_requirements_are_separate_items(self, run):
        _, result = run
        texts = [i["text"] for i in items(result, "skills")]
        assert {"Excel", "ATS tools", "Fluent English", "Fluent Arabic"} <= set(texts)
        assert [i["text"] for i in items(result, "soft_skills")] == ["Strong communication", "Attention to detail"]

    def test_experience_subject_and_duration_are_preserved(self, run):
        _, result = run
        first = items(result, "experience")[0]
        assert first["experience"] == {"subject": "recruitment", "min_years": 5} and first["origin"] == "stated"

    def test_responsibilities_are_kept_and_marked(self, run):
        _, result = run
        resp = [i for i in items(result, "experience") if i["origin"] == "from_responsibilities"]
        assert len(resp) == 3 and all(i["importance"] == "required" and i["experience"] is None for i in resp)
        assert resp[0]["text"] == "Manage end-to-end hiring for engineering roles"

    def test_certification_stays_a_plain_required_item_no_mandatory_flag(self, run):
        _, result = run
        cert = items(result, "certifications")[0]
        assert cert["importance"] == "required" and "mandatory" not in cert

    def test_source_wording_is_the_exact_job_description_text(self, run):
        fx, result = run
        for c in CATEGORIES:
            for item in items(result, c):
                assert item["source_text"] is not None and item["source_text"] in fx["jd"], item

    def test_non_scoreable_lists_are_kept_without_any_reclassification(self, run):
        fx, result = run
        assert [c["text"] for c in result.conditions["non_scoreable_requirements"]] == ["Valid work permit"]
        assert [c["text"] for c in result.conditions["post_hiring_conditions"]] == ["Background check"]
        assert result.conditions["informational_items"][0]["category"] == "benefits"
        for lst in result.conditions.values():
            for c in lst:
                assert c["is_scoreable"] is False and c["source_text"] in fx["jd"]

    def test_ids_are_unique_code_generated(self, run):
        _, result = run
        ids = collect_item_ids(result.requirements)
        assert len(ids) == 14 and all(i.startswith("req_") for i in ids)

    def test_original_is_preserved_unchanged_for_comparison(self, run):
        _, result = run
        assert result.original == result.requirements and result.original is not result.requirements
        assert result.original_digest == original_digest(result.original)
        assert edited_category_names(result.original, result.requirements) == []
        edited = set_text(result.requirements, items(result, "skills")[0]["id"], "Advanced Excel")
        assert edited_category_names(result.original, edited) == ["skills"]
        assert result.original_digest == original_digest(result.original)         # the original did not move

    def test_original_survives_mutation_of_the_working_copy(self):
        _, result = run_fixture("en_recruitment_specialist")
        digest = result.original_digest
        result.requirements["categories"]["skills"]["items"][0]["text"] = "mutated"
        result.requirements["categories"]["skills"]["weight"] = 1
        assert original_digest(result.original) == digest

    def test_raw_ai_output_is_kept_as_received(self, run):
        fx, result = run
        assert result.raw_ai_output == fx["ai_response"] and result.raw_ai_output is not fx["ai_response"]

    def test_analysis_envelope(self, run):
        _, result = run
        analysis = result.analysis
        assert is_requirements_v2(None, analysis) and analysis["requirements"] == result.requirements
        assert analysis["extraction"]["prompt_code"] == "criteria_extraction_v2"
        assert analysis["extraction"]["prompt_version"] == "criteria_extraction_v2-1"
        assert [r["code"] for r in analysis["extraction"]["review"]] == codes(result)
        assert analysis["non_scoreable_requirements"] and analysis["scoreability"]["status"] == "scoreable"
        analysis["requirements"]["categories"]["skills"]["weight"] = 0                 # a copy: no aliasing
        assert result.requirements["categories"]["skills"]["weight"] != 0

    def test_prompt_identity_is_recorded(self, run):
        _, result = run
        assert result.prompt_code == "criteria_extraction_v2" and len(result.prompt_sha256) == 64


# ══ Arabic fixture ════════════════════════════════════════════════════════════

class TestArabicFixture:

    @pytest.fixture
    def run(self):
        return run_fixture("ar_senior_accountant")

    def test_valid_ready_draft_without_review_notes_except_ineligible_weights(self, run):
        _, result = run
        assert result.ok and validate_final(result.requirements).ok and result.readiness.state == "ready"
        assert codes(result) == ["proposal_for_ineligible_category"]            # certifications: preferred only

    def test_quotes_with_tatweel_extra_spaces_and_missing_diacritics_resolve_to_the_exact_jd_text(self, run):
        fx, result = run
        for c in CATEGORIES:
            for item in items(result, c):
                assert item["source_text"] in fx["jd"], item
        resp = [i for i in items(result, "experience") if i["origin"] == "from_responsibilities"]
        assert resp[0]["source_text"] == "إعداد القوائم المالية الشهرية والسنوية"          # the JD has no tatweel
        assert items(result, "skills")[1]["source_text"] == "إجادة برنامج Excel ونظام SAP"

    def test_arabic_indic_digits_experience_and_subject(self, run):
        _, result = run
        exp = items(result, "experience")[0]
        assert exp["experience"] == {"subject": "المحاسبة", "min_years": 3}
        assert exp["text"] == "خبرة لا تقل عن ٣ سنوات في المحاسبة"

    def test_or_alternatives_in_arabic(self, run):
        _, result = run
        edu = items(result, "education")[0]
        assert edu["alternatives"] == ["المحاسبة", "الإدارة المالية"] and len(items(result, "education")) == 1

    def test_preferred_cue_with_diacritics_is_verified_against_the_jd(self, run):
        fx, result = run
        cert = items(result, "certifications")[0]
        assert cert["importance"] == "preferred" and cert["weight"] is None
        assert result.requirements["categories"]["certifications"]["weight"] == 0
        # the same cue written without diacritics also verifies
        resp = copy.deepcopy(fx["ai_response"])
        resp["categories"]["certifications"][0]["importance_cue"] = "ويفضل"
        assert items(process_ai_output(resp, fx["jd"]), "certifications")[0]["importance"] == "preferred"

    def test_responsibilities_conditions_and_weights(self, run):
        _, result = run
        assert [i["origin"] for i in items(result, "experience")] == ["stated", "from_responsibilities", "from_responsibilities"]
        assert [i["weight"] for i in items(result, "experience")] == [34, 33, 33]
        assert result.conditions["non_scoreable_requirements"][0]["category"] == "location"
        assert sum(result.requirements["categories"][c]["weight"] for c in CATEGORIES) == 100


# ══ preferred-only / empty ════════════════════════════════════════════════════

class TestPreferredOnlyAndEmpty:

    def test_preferred_only_has_zero_weights_and_needs_confirmation(self):
        fx, result = run_fixture("en_preferred_only")
        assert result.ok
        assert all(result.requirements["categories"][c]["weight"] == 0 for c in CATEGORIES)
        assert all(i["weight"] is None and i["importance"] == "preferred" for c in CATEGORIES for i in items(result, c))
        assert result.readiness.state == "needs_confirmation"
        assert validate_final(result.requirements).ok
        assert "category_weights_ignored_no_required_items" in codes(result)        # the AI proposed 40/30/30

    def test_confirmation_is_not_part_of_the_extraction_and_works_afterwards(self):
        _, result = run_fixture("en_preferred_only")
        assert result.requirements["scoring_confirmation"] is None and result.original["scoring_confirmation"] is None
        confirmed = confirm_no_numeric_score(result.requirements, user_id="u1", confirmed_at="t")
        assert compute_readiness(confirmed).scoring_mode == "none"

    def test_empty_analysis_stays_available_for_review(self):
        fx, result = run_fixture("en_open_role_empty")
        assert result.ok and result.readiness.state == "needs_items"
        assert all(not items(result, c) for c in CATEGORIES)
        assert result.scoreability == {"status": "open_broad", "reason": fx["ai_response"]["scoreability"]["reason"]}
        assert validate_final(result.requirements).ok
        assert result.category_weights["status"] == "no_eligible_categories"
        assert result.analysis is not None


# ══ importance ════════════════════════════════════════════════════════════════

JD_IMP = "Requirements: Python. Nice to have: Docker. SQL is a plus."


class TestImportance:

    def test_preferred_with_a_cue_found_in_the_jd_stays_preferred(self):
        r = parse({"skills": [it("Docker", "Nice to have: Docker", "preferred", "Nice to have"),
                              it("SQL", "SQL is a plus", "preferred", "is a plus")]}, JD_IMP)
        assert [i["importance"] for i in items(r, "skills")] == ["preferred", "preferred"]
        assert "preferred_not_supported_by_job_description" not in codes(r)

    @pytest.mark.parametrize("cue,code", [(None, "preferred_cue_missing"), ("", "preferred_cue_missing"),
                                          ("  ", "preferred_cue_missing"), (5, "preferred_cue_missing"),
                                          (["a plus"], "preferred_cue_missing"),
                                          ("optional", "preferred_cue_not_in_job_description")])
    def test_preferred_without_a_usable_cue_is_preserved_and_flagged_for_review(self, cue, code):
        """The AI's Preferred classification is never changed because its cue is missing or unverifiable."""
        r = parse({"skills": [it("Docker", "Nice to have: Docker", "preferred", cue)]}, JD_IMP, weights=None)
        item = items(r, "skills")[0]
        assert item["importance"] == "preferred" and item["weight"] is None
        assert codes(r) == [code] and r.item_review[item["id"]][0].message.startswith("Needs review: ")
        assert r.raw_ai_output["categories"]["skills"][0]["importance_cue"] == cue          # the AI answer is kept

    @pytest.mark.parametrize("value", [None, "must", "mandatory", "", 3, "Required "])
    def test_missing_or_unknown_importance_defaults_to_required(self, value):
        raw = it("Python", "Python")
        if value is None:
            del raw["importance"]
        else:
            raw["importance"] = value
        r = parse({"skills": [raw]}, JD_IMP)
        assert items(r, "skills")[0]["importance"] == "required"
        if value != "Required ":                                    # surrounding space / case is tolerated silently
            assert any(c.startswith("importance_") for c in codes(r))
        else:
            assert not any(c.startswith("importance_") for c in codes(r))

    def test_a_cue_on_a_required_item_is_ignored(self):
        r = parse({"skills": [it("Python", "Python", "required", "Nice to have")]}, JD_IMP, weights={"skills": 100})
        assert items(r, "skills")[0]["importance"] == "required" and codes(r) == []

    def test_the_whole_flat_list_defaults_to_required(self):
        jd = "Requirements:\n- Python\n- SQL\n- Docker"
        r = parse({"skills": [it("Python"), it("SQL"), it("Docker")]}, jd)
        assert [i["importance"] for i in items(r, "skills")] == ["required"] * 3
        assert [i["weight"] for i in items(r, "skills")] == [34, 33, 33]


# ══ evidence ══════════════════════════════════════════════════════════════════

class TestSourceEvidence:

    def test_missing_source_text_keeps_the_item_and_reports_it(self):
        raw = it("Python", "x")
        del raw["source_text"]
        r = parse({"skills": [raw]}, "Python")
        assert len(items(r, "skills")) == 1 and items(r, "skills")[0]["source_text"] is None
        assert "source_text_missing" in codes(r)

    @pytest.mark.parametrize("quote", ["Rust and Go", "Pyth0n", "python3 ", 12])
    def test_a_quote_that_is_not_in_the_jd_is_not_stored_but_the_item_is_kept(self, quote):
        r = parse({"skills": [it("Python", quote)]}, "We need Python")
        item = items(r, "skills")[0]
        assert item["source_text"] is None and item["text"] == "Python" and item["weight"] == 100
        message = [i.message for i in r.review if i.code == "source_text_not_found"][0]
        assert str(quote).strip() in message and "skills[0]" in message

    def test_a_translated_quote_is_not_accepted(self):
        r = parse({"skills": [it("Excel", "إجادة برنامج Excel")]}, "Proficiency in Excel")
        assert items(r, "skills")[0]["source_text"] is None and "source_text_not_found" in codes(r)

    def test_stored_source_is_the_jd_slice_not_the_model_copy(self):
        r = parse({"skills": [it("Python", "we  NEED python")]}, "Intro. We need Python, daily.")
        assert items(r, "skills")[0]["source_text"] == "We need Python"

    def test_unverifiable_condition_quotes_are_reported_but_kept(self):
        r = parse(None, "Work permit needed", non_scoreable_requirements=[
            {"text": "Work permit", "category": "work_authorization", "reason": "r", "source_text": "Visa required"}])
        assert r.conditions["non_scoreable_requirements"][0]["source_text"] is None
        assert "source_text_not_found" in codes(r)


# ══ experience ════════════════════════════════════════════════════════════════

JD_EXP = "Five years of recruitment experience and 6 months of sourcing."


class TestExperience:

    def test_subject_and_years_are_kept_with_exact_values(self):
        r = parse({"experience": [it("Five years of recruitment experience", "Five years of recruitment experience",
                                     experience={"subject": " recruitment ", "min_years": 5})]}, JD_EXP,
                  weights={"experience": 100})
        assert items(r, "experience")[0]["experience"] == {"subject": "recruitment", "min_years": 5}
        assert codes(r) == []

    @pytest.mark.parametrize("years,expected_code", [(5.0, None), ("5", "experience_years_converted"),
                                                      (-1, "experience_years_invalid"), (True, "experience_years_invalid"),
                                                      (2.5, "experience_years_invalid"), ("five", "experience_years_invalid")])
    def test_year_values_are_coerced_only_when_unambiguous(self, years, expected_code):
        r = parse({"experience": [it("Five years of recruitment experience", "Five years of recruitment experience",
                                     experience={"subject": "recruitment", "min_years": years})]}, JD_EXP)
        stored = items(r, "experience")[0]["experience"]
        assert stored["subject"] == "recruitment"
        assert stored["min_years"] == (5 if years in (5.0, "5") else None)
        if expected_code:
            assert expected_code in codes(r)

    def test_non_year_duration_keeps_the_wording_and_a_null_duration(self):
        r = parse({"experience": [it("6 months of sourcing", "6 months of sourcing",
                                     experience={"subject": "sourcing", "min_years": None})]}, JD_EXP)
        item = items(r, "experience")[0]
        assert item["text"] == "6 months of sourcing" and item["experience"] == {"subject": "sourcing", "min_years": None}

    def test_duration_without_a_subject_is_reported(self):
        r = parse({"experience": [it("Five years of recruitment experience", "Five years of recruitment experience",
                                     experience={"subject": None, "min_years": 5})]}, JD_EXP)
        assert items(r, "experience")[0]["experience"] == {"subject": None, "min_years": 5}
        assert "experience_subject_missing" in codes(r)

    def test_a_duration_that_contradicts_the_quoted_wording_is_reported_not_changed(self):
        r = parse({"experience": [it("Five years of recruitment experience", "Five years of recruitment experience",
                                     experience={"subject": "recruitment", "min_years": 7})]}, JD_EXP)
        assert items(r, "experience")[0]["experience"]["min_years"] == 7
        assert "experience_years_not_in_source_text" in codes(r)

    def test_empty_structure_is_not_kept(self):
        r = parse({"experience": [it("x", "Five years of recruitment experience", experience={"subject": None, "min_years": None})]},
                  JD_EXP)
        assert items(r, "experience")[0]["experience"] is None and "experience_structure_empty" in codes(r)

    @pytest.mark.parametrize("bad", ["five years", [5], {"subject": 3, "min_years": None}])
    def test_malformed_structure_does_not_break_the_item(self, bad):
        r = parse({"experience": [it("Five years of recruitment experience", "Five years of recruitment experience", experience=bad)]},
                  JD_EXP)
        assert len(items(r, "experience")) == 1 and r.ok
        assert items(r, "experience")[0]["experience"] is None
        assert any(c.startswith("experience_") for c in codes(r))

    def test_experience_structure_outside_the_experience_category_is_not_kept(self):
        r = parse({"skills": [it("Recruiting", "Five years of recruitment experience",
                                 experience={"subject": "recruitment", "min_years": 5})]}, JD_EXP)
        assert items(r, "skills")[0]["experience"] is None
        assert "experience_structure_outside_experience_category" in codes(r) and r.ok

    def test_responsibility_outside_experience_is_kept_and_reported(self):
        r = parse({"skills": [it("Manage hiring", "Manage hiring", origin="from_responsibilities")]}, "Manage hiring")
        assert items(r, "skills")[0]["origin"] == "from_responsibilities"
        assert "responsibility_outside_experience_category" in codes(r)

    def test_invalid_origin_defaults_to_stated_with_a_warning(self):
        r = parse({"skills": [it("Python", "Python", origin="duties")]}, "Python")
        assert items(r, "skills")[0]["origin"] == "stated" and "origin_defaulted_stated" in codes(r)


# ══ alternatives and duplicates ═══════════════════════════════════════════════

class TestAlternativesAndDuplicates:

    def test_valid_alternatives_are_kept_trimmed(self):
        r = parse({"certifications": [it("CIPD or SHRM", "CIPD or SHRM", alternatives=[" CIPD ", "SHRM"])]}, "CIPD or SHRM")
        assert items(r, "certifications")[0]["alternatives"] == ["CIPD", "SHRM"] and len(items(r, "certifications")) == 1

    @pytest.mark.parametrize("alts", [["CIPD"], [], "CIPD or SHRM", [1, 2], ["CIPD", ""], {"a": 1}])
    def test_invalid_alternatives_are_dropped_with_a_warning_and_the_wording_stays(self, alts):
        r = parse({"certifications": [it("CIPD or SHRM", "CIPD or SHRM", alternatives=alts)]}, "CIPD or SHRM")
        item = items(r, "certifications")[0]
        assert item["alternatives"] is None and item["text"] == "CIPD or SHRM"
        assert "alternatives_invalid_dropped" in codes(r)

    def test_duplicate_and_similar_items_are_kept_without_comment(self):
        cats = {"skills": [it("Excel", "Excel"), it("Excel", "Excel"), it("MS Excel", "Excel"), it("excel", "Excel")],
                "domain_knowledge": [it("Excel", "Excel")]}
        r = parse(cats, "Excel", weights={"skills": 60, "domain_knowledge": 40})
        assert [i["text"] for i in items(r, "skills")] == ["Excel", "Excel", "MS Excel", "excel"]
        assert [i["weight"] for i in items(r, "skills")] == [25, 25, 25, 25]
        assert len(items(r, "domain_knowledge")) == 1
        assert len(collect_item_ids(r.requirements)) == 5
        assert codes(r) == [] and validate_final(r.requirements).ok


# ══ malformed output ══════════════════════════════════════════════════════════

class TestMalformedOutput:

    @pytest.mark.parametrize("raw,code", [
        (None, "no_response"), ("", "no_response"), ("   \n", "no_response"), (b"{}", "no_response"),
        ("not json", "invalid_json"), ('{"categories": {', "invalid_json"),
        ("```json\n{}\n```", "invalid_json"),                                    # no repair of fenced output
        ("[]", "not_an_object"), ('"text"', "not_an_object"), ("3", "not_an_object"), ("null", "not_an_object"),
        ("{}", "missing_categories"), ('{"categories": []}', "missing_categories"),
        ('{"categories": "skills"}', "missing_categories"),
    ])
    def test_unusable_output_fails_without_a_draft(self, raw, code):
        r = parse_response(raw, "jd")
        assert not r.ok and r.status == "failed" and [e.code for e in r.errors] == [code]
        assert r.requirements is None and r.original is None and r.analysis is None and r.readiness is None

    def test_truncated_output_is_never_trusted_even_if_it_parses(self):
        good = json.dumps(ai({"skills": [it("Python", "Python")]}))
        r = parse_response(good, "Python", finish_reason="length")
        assert not r.ok and r.errors[0].code == "output_truncated"
        assert parse_response(good, "Python", finish_reason="stop").ok

    def test_structure_failure_still_exposes_the_parsed_output_for_diagnosis(self):
        r = parse_response('{"foo": 1}', "jd")
        assert r.raw_ai_output == {"foo": 1} and r.errors[0].code == "missing_categories"

    @pytest.mark.parametrize("bad_item", [7, None, ["x"], {"importance": "required"}, {"text": ""}, {"text": "  "},
                                          {"text": 5}])
    def test_an_unusable_item_is_reported_and_kept_in_unmapped_never_lost(self, bad_item):
        r = parse({"skills": [it("Python", "Python"), bad_item]}, "Python")
        assert r.ok and len(items(r, "skills")) == 1
        assert len(r.unmapped) == 1 and r.unmapped[0]["raw"] == bad_item and r.unmapped[0]["category"] == "skills"
        assert "item_unusable" in codes(r)

    def test_a_bare_string_item_is_accepted_as_required_text_with_a_warning(self):
        r = parse({"skills": ["Python"]}, "Python")
        item = items(r, "skills")[0]
        assert item["text"] == "Python" and item["importance"] == "required" and item["source_text"] is None
        assert "item_is_plain_string" in codes(r) and "source_text_missing" not in codes(r)

    def test_unexpected_item_fields_are_ignored_and_reported(self):
        r = parse({"skills": [it("Python", "Python", weight=90, mandatory=True)]}, "Python")
        item = items(r, "skills")[0]
        assert item["weight"] == 100 and "mandatory" not in item
        assert "item_fields_ignored" in codes(r) and validate_final(r.requirements).ok

    def test_unknown_and_malformed_categories_are_kept_in_unmapped(self):
        raw = ai({"skills": "Python", "languages": [it("Arabic")]})
        del raw["categories"]["soft_skills"]
        r = process_ai_output(raw, "Python Arabic")
        assert r.ok
        reasons = {(u["category"], u["reason"]) for u in r.unmapped}
        assert reasons == {("skills", "category_not_a_list"), ("languages", "unknown_category")}
        assert {"category_not_a_list", "unknown_category", "category_missing_in_output"} <= set(codes(r))

    def test_no_item_is_ever_silently_lost(self):
        raw = ai({"skills": [it("A"), 5, "B", {"text": ""}], "experience": [it("C"), None],
                  "oops": [it("D"), it("E")]})
        r = process_ai_output(raw, "A B C D E")
        sent = 4 + 2 + 2
        kept = sum(len(items(r, c)) for c in CATEGORIES)
        unmapped_items = sum(len(u["raw"]) if u["reason"] == "unknown_category" else 1 for u in r.unmapped)
        assert kept + unmapped_items == sent

    def test_non_dict_condition_entries_and_lists_are_reported(self):
        r = parse(None, "jd", non_scoreable_requirements="travel", post_hiring_conditions=[3, {"text": ""}, "Background check"],
                  informational_items=None)
        assert r.conditions["post_hiring_conditions"] == [{"text": "Background check", "category": "other", "reason": "",
                                                           "source_text": None, "is_scoreable": False}]
        assert {u.get("reason") for u in r.unmapped} == {"not_a_list", "condition_unusable"}
        assert {"conditions_not_a_list", "condition_unusable"} <= set(codes(r))

    def test_invalid_or_inconsistent_scoreability_is_reported(self):
        assert "scoreability_invalid" in codes(parse(None, "jd", scoreability={"status": "great"}))
        assert parse(None, "jd", scoreability="yes").scoreability is None
        assert "scoreability_inconsistent" in codes(parse({"skills": [it("Python", "Python")]}, "Python",
                                                          scoreability={"status": "insufficient", "reason": "thin"}))
        assert "scoreability_inconsistent" in codes(parse(None, "jd", scoreability={"status": "scoreable"}))
        assert parse(None, "jd", scoreability=None).scoreability is None

    def test_ai_warnings_are_carried_over(self):
        r = parse(None, "jd", warnings=["conflicting years", 3, "second"])
        assert r.ai_warnings == ("conflicting years", "second")
        assert parse(None, "jd", warnings="oops").ai_warnings == ()


# ══ no legacy post-processing ═════════════════════════════════════════════════

class TestNoLegacyPostProcessing:

    def test_weak_words_and_logistics_phrases_are_never_removed_or_moved(self):
        texts = ["support", "assist", "help", "coordinate", "follow up", "Full-time availability", "must be on-site",
                 "Pass a background check", "Valid visa", "Customer service", "advisable references", "network attached storage",
                 "shift-left testing", "willing to work at the main office"]
        r = parse({"other_requirements": [it(t, t) for t in texts]}, " ".join(texts), weights={"other_requirements": 100})
        assert [i["text"] for i in items(r, "other_requirements")] == texts
        assert r.conditions == {"non_scoreable_requirements": [], "post_hiring_conditions": [], "informational_items": []}
        assert codes(r) == []

    def test_non_academic_education_fields_and_cross_category_repeats_stay(self):
        cats = {"education": [it("Customer Service", "Customer Service", alternatives=["Communication", "Teamwork"])],
                "skills": [it("Communication", "Communication")], "soft_skills": [it("Communication", "Communication")]}
        r = parse(cats, "Customer Service Communication Teamwork", weights={"education": 20, "skills": 40, "soft_skills": 40})
        assert items(r, "education")[0]["alternatives"] == ["Communication", "Teamwork"]
        assert len(items(r, "skills")) == 1 and len(items(r, "soft_skills")) == 1

    def test_weights_are_never_taken_from_the_ai(self):
        r = parse({"skills": [it("A", "A", weight=80), it("B", "B", weight=20)]}, "A B")
        assert [i["weight"] for i in items(r, "skills")] == [50, 50]


# ══ weights ═══════════════════════════════════════════════════════════════════

def many(n, prefix="skill", importance="required"):
    return [it(f"{prefix} {i}", f"{prefix} {i}", importance, "is a plus" if importance == "preferred" else None)
            for i in range(n)]


class TestWeights:

    @pytest.mark.parametrize("n,expected", [(1, [100]), (2, [50, 50]), (3, [34, 33, 33]), (7, [15, 15, 14, 14, 14, 14, 14])])
    def test_required_weights_are_equal_integers_with_remainder_in_list_order(self, n, expected):
        r = parse({"skills": many(n)}, " ".join(f"skill {i}" for i in range(n)), weights={"skills": 100})
        assert [i["weight"] for i in items(r, "skills")] == expected

    def test_preferred_items_between_required_ones_do_not_take_weight_or_a_remainder_slot(self):
        cats = {"skills": [it("A", "A"), it("B", "B", "preferred", "a plus"), it("C", "C"), it("D", "D")]}
        r = parse(cats, "A B a plus C D", weights={"skills": 100})
        assert [i["weight"] for i in items(r, "skills")] == [34, None, 33, 33]

    def test_exactly_100_required_items_get_weight_one_each_and_are_valid(self):
        r = parse({"skills": many(100)}, " ".join(f"skill {i}" for i in range(100)), weights={"skills": 100})
        assert {i["weight"] for i in items(r, "skills")} == {1} and validate_final(r.requirements).ok
        assert r.readiness.state == "ready"

    def test_over_limit_keeps_every_item_leaves_weights_unset_and_stays_reviewable(self):
        r = parse({"skills": many(101), "education": [it("BSc", "BSc")]}, "BSc", weights={"skills": 70, "education": 30})
        assert r.ok and len(items(r, "skills")) == 101
        assert all(i["weight"] is None for i in items(r, "skills"))
        assert items(r, "education")[0]["weight"] == 100                      # other categories unaffected
        assert [i.category for i in r.review if i.code == "too_many_required_items"] == ["skills"]
        assert r.requirements["categories"]["skills"]["weight"] == 70         # the category still gets its weight
        assert r.readiness.state == "needs_review" and not validate_final(r.requirements).ok
        assert r.original == r.requirements and len(collect_item_ids(r.requirements)) == 102

    def test_over_limit_is_resolved_by_reclassifying_without_losing_items(self):
        from services.requirements_v2 import equalize_category, set_importance
        r = parse({"skills": many(101)}, "", weights={"skills": 100})
        doc = r.requirements
        doc = set_importance(doc, items(r, "skills")[0]["id"], "preferred")
        doc = equalize_category(doc, "skills")
        assert validate_final(doc).ok and len(doc["categories"]["skills"]["items"]) == 101

    def test_preferred_only_categories_get_zero_even_if_proposed(self):
        cats = {"skills": [it("A", "A")], "domain_knowledge": many(2, "dom", "preferred")}
        r = parse(cats, "A dom 0 dom 1 is a plus", weights={"skills": 50, "domain_knowledge": 50})
        assert r.requirements["categories"]["domain_knowledge"]["weight"] == 0
        assert r.requirements["categories"]["skills"]["weight"] == 100
        assert "proposal_for_ineligible_category" in codes(r)

    def test_proposed_weights_are_normalized_to_a_total_of_100(self):
        cats = {"skills": [it("A", "A")], "experience": [it("B", "B")], "education": [it("C", "C")]}
        for proposal in ({"skills": 1, "experience": 1, "education": 1}, {"skills": 7, "experience": 3, "education": 2},
                         {"skills": 0.5, "experience": 0.3, "education": 0.2}, {"skills": 500, "experience": 300, "education": 200}):
            r = parse(cats, "A B C", weights=proposal)
            assert sum(r.requirements["categories"][c]["weight"] for c in CATEGORIES) == 100, proposal
            assert validate_final(r.requirements).ok
        r = parse(cats, "A B C", weights={"skills": 1, "experience": 1, "education": 1})
        assert [r.requirements["categories"][c]["weight"] for c in ("skills", "experience", "education")] == [34, 33, 33]

    def test_a_zero_proposal_for_a_category_with_required_items_is_raised_to_one_with_a_warning(self):
        cats = {"skills": [it("A", "A")], "education": [it("C", "C")]}
        r = parse(cats, "A C", weights={"skills": 100, "education": 0})
        assert r.requirements["categories"]["education"]["weight"] == 1 and r.requirements["categories"]["skills"]["weight"] == 99
        assert "category_raised_to_minimum" in codes(r) and validate_final(r.requirements).ok

    @pytest.mark.parametrize("proposal", [None, "heavy", [], {}, {c: 0 for c in CATEGORIES}, {"skills": "50", "education": None},
                                          {"skills": -5, "education": float("nan")}, {"skills": True}, {"experience": 100}])
    def test_unusable_proposal_applies_nothing_and_reports_an_explicit_fallback_suggestion(self, proposal):
        cats = {"skills": [it("A", "A")], "education": [it("C", "C")]}
        r = parse(cats, "A C", weights=proposal)
        assert r.ok
        assert all(r.requirements["categories"][c]["weight"] == 0 for c in CATEGORIES)          # nothing applied
        assert r.category_weights["status"] == "fallback_required" and r.category_weights["applied"] is None
        assert r.category_weights["suggested_fallback"]["skills"] == 50 and r.category_weights["suggested_fallback"]["education"] == 50
        assert "category_weights_unusable" in codes(r)
        assert r.readiness.state == "needs_review" and not validate_final(r.requirements).ok

    def test_missing_proposal_key_is_the_same_as_unusable(self):
        r = parse({"skills": [it("A", "A")]}, "A", weights=None)
        assert r.category_weights["status"] == "fallback_required" and "category_weights_unusable" in codes(r)

    def test_proposal_survives_into_the_result_as_received(self):
        proposal = {"skills": 30, "education": 70, "extra": "x"}
        r = parse({"skills": [it("A", "A")], "education": [it("C", "C")]}, "A C", weights=proposal)
        assert r.category_weights["proposed"] == proposal and r.category_weights["proposed"] is not proposal


# ══ identifiers and determinism ═══════════════════════════════════════════════

class TestIdentifiers:

    def test_id_factory_injection_gives_deterministic_ids(self):
        counter = iter(range(1, 100))
        r = process_ai_output(ai({"skills": many(3)}, {"skills": 100}), "", id_factory=lambda used: f"req_t{next(counter)}")
        assert [i["id"] for i in items(r, "skills")] == ["req_t1", "req_t2", "req_t3"]

    def test_the_factory_is_told_which_ids_are_taken(self):
        seen = []

        def factory(used):
            seen.append(set(used))
            return f"req_x{len(seen)}"
        process_ai_output(ai({"skills": many(3)}, {"skills": 100}), "", id_factory=factory)
        assert seen == [set(), {"req_x1"}, {"req_x1", "req_x2"}]

    def test_same_input_gives_the_same_structure_apart_from_ids(self):
        def strip_ids(doc):
            d = copy.deepcopy(doc)
            for c in CATEGORIES:
                for i in d["categories"][c]["items"]:
                    i["id"] = "-"
            return d
        fx = fixture("en_recruitment_specialist")
        a = parse_response(json.dumps(fx["ai_response"]), fx["jd"])
        b = parse_response(json.dumps(fx["ai_response"]), fx["jd"])
        assert strip_ids(a.requirements) == strip_ids(b.requirements)
        assert collect_item_ids(a.requirements).isdisjoint(collect_item_ids(b.requirements))

    def test_inputs_are_not_mutated(self):
        fx = fixture("en_recruitment_specialist")
        before = copy.deepcopy(fx["ai_response"])
        process_ai_output(fx["ai_response"], fx["jd"])
        assert fx["ai_response"] == before
