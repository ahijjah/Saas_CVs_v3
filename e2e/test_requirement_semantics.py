"""Browser journeys for requirement semantics, through the real UI and the real API, on job descriptions unrelated to the other journeys.

Job Analysis shows how each criterion combines (any one of / all of / examples / accepted only after review / a flagged ambiguity) beside its source
wording; the recruiter confirms the AI's reading before scoring; candidate scoring shows which alternative was met and why a case needs review; and the
Candidate Matrix shows the same criteria and outcomes: a failed gate is Not Eligible, a conditional alternative is Review Required (never a fail), and
an English and an Arabic job behave the same way. Screenshots: e2e/artifacts/requirement-semantics/.
"""
from __future__ import annotations

import io
import re

import pytest
from playwright.sync_api import expect
from reportlab.pdfgen import canvas

from e2e.conftest import ARTIFACTS
from e2e.support.ui import panel

SHOTS = ARTIFACTS / "requirement-semantics"
EN_JD = ("SCENARIO-SEMANTICS-EN\nSite Supervisor\nRequirements:\n"
         "A diploma in civil engineering, or a degree in an engineering field together with two years on construction sites. "
         "Long site experience is considered after review where the candidate lacks a diploma.\n"
         "Experience with scheduling software such as spreadsheets or a project planner.\nFluent English.\n"
         "Experience on large or complex projects.\nStrong negotiation skills.")
AR_JD = ("SCENARIO-SEMANTICS-AR\nمشرف موقع\nالمطلوب: دبلوم في الهندسة المدنية أو بكالوريوس في الهندسة مع سنتين في مواقع البناء. "
         "يُنظر في الخبرة الميدانية الطويلة بعد التقييم لمن لا يملك دبلومًا.\nإجادة التعامل مع برامج الجدولة مثل برنامج كذا أو برنامج آخر.\nمهارات تفاوض قوية.")
FILLER = ["Experienced site professional working across residential and industrial projects.",
          "Coordinated subcontractors, reviewed drawings and wrote daily progress reports.",
          "Worked with clients, engineers and suppliers across several regions."]
PLAIN = {"Experience with scheduling software": "advanced", "Fluent English": "advanced", "Experience on large or complex projects": "advanced",
         "Strong negotiation skills": "advanced"}


def _cv(name: str, leaves: dict[str, str], plain: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 800
    lines = [f"{name} - candidate summary", *FILLER, *[f"Leaf {k}: {v}." for k, v in leaves.items()], *[f"{k}: {v}." for k, v in (plain or {}).items()]]
    for line in lines:
        pdf.drawString(40, y, line)
        y -= 18
    pdf.save()
    return buffer.getvalue()


CANDIDATES = {
    "Alpha Direct": _cv("Alpha Direct", {"o1": "advanced", "o2.1": "none", "o2.2": "none", "o3": "none"}, PLAIN),      # a direct option met
    "Bravo Review": _cv("Bravo Review", {"o1": "none", "o2.1": "none", "o2.2": "none", "o3": "advanced"}, PLAIN),      # only the review-only option met
    "Charlie Fails": _cv("Charlie Fails", {"o1": "none", "o2.1": "none", "o2.2": "none", "o3": "none"}, PLAIN),        # every option established as not met
}


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.fixture
def admin(api):
    return api.login("admin")


def _education(doc):
    return doc["document"]["categories"]["education"]["items"][0]


def test_job_analysis_shows_the_interpretation_the_recruiter_confirms_it_and_the_matrix_agrees(api, ui, admin):
    token, _ = admin
    job = api.create_job(token, "E2E semantics en", EN_JD)
    doc = api.requirements(token, job)
    education = _education(doc)
    skills = {i["text"]: i for i in doc["document"]["categories"]["skills"]["items"]}
    assert [o["acceptance"] for o in education["logic"]["options"]] == ["direct", "direct", "review"]
    assert education["alternatives"] == [o["text"] for o in education["logic"]["options"]]

    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id(f"logic-options-{education['id']}")).to_contain_text("Any one of")
    expect(page.get_by_test_id(f"logic-parts-{education['id']}-1")).to_contain_text("Degree in an engineering field + Two years on construction sites")
    expect(page.get_by_test_id(f"logic-option-{education['id']}-2")).to_have_attribute("data-acceptance", "review")
    expect(page.get_by_test_id(f"logic-option-{education['id']}-2")).to_contain_text("Accepted only after review")
    expect(page.get_by_test_id(f"logic-examples-{skills['Experience with scheduling software']['id']}")).to_contain_text("spreadsheets · a project planner")
    expect(panel(page).get_by_text("Fluent English", exact=True)).to_be_visible()                     # a language stays its own item
    expect(panel(page).get_by_text("Strong negotiation skills", exact=True)).to_be_visible()          # a soft skill stays its own item
    ambiguous = doc["document"]["categories"]["experience"]["items"][0]
    expect(page.get_by_test_id(f"logic-ambiguity-{ambiguous['id']}").first).to_contain_text("unclear whether both large and complex")
    expect(panel(page).get_by_text("Structure to confirm")).to_be_visible()
    _shot(page, "job-analysis-en")

    # the AI's reading of an ambiguous statement must be confirmed by a person before scoring is allowed
    panel(page).get_by_role("button", name="Confirm this structure").click()
    expect(panel(page).get_by_text("Structure to confirm")).to_have_count(0, timeout=15000)

    doc = api.requirements(token, job)
    status, body = api.edit(token, job, doc["requirements_revision"], [
        {"op": "set_gate", "item_id": _education(doc)["id"], "verification_method": "cv_evidence", "linked_question_id": None}])
    assert status == 200, body
    for name, pdf in CANDIDATES.items():
        api.upload_cv(token, job, name.replace(" ", ""), pdf=pdf)
    api.score_pending(token, job)
    api.wait_scored(job, len(CANDIDATES))

    page = ui.open("admin", job, token_user=admin, path=f"/jobs/{job}/matrix")
    expect(page.get_by_test_id("matrix-table")).to_be_visible(timeout=30000)
    rows = {n: page.locator("tr[data-testid^='matrix-row-']", has_text=n) for n in CANDIDATES}
    for locator in rows.values():
        expect(locator).to_have_count(1, timeout=30000)
    expect(rows["Alpha Direct"].locator("[data-testid^='eligibility-']")).to_have_text("Eligible")
    expect(rows["Alpha Direct"].locator("[data-testid^='score-']")).to_have_text("100")
    expect(rows["Bravo Review"].locator("[data-testid^='eligibility-']")).to_have_text("Review Required")      # a conditional alternative never fails a gate
    expect(rows["Bravo Review"].locator("[data-testid^='score-']")).to_contain_text("—")                       # and never becomes a number
    expect(rows["Charlie Fails"].locator("[data-testid^='eligibility-']")).to_have_text("Not Eligible")        # every option established as not met
    expect(rows["Bravo Review"].locator("[data-testid^='flags-']")).to_be_visible()
    expect(rows["Bravo Review"]).to_contain_text("Conditional alternative")

    page.get_by_test_id("matrix-columns-button").click()
    page.get_by_test_id(f"toggle-mandatory:{education['id']}").check()
    for name, status_ in (("Alpha Direct", "met"), ("Bravo Review", "unclear"), ("Charlie Fails", "not_met")):
        expect(rows[name].locator(f"[data-testid^='result-mandatory:{education['id']}']")).to_have_attribute("data-status", status_)
    rows["Bravo Review"].locator(f"[data-testid^='result-mandatory:{education['id']}']").click()
    drawer = page.get_by_test_id("matrix-drawer")
    expect(drawer.get_by_test_id(f"matched-{education['id']}")).to_have_text("Met through: Long site experience where the candidate lacks a diploma")
    expect(drawer.get_by_test_id(f"reason-{education['id']}")).to_contain_text("accepted only after review")
    expect(drawer.get_by_test_id(f"option-{education['id']}-o1")).to_have_attribute("data-result", "not_met")
    expect(drawer.get_by_test_id(f"option-{education['id']}-o3")).to_have_attribute("data-result", "met")
    _shot(page, "matrix-drawer-en")
    page.keyboard.press("Escape")

    page.get_by_test_id("matrix-filters-button").click()
    page.get_by_test_id("filter-flag-conditional_alternative").click()
    expect(page.locator("tr[data-testid^='matrix-row-']")).to_have_count(1)
    expect(page.locator("tr[data-testid^='matrix-row-']")).to_contain_text("Bravo Review")

    # candidate scoring shows the same thing
    status, body = api.matrix_rows(token, job)
    bravo = next(r for r in body["rows"] if r["candidate_name"] == "Bravo Review")
    page = ui.open("admin", job, token_user=admin, path=f"/applications?job_id={job}&app_id={bravo['application_id']}")
    expect(page.get_by_test_id(f"matched-{education['id']}")).to_contain_text("Long site experience where the candidate lacks a diploma", timeout=20000)
    expect(page.get_by_test_id(f"result-{education['id']}")).to_have_text("Unclear")
    expect(page.get_by_test_id(f"option-{education['id']}-o3")).to_contain_text("accepted only after review")
    expect(page.get_by_test_id(f"reason-{education['id']}")).to_be_visible()
    _shot(page, "candidate-scoring-en")


def test_the_same_behavior_in_arabic_and_right_to_left(api, ui, admin):
    token, _ = admin
    job = api.create_job(token, "E2E semantics ar", AR_JD)
    doc = api.requirements(token, job)
    education = _education(doc)
    assert [o["acceptance"] for o in education["logic"]["options"]] == ["direct", "direct", "review"]
    page = ui.open("admin", job, token_user=admin, lang="ar")
    expect(panel(page)).to_be_visible(timeout=20000)
    assert page.evaluate("document.documentElement.dir") == "rtl"
    expect(page.get_by_test_id(f"logic-options-{education['id']}")).to_contain_text("أيٌّ من")
    expect(page.get_by_test_id(f"logic-option-{education['id']}-2")).to_contain_text("مقبول بعد المراجعة فقط")
    expect(page.get_by_test_id(f"logic-parts-{education['id']}-1")).to_contain_text("بكالوريوس في الهندسة + سنتين في مواقع البناء")
    skills = doc["document"]["categories"]["skills"]["items"][0]
    expect(page.get_by_test_id(f"logic-examples-{skills['id']}")).to_contain_text("برنامج كذا · برنامج آخر")
    expect(page.get_by_text("الخبرة الميدانية الطويلة لمن لا يملك دبلومًا", exact=True)).to_have_attribute("dir", "auto")
    _shot(page, "job-analysis-ar")

    status, body = api.edit(token, job, doc["requirements_revision"], [
        {"op": "set_gate", "item_id": education["id"], "verification_method": "cv_evidence", "linked_question_id": None}])
    assert status == 200, body
    api.upload_cv(token, job, "BravoAr", pdf=_cv("Bravo Review", {"o1": "none", "o2.1": "none", "o2.2": "none", "o3": "advanced"}))
    api.score_pending(token, job)
    api.wait_scored(job, 1)
    page = ui.open("admin", job, token_user=admin, path=f"/jobs/{job}/matrix", lang="ar")
    expect(page.get_by_test_id("matrix-table")).to_be_visible(timeout=30000)
    row = page.locator("tr[data-testid^='matrix-row-']").first
    expect(row.locator("[data-testid^='eligibility-']")).to_have_text("مطلوب مراجعة", timeout=30000)
    expect(row).to_contain_text("بديل مشروط")
    _shot(page, "matrix-ar")
