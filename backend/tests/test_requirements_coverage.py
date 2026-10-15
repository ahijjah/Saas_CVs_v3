"""The coverage warning (services/requirements_coverage.py): total omission of a duties section, English and Arabic.

Positive cases: a recognised duties heading with duty lines and no from_responsibilities item.
Controls: no duty content, no heading, a duty item present (partial completeness is not judged), a heading inside a
sentence, and a stored JD that no longer matches the extraction. The detector never changes items, readiness or weights.
"""
import json
import pathlib

from services import requirements_coverage as cov

FIX = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20"


def doc(*, duty_items=0, other_items=0):
    items = [{"text": f"duty {i}", "origin": "from_responsibilities"} for i in range(duty_items)]
    items += [{"text": f"skill {i}", "origin": "stated"} for i in range(other_items)]
    return {"categories": {"experience": {"weight": 30, "items": items}, "skills": {"weight": 70, "items": []}}}


EN_JD = """Senior Analyst

Responsibilities:
- Prepare the monthly reports for the finance team.
- Maintain the reconciliation process with the vendors.

Qualifications:
- Bachelor's degree in accounting.
"""

EN_KEY_JD = """Role

Key Responsibilities
Lead the onboarding of new clients and their accounts.
Review the contract terms before each renewal.

Requirements
Three years of experience in account management.
"""

AR_JD = """مدير المشروع

المسؤوليات:
- إعداد التقارير الشهرية لفريق المالية.
- متابعة إجراءات التسوية مع الموردين.

المؤهلات:
- بكالوريوس في المحاسبة.
"""

AR_TASKS_JD = """منسق

المهام
تنسيق جدول العمل اليومي بين الفرق المختلفة.

المتطلبات
خبرة سنتان في العمل الإداري.
"""


# ── positive cases ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_english_responsibilities_with_no_duty_item_warns():
    w = cov.coverage_warnings(EN_JD, doc(other_items=3))
    assert w == [{"code": "duties_may_be_omitted", "heading": "Responsibilities", "candidate_lines": 2}]


def test_english_key_responsibilities_heading_without_colon_warns():
    w = cov.coverage_warnings(EN_KEY_JD, doc(other_items=1))
    assert [x["code"] for x in w] == ["duties_may_be_omitted"] and w[0]["heading"] == "Key Responsibilities"


def test_arabic_responsibilities_heading_warns():
    assert [x["heading"] for x in cov.coverage_warnings(AR_JD, doc(other_items=2))] == ["المسؤوليات"]


def test_arabic_tasks_heading_warns():
    assert [x["heading"] for x in cov.coverage_warnings(AR_TASKS_JD, doc())] == ["المهام"]


def test_the_stored_technical_coordinator_jd_warns_on_the_stored_answer():
    ev = json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))
    raw = json.loads(ev["pipeline"]["raw_response"]["text"])
    requirements = {"categories": {c: {"weight": 0, "items": [dict(i) for i in v]} for c, v in raw["categories"].items()}}
    w = cov.coverage_warnings(ev["description"], requirements)
    assert [x["heading"] for x in w] == ["Responsibilities"]


# ── controls ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_duty_item_present_suppresses_the_warning_even_if_others_are_missing():
    assert cov.coverage_warnings(EN_JD, doc(duty_items=1)) == []


def test_an_empty_duties_heading_does_not_warn():
    jd = "Responsibilities:\n\nQualifications:\n- Bachelor's degree in accounting.\n"
    assert cov.coverage_warnings(jd, doc()) == []


def test_a_duties_heading_with_only_a_placeholder_does_not_warn():
    assert cov.coverage_warnings("Responsibilities:\nN/A\n\nRequirements:\n- Three years.\n", doc()) == []


def test_a_jd_without_a_duties_heading_does_not_warn():
    jd = "Requirements:\n- Bachelor's degree in accounting.\n- Excel proficiency.\n"
    assert cov.coverage_warnings(jd, doc()) == []


def test_the_word_inside_a_sentence_is_not_a_heading():
    jd = "Requirements:\n- A strong sense of responsibilities is valued in every team member.\n"
    assert cov.coverage_warnings(jd, doc()) == []


def test_a_duty_item_in_another_category_still_counts_as_extracted():
    d = {"categories": {"soft_skills": {"weight": 0, "items": [{"text": "x", "origin": "from_responsibilities"}]}}}
    assert cov.coverage_warnings(EN_JD, d) == []


def test_no_jd_or_no_document_gives_no_warning():
    assert cov.coverage_warnings(None, doc()) == []
    assert cov.coverage_warnings(EN_JD, None) == []


def test_the_detector_does_not_change_its_inputs():
    d = doc(other_items=2)
    before = json.dumps(d, sort_keys=True)
    cov.coverage_warnings(EN_JD, d)
    assert json.dumps(d, sort_keys=True) == before


# ── the view: the warning is shown only for the JD the extraction recorded ───────────────────────────────────────────

def test_view_shows_the_warning_only_when_the_stored_jd_hash_matches():
    from services.requirements_api import coverage_for        # lazy: the module needs the database layer
    record = {"job_description_sha256": cov.jd_sha256(EN_JD)}
    assert coverage_for(EN_JD, record, doc(other_items=1))[0]["code"] == "duties_may_be_omitted"
    assert coverage_for(EN_JD + "\nEdited later.", record, doc(other_items=1)) == []       # the JD changed: no claim about it
    assert coverage_for(EN_JD, None, doc(other_items=1)) == []                             # no record: nothing to attribute
    assert coverage_for(None, record, doc(other_items=1)) is None                          # no JD text at all
