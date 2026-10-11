"""Offline tests for the read-only experience-criteria population diagnostic."""
import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "inspect_experience_criteria_population", BACKEND / "scripts" / "inspect_experience_criteria_population.py")
pop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pop)


@pytest.mark.parametrize("text, value, unit", [
    ("Minimum 5 years of experience as Project Manager", 5.0, "years"),
    ("5+ years in B2B sales", 5.0, "years"),
    ("3-5 years experience in logistics", 3.0, "years"),
    ("At least three years of hospital experience", 3.0, "years"),
    ("18 months of call-centre experience", 18.0, "months"),
    ("خبرة ٥ سنوات في القطاع المصرفي", 5.0, "years"),
    ("خبرة لا تقل عن سنتين", 2.0, "years"),
])
def test_threshold_detection(text, value, unit):
    th = pop.parse_threshold(text)
    assert th is not None and (th["value"], th["unit"]) == (value, unit)


@pytest.mark.parametrize("text", [
    "Experience managing construction teams",
    "Experience as a Site Engineer",
    "Valid driving licence",
    "",
    None,
])
def test_no_threshold_detection(text):
    assert pop.parse_threshold(text) is None


@pytest.mark.parametrize("text, policy", [
    ("Minimum 5 years of relevant experience", "pure_duration"),
    ("3+ years of professional experience", "pure_duration"),
    ("Experience in the banking sector", "sector"),
    ("2 years of experience in the healthcare industry", "sector"),
    ("Experience as a Quantity Surveyor", "explicit_role"),
    ("Experience managing subcontractors", "functional"),
    ("Experience with Primavera P6 scheduling", "functional"),
    ("Strong communication skills", "UNKNOWN"),
    ("Background in projects", "UNKNOWN"),
])
def test_conservative_text_policy_inference(text, policy):
    got, conf, reason = pop.infer_text_policy(text)
    assert got == policy, (text, got, reason)
    if policy == "UNKNOWN":
        assert conf == "none"


def test_pipeline_rows_mirror_flatten_criteria():
    from services.llm_criteria_mapper import _flatten_criteria
    for a in [{"experience": {"minimum_years": 5, "relevant_roles": ["Construction Project Manager",
                                                                    "Assistant Project Manager"]}},
              {"experience": {"minimum_years": 3, "relevant_roles": [], "requirement_type": "preferred"}},
              {"experience": {"minimum_years": 0, "relevant_roles": ["Nurse", "Midwife"]}},
              {"experience": {}}, {}]:
        mine = [(r["criterion_text"], r["required"]) for r in pop.pipeline_experience_rows(a)]
        real = [(c["text"], c["required"]) for c in _flatten_criteria(a) if c["dimension"] == "experience"]
        assert mine == real, (a, mine, real)


def test_pipeline_policy_inference():
    (yr,) = pop.pipeline_experience_rows({"experience": {"minimum_years": 5, "relevant_roles": ["CPM"]}})
    assert (yr["inferred_policy"], yr["confidence"], yr["has_threshold"]) == ("explicit_role", "high", True)
    (yo,) = pop.pipeline_experience_rows({"experience": {"minimum_years": 4, "relevant_roles": []}})
    assert (yo["inferred_policy"], yo["confidence"]) == ("pure_duration", "low")
    ro = pop.pipeline_experience_rows({"experience": {"minimum_years": 0, "relevant_roles": ["Nurse", "Midwife"]}})
    assert [(r["inferred_policy"], r["has_threshold"], r["required"]) for r in ro] == [
        ("explicit_role", False, False)] * 2


def _jobs():
    return [
        {"job_id": "1", "job_code": "JOB-1", "title": "Construction_Project_Manager",
         "analysis_json": {"experience": {"minimum_years": 5,
                                          "relevant_roles": ["Construction Project Manager"]},
                           "domain_knowledge": ["commercial construction"],
                           "other_requirements": ["Valid driving licence", "Experience in the oil and gas sector"]}},
        {"job_id": "2", "job_code": "JOB-2", "title": "Nurse",
         "analysis_json": {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]},
                           "other_requirements": ["Background in projects", "2 years of relevant experience"]}},
        {"job_id": "3", "job_code": "JOB-3", "title": "No analysis", "analysis_json": None},
    ]


def test_scoring_impact_identification_and_unknown_handling():
    rows, meta = pop.build_rows(_jobs())
    by = {(r["job_code"], r["criterion_text"]): r for r in rows}
    # threshold explicit_role -> RELATED is audit-only
    assert by[("JOB-1", "Minimum 5 years of experience in a relevant role (Construction Project Manager)")][
        "related_impact"] == "audit_only"
    # no-years explicit_role -> RELATED changes the status (N3 PARTIAL vs N4 ABSENT)
    assert by[("JOB-2", "Registered Nurse")]["related_impact"] == "scoring"
    # no-years sector requirement from other_requirements -> scoring
    sector = by[("JOB-1", "Experience in the oil and gas sector")]
    assert (sector["inferred_policy"], sector["related_impact"]) == ("sector", "scoring")
    # pure duration -> not applicable
    assert by[("JOB-2", "2 years of relevant experience")]["related_impact"] == "not_applicable"
    # non-experience text is excluded; nothing invented
    assert ("JOB-1", "Valid driving licence") not in by
    assert ("JOB-2", "Background in projects") not in by          # no experience word, no threshold
    assert meta["jobs_without_analysis"] == 1 and meta["jobs_with_experience_criteria"] == 2
    agg = pop.aggregate(rows, meta)
    assert agg["H_no_threshold_explicit_role"] == 1 and agg["related_impact"]["scoring"] == 2
    assert agg["K_unknown"] == 0
    # setting evidence comes only from stored domain_knowledge
    assert by[("JOB-1", "Minimum 5 years of experience in a relevant role (Construction Project Manager)")][
        "setting_evidence"] == ["commercial construction"]


def test_unknown_policy_is_reported_not_guessed():
    rows, meta = pop.build_rows([{"job_id": "9", "job_code": "JOB-9", "title": "X",
                                  "analysis_json": {"other_requirements": ["Proven experience required"]}}])
    (r,) = rows
    assert (r["inferred_policy"], r["related_impact"], r["confidence"]) == ("UNKNOWN", "unknown", "none")
    assert r["criterion_text"] == "Proven experience required"
    out = pop.render(rows, meta)
    assert "UNKNOWN policy (1)" in out and "RELATED scoring-impact population (0 criteria)" in out


def test_render_sections_present():
    rows, meta = pop.build_rows(_jobs())
    out = pop.render(rows, meta)
    for frag in ("RELATED scoring-impact population (2 criteria)", "Threshold criteria where RELATED is "
                 "audit/explainability only (1)", "Pure-duration criteria (1)", "DIAGNOSTIC INFERENCE"):
        assert frag in out, frag


def test_sql_is_select_only():
    import re
    sql = pop.JOBS_SQL.upper()
    assert sql.strip().startswith("SELECT")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT)\b", sql)
    assert "CANDIDATE" not in sql and "EXTRACTED_TEXT" not in sql
