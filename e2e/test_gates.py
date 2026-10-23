"""Browser journeys for Gates / Knockout Criteria, through the real UI and the real API.

Journeys: the AI suggestion is only a suggestion until an admin confirms it, and a Preferred item has no gate control; a Mandatory Gate
makes the candidate Review Required, then Not Eligible, with the score unable to override it; an override needs a reason and leaves the
history; read-only roles see no controls; Create Job offers Response Required and Use as Eligibility Gate separately; English and Arabic.
Screenshots of the three main screens are written to e2e/artifacts/gates/.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import ARTIFACTS
from e2e.support.ui import panel, upload_cv_through_ui

GATES_JD = ("SCENARIO-GATES\nSenior Python developer.\nCandidates must hold a valid driving licence.\nPython is a plus.\n"
            "The role builds reporting APIs and PostgreSQL schemas for finance teams.")
SHOTS = ARTIFACTS / "gates"


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.fixture
def admin(api):
    return api.login("admin")


def _job(api, admin, title="gates"):
    token, _ = admin
    job = api.create_job(token, f"E2E {title}", GATES_JD)
    return job, api.requirements(token, job)


def _row(page, text):
    return panel(page).locator("li", has_text=text).first


def test_suggestion_is_unconfirmed_until_an_admin_confirms_and_preferred_has_no_gate_control(api, ui, admin):
    token, _ = admin
    job, doc = _job(api, admin)
    items = doc["document"]["categories"]["skills"]["items"]
    licence = next(i for i in items if i["text"] == "Valid driving licence")
    python = next(i for i in items if i["text"] == "Python")
    assert licence["gate_suggestion"]["cue"].lower() == "must" and licence.get("gate") is None      # suggested, not enforced
    assert python["importance"] == "preferred" and python.get("gate_suggestion") is None

    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(panel(page).get_by_text("AI Suggested").first).to_be_visible()
    expect(panel(page).get_by_text("Mandatory Gate", exact=True)).to_have_count(0)
    _shot(page, "job-analysis-suggested")

    panel(page).get_by_role("button", name="Edit analysis").click()
    expect(page.get_by_test_id(f"gate-controls-{licence['id']}")).to_be_visible()
    expect(page.get_by_test_id(f"gate-controls-{python['id']}")).to_have_count(0)               # Preferred: no control
    page.get_by_test_id(f"gate-controls-{licence['id']}").get_by_label("Mandatory Gate (eligibility)").check()
    box = page.get_by_test_id(f"gate-controls-{licence['id']}")
    box.get_by_label("Verification method").select_option("manual")
    box.get_by_label("If the check fails").select_option("not_eligible")
    panel(page).get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    expect(panel(page).get_by_text("Mandatory Gate", exact=True).first).to_be_visible()
    expect(panel(page).get_by_text("AI Suggested")).to_have_count(0)                            # confirmed: no longer only a suggestion
    _shot(page, "job-analysis-confirmed")

    saved = api.requirements(token, job)["document"]["categories"]["skills"]["items"]
    gate = next(i for i in saved if i["text"] == "Valid driving licence")["gate"]
    assert gate["verification_method"] == "manual" and gate["failure_action"] == "not_eligible" and "confirmed_by" not in gate


def test_a_recruiter_and_a_viewer_cannot_confirm_a_gate(api, admin):
    job, doc = _job(api, admin, "gates roles")
    item = doc["document"]["categories"]["skills"]["items"][0]["id"]
    op = [{"op": "set_gate", "item_id": item, "verification_method": "manual", "failure_action": "not_eligible", "linked_question_id": None}]
    for role in ("recruiter", "viewer"):
        token, _ = api.login(role)
        status, _ = api.edit(token, job, doc["requirements_revision"], op)
        assert status == 403, role


def test_gate_summary_review_then_not_eligible_then_overridden_with_history(api, ui, admin):
    token, _ = admin
    job, doc = _job(api, admin, "gates summary")
    licence = next(i for i in doc["document"]["categories"]["skills"]["items"] if i["text"] == "Valid driving licence")
    status, body = api.edit(token, job, doc["requirements_revision"], [
        {"op": "set_gate", "item_id": licence["id"], "verification_method": "manual", "failure_action": "not_eligible", "linked_question_id": None}])
    assert status == 200, body

    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    upload_cv_through_ui(page)
    page.get_by_role("button", name="Score uploaded CVs").click()
    assert api.score_row(job) is not None
    app = api.application_id(job)
    api.gate_status(token, app, "review_required")                                             # waiting for the manual verification

    path = f"/applications?job_id={job}&app_id={app}"
    page = ui.open("admin", job, token_user=admin, path=path)
    summary = page.get_by_test_id("gate-summary")
    expect(summary).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("gate-overall-status")).to_have_text("Review Required")
    expect(page.get_by_test_id("gate-score-note")).to_contain_text("cannot override a failed gate")
    # the Gate Summary is above the numeric score
    assert summary.bounding_box()["y"] < page.get_by_test_id("eligibility-chip").bounding_box()["y"]

    summary.get_by_role("button", name="Record result").click()
    summary.get_by_role("button", name="Failed").click()
    expect(page.get_by_test_id("gate-overall-status")).to_have_text("Not Eligible", timeout=15000)
    expect(summary).to_have_attribute("data-status", "not_eligible")
    expect(page.get_by_test_id("eligibility-chip")).to_have_text("Not Eligible")                # beside the score, replacing its decision
    _shot(page, "candidate-gate-summary-not-eligible")

    summary.get_by_role("button", name="Override").click()
    confirm = summary.get_by_role("button", name="Confirm override")
    expect(confirm).to_be_disabled()                                                           # a reason is required
    summary.get_by_label("Reason (required)").fill("Licence verified in person by the hiring manager")
    confirm.click()
    expect(page.get_by_test_id("gate-overall-status")).to_have_text("Eligible", timeout=15000)
    expect(summary.get_by_text("Licence verified in person by the hiring manager").first).to_be_visible()
    summary.get_by_text("History").first.click()
    expect(summary.locator("[data-testid^='gate-history-']")).to_contain_text("Failed → Overridden")
    _shot(page, "candidate-gate-summary-overridden")

    body = api.gates(token, app)[1]
    [gate] = body["gates"]
    assert gate["result"] == "overridden" and gate["computed_result"] == "failed"

    # read-only roles see the same facts and no controls
    for role in ("recruiter", "viewer"):
        user = api.login(role)
        page = ui.open(role, job, token_user=user, path=path)
        expect(page.get_by_test_id("gate-summary")).to_be_visible(timeout=20000)
        expect(page.get_by_role("button", name="Override")).to_have_count(0)
        expect(page.get_by_role("button", name="Record result")).to_have_count(0)
        status, _ = api.edit(user[0], job, 2, [{"op": "clear_gate", "item_id": licence["id"]}])
        assert status == 403


def test_create_job_offers_response_required_and_eligibility_gate_separately(api, ui, admin):
    page = ui.open("admin", "none", token_user=admin, path="/jobs")
    page.get_by_role("button", name="Add New Job").first.click()
    page.get_by_role("button", name="Add Knockout Questions").click()
    page.get_by_role("button", name="Add Question").click()
    expect(page.get_by_text("Response Required", exact=True).first).to_be_visible()
    gate = page.get_by_label("Use as Eligibility Gate")
    expect(gate).not_to_be_checked()                                                           # advisory unless switched on
    expect(page.get_by_label("If the check fails")).to_have_count(0)
    gate.check()
    expect(page.get_by_role("alert").filter(has_text="Set the passing criteria")).to_be_visible()
    page.get_by_label("yes", exact=True).check()
    expect(page.get_by_role("alert").filter(has_text="Set the passing criteria")).to_have_count(0)
    expect(page.get_by_label("If the check fails")).to_be_visible()
    _shot(page, "create-job-eligibility-gate")


def test_arabic_labels_and_direction(api, ui, admin):
    page = ui.open("admin", "none", token_user=admin, path="/jobs", lang="ar")
    page.get_by_role("button", name="إضافة وظيفة جديدة").first.click()
    page.get_by_role("button", name="إضافة أسئلة إقصاء").click()
    page.get_by_role("button", name="إضافة سؤال").click()
    expect(page.get_by_text("الرد مطلوب", exact=True).first).to_be_visible()
    expect(page.get_by_label("استخدام كبوابة أهلية")).to_be_visible()
    assert page.evaluate("document.documentElement.dir || document.body.dir || getComputedStyle(document.body).direction") == "rtl"
    _shot(page, "create-job-eligibility-gate-ar")
