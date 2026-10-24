"""Browser journeys for the Final Candidate Matrix, through the real UI and the real API.

Journeys: an admin configures and saves the job layout; a recruiter keeps a personal view without changing the shared one; a viewer
cannot change or export; a high-score candidate with a failed gate shows as Not Eligible; an unclear mandatory item shows as Review Required;
the evidence drawer opens from a result; the export matches the visible columns; the matrix reads in Arabic and right-to-left.
Screenshots (English and Arabic) are written to e2e/artifacts/candidate-matrix/.
"""
from __future__ import annotations

import io

import pytest
from playwright.sync_api import expect
from reportlab.pdfgen import canvas

from e2e.conftest import ARTIFACTS

MATRIX_JD = ("SCENARIO-MATRIX\nSenior support engineer.\nSQL troubleshooting, Python, incident management, English fluency and Arabic fluency are required.\n"
             "Kubernetes is preferred.\nThe role supports payment platforms and reporting services for finance teams.")
SHOTS = ARTIFACTS / "candidate-matrix"
FILLER = ["Experienced support engineer working with payment platforms, reporting services and finance teams.",
          "Handled production incidents, wrote runbooks and trained colleagues on escalation procedures.",
          "Worked in English and Arabic with customers and engineering teams across several regions."]


def _cv(name: str, levels: dict[str, str]) -> bytes:
    """A real PDF whose text states a level per requirement ("SQL troubleshooting: advanced.")."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 800
    for line in [f"{name} - candidate summary", *FILLER, *[f"{req}: {level}." for req, level in levels.items()]]:
        pdf.drawString(40, y, line)
        y -= 18
    pdf.save()
    return buffer.getvalue()


ALL = {"SQL troubleshooting": "advanced", "Python": "advanced", "Incident management": "advanced", "English fluency": "advanced",
       "Arabic fluency": "advanced", "Kubernetes": "yes"}
CANDIDATES = {
    "Alpha Clean": ALL,                                                              # every requirement met -> Eligible, score 100
    "Bravo Gate": {**ALL, "SQL troubleshooting": "basic"},                           # a partially met gate: score 90, Not Eligible
    "Charlie Unclear": {k: v for k, v in ALL.items() if k != "Arabic fluency"},      # the CV does not say: Review Required, no score
}


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.fixture
def admin(api):
    return api.login("admin")


def _matrix_job(api, admin, title):
    token, _ = admin
    job = api.create_job(token, f"E2E {title}", MATRIX_JD)
    doc = api.requirements(token, job)
    sql = next(i for i in doc["document"]["categories"]["skills"]["items"] if i["text"] == "SQL troubleshooting")
    status, body = api.edit(token, job, doc["requirements_revision"], [
        {"op": "set_gate", "item_id": sql["id"], "verification_method": "cv_evidence", "linked_question_id": None}])
    assert status == 200, body
    for name, levels in CANDIDATES.items():
        api.upload_cv(token, job, name.replace(" ", ""), pdf=_cv(name, levels))
    api.score_pending(token, job)
    api.wait_scored(job, len(CANDIDATES))
    return job, sql["id"]


def _rows(page):
    return {name: page.locator("tr[data-testid^='matrix-row-']", has_text=name) for name in CANDIDATES}


def _open(ui, role, job, user, lang="en"):
    page = ui.open(role, job, token_user=user, path=f"/jobs/{job}/matrix", lang=lang)
    expect(page.get_by_test_id("matrix-table")).to_be_visible(timeout=30000)
    return page


def test_failed_gate_and_unclear_item_show_as_not_eligible_and_review_required_and_the_admin_saves_the_job_layout(api, ui, admin):
    token, _ = admin
    job, sql_id = _matrix_job(api, admin, "matrix admin")
    page = _open(ui, "admin", job, admin)
    rows = _rows(page)
    for locator in rows.values():
        expect(locator).to_have_count(1, timeout=30000)
    expect(rows["Alpha Clean"].locator("[data-testid^='eligibility-']")).to_have_text("Eligible")
    expect(rows["Bravo Gate"].locator("[data-testid^='eligibility-']")).to_have_text("Not Eligible")
    expect(rows["Bravo Gate"].locator("[data-testid^='score-']")).to_have_text("90")                       # a high score, and still Not Eligible
    expect(rows["Charlie Unclear"].locator("[data-testid^='eligibility-']")).to_have_text("Review Required")
    expect(rows["Charlie Unclear"].locator("[data-testid^='score-']")).to_contain_text("—")                # no score is invented for an unclear item
    expect(rows["Charlie Unclear"].locator("[data-testid^='mandatory-summary-']")).to_contain_text("Unclear 1")
    expect(rows["Alpha Clean"].locator("[data-testid^='mandatory-summary-']")).to_contain_text("Met 5")
    expect(rows["Alpha Clean"].locator("[data-testid^='preferred-summary-']")).to_contain_text("Yes 1")
    expect(rows["Bravo Gate"].locator("[data-testid^='gates-summary-']")).to_contain_text("Failed 1")
    expect(page.get_by_test_id("matrix-principle")).to_contain_text("one decision-support element")
    expect(page.get_by_test_id("matrix-source")).to_have_text("System default")

    page.get_by_test_id("matrix-columns-button").click()
    for column in (f"mandatory:{sql_id}", f"gate:req:{sql_id}"):
        page.get_by_test_id(f"toggle-{column}").check()
    page.get_by_label("Hide: AI / review flags").click()
    page.get_by_label("Move up: Overall score").click()
    expect(page.get_by_test_id(f"header-mandatory:{sql_id}")).to_be_visible()
    expect(page.get_by_test_id("header-decision.flags")).to_have_count(0)
    expect(rows["Bravo Gate"].locator(f"[data-testid^='result-mandatory:{sql_id}']")).to_have_attribute("data-status", "partially_met")
    expect(rows["Bravo Gate"].locator(f"[data-testid^='result-gate:req:{sql_id}']")).to_have_attribute("data-status", "failed")
    _shot(page, "matrix-en-columns")
    page.get_by_test_id("matrix-save-shared").click()
    expect(page.get_by_test_id("matrix-notice")).to_have_text("Saved.")
    expect(page.get_by_test_id("matrix-source")).to_have_text("Job configuration")

    status, body = api.matrix_rows(token, job, page_size=10)
    assert status == 200 and body["columns"][:3] == ["candidate.name", "decision.score", "decision.eligibility"] and "decision.flags" not in body["columns"]
    page = _open(ui, "admin", job, admin)                                                                    # the saved layout survives a reload
    expect(page.get_by_test_id(f"header-gate:req:{sql_id}")).to_be_visible()
    _shot(page, "matrix-en")


def test_a_recruiter_keeps_a_personal_view_and_never_changes_the_shared_one(api, ui, admin):
    token, _ = admin
    job, sql_id = _matrix_job(api, admin, "matrix recruiter")
    shared = {"columns": ["candidate.name", "decision.eligibility", "decision.score"], "sort": {"column": "decision.score", "direction": "desc"}}
    assert api.matrix_put(token, job, "job", shared)[0] == 200
    recruiter = api.login("recruiter")
    page = _open(ui, "recruiter", job, recruiter)
    expect(page.get_by_test_id("matrix-source")).to_have_text("Job configuration")
    expect(page.get_by_test_id("matrix-save-shared")).to_have_count(0)                                      # a recruiter cannot save the shared layout
    page.get_by_test_id("matrix-columns-button").click()
    page.get_by_test_id("toggle-mandatory.summary").check()
    page.get_by_test_id("toggle-workflow.status").check()
    page.get_by_test_id("matrix-save-personal").click()
    expect(page.get_by_test_id("matrix-source")).to_have_text("Your personal view")
    page = _open(ui, "recruiter", job, recruiter)
    expect(page.get_by_test_id("header-mandatory.summary")).to_be_visible()
    page = _open(ui, "admin", job, admin)                                                                    # the admin still sees the shared layout
    expect(page.get_by_test_id("matrix-source")).to_have_text("Job configuration")
    expect(page.get_by_test_id("header-mandatory.summary")).to_have_count(0)
    page = _open(ui, "recruiter", job, recruiter)
    page.get_by_test_id("matrix-reset-personal").click()
    expect(page.get_by_test_id("matrix-source")).to_have_text("Job configuration")
    expect(page.get_by_test_id("header-mandatory.summary")).to_have_count(0)


def test_a_viewer_can_read_but_cannot_save_or_export(api, ui, admin):
    job, _ = _matrix_job(api, admin, "matrix viewer")
    viewer = api.login("viewer")
    page = _open(ui, "viewer", job, viewer)
    expect(page.locator("tr[data-testid^='matrix-row-']")).to_have_count(3, timeout=30000)
    for control in ("matrix-save-personal", "matrix-save-shared", "matrix-reset-personal", "matrix-reset-shared", "matrix-export"):
        expect(page.get_by_test_id(control)).to_have_count(0)
    expect(page.get_by_text("Read-only")).to_be_visible()
    layout = {"columns": ["candidate.name", "decision.score"]}
    for scope in ("job", "personal"):
        assert api.matrix_put(viewer[0], job, scope, layout)[0] == 403
    status, _ = api.matrix_rows(viewer[0], job)
    assert status == 200


def test_the_evidence_drawer_opens_from_a_result_and_leads_to_the_candidate(api, ui, admin):
    job, sql_id = _matrix_job(api, admin, "matrix drawer")
    page = _open(ui, "admin", job, admin)
    charlie = _rows(page)["Charlie Unclear"]
    charlie.locator("[data-testid^='mandatory-summary-']").click()
    drawer = page.get_by_test_id("matrix-drawer")
    expect(drawer).to_be_visible()
    expect(drawer.get_by_text("Unclear", exact=True).first).to_be_visible()
    expect(drawer).to_contain_text("Arabic fluency")
    expect(drawer).to_contain_text("Confidence")
    page.keyboard.press("Escape")
    expect(drawer).to_have_count(0)
    charlie.locator("[data-testid^='open-candidate-']").click()
    expect(page).to_have_url(__import__("re").compile(r"/applications\?job_id=.*app_id="))


def test_the_export_has_exactly_the_visible_columns_and_hides_everything_else(api, ui, admin):
    job, sql_id = _matrix_job(api, admin, "matrix export")
    page = _open(ui, "admin", job, admin)
    page.get_by_test_id("matrix-columns-button").click()
    page.get_by_test_id(f"toggle-mandatory:{sql_id}").check()
    for hidden in ("mandatory.summary", "preferred.summary", "gates.summary", "decision.flags", "candidate.applied_at", "workflow.status", "decision.scoring"):
        visible = page.get_by_test_id(f"toggle-{hidden}")
        if visible.is_checked():
            visible.uncheck()
    with page.expect_download() as download:
        page.get_by_test_id("matrix-export").click()
    data = open(download.value.path(), "rb").read()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    lines = [l for l in text.splitlines() if l]
    header = next(l for l in lines if l.startswith("Candidate,"))
    assert header == "Candidate,Eligibility,Overall score,SQL troubleshooting"
    body = {l.split(",")[0]: l for l in lines[lines.index(header) + 1:]}
    assert body["Bravo Gate"] == "Bravo Gate,Not Eligible,90,Partially Met"
    assert body["Charlie Unclear"].startswith("Charlie Unclear,Review Required,,")
    for leaked in ("Preferred criteria", "Application date", "Workflow status", "The CV states", "quote"):
        assert leaked not in text
    assert "Final Candidate Matrix" in text and "not a ranking" in text


def test_the_matrix_reads_in_arabic_and_right_to_left(api, ui, admin):
    job, _ = _matrix_job(api, admin, "matrix arabic")
    page = _open(ui, "admin", job, admin, lang="ar")
    assert page.evaluate("document.documentElement.dir") == "rtl"
    expect(page.get_by_test_id("matrix-principle")).to_contain_text("لا يتم ترتيب المرشحين تلقائيًا")
    rows = _rows(page)
    expect(rows["Bravo Gate"].locator("[data-testid^='eligibility-']")).to_have_text("غير مؤهل")
    expect(rows["Charlie Unclear"].locator("[data-testid^='eligibility-']")).to_have_text("مطلوب مراجعة")
    expect(rows["Alpha Clean"].locator("[data-testid^='eligibility-']")).to_have_text("مؤهل")
    expect(page.get_by_test_id("header-candidate.name")).to_contain_text("المرشح")
    box = page.get_by_test_id("header-candidate.name").bounding_box()
    scroll = page.get_by_test_id("matrix-scroll").bounding_box()
    assert box["x"] + box["width"] >= scroll["x"] + scroll["width"] - 2                                      # the frozen candidate column sits at the right edge in RTL
    _shot(page, "matrix-ar")
