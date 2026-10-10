#!/usr/bin/env python3
"""FULL-APPLICATION verification of the requirements-v2 weight controls and validation messages, on the real JobDetails page.

  cd backend && python ../tests/requirementsV2/browser/creation/verify_weight_editor.py [--out DIR]

Real: PostgreSQL with the schema and migrations, Redis, the Celery worker, the FastAPI application (login, job creation, the requirements API and its
validation), the built single-page application, Chromium. The job is created through the real API; the worker stores the document from the
recorded answer (the fake transport). Writes screenshots and a results file to --out. Exit code 77 = prerequisites missing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
try:
    from playwright.sync_api import sync_playwright
    import stack as S
except Exception as exc:                                       # pragma: no cover
    print(f"prerequisites missing: {exc}")
    raise SystemExit(77)

EMAIL = "admin@weights.example"
CASES = {c["id"]: c for c in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((S.BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases").glob("B*.json")))}
RESULTS: list = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)


def seed(db, tenant):
    from auth.password import hash_password
    uid = "a2000000-0000-0000-0000-000000000001"
    db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES (%s,'Weights Tenant','weights.example','active','active','organization')", (tenant,))
    db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,%s,'admin','admin','active')",
         (uid, tenant, EMAIL, hash_password(S.PASSWORD)))
    db.q("UPDATE system_config SET value = 'true' WHERE key = 'requirements_v2.enabled'")
    db.q("UPDATE ai_prompts SET is_active = TRUE WHERE prompt_code = 'criteria_extraction_v2' AND version = 3")


class Page:
    def __init__(self, p, browser_ctx, url, shots, requests):
        self.p, self.ctx, self.url, self.shots, self.requests = p, browser_ctx, url, shots, requests
        self.page = browser_ctx.new_page()
        self.page.on("request", lambda r: requests.append((r.method, r.url, r.post_data)) if "/requirements" in r.url and r.method == "PUT" else None)
        self.page.route(re.compile(r"^https?://(?!127\.0\.0\.1)"), lambda route: route.abort())

    def login(self):
        self.page.goto(f"{self.url}/login")
        self.page.evaluate("() => localStorage.clear()")
        self.page.goto(f"{self.url}/login")
        self.page.wait_for_selector("input[type=email]", timeout=30000)
        self.page.fill("input[type=email]", EMAIL)
        self.page.fill("input[type=password]", S.PASSWORD)
        self.page.locator("form button[type=submit]").first.click()
        self.page.wait_for_function("!!localStorage.getItem('token') && !location.pathname.startsWith('/login')", timeout=30000)

    def open_job(self, job_id, lang="en"):
        self.page.evaluate(f"() => localStorage.setItem('app_lang', '{lang}')")
        self.page.goto(f"{self.url}/jobs/{job_id}")
        self.page.wait_for_selector("[data-testid=requirements-v2]", timeout=40000)
        self.page.wait_for_function("() => !!document.querySelector('[data-testid=save]') || !!document.querySelector('[data-testid=extraction-status]')", timeout=40000)

    def card(self, cat):
        return self.page.locator(f"#req-cat-{cat}")

    def weights(self, cat):
        return [int(v) for v in self.card(cat).locator("input[id^='req-weight-']").evaluate_all("els => els.map(e => e.value)") if v.strip().isdigit()]

    def set_weight(self, cat, index, value):
        field = self.card(cat).locator("input[id^='req-weight-']").nth(index)
        field.fill(str(value))                       # one change, as a paste would make it

    def required_total(self, cat):
        return self.page.locator(f"[data-testid=required-total-{cat}]").inner_text()

    def summary(self):
        return self.page.locator("[data-testid=issue-summary]").inner_text() if self.page.query_selector("[data-testid=issue-summary]") else ""

    def save(self):
        """Click Save and return (status, json body) of the requirements PUT the click sent, or None if it sent nothing."""
        try:
            with self.page.expect_response(lambda r: "/requirements" in r.url and r.request.method == "PUT", timeout=8000) as info:
                self.page.locator("[data-testid=save]").click()
            resp = info.value
            return resp.status, resp.json()
        except Exception:
            return None

    def equalize(self, cat):
        return self.card(cat).get_by_role("button", name=re.compile(r"^Equalize"))

    def shot(self, name):
        path = self.shots / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=False)
        return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(pathlib.Path(tempfile.gettempdir()) / "req_v2_weights_run"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    shots = out / "screenshots"
    stack = S.start(out, seed=seed)
    try:
        # the job is created through the real API; the real worker stores its document from the recorded answer
        token = stack.login_token(EMAIL)
        status, body = stack.post_json("/jobs", {"title": "Senior HR Manager", "description": CASES["B01_en_hr_manager"]["jd"],
                                                  "requirements_format": "v2"}, token)
        check("the job is created through the API as v2", status == 201 and body.get("requirements_format") == "v2", (status, body))
        job = body["job_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            row = stack.db.q("SELECT criteria_extraction_status FROM job_criteria WHERE job_id = %s", (job,))
            if row and row[0][0] == "completed":
                break
            time.sleep(0.5)
        check("the real worker stores the document", row and row[0][0] == "completed", row)

        from playwright.sync_api import sync_playwright as _sp
        with _sp() as p:
            chromium = "/opt/pw-browsers/chromium"
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"], **({"executable_path": chromium} if os.path.exists(chromium) else {}))
            ctx = browser.new_context(viewport={"width": 1280, "height": 960})
            pg = Page(p, ctx, stack.spa, shots, [])
            pg.login()
            pg.open_job(job)
            shots.mkdir(parents=True, exist_ok=True)

            # ── 1. the reported defect, on the real page ──────────────────────────────────────────────────────────────────────────
            pg.card("skills").scroll_into_view_if_needed()
            check("the extracted two Required skills are 50 and 50", pg.weights("skills")[:2] == [50, 50], pg.weights("skills"))
            check("Equalize is disabled while the weights already are the equal split", pg.equalize("skills").is_disabled())
            pg.set_weight("skills", 0, 60)
            check("60 and 50 show 110% in the Required total at once",
                  "110%" in pg.required_total("skills") and "+10" in pg.required_total("skills"), pg.required_total("skills"))
            check("the summary names the Skills Required total and the difference",
                  "Required item weights in Skills total 110%, not 100% (difference +10)." in pg.summary(), pg.summary()[:200])
            check("Equalize is enabled while the weights are not the equal split", pg.equalize("skills").is_enabled())
            shot_a = pg.shot("1-reported-defect-60-50")
            pg.equalize("skills").click()
            check("Equalize restores 50 and 50 in both inputs", pg.weights("skills")[:2] == [50, 50], pg.weights("skills"))
            check("Equalize is disabled once the weights are the equal split", pg.equalize("skills").is_disabled())
            check("Equalize does not save: no request was sent", len(pg.requests) == 0, pg.requests)

            # Save a refused draft: the real server answers with its numbers, and the draft is kept
            pg.set_weight("skills", 0, 60)
            reply = pg.save()
            check("the save reaches the server, which refuses 110%", reply is not None and reply[0] == 422, reply and reply[0])
            issue = reply[1]["detail"]["issues"][0] if reply else {}
            check("the server refusal carries the real rule code", issue.get("code") == "required_weights_total", issue.get("code"))
            server_params = issue.get("params")
            check("the server sends its numbers for the total (total, expected, difference)",
                  server_params == {"total": 110, "expected": 100, "difference": 10}, server_params)
            check("the message names the category and the numbers from the server",
                  "Required item weights in Skills total 110%, not 100% (difference +10)." in pg.summary(), pg.summary()[:200])
            check("the draft is kept after the refusal", pg.weights("skills")[0] == 60)
            check("the affected Required weights are highlighted", pg.card("skills").locator("input[id^='req-weight-']").first.get_attribute("aria-invalid") == "true")

            # ── 2. a typed decimal: kept as typed, named, nothing sent ──────────────────────────────────────────────────────────────
            requests_before = len(pg.requests)
            pg.set_weight("skills", 1, "12.5")
            check("a typed decimal stays in the field", pg.card("skills").locator("input[id^='req-weight-']").nth(1).input_value() == "12.5")
            check("the decimal is named in the summary", "“12.5” is not a whole number" in pg.summary(), pg.summary()[:200])
            sent = pg.save()
            check("saving with a decimal sends nothing", sent is None and len(pg.requests) == requests_before, len(pg.requests) - requests_before)
            shot_b = pg.shot("2-typed-decimal")
            pg.set_weight("skills", 1, 50)

            # ── 3. several problems at once, each with a way to its field ───────────────────────────────────────────────────────────
            pg.set_weight("skills", 0, 60)
            pg.set_weight("skills", 1, "12.5")
            pg.page.locator("#req-cat-experience-w").fill("50")       # the Experience CATEGORY weight: the category total becomes 110
            summary = pg.page.locator("[data-testid=issue-summary]")
            check("several problems are listed together", summary.count() == 1 and "not a whole number" in summary.inner_text()
                  and "Required item weights in Skills" in summary.inner_text() and "Category weights total" in summary.inner_text(), summary.inner_text()[:400])
            check("each problem has a Go to control", summary.get_by_role("button", name="Go to").count() >= 3, summary.get_by_role("button", name="Go to").count())
            shot_c = pg.shot("3-several-problems")
            pg.set_weight("skills", 0, 50); pg.set_weight("skills", 1, 50); pg.page.locator("#req-cat-experience-w").fill("40")
            check("the problems clear when the values are valid again", pg.page.locator("[data-testid=issue-summary]").count() == 0,
                  pg.summary()[:400])

            # ── 4. a valid change is saved for real and survives a reload ────────────────────────────────────────────────────────────
            pg.set_weight("skills", 0, 40); pg.set_weight("skills", 1, 60)
            reply = pg.save()
            check("a valid draft is saved (200)", reply is not None and reply[0] == 200, reply and json.dumps(reply[1])[:400])
            weights_db = stack.db.q("SELECT analysis_json->'requirements'->'categories'->'skills'->'items' FROM job_criteria WHERE job_id = %s", (job,))
            items = weights_db[0][0] if weights_db else []
            if isinstance(items, str):
                items = json.loads(items)
            req = [i["weight"] for i in items["items"] if i["importance"] == "required"] if isinstance(items, dict) else [i["weight"] for i in items if i["importance"] == "required"]
            check("the saved Required weights are stored as entered (40 and 60)", sorted(req)[:2] == [40, 60], req)
            pg.page.reload()
            pg.page.wait_for_selector("[data-testid=requirements-v2]", timeout=30000)
            check("the saved weights are shown after a reload", pg.weights("skills")[:2] == [40, 60], pg.weights("skills"))

            # ── 5. a category with many items: compact, details closed, Equalize still exact ─────────────────────────────────────
            for _ in range(25):
                pg.card("experience").get_by_role("button", name=re.compile(r"^\+ .*[Rr]equired|^\+ Add")).first.click()
            rows = pg.card("experience").locator("li[id^='req-item-']")
            check("a category with many items shows one compact row per item", rows.count() >= 29, rows.count())
            check("details and actions are closed in every row",
                  pg.card("experience").locator("[id^='req-details-'][hidden]").count() == rows.count()
                  and pg.card("experience").locator("[id^='req-actions-'][hidden]").count() == rows.count())
            pg.equalize("experience").click()
            exp_weights = pg.weights("experience")
            check("Equalize splits the Required weights of the large category to 100", sum(w for w in exp_weights) == 100, exp_weights[:5])
            shot_d = pg.shot("4-many-items")

            # ── 6. Arabic: the same totals and messages in Arabic, right to left ─────────────────────────────────────────────────────
            pg.open_job(job, lang="ar")
            pg.set_weight("skills", 0, 60)
            check("the Arabic total says the difference (120% against 100%: +20)", "الفرق +20" in pg.required_total("skills") and "120٪" in pg.required_total("skills"),
                  pg.required_total("skills"))
            check("the Arabic summary names the total", "مجموع أوزان المتطلبات الإلزامية في" in pg.summary(), pg.summary()[:200])
            pg.shot("5-arabic-totals")
            pg.set_weight("skills", 0, 40)

            # ── 7. mobile width: the summary and a category stay usable ─────────────────────────────────────────────────────────────
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            mp = Page(p, mobile, stack.spa, shots, [])
            mp.login()
            mp.open_job(job)
            mp.set_weight("skills", 0, 60)
            overflow = mp.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("no horizontal page scroll at phone width", overflow <= 1, overflow)
            mp.card("skills").scroll_into_view_if_needed()
            mp.shot("6-mobile-skills")
            mobile.close()

            browser.close()
    finally:
        results = {"checks": RESULTS}
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        S.close(stack)
        failed = [r for r in RESULTS if not r[1]]
        print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  ({out / 'results.json'})")
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
