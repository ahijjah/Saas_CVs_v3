#!/usr/bin/env python3
"""FULL-APPLICATION verification of requirements-v2 job creation (real login, real job creation, real worker, real editor).

  cd backend && python ../tests/requirementsV2/browser/creation/verify_creation_flow.py [--out DIR]

What runs for real
  * PostgreSQL 16 (system binaries) with db/schema.sql and every migration (the known pre-existing migration failures are reported by realdb_helper),
  * Redis (system redis-server) as the Celery broker,
  * the REAL Celery worker (`celery worker`) that executes workers.requirements_v2_extraction_worker,
  * the REAL FastAPI application (uvicorn), real JWT and bcrypt logins, real row-level security,
  * the REAL single-page application (vite build, served on its own origin, calling the API by its real URL),
  * Chromium (Playwright) through the real login form and the real "Add New Job" dialog.
Substituted, and only these: the model transport (a fake that replays the recorded v2-2 answers; see fake_provider_runtime.py) and the legacy
criteria task body (a no-op). No provider client can be constructed, so no paid call and no network request to a model provider is possible.
Outside requests (fonts, CDN, analytics) are blocked. Exit code 77 = prerequisites missing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent.parent
BACKEND = REPO / "backend"
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

try:
    from playwright.sync_api import sync_playwright
    import stack as S
except Exception as exc:                                     # pragma: no cover
    print(f"prerequisites missing: {exc}")
    raise SystemExit(77)

T1 = "11111111-1111-1111-1111-111111111111"
PASSWORD = S.PASSWORD
USERS = {"admin": ("a1000000-0000-0000-0000-000000000001", "admin"), "viewer": ("a1000000-0000-0000-0000-000000000003", "viewer")}
EMAIL = {"admin": "admin@creation.example", "viewer": "viewer@creation.example"}
CASES = {c["id"]: c for c in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases").glob("B*.json")))}
RESULTS: list = []
EVIDENCE: dict = {}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)


def seed_creation(db, tenant):
    from auth.password import hash_password
    db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES (%s,'Creation Tenant','creation.example','active','active','organization')", (tenant,))
    for key, (uid, role) in USERS.items():
        db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,%s,%s,%s,'active')",
             (uid, tenant, EMAIL[key], hash_password(PASSWORD), key, role))
    db.q("UPDATE system_config SET value = 'true' WHERE key = 'requirements_v2.enabled'")
    db.q("UPDATE ai_prompts SET is_active = TRUE WHERE prompt_code = 'criteria_extraction_v2' AND version = 3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(pathlib.Path(tempfile.gettempdir()) / "req_v2_creation_run"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    stack = S.start(out, seed=seed_creation)
    check("the stack starts: the API, the Celery worker and the built application", True)   # stack.start() raises if any of them does not start
    try:
        with sync_playwright() as p:
            chromium = "/opt/pw-browsers/chromium"
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"], **({"executable_path": chromium} if os.path.exists(chromium) else {}))
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            requests_out = []
            page.on("request", lambda r: requests_out.append((r.method, r.url, r.post_data)) if r.url.startswith(stack.api) and r.method == "POST" else None)
            page.route(re.compile(r"^https?://(?!127\.0\.0\.1)"), lambda route: route.abort())   # no external requests
            run = Run(p, page, ctx, stack.spa, stack.api, stack.db, requests_out, out)
            try:
                run.scenarios()
            finally:
                browser.close()
    finally:
        (out / "results.json").write_text(json.dumps({"checks": RESULTS, "evidence": EVIDENCE}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        S.close(stack)
        failed = [r for r in RESULTS if not r[1]]
        print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  ({out / 'results.json'})")
    return 1 if failed or not RESULTS else 0


class Run:
    def __init__(self, p, page, ctx, spa, api, db, requests_out, out):
        self.p, self.page, self.ctx, self.spa, self.api, self.db = p, page, ctx, spa, api, db
        self.requests_out, self.out = requests_out, out

    # ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    def login(self, who):
        self.ctx.clear_cookies()
        self.page.goto(f"{self.spa}/login")
        self.page.evaluate("() => { localStorage.clear(); }")       # sign out: drop the stored session, then load the login form afresh
        self.page.goto(f"{self.spa}/login")
        self.page.wait_for_selector("input[type=email]", timeout=30000)
        self.page.fill("input[type=email]", EMAIL[who])
        self.page.fill("input[type=password]", PASSWORD)
        self.page.locator("form button[type=submit]").first.click()
        self.page.wait_for_function("!!localStorage.getItem('token') && !location.pathname.startsWith('/login')", timeout=30000)

    def open_creation(self):
        self.page.goto(f"{self.spa}/jobs")
        self.page.get_by_role("button", name="Add New Job").click()
        self.page.wait_for_selector("input[name=job_title]", timeout=20000)

    def create(self, title, jd, fmt):
        """fmt: 'v2' or 'legacy' (the choice is shown when the switch is on); None when the switch is off (no choice is shown)."""
        self.open_creation()
        self.page.fill("input[name=job_title]", title)
        self.page.fill("textarea[name=job_description]", jd)
        if fmt:
            self.page.locator(f"[data-testid=format-{fmt}]").check()
        self.page.get_by_role("button", name="Create Job").click()
        self.page.wait_for_url(re.compile(r".*/jobs/[0-9a-f-]{36}$"), timeout=30000)
        return self.page.url.rsplit("/", 1)[-1]

    def extraction_states(self, timeout=90):
        """Every distinct state the card shows, in order, until the editor opens or the attempt fails."""
        seen, end = [], time.time() + timeout
        while time.time() < end:
            if self.page.query_selector("[data-testid=save]"):
                seen.append("editor")
                break
            status = self.page.evaluate("() => { const e = document.querySelector('[data-testid=extraction-status]'); return e ? e.getAttribute('data-status') : null; }")
            if status and (not seen or seen[-1] != status):
                seen.append(status)
            if status == "failed":
                break
            self.page.wait_for_timeout(250)
        return seen

    def db_row(self, job):
        return self.db.q("SELECT criteria_extraction_status, requirements_schema_version, requirements_revision, requirements_extraction_token IS NULL, "
                         "analysis_json IS NOT NULL FROM job_criteria WHERE job_id = %s", (job,))

    # ── scenarios ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    def scenarios(self):
        en, ar, pref = CASES["B01_en_hr_manager"], CASES["B07_ar_accountant"], CASES["B04_en_preferred_only"]

        # A. admin, switch on: the choice is offered; v2 creation shows queued -> processing -> completed, then the editor
        self.login("admin")
        self.open_creation()
        choice_visible = self.page.locator("[data-testid=analysis-format]").count() == 1
        check("switch on: the analysis-format choice is shown", choice_visible)
        labels = self.page.locator("[data-testid=analysis-format]").inner_text()
        check("the choice is labelled Standard analysis and Requirements v2", "Standard analysis" in labels and "Requirements v2" in labels, labels[:200])
        self.page.get_by_role("button", name="Cancel").click()

        job_en = self.create("Senior HR Manager (v2)", "[[SLOW-6]]\n" + en["jd"], "v2")
        states = self.extraction_states()
        EVIDENCE["english_v2_states"] = states
        check("v2 creation shows queued/processing, then the editor", "editor" in states and any(s in states for s in ("pending", "processing")), states)
        check("the editor shows the category total for the saved requirements", self.page.locator("[data-testid=category-total]").count() == 1)
        check("the editor says that evaluation is not available yet", self.page.locator("[data-testid=evaluation-unavailable]").count() == 1)
        row = self.db_row(job_en)[0]
        check("the database row is a v2 job with the new schema marker", row[1] == 2 and row[0] == "completed", row)
        posted = [json.loads(b) for (m, u, b) in self.requests_out if u.endswith("/jobs") and b and "requirements_format" in b]
        check("the creation request carried requirements_format=v2", bool(posted) and posted[-1].get("requirements_format") == "v2",
              posted[-1:] if posted else "none")
        # the Required/Preferred items are per item inside a category
        body = self.page.inner_text("[data-testid=requirements-v2]")
        check("Required and Preferred items appear inside the editor", "Required" in body and "Preferred" in body)
        EVIDENCE["english_job"] = job_en

        # B. Arabic v2 job: the editor opens right-to-left with Arabic items
        job_ar = self.create("محاسب (v2)", ar["jd"], "v2")
        states_ar = self.extraction_states()
        check("Arabic v2 job completes and opens the editor", "editor" in states_ar, states_ar)
        item_values = self.page.eval_on_selector_all("[data-testid=requirements-v2] input, [data-testid=requirements-v2] textarea",
                                                     "els => els.map(e => e.value)")
        check("the Arabic job's items are shown in the editor", any(any("\u0600" <= ch <= "\u06ff" for ch in v) for v in item_values), item_values[:3])
        EVIDENCE["arabic_job"] = job_ar

        # C. failure, retry and a viewer: one attempt fails (terminal authentication error); the admin requests a new attempt
        job_fail = self.create("Failing role (v2)", "[[SLOW-6]][[FAIL-ONCE]]\n" + pref["jd"], "v2")
        states_fail = self.extraction_states()
        EVIDENCE["failure_states"] = states_fail
        check("a failed attempt shows processing then failed", "failed" in states_fail and "processing" in states_fail, states_fail)
        check("the failure shows its reason", "model call failed" in self.page.inner_text("[data-testid=extraction-error]").lower()
              if self.page.query_selector("[data-testid=extraction-error]") else False)
        check("an administrator sees the retry button", self.page.query_selector("[data-testid=extraction-retry]") is not None)
        self.page.click("[data-testid=extraction-retry]")
        self.page.wait_for_function("() => { const e = document.querySelector('[data-testid=extraction-status]'); return !e || e.getAttribute('data-status') !== 'failed'; }", timeout=30000)
        states_retry = self.extraction_states()
        EVIDENCE["retry_states"] = states_retry
        check("the retry runs and opens the editor", "editor" in states_retry, states_retry)
        pref_row = self.db.q("SELECT weight_skills + weight_experience + weight_education + weight_certifications + weight_soft_skills + "
                             "weight_domain_knowledge + weight_other, requirements_schema_version FROM job_criteria WHERE job_id = %s", (job_fail,))[0]
        check("the retried preferred-only job is stored with zero weights (v2 marker)", pref_row == (0, 2), pref_row)
        model_calls = [json.loads(l) for l in (self.out / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()]
        fail_calls = [c for c in model_calls if c.get("outcome", "").startswith("auth error")]
        check("the failed attempt made one model call and was not retried automatically", len(fail_calls) == 1, len(fail_calls))
        EVIDENCE["model_calls_total"] = len(model_calls)

        # the viewer sees the same job (with no retry button, and no edit)
        self.login("viewer")
        self.page.goto(f"{self.spa}/jobs/{job_fail}")  # the viewer's session is a real login, not a shared one
        self.page.wait_for_selector("[data-testid=requirements-v2]", timeout=30000)
        check("a viewer opens the job and sees the editor read-only", self.page.query_selector("[data-testid=save]") is None)
        check("a viewer sees no retry button", self.page.query_selector("[data-testid=extraction-retry]") is None)

        # D. switch OFF: the existing creation screen; the legacy workflow is unchanged
        self.db.q("UPDATE system_config SET value = 'false' WHERE key = 'requirements_v2.enabled'")
        self.login("admin")
        self.open_creation()
        check("switch off: the analysis-format choice is not shown", self.page.locator("[data-testid=analysis-format]").count() == 0)
        self.page.get_by_role("button", name="Cancel").click()
        job_legacy = self.create("Legacy role", en["jd"], None)
        self.page.wait_for_selector("text=Skills Analysis", timeout=30000)
        row = self.db_row(job_legacy)[0]
        check("switch off: creation is the legacy workflow (no schema marker, the legacy section shown)",
              row[1] is None and self.page.query_selector("[data-testid=requirements-v2]") is None, row)
        posted = [json.loads(b) for (m, u, b) in self.requests_out if u.endswith("/jobs") and b and json.loads(b).get("title") == "Legacy role"]
        check("switch off: the creation request carries no requirements_format", bool(posted) and "requirements_format" not in posted[-1], posted[-1:] if posted else "none")
        check("switch off: the legacy job is listed with the legacy workflow", row[0] in ("pending", "processing", "completed", "failed", "insufficient"), row)
        self.db.q("UPDATE system_config SET value = 'true' WHERE key = 'requirements_v2.enabled'")

        # E. switch ON again with the legacy choice: a legacy job, no v2 payload
        self.open_creation()
        check("switch back on: the choice is shown again", self.page.locator("[data-testid=analysis-format]").count() == 1)
        job_legacy2 = self.create("Legacy role (chosen)", en["jd"], "legacy")
        self.page.wait_for_selector("text=Skills Analysis", timeout=30000)
        row = self.db_row(job_legacy2)[0]
        check("legacy chosen while on: a legacy job, no schema marker", row[1] is None, row)
        posted = [json.loads(b) for (m, u, b) in self.requests_out if u.endswith("/jobs") and b and json.loads(b).get("title") == "Legacy role (chosen)"]
        check("legacy chosen while on: no requirements_format in the request", bool(posted) and "requirements_format" not in posted[-1], posted[-1:] if posted else "none")

        # the audit row of an attempt records the prompt version and the live policy
        audit = self.db.q("SELECT details FROM audit_logs WHERE action = 'requirements_extraction_completed' AND resource_id = %s ORDER BY created_at DESC LIMIT 1", (job_en,))
        details = audit[0][0] if audit else {}
        if isinstance(details, str):
            details = json.loads(details)
        check("the english attempt's audit records prompt version, hash and the policy", details.get("prompt_version") == 3 and details.get("prompt_sha256")
              and details.get("acknowledgment_policy") is True, details)
        EVIDENCE["english_audit"] = {k: details.get(k) for k in ("prompt_version", "prompt_label", "prompt_sha256", "requested_model", "returned_model", "settings", "acknowledgment_policy")}


if __name__ == "__main__":
    sys.exit(main())
