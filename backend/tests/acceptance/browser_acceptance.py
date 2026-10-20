"""Browser acceptance for requirements v2: the real UI (Vite dev server) against the real API started by live_backend.py.

    python tests/acceptance/browser_acceptance.py --state <state.json> --app http://localhost:3000

The UI's configured API origin is forwarded to the live backend. Authentication is the real login endpoint; the session is stored the
way the app stores it. Jobs and candidates are created through the API (the same routes the UI calls). Only external AI, the
embedding model and e-mail are scripted, by live_backend.py. Every check is an assertion; the first failure stops the run."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

UI_API = "http://72.62.31.221:8000"          # the origin the UI is configured with (config.ts)
CORS = {"Access-Control-Allow-Origin": "http://localhost:3000", "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"}
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "requirements_v2_cv_smoke.pdf"

SCENARIOS = {
    "english": "Senior Python developer.\nPython and SQL are required for every candidate.\nFive years of backend experience is preferred.\nThe role builds reporting APIs and PostgreSQL schemas for finance teams.",
    "arabic": "مطور أول لبناء واجهات برمجية.\nمطلوب بايثون وإتقان لغة SQL لكل مرشح.\nخبرة سابقة في الذكاء الاصطناعي مفضلة.\nيعمل الفريق على تقارير الأداء للعملاء.",
    "preferred": "SCENARIO-PREFERRED\nSenior developer for the reporting team.\nKnowledge of reporting tools is preferred.\nThe role supports finance analysts every day.",
    "classification": "SCENARIO-CLASSIFICATION\nSenior developer.\nPython is required for the role.\nFive years of backend experience is preferred.\nThe role builds reporting APIs for finance teams.",
    "split": "SCENARIO-SPLIT\nSenior developer.\nPython or Java is required for the role.\nFive years of experience is preferred.\nThe role builds reporting APIs for finance teams.",
    "structure": "SCENARIO-STRUCTURE\nSenior developer.\nPython is required for the role.\nFive years of backend experience is required.\nThe role builds reporting APIs for finance teams.",
    "invalid": "SCENARIO-INVALID\nSenior developer.\nPython and SQL are required for every candidate.\nThe role builds reporting APIs for finance teams.",
}


def _request(method, url, token=None, body=None, raw=None, headers=None):
    hdrs = {"Content-Type": "application/json"} | (headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


class Backend:
    def __init__(self, state):
        self.state = state
        self.base = f"http://127.0.0.1:{state['port']}"

    def login(self, role):
        user = self.state["users"][role]
        status, body = _request("POST", f"{self.base}/auth/login", body={"email": user["email"], "password": user["password"]})
        assert status == 200, body
        return body["token"], body["user"]

    def create_job(self, token, title, description):
        status, body = _request("POST", f"{self.base}/jobs", token, {"title": title, "description": description})
        assert status == 201, body
        return body["job_id"]

    def requirements(self, token, job):
        for _ in range(60):
            status, body = _request("GET", f"{self.base}/jobs/{job}/requirements", token)
            if status == 200:
                return body
            time.sleep(1)
        raise AssertionError(f"requirements never became available for {job}: {status} {body}")

    def edit(self, token, job, revision, operations):
        return _request("POST", f"{self.base}/jobs/{job}/requirements/edits", token,
                        {"client_revision": revision, "operations": operations})

    def upload_cv(self, token, job, name):
        boundary = "----accept"
        parts = [(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
                 for k, v in (("job_id", job), ("candidate_name", name), ("candidate_email", f"{name.lower()}@acceptance.com"))]
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="cv.pdf"\r\n'
                      f'Content-Type: application/pdf\r\n\r\n').encode() + FIXTURE.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, body = _request("POST", f"{self.base}/applications/upload", token, raw=b"".join(parts),
                                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert status == 202, body
        return body

    def score_pending(self, token, job):
        return _request("POST", f"{self.base}/applications/score-pending", token, {"job_id": job})


def forward(route, request, backend):
    """The browser's own request, retargeted to the live backend unchanged (body, headers, CORS); the backend answers CORS for the UI."""
    route.continue_(url=request.url.replace(UI_API, backend.base))


def open_as(browser, backend, role, job, lang="en", token_user=None):
    token, user = token_user or backend.login(role)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script(f"localStorage.setItem('token', {json.dumps(token)});"
                            f"localStorage.setItem('user', {json.dumps(json.dumps(user))});"
                            f"localStorage.setItem('app_lang', '{lang}');")
    context.route(f"{UI_API}/**", lambda route, request: forward(route, request, backend))
    page = context.new_page()
    page.goto(f"http://localhost:3000/jobs/{job}")
    return context, page


def upload_cv_through_ui(page: Page):
    """The candidate upload the recruiter performs: the page's own file input, then the page's own list of this session's CVs."""
    page.get_by_text("Manual Upload", exact=True).first.click()
    page.set_input_files("#cv-file-input", str(FIXTURE))
    expect(page.get_by_text("CV(s) uploaded successfully.")).to_be_visible(timeout=15000)


def panel(page: Page):
    return page.locator("section[aria-labelledby='requirements-heading']")


def readiness(page: Page) -> str:
    return panel(page).get_by_test_id("readiness-state").inner_text()


def wait_revision(page: Page, revision: int):
    expect(panel(page).get_by_text(f"Revision {revision}", exact=True)).to_be_visible(timeout=15000)


def check(name):
    print(f"PASS  {name}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--app", default="http://localhost:3000")
    args = parser.parse_args()
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    backend = Backend(state)
    admin_token, admin_user = backend.login("admin")

    jobs = {name: backend.create_job(admin_token, f"Acceptance {name}", text) for name, text in SCENARIOS.items()}
    for name, job in jobs.items():
        backend.requirements(admin_token, job) if name != "invalid" else None

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args=["--no-sandbox"])
        try:
            run_english(browser, backend, admin_token, admin_user, jobs)
            run_arabic(browser, backend, admin_token, admin_user, jobs)
            run_review_actions(browser, backend, admin_token, admin_user, jobs)
            run_conflict_and_validation(browser, backend, admin_token, admin_user, jobs)
            run_viewer(browser, backend, jobs)
            run_invalid(browser, backend, admin_token, admin_user, jobs)
        finally:
            browser.close()
    print("ALL ACCEPTANCE CHECKS PASSED", flush=True)


def run_english(browser, backend, token, user, jobs):
    job = jobs["english"]
    context, page = open_as(browser, backend, "admin", job, token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    check("English job: requirements panel displays the document")
    expect(panel(page).get_by_text("Ready for scoring")).to_be_visible()
    check("English job: readiness reads ready")
    expect(panel(page).get_by_role("textbox", name="Requirement text").first).to_have_value("Python")
    check("English job: item wording is shown exactly")
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python 3")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("English job: an edit is saved at the next revision")
    panel(page).get_by_role("button", name="Move SQL up").click()
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 3.")).to_be_visible(timeout=15000)
    order = panel(page).get_by_role("textbox", name="Requirement text").evaluate_all("els => els.map(e => e.value)")[:2]
    assert order == ["SQL", "Python 3"], order
    check("English job: items are reordered within the category")
    upload_cv_through_ui(page)
    score = page.get_by_role("button", name="Score uploaded CVs")
    expect(score).to_be_enabled(timeout=15000)
    check("English job: scoring is enabled when the document is ready")
    score.click()
    scored = poll_score(backend, job)
    assert scored is not None and scored["requirements_revision_used"] == 3, scored
    check("English job: the score records the requirements revision used (3)")
    context.close()


def poll_score(backend, job):
    import psycopg2
    dsn = backend.state["dsn"].replace("postgresql+asyncpg://", "postgresql://")
    for _ in range(60):
        conn = psycopg2.connect(dsn, options="-c search_path=cv_analyzer")
        cur = conn.cursor()
        cur.execute("SELECT s.requirements_revision_used, s.final_score FROM application_scores s"
                    " JOIN applications a ON a.application_id = s.application_id WHERE a.job_id = %s", (job,))
        row = cur.fetchone()
        conn.close()
        if row:
            return {"requirements_revision_used": row[0], "final_score": row[1]}
        time.sleep(1)
    return None


def run_arabic(browser, backend, token, user, jobs):
    job = jobs["arabic"]
    context, page = open_as(browser, backend, "admin", job, token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    expect(field).to_have_attribute("dir", "auto")
    expect(field).to_have_value("بايثون")
    check("Arabic job: requirement text is shown with dir=auto and exact wording")
    field.fill("بايثون المتقدمة")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Saved. The requirements are now at revision 2.")).to_be_visible(timeout=15000)
    check("Arabic job: an Arabic edit is saved")
    context.close()


def run_review_actions(browser, backend, token, user, jobs):
    context, page = open_as(browser, backend, "admin", jobs["preferred"], token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    upload_cv_through_ui(page)
    expect(page.get_by_role("button", name="Score uploaded CVs")).to_be_disabled()
    check("Preferred-only job: scoring is disabled while blocked")
    panel(page).get_by_role("button", name="Confirm: no numeric score").click()
    expect(page.get_by_text("Confirmed: no numeric score.")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0)
    expect(page.get_by_role("button", name="Score uploaded CVs")).to_be_enabled(timeout=15000)
    check("Preferred-only job: confirmation enables scoring")
    context.close()

    context, page = open_as(browser, backend, "admin", jobs["classification"], token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    panel(page).get_by_role("button", name="Accept this classification").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("Classification job: acknowledging the warning unblocks readiness")
    context.close()

    context, page = open_as(browser, backend, "admin", jobs["split"], token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_test_id("scoring-blocked")).to_be_visible()
    check("Structural job: an open finding blocks readiness")
    page.get_by_role("combobox", name="Decision for this finding").select_option("fixed")
    page.get_by_role("checkbox", name="Python").check()
    panel(page).get_by_role("button", name="Resolve this finding").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("Structural job: resolving the finding with evidence unblocks readiness")
    context.close()

    context, page = open_as(browser, backend, "admin", jobs["structure"], token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    experience = panel(page).get_by_role("textbox", name="Requirement text").nth(1)
    experience.fill("Backend engineering experience")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_text("Structure to confirm")).to_be_visible(timeout=15000)
    check("Structure job: a changed wording leaves its structure pending")
    panel(page).get_by_role("button", name="Confirm this structure").click()
    expect(page.get_by_test_id("scoring-blocked")).to_have_count(0, timeout=15000)
    check("Structure job: confirming the structure unblocks readiness")
    context.close()


def run_conflict_and_validation(browser, backend, token, user, jobs):
    job = jobs["english"]
    context, page = open_as(browser, backend, "admin", job, token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    wait_revision(page, 3)
    current = backend.requirements(token, job)["requirements_revision"]
    assert current == 3, current
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python 3 with pandas")
    status, _ = backend.edit(token, job, 3, [{"op": "set_text", "item_id": backend.requirements(token, job)["document"]["categories"]["skills"]["items"][1]["id"], "text": "SQL and data"}])
    assert status == 200, status
    panel(page).get_by_role("button", name="Save requirements").click()
    alert = page.get_by_role("alert").filter(has_text="The requirements changed while you were editing.")
    expect(alert).to_be_visible(timeout=15000)
    check("Revision conflict: the server refuses a stale save and the page says so")
    expect(field).to_have_value("Python 3 with pandas")
    check("Revision conflict: the unsaved edit is kept")
    alert.get_by_role("button", name="Apply my edits to the current requirements").click()
    expect(page.get_by_text("Your edits were applied to the current requirements.")).to_be_visible(timeout=15000)
    check("Revision conflict: applying the edits to the current revision succeeds")
    context.close()

    context, page = open_as(browser, backend, "admin", job, token_user=(token, user))
    expect(panel(page)).to_be_visible(timeout=20000)
    wait_revision(page, 5)
    weight = panel(page).get_by_role("spinbutton", name="Skills category weight", exact=True)
    weight.fill("150")
    field = panel(page).get_by_role("textbox", name="Requirement text").first
    field.fill("Python with Go")
    panel(page).get_by_role("button", name="Save requirements").click()
    expect(page.get_by_role("alert").filter(has_text="do not pass the saving rules")).to_be_visible(timeout=15000)
    expect(field).to_have_value("Python with Go")
    expect(weight).to_have_value("150")
    check("Validation error: the server refuses the save and the unsaved edits stay on the page")
    context.close()


def run_viewer(browser, backend, jobs):
    """A viewer reads the requirements, weights included, and is given no way to change them."""
    context, page = open_as(browser, backend, "viewer", jobs["english"])
    expect(panel(page)).to_be_visible(timeout=20000)
    expect(page.get_by_text("Only administrators and HR managers can edit requirements")).to_be_visible()
    check("Viewer: requirements are readable and the page says they are read-only")
    expect(page.get_by_role("button", name="Save requirements")).to_have_count(0)
    expect(page.get_by_role("textbox", name="Requirement text").first).to_be_disabled()
    check("Viewer: no edit control is offered and the fields are disabled")
    context.close()


def run_invalid(browser, backend, token, user, jobs):
    job = jobs["invalid"]
    time.sleep(0)
    for _ in range(40):
        import psycopg2
        conn = psycopg2.connect(backend.state["dsn"].replace("postgresql+asyncpg://", "postgresql://"), options="-c search_path=cv_analyzer")
        cur = conn.cursor()
        cur.execute("SELECT criteria_extraction_status, analysis_json IS NULL FROM job_criteria WHERE job_id = %s", (job,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] == "failed":
            break
        time.sleep(1)
    assert row and row[0] == "failed" and row[1] is True, row
    check("Invalid extraction: the row is failed and no document is stored")
    context, page = open_as(browser, backend, "admin", job, token_user=(token, user))
    expect(page.get_by_text("AI criteria analysis failed.")).to_be_visible(timeout=20000)
    check("Invalid extraction: the failed state is visible in the page")
    context.close()


if __name__ == "__main__":
    main()
