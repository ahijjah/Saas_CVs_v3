"""Browser journeys for requirements V2, through the real UI and the real API.

Every journey creates its own job(s), so each one runs alone and in any order. The numbered checks are the 24 checks of the PR #33
browser acceptance; each is asserted in exactly one journey, except check 10, which is asserted in the preferred-only journey and in
the scoring-enabled/disabled journey. Journeys (k) also assert R1 and R2, the retry checks added by this suite.

Scripted external services: the model call is selected by the scenario marker in the job description (see support/fakes.py).
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.support.ui import check, panel, readiness, upload_cv_through_ui, wait_revision

EN = "Senior Python developer.\nPython and SQL are required for every candidate.\nFive years of backend experience is preferred.\nThe role builds reporting APIs and PostgreSQL schemas for finance teams."
AR = "مطور أول لبناء واجهات برمجية.\nمطلوب بايثون وإتقان لغة SQL لكل مرشح.\nخبرة سابقة في الذكاء الاصطناعي مفضلة.\nيعمل الفريق على تقارير الأداء للعملاء."
SCENARIOS = {
    "preferred": "SCENARIO-PREFERRED\nSenior developer for the reporting team.\nKnowledge of reporting tools is preferred.\nThe role supports finance analysts every day.",
    "classification": "SCENARIO-CLASSIFICATION\nSenior developer.\nPython is required for the role.\nFive years of backend experience is preferred.\nThe role builds reporting APIs for finance teams.",
    "split": "SCENARIO-SPLIT\nSenior developer.\nPython or Java is required for the role.\nFive years of experience is preferred.\nThe role builds reporting APIs for finance teams.",
    "structure": "SCENARIO-STRUCTURE\nSenior developer.\nPython is required for the role.\nFive years of backend experience is required.\nThe role builds reporting APIs for finance teams.",
    "invalid": "SCENARIO-INVALID\nSenior developer.\nPython and SQL are required for every candidate.\nThe role builds reporting APIs for finance teams.",
}


@pytest.fixture
def admin(api):
    return api.login("admin")


def _job(api, admin, title, description):
    token, _ = admin
    job = api.create_job(token, f"E2E {title}", description)
    api.requirements(token, job)
    return job


# (a) English creation -> extraction -> review/edit -> ready
def test_english_creation_extraction_review_and_edit(api, ui, admin):
    token, user = admin
    job = _job(api, admin, "english review", EN)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    check("1 English job: requirements panel displays the document")
    expect(panel(page).get_by_text("Ready for scoring")).to_be_visible()
    check("2 English job: readiness reads ready")
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    expect(field).to_have_value("Python")
    check("3 English job: item wording is shown exactly")
    field.fill("Python 3")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("4 English job: an edit is saved at the next revision")


# (g) reorder within a category
def test_english_reorder_within_category(api, ui, admin):
    job = _job(api, admin, "english reorder", EN)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    panel(page).get_by_role("button", name="Move SQL up").click()
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    order = panel(page).get_by_role("textbox", name="Requirement text").evaluate_all("els => els.map(e => e.value)")[:2]
    assert order == ["SQL", "Python"], order
    check("5 English job: items are reordered within the category")


# (l) scoring disabled while blocked, enabled when ready, score records the revision used
def test_scoring_disabled_while_blocked_and_enabled_when_ready(api, ui, admin):
    token, _ = admin
    blocked = _job(api, admin, "scoring blocked", SCENARIOS["preferred"])
    page = ui.open("admin", blocked, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    upload_cv_through_ui(page)
    expect(page.get_by_role("button", name="Score uploaded CVs")).to_be_disabled()
    check("10 Preferred-only job: scoring is disabled while blocked")

    ready = _job(api, admin, "scoring ready", EN)
    page = ui.open("admin", ready, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    upload_cv_through_ui(page)
    score = page.get_by_role("button", name="Score uploaded CVs")
    expect(score).to_be_enabled(timeout=15000)
    check("6 English job: scoring is enabled when the document is ready")
    score.click()
    scored = api.score_row(ready)
    assert scored is not None and scored["requirements_revision_used"] == 1, scored
    check("7 English job: the score records the requirements revision used (1)")


# (b) Arabic creation and editing
def test_arabic_creation_and_editing(api, ui, admin):
    job = _job(api, admin, "arabic", AR)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    expect(field).to_have_attribute("dir", "auto")
    expect(field).to_have_value("بايثون")
    check("8 Arabic job: requirement text is shown with dir=auto and exact wording")
    field.fill("بايثون المتقدمة")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("9 Arabic job: an Arabic edit is saved")


# (c) preferred-only confirmation
def test_preferred_only_confirmation_enables_scoring(api, ui, admin):
    job = _job(api, admin, "preferred", SCENARIOS["preferred"])
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    upload_cv_through_ui(page)
    expect(page.get_by_role("button", name="Score uploaded CVs")).to_be_disabled()
    check("10 Preferred-only job: scoring is disabled while blocked")
    panel(page).get_by_role("button", name="Confirm: no numeric score").click()
    expect(page.get_by_text("Confirmed: no numeric score.")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0)
    expect(page.get_by_role("button", name="Score uploaded CVs")).to_be_enabled(timeout=15000)
    check("11 Preferred-only job: confirmation enables scoring")


# (d) classification acknowledgment
def test_classification_acknowledgment_unblocks_readiness(api, ui, admin):
    job = _job(api, admin, "classification", SCENARIOS["classification"])
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    panel(page).get_by_role("button", name="Accept this classification").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("12 Classification job: acknowledging the warning unblocks readiness")


# (f) structural-finding resolution
def test_structural_finding_resolution(api, ui, admin):
    job = _job(api, admin, "split", SCENARIOS["split"])
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    check("13 Structural job: an open finding blocks readiness")
    page.get_by_role("combobox", name="Decision for this finding").select_option("fixed")
    page.get_by_role("checkbox", name="Python").check()
    panel(page).get_by_role("button", name="Resolve this finding").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("14 Structural job: resolving the finding with evidence unblocks readiness")


# (e) structure confirmation
def test_structure_confirmation_unblocks_readiness(api, ui, admin):
    job = _job(api, admin, "structure", SCENARIOS["structure"])
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    experience = panel(page).get_by_role("textbox", name="Requirement text").nth(1)
    experience.fill("Backend engineering experience")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Structure to confirm")).to_be_visible(timeout=15000)
    check("15 Structure job: a changed wording leaves its structure pending")
    panel(page).get_by_role("button", name="Confirm this structure").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("16 Structure job: confirming the structure unblocks readiness")


# (h) revision conflict: reload and reapply
def test_revision_conflict_reload_and_reapply(api, ui, admin):
    token, _ = admin
    job = _job(api, admin, "conflict", EN)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    wait_revision(page, 1)
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python 3 with pandas")
    # Another editor saves first, so this page's save at revision 1 is stale.
    status, body = api.edit(token, job, 1, [{"op": "set_text", "item_id": api.item_id(token, job, "skills", 1), "text": "SQL and data"}])
    assert status == 200, body
    panel(page).get_by_role("button", name="Save requirements").click()
    alert = page.get_by_role("alert").filter(has_text="The requirements changed while you were editing.")
    expect(alert).to_be_visible(timeout=15000)
    check("17 Revision conflict: the server refuses a stale save and the page says so")
    expect(field).to_have_value("Python 3 with pandas")
    check("18 Revision conflict: the unsaved edit is kept")
    alert.get_by_role("button", name="Apply my edits to the current requirements").click()
    expect(page.get_by_text("Your edits were applied to the current requirements.")).to_be_visible(timeout=15000)
    check("19 Revision conflict: applying the edits to the current revision succeeds")


# (i) validation error preserves local edits
def test_validation_error_preserves_local_edits(api, ui, admin):
    job = _job(api, admin, "validation", EN)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    wait_revision(page, 1)
    weight = panel(page).get_by_role("spinbutton", name="Skills category weight", exact=True)
    weight.fill("150")
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python with Go")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_role("alert").filter(has_text="do not pass the saving rules")).to_be_visible(timeout=15000)
    expect(field).to_have_value("Python with Go")
    expect(weight).to_have_value("150")
    check("20 Validation error: the server refuses the save and the unsaved edits stay on the page")


# (j) unauthorized / viewer refusal
def test_viewer_is_refused_and_sees_no_editor(api, ui, admin):
    job = _job(api, admin, "viewer", EN)
    page = ui.open("viewer", job)
    refusal = page.get_by_test_id("requirements-unavailable")
    expect(refusal).to_be_visible(timeout=20000)
    expect(refusal).to_contain_text("available to administrators and recruiters only")
    check("21 Viewer: requirements are refused by the approved API and the page says so")
    expect(page.get_by_role("button", name="Save requirements")).to_have_count(0)
    expect(page.get_by_role("textbox", name="Requirement text")).to_have_count(0)
    check("22 Viewer: no requirement text and no edit control is rendered")


# (k) extraction failure and retry
def test_extraction_failure_and_retry(api, ui, admin):
    token, _ = admin
    invalid = api.create_job(token, "E2E extraction failure", SCENARIOS["invalid"])
    state = api.criteria_state(invalid)
    assert state and state[0] == "failed" and state[1] is True, state
    check("23 Invalid extraction: the row is failed and no document is stored")
    page = ui.open("admin", invalid, token_user=admin)
    expect(page.get_by_text("AI criteria analysis failed.")).to_be_visible(timeout=20000)
    check("24 Invalid extraction: the failed state is visible in the page")

    broken = f"SCENARIO-RETRY-BROKEN\n{EN}"
    job = api.create_job(token, "E2E retry", broken)
    api.retry_state(job, retries=0)
    page = ui.open("admin", job, token_user=admin)
    expect(page.get_by_text("AI criteria analysis failed.")).to_be_visible(timeout=20000)
    retry = page.get_by_role("button", name="Retry AI Analysis")
    expect(retry).to_be_enabled(timeout=15000)
    check("R1 Retry: a failed job offers retry while its description is unchanged from the first attempt")
    retry.click()
    api.retry_state(job, retries=1)
    page.reload()                       # the page reads job details once; reload to see the settled state
    expect(page.get_by_text("AI criteria analysis failed.")).to_be_visible(timeout=20000)
    expect(page.get_by_role("button", name="Retry AI Analysis")).to_be_enabled(timeout=15000)
    # Current behaviour for requirements V2: a failed attempt does not record the failed description, so an unchanged description
    # does not block retry. Retries are bounded by criteria_extraction_max_retries only (recorded in the release notes).
    check("R2 Retry: a retry on the same description fails again, is counted, and stays available (bounded by max retries)")
    api.update_description(token, job, f"SCENARIO-RETRY-FIXED\n{EN}")
    page.reload()
    expect(retry).to_be_enabled(timeout=15000)
    retry.click()
    api.requirements(token, job, timeout=90)     # the document exists once the retry has completed
    page.reload()
    expect(panel(page).get_by_text("Ready for scoring")).to_be_visible(timeout=30000)
    check("R3 Retry: after the description is corrected, retry produces a ready document")
