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
    "classification": "SCENARIO-CLASSIFICATION\nSenior developer.\nPython is required for the role.\nFive years of backend experience is valued.\nThe role builds reporting APIs for finance teams.",
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
    expect(panel(page).get_by_text("Ready for use")).to_be_visible()
    check("2 English job: readiness reads ready")
    expect(panel(page).get_by_text("Python", exact=True)).to_be_visible()
    check("3 English job: item wording is shown exactly")
    panel(page).get_by_role("button", name="Edit analysis").click()
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    expect(field).to_have_value("Python")
    field.fill("Python 3")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("4 English job: an edit is saved at the next revision")


# (g) reorder within a category
def test_english_reorder_within_category(api, ui, admin):
    job = _job(api, admin, "english reorder", EN)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    panel(page).get_by_role("button", name="Edit analysis").click()
    panel(page).get_by_role("button", name="Move SQL up").click()
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    panel(page).get_by_role("button", name="Edit analysis").click()
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
    arabic_text = panel(page).get_by_text("بايثون", exact=True)
    expect(arabic_text).to_have_attribute("dir", "auto")
    check("8 Arabic job: requirement text is shown with dir=auto and exact wording")
    panel(page).get_by_role("button", name="Edit analysis").click()
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    expect(field).to_have_attribute("dir", "auto")
    expect(field).to_have_value("بايثون")
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
    panel(page).get_by_role("button", name="Edit analysis").click()
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
    panel(page).get_by_role("button", name="Edit analysis").click()
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
    panel(page).get_by_role("button", name="Edit analysis").click()
    weight = panel(page).get_by_role("spinbutton", name="Skills category weight", exact=True)
    weight.fill("150")
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python with Go")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_role("alert").filter(has_text="do not pass the saving rules")).to_be_visible(timeout=15000)
    expect(field).to_have_value("Python with Go")
    expect(weight).to_have_value("150")
    check("20 Validation error: the server refuses the save and the unsaved edits stay on the page")


# (j) viewer and recruiter read Requirements, with the weights, and cannot edit them
@pytest.mark.parametrize("role", ["viewer", "recruiter"])
def test_read_only_roles_see_requirements_and_no_editor(api, ui, admin, role):
    job = _job(api, admin, f"{role} read only", EN)
    page = ui.open(role, job)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(panel(page).get_by_text("Only administrators and HR managers can edit requirements")).to_be_visible()
    check(f"21 {role}: requirements are readable and the page says they are read-only")
    expect(page.get_by_role("button", name="Save requirements")).to_have_count(0)
    for field in panel(page).get_by_role("textbox", name="Requirement text").all():
        expect(field).to_be_disabled()
    for weight in panel(page).get_by_role("spinbutton").all():
        expect(weight).to_be_disabled()
    check(f"22 {role}: weights are shown, none can be changed, and there is no save control")


def test_viewer_is_refused_and_sees_no_editor(api, ui, admin):
    """A viewer reads the requirements (weights included) but is given no way to change them. Kept under this name because the
    teardown check in mutation_cleanup_check.sh runs it."""
    job = _job(api, admin, "viewer", EN)
    page = ui.open("viewer", job)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_role("button", name="Save requirements")).to_have_count(0)
    expect(page.get_by_role("button", name="Edit analysis")).to_have_count(0)
    expect(page.get_by_role("textbox", name="Requirement text")).to_have_count(0)


def test_role_matrix_over_http(api, admin):
    """The approved matrix on the live stack: the refusal is a 403 before any lookup, and the allowed roles reach the handler."""
    import json
    import urllib.error
    import urllib.request
    token, _ = admin
    job = _job(api, admin, "role matrix", EN)
    tokens = {role: api.login(role)[0] for role in ("admin", "hr_manager", "recruiter", "viewer")}
    missing = "00000000-0000-4000-8000-000000000000"

    def status(role, method, path, body=None):
        payload = body if body is not None else ({} if method != "GET" else None)
        request = urllib.request.Request(f"{api.base}{path}", method=method, headers={"Authorization": f"Bearer {tokens[role]}", "Content-Type": "application/json"},
                                         data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status
        except urllib.error.HTTPError as error:        # the export answers with a file, so the body is not parsed
            return error.code

    # Requirements: every tenant role reads (weights included); admin and HR manager edit; recruiter and viewer get 403 before the body
    for role in tokens:
        assert status(role, "GET", f"/jobs/{job}/requirements") == 200, role
    for role in ("recruiter", "viewer"):
        assert status(role, "POST", f"/jobs/{job}/requirements/edits") == 403, role
    assert status("hr_manager", "POST", f"/jobs/{job}/requirements/edits") == 422        # past the guard, into validation
    # Application changes: 403 for recruiter and viewer even for an application that does not exist; 404 for the roles that pass
    for role in ("recruiter", "viewer"):
        for method, path in (("PATCH", f"/applications/{missing}/workflow-status"), ("PATCH", f"/applications/{missing}/recruiter-notes"),
                             ("DELETE", f"/applications/{missing}"), ("POST", f"/applications/{missing}/comments")):
            assert status(role, method, path) == 403, (role, path)
    assert status("hr_manager", "DELETE", f"/applications/{missing}") == 404
    # Export: a viewer is refused
    assert status("viewer", "GET", "/applications/export") == 403
    for role in ("admin", "hr_manager", "recruiter"):
        assert status(role, "GET", "/applications/export") != 403, role
    # Job creation: admin and HR manager only
    assert status("recruiter", "POST", "/jobs") == 403 and status("viewer", "POST", "/jobs") == 403
    # Tenant users and subscription: admin only; the tenant audit log: admin only, and only their own tenant
    for role in ("hr_manager", "recruiter", "viewer"):
        assert status(role, "GET", "/tenant/users") == 403, role
        assert status(role, "POST", "/tenant/subscription/activate-trial") == 403, role
        assert status(role, "GET", "/admin/audit-logs") == 403, role
    assert status("admin", "GET", "/tenant/users") == 200
    assert status("admin", "GET", "/admin/audit-logs") == 200
    assert status("admin", "GET", f"/admin/audit-logs?tenant_id={missing}") == 403
    check("26 Role matrix over HTTP on the live stack")


def test_hr_manager_edits_and_saves_requirements(api, ui, admin):
    job = _job(api, admin, "hr manager edit", EN)
    page = ui.open("hr_manager", job)
    expect(panel(page)).to_be_visible(timeout=20000)
    panel(page).get_by_role("button", name="Edit analysis").click()
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python 3")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("25 HR manager: can edit and save requirements")


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
    # The model rejected this exact description, so the server refuses a retry until the description changes.
    assert api.retry_status(token, job) == 400
    check("R1 Retry: an unchanged description after an invalid extraction is refused by the server")
    api.update_description(token, job, f"SCENARIO-RETRY-FIXED\n{EN}")
    assert api.retry_status(token, job) == 202
    check("R2 Retry: a changed description permits the retry")
    api.requirements(token, job, timeout=90)     # the document exists once the retry has completed
    page.reload()
    expect(panel(page).get_by_text("Ready for use")).to_be_visible(timeout=30000)
    check("R3 Retry: the corrected description produces a ready document")
