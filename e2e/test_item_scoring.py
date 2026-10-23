"""Browser journeys for item-level scoring (Requirements V2), through the real UI and the real API.

Journeys: a CV is scored per Required item; an unclear Required item makes the score a provisional range with no decision (Review Required);
an administrator resolves it with a reason and the final score and decision appear; a recruiter sees the same facts and no controls; a
requirements edit makes the score stale and a rescore brings it up to date; the screen reads in Arabic and right-to-left.
Screenshots are written to e2e/artifacts/item-scoring/.
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

from e2e.conftest import ARTIFACTS
from e2e.support.api import ApiError
from e2e.support.ui import panel, upload_cv_through_ui

ITEMS_JD = ("SCENARIO-ITEMS\nSenior Python developer.\nPython is required. Fluent Arabic is required.\nBackend experience is preferred.\n"
            "The role builds reporting APIs and PostgreSQL schemas for finance teams.")
SHOTS = ARTIFACTS / "item-scoring"


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.fixture
def admin(api):
    return api.login("admin")


def _scored_job(api, ui, admin, title):
    token, _ = admin
    job = api.create_job(token, f"E2E {title}", ITEMS_JD)
    doc = api.requirements(token, job)
    page = ui.open("admin", job, token_user=admin)
    expect(panel(page)).to_be_visible(timeout=20000)
    upload_cv_through_ui(page)
    page.get_by_role("button", name="Score uploaded CVs").click()
    assert api.score_row(job) is not None
    return job, doc, api.application_id(job)


def _wait_for(read, wanted, timeout=60):
    body = None
    for _ in range(timeout):
        body = read()
        if wanted(body):
            return body
        time.sleep(1)
    raise ApiError(f"never reached the wanted state: {body}")


def test_unclear_item_review_resolution_and_final_score(api, ui, admin):
    token, _ = admin
    job, _, app = _scored_job(api, ui, admin, "item scoring")
    status, view = api.item_scoring(token, app)
    assert status == 200 and view["scoring_method"] == "item_level_v2"
    assert view["status"] == "review_required" and view["final_score"] is None and view["decision"] is None
    assert view["range"] == {"min": "50", "max": "100"} and view["unclear_count"] == 1

    path = f"/applications?job_id={job}&app_id={app}"
    page = ui.open("admin", job, token_user=admin, path=path)
    panel_ = page.get_by_test_id("item-scoring")
    expect(panel_).to_be_visible(timeout=20000)
    expect(panel_).to_have_attribute("data-status", "review_required")
    expect(page.get_by_test_id("provisional-range")).to_have_text("50 – 100")
    expect(page.get_by_test_id("final-score")).to_have_count(0)
    expect(page.get_by_test_id("item-scoring-outcome")).to_have_text("Review Required")
    expect(page.get_by_test_id("preferred-summary")).to_have_text("1 Yes · 0 No · 0 Not Clear")
    python = next(i for c in view["categories"] for i in c["items"] if i["text"] == "Python")
    arabic = next(i for c in view["categories"] for i in c["items"] if i["text"] == "Arabic fluency")
    expect(page.get_by_test_id(f"result-{python['item_id']}")).to_have_text("Met")
    expect(page.get_by_test_id(f"earned-{python['item_id']}")).to_contain_text("50")
    expect(page.get_by_test_id(f"result-{arabic['item_id']}")).to_have_text("Unclear")
    expect(panel_.get_by_text("“Strong Python and SQL skills”")).to_be_visible()
    _shot(page, "review-required")

    page.get_by_test_id(f"resolve-{arabic['item_id']}").click()
    save = page.get_by_test_id(f"resolve-save-{arabic['item_id']}")
    expect(save).to_be_disabled()                                                              # a reason is required
    panel_.get_by_label("Partially Met").check()
    panel_.get_by_label("Reason (required)").fill("Spoken Arabic confirmed on the screening call, written level not tested")
    save.click()
    expect(page.get_by_test_id("final-score")).to_have_text("75", timeout=15000)
    expect(page.get_by_test_id("item-scoring-outcome")).to_have_text("Partial")
    expect(page.get_by_test_id(f"result-{arabic['item_id']}")).to_have_text("Partially Met")
    expect(page.get_by_test_id(f"resolution-{arabic['item_id']}")).to_contain_text("AI result: Unclear")
    _shot(page, "resolved")

    status, view = api.item_scoring(token, app)
    assert view["status"] == "scored" and view["final_score"] == 75 and view["decision"] == "partial"
    assert [e["event_type"] for e in next(i for c in view["categories"] for i in c["items"] if i["item_id"] == arabic["item_id"])["history"]][-1] == "resolved"

    # a recruiter sees the same facts and no controls; the server refuses the write
    recruiter = api.login("recruiter")
    page = ui.open("recruiter", job, token_user=recruiter, path=path)
    expect(page.get_by_test_id("final-score")).to_have_text("75", timeout=20000)
    expect(page.get_by_role("button", name="Resolve")).to_have_count(0)
    status, _ = api.resolve_item(recruiter[0], app, arabic['item_id'], 'met', 'x')
    assert status == 403


def test_a_requirements_edit_makes_the_score_stale_and_a_rescore_updates_it(api, ui, admin):
    token, _ = admin
    job, doc, app = _scored_job(api, ui, admin, "item staleness")
    python = next(i for i in doc["document"]["categories"]["skills"]["items"] if i["text"] == "Python")
    status, body = api.edit(token, job, doc["requirements_revision"], [
        {"op": "set_gate", "item_id": python["id"], "verification_method": "manual", "linked_question_id": None}])
    assert status == 200, body

    page = ui.open("admin", job, token_user=admin, path=f"/applications?job_id={job}&app_id={app}")
    expect(page.get_by_test_id("item-scoring-stale")).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("revision-line")).to_have_text("Scored against revision 1; current revision is 2.")
    _shot(page, "stale")
    page.get_by_test_id("rescore").click()
    expect(page.get_by_test_id("rescore-queued")).to_be_visible()
    view = _wait_for(lambda: api.item_scoring(token, app)[1], lambda v: v.get("requirements_revision_used") == 2)
    assert view["stale"] is False

    # the earlier run stays as history: two runs, one current
    with api._worker_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE is_current) FROM application_scoring_runs WHERE application_id = %s", (app,))
        assert cur.fetchone() == (2, 1)


def test_arabic_labels_and_direction(api, ui, admin):
    token, _ = admin
    job, _, app = _scored_job(api, ui, admin, "item arabic")
    page = ui.open("admin", job, token_user=admin, path=f"/applications?job_id={job}&app_id={app}", lang="ar")
    expect(page.get_by_test_id("item-scoring")).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("item-scoring-outcome")).to_have_text("مطلوب مراجعة")
    expect(page.get_by_test_id("preferred-summary")).to_have_text("1 نعم · 0 لا · 0 غير واضح")
    assert page.evaluate("document.documentElement.dir || document.body.dir || getComputedStyle(document.body).direction") == "rtl"
    _shot(page, "review-required-ar")
