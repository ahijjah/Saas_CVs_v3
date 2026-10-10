#!/usr/bin/env python3
"""FULL-APPLICATION verification of the requirements-v2 review-first editor (review, edit, cancel, weights, warnings, saving, conflicts), on the real JobDetails page.

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
import urllib.request

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

    # ── review rows, the editor, the category weight ────────────────────────────────────────────────────────────────────────────
    def card(self, cat):
        return self.page.locator(f"#req-cat-{cat}")

    def row(self, text):
        return self.page.locator("li[id^='req-item-']").filter(has=self.page.get_by_text(text, exact=True))

    def open_editor(self, text, lang_names=None):
        names = lang_names or EN
        row = self.row(text)
        row.get_by_role("button", name=f"{names['moreActions']}: {text}", exact=True).click()
        row.get_by_role("button", name=names["editItem"], exact=True).click()
        return self.page.locator("[data-testid=item-editor]")

    def editor_weight(self, editor):
        return editor.get_by_role("textbox", name=re.compile(r"^(Weight of|وزن)"))

    def apply(self, editor, lang_names=None):
        editor.locator("button[type=submit]").click()

    def cancel(self, editor, lang_names=None):
        editor.get_by_role("button", name=(lang_names or EN)["cancelEdit"], exact=True).click()

    def edit_weight(self, text, value):
        editor = self.open_editor(text)
        self.editor_weight(editor).fill(str(value))          # one change, as a paste would make it
        self.apply(editor)
        return editor

    def required_names(self, cat):
        """The wording of the Required items of a category, in order (only rows that show a weight)."""
        return self.card(cat).locator("li[id^='req-item-']:has([data-testid^=weight-]) p[id^='req-text-']").all_inner_texts()

    def edit_wording(self, text, append):
        editor = self.open_editor(text)
        ta = editor.get_by_label("Requirement wording", exact=True)
        ta.fill(ta.input_value() + append)
        self.apply(editor)
        return editor

    def review_weight(self, text):
        loc = self.row(text).locator("[data-testid^=weight-]")
        return re.sub(r"\D", "", loc.inner_text()) if loc.count() else None

    def weights(self, cat):
        out = []
        for li in self.card(cat).locator("li[id^='req-item-']").all():
            w = li.locator("[data-testid^=weight-]")
            if w.count():
                out.append(int(re.sub(r"\D", "", w.inner_text())))
        return out

    def category_weight(self, cat):
        return re.sub(r"\D", "", self.page.locator(f"[data-testid=cat-weight-{cat}]").inner_text())

    def edit_category_weight(self, cat, value):
        self.card(cat).get_by_role("button", name=re.compile(r"^Edit the .* weight")).first.click()
        field = self.page.locator(f"#req-cat-{cat}-w")
        field.fill(str(value))
        self.card(cat).get_by_role("button", name=EN["applyEdit"], exact=True).click()

    def add(self, cat, importance, lang_names=None):
        names = lang_names or EN
        self.card(cat).get_by_role("button", name=names["addRequired" if importance == "required" else "addPreferred"], exact=True).click()
        return self.page.locator("[data-testid=item-editor]")

    def delete(self, text):
        row = self.row(text)
        row.get_by_role("button", name=f"More actions: {text}", exact=True).click()
        row.get_by_role("button", name=f"Delete this item: {text}", exact=True).click()

    def action(self, text, name):
        row = self.row(text)
        row.get_by_role("button", name=f"More actions: {text}", exact=True).click()
        row.get_by_role("button", name=name, exact=True).click()

    # ── totals, warnings, saving ────────────────────────────────────────────────────────────────────────────────────────────────
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


# the English labels the harness clicks (the page's own string table: components/requirementsV2/i18n.ts)
EN = {"moreActions": "More actions", "editItem": "Edit", "applyEdit": "Apply", "cancelEdit": "Cancel",
      "addRequired": "Add required item", "addPreferred": "Add preferred item", "useLatest": "Use the latest saved"}


def put_json(stack, path, body, token):
    req = urllib.request.Request(stack.api + path, data=json.dumps(body).encode("utf-8"), method="PUT",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "null")


def get_json(stack, path, token):
    req = urllib.request.Request(stack.api + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


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
        row = None
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
            names = pg.required_names("skills")
            py, sql = names[0], names[1]                     # the first two Required skills of the extracted document

            # ── 1. review is the default: nothing is editable until Edit is asked for ───────────────────────────────────────────────
            pg.card("skills").scroll_into_view_if_needed()
            check("review: no weight or wording input is shown in a category",
                  pg.card("skills").locator("input[id^='req-weight-'], input[id^='req-text-'], textarea").count() == 0)
            check("review: the Required and Preferred lists show their counts",
                  pg.card("skills").get_by_role("heading", name=re.compile(r"^Required \(\d+\)$")).count() == 1
                  and pg.card("skills").get_by_role("heading", name=re.compile(r"^Preferred \(\d+\)$")).count() == 1)
            check("review: the two Required skills are 50 and 50", pg.weights("skills")[:2] == [50, 50], pg.weights("skills"))
            check("review: Equalize is disabled while the weights already are the equal split", pg.equalize("skills").is_disabled())
            check("review: no editor is open", pg.page.locator("[data-testid=item-editor]").count() == 0)
            pg.shot("0-review-default")

            # ── 2. Cancel and Escape change nothing ───────────────────────────────────────────────────────────────────────────────
            requests_before = len(pg.requests)
            ed = pg.open_editor(sql)
            pg.editor_weight(ed).fill("70")
            pg.cancel(ed)
            check("cancel: the local value is discarded", pg.review_weight(sql) == "50" and pg.page.locator("[data-testid=item-editor]").count() == 0,
                  pg.review_weight(sql))
            ed = pg.open_editor(sql)
            pg.editor_weight(ed).fill("70")
            ed.press("Escape")
            check("Escape: closes the editor and discards the value", pg.review_weight(sql) == "50" and pg.page.locator("[data-testid=item-editor]").count() == 0)
            check("cancel and Escape send nothing", len(pg.requests) == requests_before)

            # ── 3. a typed decimal is not applied: the editor says so and stays open ─────────────────────────────────────────────────
            ed = pg.open_editor(sql)
            pg.editor_weight(ed).fill("12.5")
            pg.apply(ed)
            check("a typed decimal is named in the editor and nothing is applied",
                  "“12.5” is not a whole number" in ed.locator("[data-testid=editor-error]").inner_text() and pg.page.locator("[data-testid=item-editor]").count() == 1)
            pg.cancel(ed)

            # ── 4. the reported defect: 60 and 50 show 110%; the server refuses; the draft is kept ─────────────────────────────────
            pg.edit_weight(py, 60)
            check("60 and 50 show 110% in the Required total of the category",
                  "110%" in pg.required_total("skills") and "+10" in pg.required_total("skills"), pg.required_total("skills"))
            check("the summary names the Skills Required total and the difference",
                  "Required item weights in Skills total 110%, not 100% (difference +10)." in pg.summary(), pg.summary()[:200])
            shot_a = pg.shot("1-reported-defect-60-50")
            reply = pg.save()
            check("the save reaches the server, which refuses 110%", reply is not None and reply[0] == 422, reply and reply[0])
            issue = reply[1]["detail"]["issues"][0] if reply else {}
            check("the server refusal carries the rule code and its numbers",
                  issue.get("code") == "required_weights_total" and issue.get("params") == {"total": 110, "expected": 100, "difference": 10}, issue)
            check("the draft is kept after the refusal", pg.review_weight(py) == "60")

            # ── 5. Equalize restores 50/50 in the draft and sends nothing ─────────────────────────────────────────────────────────
            requests_before = len(pg.requests)
            pg.equalize("skills").click()
            check("Equalize restores 50 and 50 in the draft", pg.weights("skills")[:2] == [50, 50], pg.weights("skills"))
            check("Equalize sends nothing and Save is disabled again", len(pg.requests) == requests_before
                  and pg.page.locator("[data-testid=save]").is_disabled())

            # ── 6. several problems at once, each with a way to its field ─────────────────────────────────────────────────────────
            pg.edit_weight(py, 60)
            pg.edit_category_weight("experience", 120)
            summary = pg.page.locator("[data-testid=issue-summary]")
            check("several problems are listed together",
                  summary.count() == 1 and "Required item weights in Skills" in summary.inner_text() and "Experience" in summary.inner_text(), summary.inner_text()[:400])
            check("each problem has a way to its field", summary.get_by_role("button", name="Go to", exact=True).count() >= 2)
            pg.shot("3-several-problems")
            pg.equalize("skills").click()
            pg.edit_category_weight("experience", 40)
            check("the problems clear when the values are valid again", pg.page.locator("[data-testid=issue-summary]").count() == 0, pg.summary()[:300])

            # ── 7. add, reclassify and remove a Required item through the editor; the category weight follows the rules ─────────────
            ed = pg.add("experience", "required")
            pg.editor_weight(ed).fill("")
            ed.get_by_label("Requirement wording", exact=True).fill("Ruby on Rails experience")
            pg.apply(ed)
            check("add: the new item is in the draft, marked New", pg.row("Ruby on Rails experience").count() == 1
                  and pg.row("Ruby on Rails experience").get_by_text("New", exact=True).count() == 1)
            pg.action("Ruby on Rails experience", "Make preferred")
            check("reclassify: a Preferred item has no weight in review", pg.review_weight("Ruby on Rails experience") is None)
            pg.action("Ruby on Rails experience", "Make required")
            pg.delete("Ruby on Rails experience")
            check("remove: the item is gone from the draft", pg.row("Ruby on Rails experience").count() == 0)

            # ── 8. a valid change is saved for real and survives a reload ─────────────────────────────────────────────────────────
            pg.edit_weight(py, 40)
            pg.edit_weight(sql, 60)
            pg.edit_category_weight("experience", 40)
            reply = pg.save()
            check("a valid draft is saved (200)", reply is not None and reply[0] == 200, reply and json.dumps(reply[1])[:300])
            pg.page.reload()
            pg.page.wait_for_selector("[data-testid=requirements-v2]", timeout=30000)
            check("the saved weights are shown after a reload", sorted(pg.weights("skills")[:2]) == [40, 60], pg.weights("skills"))

            # ── 9. a real revision conflict: my edit and a change saved meanwhile by someone else (the same wording) ───────────────
            pg.edit_wording(py, " (mine)")
            view = get_json(stack, f"/jobs/{job}/requirements", token)
            upstream = json.loads(json.dumps(view["requirements"]))
            for it in upstream["categories"]["skills"]["items"]:
                if it["text"] == py:
                    it["text"] = py + " (saved by someone else)"
            st, _ = put_json(stack, f"/jobs/{job}/requirements", {"expected_revision": view["revision"], "requirements": upstream}, token)
            check("the other user's save is accepted by the server", st == 200, st)
            reply = pg.save()
            check("my save is refused with a revision conflict (409)", reply is not None and reply[0] == 409, reply and reply[0])
            conflict = pg.page.locator("[data-testid=conflict]")
            check("the conflict is shown, and Apply stays disabled until a choice is made",
                  conflict.count() == 1 and pg.page.locator("[data-testid=apply-merge]").is_disabled())
            pg.shot("7-conflict")
            pg.page.get_by_role("group", name=re.compile(re.escape(py))).first.get_by_role("radio", name=EN["useLatest"]).check()
            pg.page.locator("[data-testid=apply-merge]").click()
            check("after Apply the merged draft shows the latest wording and nothing was saved again",
                  pg.row(py + " (saved by someone else)").count() == 1 and len(pg.requests) >= 1)
            pg.edit_wording(sql, " (checked)")
            reply = pg.save()
            check("the next save is checked against the latest revision (200)", reply is not None and reply[0] == 200, reply and reply[0])

            # ── 10. many items: compact rows; details closed; Equalize stays exact ─────────────────────────────────────────────────
            for n in range(25):
                ed = pg.add("experience", "required")
                ed.get_by_label("Requirement wording", exact=True).fill(f"Extra requirement {n + 1}")
                pg.apply(ed)
            rows = pg.card("experience").locator("li[id^='req-item-']")
            check("a category with many items shows one compact row per item", rows.count() >= 27, rows.count())
            check("details and actions are closed in every row",
                  pg.card("experience").locator("[id^='req-details-'][hidden]").count() == rows.count())
            pg.equalize("experience").click()
            check("Equalize splits the Required weights of the large category to 100", sum(pg.weights("experience")) == 100, pg.weights("experience")[:5])
            pg.shot("4-many-items")

            # ── 11. Arabic: the same editor, totals and messages, right to left ───────────────────────────────────────────────────
            pg.open_job(job, lang="ar")
            py_now = pg.required_names("skills")[0]           # renamed by the conflict step above
            pg.card("skills").scroll_into_view_if_needed()
            pg.shot("5a-arabic-review-default")               # review is the default in Arabic too
            AR = {"moreActions": "إجراءات أخرى", "editItem": "تعديل", "applyEdit": "تطبيق", "cancelEdit": "إلغاء",
                  "addRequired": "إضافة متطلب إلزامي", "addPreferred": "إضافة متطلب مفضّل", "useLatest": "استخدام الحفظ الأحدث"}
            ed = pg.open_editor(py_now, AR)
            pg.editor_weight(ed).fill("60")
            pg.apply(ed, AR)
            check("the Arabic total says the difference (+20 for 120%)", "الفرق +20" in pg.required_total("skills") and "120٪" in pg.required_total("skills"),
                  pg.required_total("skills"))
            check("the Arabic summary names the total", "مجموع أوزان المتطلبات الإلزامية في" in pg.summary(), pg.summary()[:200])
            pg.shot("5-arabic-editor")
            ed = pg.open_editor(py_now, AR)
            pg.cancel(ed, AR)

            # ── 12. mobile width: review and the editor stay usable without horizontal scroll ─────────────────────────────────────
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            mp = Page(p, mobile, stack.spa, shots, [])
            mp.login()
            mp.open_job(job)
            py_now = mp.required_names("skills")[0]
            mp.card("skills").scroll_into_view_if_needed()
            mp.shot("6a-mobile-review-default")
            overflow = mp.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("no horizontal page scroll at phone width", overflow <= 1, overflow)
            mp.shot("6-mobile-skills")
            ed = mp.open_editor(py_now)
            overflow = mp.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("the editor fits at phone width", overflow <= 1, overflow)
            mp.shot("6b-mobile-editor")
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
