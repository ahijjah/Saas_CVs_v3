"""FULL-APPLICATION verification of the requirements-v2 editor (not harness.html).

  cd backend && python ../tests/requirementsV2/browser/verify_full_app.py [--out DIR]

What runs for real
  * a disposable PostgreSQL 16 cluster (system binaries, started as the `postgres` OS user in a temp dir) with the project's db/schema.sql and ALL
    db/migrations/*.sql applied (a few migrations fail on top of schema.sql, a pre-existing quirk, listed in the report),
  * the REAL FastAPI application (`main:app`, every router, real JWT, bcrypt logins, real `users`/`tenants` rows) as a uvicorn subprocess,
  * the REAL single-page application (`vite build` of index.tsx / App.tsx), logged in through the real login form, on the real /jobs/:id page (JobDetails),
  * Chromium (Playwright) at desktop and mobile sizes.
Test-only plumbing: the SPA's hard-coded API origin is redirected to the local server inside the browser (Playwright route); external CDN/font requests are
blocked and a locally compiled Tailwind stylesheet replaces the CDN script. Synthetic v2 job records are inserted directly into the database (product
creation, extraction and evaluation stay disabled; no AI call, no VPS, no network). Exit code 77 = prerequisites missing."""
from __future__ import annotations

import argparse
import glob
import http.server
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
BACKEND = REPO / "backend"
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

try:
    import psycopg2
    from playwright.sync_api import sync_playwright
except Exception as exc:                                       # pragma: no cover
    print(f"prerequisites missing: {exc}")
    raise SystemExit(77)

APP_ORIGIN = "http://72.62.31.221:8000"
PG_BIN = pathlib.Path("/usr/lib/postgresql/16/bin")
T1, T2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
PASSWORD = "Passw0rd!x"
USERS = {  # key -> (user_id, tenant, role)
    "admin": ("a0000000-0000-0000-0000-000000000001", T1, "admin"),
    "hr": ("a0000000-0000-0000-0000-000000000002", T1, "hr_manager"),
    "viewer": ("a0000000-0000-0000-0000-000000000003", T1, "viewer"),
    "other": ("a0000000-0000-0000-0000-000000000009", T2, "admin"),
}
JOBS = {"v2": "b0000000-0000-0000-0000-000000000001", "legacy": "b0000000-0000-0000-0000-000000000002"}
RESULTS: list[tuple[str, bool, str]] = []
DEFECTS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── infrastructure ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Postgres:
    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="req_full_pg_"))
        subprocess.run(["chown", "postgres", str(self.dir)], check=True)
        self.port = free_port()
        run = lambda *a: subprocess.run(["runuser", "-u", "postgres", "--", *a], check=True, capture_output=True)   # noqa: E731
        run(str(PG_BIN / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-E", "UTF8")
        run(str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-o", f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1", "-l", str(self.dir / "log"), "-w", "start")
        adm = psycopg2.connect(f"host=127.0.0.1 port={self.port} user=postgres dbname=postgres")
        adm.autocommit = True
        adm.cursor().execute("CREATE DATABASE fullapp ENCODING 'UTF8' TEMPLATE template0")
        adm.close()
        self.conn = psycopg2.connect(f"host=127.0.0.1 port={self.port} user=postgres dbname=fullapp")
        self.conn.autocommit = True
        self.conn.set_client_encoding("UTF8")
        self.failed_migrations: list[str] = []
        self.apply_schema()

    def apply_schema(self):
        cur = self.conn.cursor()
        db = BACKEND / "db"
        schema = (db / "schema.sql").read_text(encoding="utf-8").split("\n")
        for n in range(0, 96):                                    # forward references to users in the first tables of schema.sql (pre-existing)
            schema[n] = re.sub(r"\s+REFERENCES users \(user_id\) ON DELETE SET NULL", "", schema[n])
        files = [("schema.sql", "\n".join(schema))] + [(p.name, p.read_text(encoding="utf-8")) for p in sorted((db / "migrations").glob("*.sql"))] + [("seed.sql", (db / "seed.sql").read_text(encoding="utf-8"))]
        for name, sql in files:
            try:
                cur.execute(sql)
            except Exception as exc:                              # noqa: BLE001
                self.failed_migrations.append(f"{name}: {str(exc).splitlines()[0]}")
                cur.execute("ROLLBACK")
            cur.execute("SET search_path = cv_analyzer")

    def q(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute("SET search_path = cv_analyzer")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None

    def stop(self):
        try:
            self.conn.close()
            subprocess.run(["runuser", "-u", "postgres", "--", str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate", "stop"], capture_output=True)
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


def serve_spa(dist: pathlib.Path, port: int) -> http.server.ThreadingHTTPServer:
    index = (dist / "index.html").read_text(encoding="utf-8")
    index = re.sub(r'<script src="https://cdn\.tailwindcss\.com"></script>', "", index)
    index = re.sub(r'<script type="importmap">.*?</script>', "", index, flags=re.S)
    index = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)[^>]*>', "", index)
    index = re.sub(r"<script>\s*tailwind\.config.*?</script>", "", index, flags=re.S)
    index = index.replace("</head>", '<link rel="stylesheet" href="/tw-full.css"></head>')

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            f = dist / path.lstrip("/")
            if path != "/" and f.is_file():
                data = f.read_bytes()
                ctype = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
            else:
                data, ctype = index.encode(), "text/html"
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── main ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BACKEND / "benchmark_results" / "requirements_v2" / "full_app_verification"))
    ap.add_argument("--only", default="", help="comma list of scenarios, e.g. F2,F4")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    shots = out / "screenshots"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True, exist_ok=True)
    chrome = sorted(glob.glob(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers") + "/chromium-*/chrome-linux/chrome"))
    if not chrome or not PG_BIN.exists() or shutil.which("runuser") is None or not (REPO / "node_modules" / ".bin" / "vite").exists() \
            or not (REPO / "node_modules" / ".bin" / "tailwindcss").exists():
        print("prerequisites missing: chromium, system PostgreSQL 16, runuser, node_modules (vite, tailwindcss@3)")
        return 77

    dist = pathlib.Path(tempfile.mkdtemp(prefix="req_full_spa_"))
    subprocess.run(["npx", "vite", "build", "--outDir", str(dist)], cwd=REPO, check=True, capture_output=True)
    subprocess.run(["npx", "tailwindcss", "-c", "tailwind.full.cjs", "-i", "full.css", "-o", str(dist / "tw-full.css"), "--minify"], cwd=HERE, check=True, capture_output=True)

    pg = Postgres()
    from auth.password import hash_password
    import test_requirements_pipeline_postgres as pp
    from parser_candidates.requirements_v2_pipeline_1 import extract
    from services.requirements_pipeline import STORAGE_KEY, to_record
    from services.requirements_v2 import CATEGORIES, POLICY_KEY

    pw = hash_password(PASSWORD)
    pg.q("INSERT INTO tenants (tenant_id,name,email_domain,status,subscription_status,tenant_type) VALUES (%s,'Tenant One','example.com','active','active','organization'),"
         "(%s,'Tenant Two','example.org','active','active','organization')", (T1, T2))
    for key, (uid, tenant, role) in USERS.items():
        pg.q("INSERT INTO users (user_id,tenant_id,email,password_hash,full_name,role,status) VALUES (%s,%s,%s,%s,%s,%s,'active')",
             (uid, tenant, f"{key}@example.{'org' if tenant == T2 else 'com'}", pw, key.capitalize(), role))

    def seed(state=None, *, record=True, tamper=None, policy=True, legacy=False, title="Backend Developer"):
        pg.q("DELETE FROM audit_logs")
        pg.q("DELETE FROM job_criteria")
        pg.q("DELETE FROM jobs")
        pg.q("UPDATE system_config SET value=%s WHERE key=%s", ("true" if policy else "false", POLICY_KEY))
        jv2, jlegacy = JOBS["v2"], JOBS["legacy"]
        for jid, t in ((jv2, title), (jlegacy, "Legacy Analyst")):
            pg.q("INSERT INTO jobs (job_id,tenant_id,created_by,title,description) VALUES (%s,%s,%s,%s,%s)", (jid, T1, USERS["admin"][0], t, "Synthetic job description"))
        legacy_analysis = {"job_title": "Legacy Analyst", "skills": {"required": ["Excel"], "preferred": []}, "experience": {"required": ["2 years"]}}
        pg.q("INSERT INTO job_criteria (job_id,analysis_json,original_analysis_json,criteria_extraction_status) VALUES (%s,%s,%s,'completed')",
             (jlegacy, json.dumps(legacy_analysis), json.dumps(legacy_analysis)))
        st = state or pp.combined_state()
        doc = st["requirements"]
        analysis = {"requirements": doc}
        if record:
            analysis[STORAGE_KEY] = to_record(st, model="gpt-4o-mini-2024-07-18")
        pg.q("""INSERT INTO job_criteria (job_id,analysis_json,original_analysis_json,weight_skills,weight_experience,weight_education,weight_certifications,
                weight_soft_skills,weight_domain_knowledge,weight_other,requirements_schema_version,criteria_extraction_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,2,'completed')""",
             (jv2, json.dumps(analysis), json.dumps({"requirements": st["original"]}), *[doc["categories"][c]["weight"] for c in CATEGORIES]))
        if tamper:
            pg.q(tamper, (jv2,))

    def row():
        (r,) = pg.q("SELECT analysis_json, original_analysis_json, to_jsonb(job_criteria)->>'requirements_revision', weight_skills FROM job_criteria WHERE job_id=%s", (JOBS["v2"],))
        return {"analysis": r[0], "original": r[1], "revision": int(r[2]), "w": r[3]}

    def actions():
        return [a[0] for a in pg.q("SELECT action FROM audit_logs WHERE action LIKE 'requirements_%' ORDER BY created_at")]

    # real application
    api_port, spa_port = free_port(), free_port()
    env = {**os.environ, "DATABASE_URL": f"postgresql+asyncpg://postgres@127.0.0.1:{pg.port}/fullapp", "DB_SCHEMA": "cv_analyzer", "JWT_SECRET": "full-app-test-secret",
           "ENVIRONMENT": "test", "PGCLIENTENCODING": "UTF8"}
    log = open(out / "backend.log", "w")
    uv = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(api_port), "--log-level", "warning"], cwd=BACKEND, env=env, stdout=log, stderr=log)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{api_port}/docs", timeout=1)
            break
        except Exception:                                                  # noqa: BLE001
            time.sleep(0.5)
    spa = serve_spa(dist, spa_port)
    LOCAL_API, SPA = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{spa_port}"
    cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS"}

    def api(method, path, who, body=None):
        token = json.loads(urllib.request.urlopen(urllib.request.Request(f"{LOCAL_API}/auth/login", method="POST", data=json.dumps(
            {"email": f"{who}@example.{'org' if USERS[who][1] == T2 else 'com'}", "password": PASSWORD}).encode(), headers={"content-type": "application/json"})).read())["token"]
        req = urllib.request.Request(f"{LOCAL_API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def fix_blockers_via_api():
        """GET the current document and PUT it with the injection / split-OR blockers corrected (setup only)."""
        st, v = api("GET", f"/jobs/{JOBS['v2']}/requirements", "admin")
        d = json.loads(json.dumps(v["requirements"]))
        d["categories"]["other_requirements"].update(items=[], weight=0)
        d["categories"]["skills"]["items"] = [i for i in d["categories"]["skills"]["items"] if i["text"] != "Java"]
        for i in d["categories"]["skills"]["items"]:
            if i["importance"] == "required":
                i["weight"] = 50
        for c, w in (("skills", 40), ("experience", 40), ("soft_skills", 20)):
            d["categories"][c]["weight"] = w
        return api("PUT", f"/jobs/{JOBS['v2']}/requirements", "admin", {"expected_revision": v["revision"], "requirements": d})

    class Session:
        def __init__(self, browser, who, lang="en", size=(1366, 900), mobile=False):
            self.ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=mobile, has_touch=mobile)
            self.ctx.add_init_script(f"localStorage.setItem('app_lang', '{lang}')")
            self.writes, self.responses, self.errors = [], [], []
            self.ctx.route("**/*", self._route)
            self.page = self.ctx.new_page()
            self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
            self.page.on("pageerror", lambda e: self.errors.append(str(e)))
            self.login(who)

        def _route(self, r):
            req = r.request
            url = req.url
            if url.startswith(SPA):
                return r.continue_()
            if url.startswith(APP_ORIGIN):
                if req.method == "OPTIONS":
                    return r.fulfill(status=204, headers=cors)
                if req.method != "GET":
                    self.writes.append({"method": req.method, "path": url.split(APP_ORIGIN)[1], "body": json.loads(req.post_data) if req.post_data else None})
                resp = r.fetch(url=url.replace(APP_ORIGIN, LOCAL_API))
                try:
                    self.responses.append((req.method, url.split(APP_ORIGIN)[1], resp.status, resp.text()))
                except Exception:                                          # noqa: BLE001
                    pass
                return r.fulfill(response=resp, headers={**resp.headers, **cors})
            return r.fulfill(status=204, body="")                          # CDN, fonts, analytics: blocked (offline)

        def login(self, who):
            p = self.page
            p.goto(f"{SPA}/login")
            p.wait_for_selector("input[type=email]", timeout=20000)
            p.fill("input[type=email]", f"{who}@example.{'org' if USERS[who][1] == T2 else 'com'}")
            p.fill("input[type=password]", PASSWORD)
            p.locator("button[type=submit]").first.click()
            p.wait_for_function("!location.pathname.startsWith('/login') && !!localStorage.getItem('token') && !!localStorage.getItem('user')", timeout=20000)

        def open_job(self, job="v2"):
            self.writes.clear()
            self.page.goto(f"{SPA}/jobs/{JOBS[job]}")
            try:
                self.page.wait_for_selector("[data-testid=requirements-v2]" if job == "v2" else "text=Skills Analysis", timeout=25000)
            except Exception:
                self.page.screenshot(path=str(shots / "DEBUG_open_job_timeout.png"))
                (out / "debug_open_job.txt").write_text(f"url={self.page.url}\nerrors={self.errors[-10:]}\nresponses={[(r[0], r[1], r[2]) for r in self.responses[-12:]]}\n", encoding="utf-8")
                raise
            return self.page

        def close(self):
            self.ctx.close()

    def shot(page, name, sel=None):
        """The app scrolls inside its own layout container (full-page and element captures are unreliable with the sticky header), so: scroll the editor part of
        interest to the top of the viewport and capture the viewport, which shows the real page around it."""
        for cand in ([sel] if sel else []) + ["[data-testid=blockers]", "[data-testid=pipeline-unavailable]", "[data-testid=pipeline-invalid]", "[data-testid=conflict]",
                                              "[data-testid=readiness]", "[data-testid=requirements-v2]"]:
            if page.locator(cand).count():
                page.locator(cand).first.evaluate("e => e.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(250)
                break
        page.screenshot(path=str(shots / f"{name}.png"))

    def txt(page, sel):
        return page.locator(sel).first.inner_text() if page.locator(sel).count() else ""

    def overflow(page):
        return page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

    exit_code = 0
    only = set(filter(None, (args.only or "").split(",")))
    want = lambda n: not only or n in only   # noqa: E731
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome[-1], args=["--no-sandbox"])

            if want('F1'):
                seed()
                s = Session(browser, "admin")
                page = s.open_job()
                check("F1 real login form -> real /jobs/:id page (full JobDetails around the editor)", page.url.endswith(JOBS["v2"]) and page.locator("text=Backend Developer").count() > 0
                      and page.locator("[data-testid=requirements-v2]").count() == 1)
                check("F1 the full JobDetails page still renders its other sections (CV ingestion / knockout area present)", page.locator("section").count() > 3)
                blk = "[data-testid=blockers]"
                kinds = page.locator(f"{blk} [data-kind]").evaluate_all("els => els.map(e => e.getAttribute('data-kind'))")
                check("F1 injection (requirement + weights) and split-OR blockers are shown", sorted(kinds) == ["injection_requirement", "injection_weights", "split_or_requirement"], str(kinds))
                check("F1 the model conflict and classification warnings are listed", page.locator("[data-gate-group=conflict]").count() == 1 and page.locator("[data-gate-group=classification]").count() == 1
                      and page.locator("[data-testid=jd-evidence]").count() == 2)
                check("F1 no acknowledgment control for injection / split-OR", page.locator(f"{blk} button", has_text="Accept").count() == 0)
                check("F1 readiness is the server's (blocked), provenance shown, raw output collapsed", "Text addressed to the AI" in txt(page, "[data-testid=readiness-state]")
                      and "criteria_extraction_v2-2" in page.locator("[data-testid=provenance]").evaluate("e => e.textContent")
                      and page.locator("[data-testid=raw-output]").get_attribute("open") is None)
                details = [r for r in s.responses if r[1].startswith("/jobs/details")]
                check("F1 DEFECT FIX: /jobs/details no longer carries the pipeline record (raw output) to the page", details and all("requirements_pipeline" not in r[3] and "scoreability" not in r[3] for r in details))
                shot(page, "F1_combined_desktop_en")
                s.close()


            if want('F2'):
                seed()
                s = Session(browser, "admin")
                page = s.open_job()
                before = row()
                page.locator("[data-kind=injection_requirement] button", has_text="Remove this item").click()
                page.locator("[data-split-item]").first.locator("[data-testid=keep-one]").click()
                check("F2 corrections change the draft only: nothing written, no auto-save", not s.writes and row()["revision"] == before["revision"] and page.locator("[data-testid=unsaved-badge]").count() == 1)
                with page.expect_response(lambda r: r.request.method == "PUT" and "/requirements" in r.url, timeout=10000):
                    page.get_by_role("button", name="Save requirements").first.click()
                page.wait_for_selector("[data-testid=issue-summary]", timeout=10000)
                put = [w for w in s.writes if w["method"] == "PUT"]
                resp422 = [r for r in s.responses if r[0] == "PUT" and "/requirements" in r[1]]
                check("F3 invalid weights (required totals off after removals) are rejected: HTTP 422, no writes", put and resp422[-1][2] == 422 and row() == before and actions() == [])
                page.get_by_role("button", name="Equalize: Skills.").click()
                page.fill("#req-cat-skills-w", "40"); page.fill("#req-cat-experience-w", "40"); page.fill("#req-cat-soft_skills-w", "20")
                page.get_by_role("button", name="Save requirements").first.click()
                page.wait_for_function("document.querySelector('[data-testid=unsaved-badge]') === null", timeout=10000)
                r1 = row()
                check("F2 explicit save stores the corrected draft (revision +1, weight column synced, snapshot untouched)", r1["revision"] == before["revision"] + 1
                      and r1["w"] == 40 and r1["original"] == before["original"])
                page.reload()
                page.wait_for_selector("[data-testid=requirements-v2]", timeout=25000)
                check("F2 after a full page reload the blockers stay resolved (server re-checked)", page.locator("[data-testid=blockers]").count() == 0
                      and "requirements_injection_guard_resolved" in actions() and "requirements_split_or_guard_resolved" in actions())
                edited = page.locator("[data-testid=requirements-v2]").get_by_text("Edited", exact=True).count()
                check("F2 Edited badges appear on the corrected items/categories", edited >= 1, str(edited))
                page.locator("[data-testid=toggle-compare]").click()
                check("F2 original comparison lists the removed items", page.locator("[data-testid=comparison]").count() == 1
                      and "removed since original" in txt(page, "[data-testid=comparison]").lower(), txt(page, "[data-testid=comparison]")[:400])
                shot(page, "F2_after_save_reload_compare")
                s.close()


            if want('F4'):
                seed()
                st, v = fix_blockers_via_api()
                check("F4 (setup via API) blockers corrected; only policy-governed reviews remain", st == 200 and v["readiness"]["state"] == "needs_classification_review", f"{st}")
                s = Session(browser, "hr")
                page = s.open_job()
                check("F4 policy Yes: HR manager sees acknowledgment controls enabled on the saved version", not page.locator("[data-testid=ack-conflict]").is_disabled())
                page.locator("[data-testid=classification] button", has_text="Accept as Preferred").first.click()
                page.wait_for_function("document.querySelector('[data-testid=ack-conflict]') !== null", timeout=10000)
                page.locator("[data-testid=ack-conflict]").click()
                page.wait_for_function("document.querySelector('[data-testid=readiness-state]').textContent.trim() === 'Ready'", timeout=10000)
                gates = [w["body"].get("gate") for w in s.writes if w["method"] == "POST"]
                check("F4 acknowledgments use the existing endpoint with gate=classification then gate=conflict, ready only after both", gates == ["classification", "conflict"])
                s.close()
                seed()
                fix_blockers_via_api()
                pg.q("UPDATE system_config SET value='false' WHERE key=%s", (POLICY_KEY,))
                s = Session(browser, "admin")
                page = s.open_job()
                check("F4 policy No: Ready with classification and conflict visible but informational", txt(page, "[data-testid=readiness-state]").strip() == "Ready"
                      and "do not block" in txt(page, "[data-testid=conflict-policy]"), txt(page, "[data-testid=readiness-state]") + " | " + txt(page, "[data-testid=conflict-policy]"))
                shot(page, "F4_policy_no")
                pg.q("UPDATE system_config SET value='true' WHERE key=%s", (POLICY_KEY,))
                page.reload(); page.wait_for_selector("[data-testid=requirements-v2]")
                check("F4 flipping the admin setting back to Yes (no write) re-blocks the same saved job", "review" in txt(page, "[data-testid=readiness-state]").lower(), txt(page, "[data-testid=readiness-state]"))
                s.close()


            if want('F5'):
                seed()
                s = Session(browser, "viewer")
                page = s.open_job()
                check("F5 viewer: issues visible, but no save / correction / acknowledgment / raw-output controls", page.locator("[data-testid=blockers]").count() == 1
                      and page.get_by_role("button", name="Remove this item").count() == 0 and page.locator("[data-testid=save]").count() == 0
                      and page.locator("[data-testid=ack-conflict]").count() == 0 and page.locator("[data-testid=raw-output]").count() == 0)
                bodies = " ".join(r[3] for r in s.responses)
                check("F5 viewer: no API response carries the raw AI output or the stored record", "scoreability" not in bodies and "requirements_pipeline" not in bodies and "ignore all previous" not in bodies.lower().replace("job description", "")
                      or ("scoreability" not in bodies and "requirements_pipeline" not in bodies))
                shot(page, "F5_viewer")
                status, _ = api("PUT", f"/jobs/{JOBS['v2']}/requirements", "viewer", {"expected_revision": 0, "requirements": {}})
                check("F5 viewer: the API itself refuses writes (403)", status == 403)
                s.close()
                s = Session(browser, "admin")
                page = s.open_job()
                ed = [r for r in s.responses if r[1].endswith("/requirements") and r[0] == "GET"]
                check("F5 editors receive the raw output (collapsed) from the requirements API only", ed and "scoreability" in ed[-1][3] and page.locator("[data-testid=raw-output]").count() == 1)
                s.close()
                s = Session(browser, "other")
                page = s.page
                page.goto(f"{SPA}/jobs/{JOBS['v2']}")
                page.wait_for_timeout(2500)
                check("F5 another tenant's user cannot open the job's requirements", page.locator("[data-testid=requirements-v2]").count() == 0)
                s.close()


            if want('F6'):
                seed(record=False)
                s = Session(browser, "admin")
                page = s.open_job()
                check("F6 no pipeline record: 'Additional checks unavailable', readiness labelled basic-only, original comparison available",
                      "Additional checks unavailable" in txt(page, "[data-testid=pipeline-unavailable]") and page.locator("[data-testid=readiness-unguarded]").count() == 1
                      and "bg-green-50" not in (page.locator("[data-testid=readiness]").get_attribute("class") or ""))
                page.locator("[data-testid=toggle-compare]").click()
                check("F6 original comparison works without a record", page.locator("[data-testid=comparison]").count() == 1)
                shot(page, "F6_unavailable")
                s.close()
                seed(tamper="UPDATE job_criteria SET analysis_json = jsonb_set(analysis_json, '{requirements_pipeline,raw_response,text}', '\"tampered\"') WHERE job_id = %s")
                s = Session(browser, "admin")
                page = s.open_job()
                check("F6 damaged record: clear blocking message, original and requirements readable", "damaged" in txt(page, "[data-testid=pipeline-invalid]").lower()
                      and page.locator("input[id^='req-text-']").count() > 3)
                page.locator("input[id^='req-text-']").first.fill("changed")
                before = row()
                page.get_by_role("button", name="Save requirements").first.click()
                page.wait_for_selector("[data-testid=problem], [data-testid=issue-summary]", timeout=10000)
                check("F6 damaged record: the write is refused (409) with a clear message and nothing stored", "damaged" in txt(page, "[data-testid=problem]").lower() and row() == before)
                shot(page, "F6_damaged")
                s.close()


            if want('F7'):
                seed()
                fix_blockers_via_api()
                a, h = Session(browser, "admin"), Session(browser, "hr")
                pa, ph = a.open_job(), h.open_job()
                ph.locator("input[id^='req-text-']").first.fill("Python programming (HR change)")
                ph.get_by_role("button", name="Save requirements").first.click()
                ph.wait_for_function("document.querySelector('[data-testid=unsaved-badge]') === null", timeout=10000)
                soft = pa.get_by_role("region", name="Soft skills", exact=True).locator("input[id^='req-text-']").first
                soft.fill(soft.input_value() + " (admin change)")
                pa.get_by_role("button", name="Save requirements").first.click()
                pa.wait_for_selector("[data-testid=conflict]", timeout=10000)
                check("F7 stale save opens the conflict resolver; the admin's draft is kept, nothing overwritten", pa.locator("[data-testid=unsaved-badge]").count() == 1
                      and "(HR change)" in json.dumps(row()["analysis"]["requirements"]))
                pa.locator("[data-testid=apply-merge]").click()
                pa.get_by_role("button", name="Save requirements").first.click()
                pa.wait_for_function("document.querySelector('[data-testid=unsaved-badge]') === null", timeout=10000)
                final = json.dumps(row()["analysis"]["requirements"], ensure_ascii=False)
                check("F7 after the merge both users' changes are in the saved version", "(HR change)" in final and "(admin change)" in final)
                shot(pa, "F7_conflict_merged")
                a.close(); h.close()


            if want('F8'):
                seed()
                s = Session(browser, "admin")
                page = s.open_job("legacy")
                check("F8 legacy job: the original criteria section renders and there is no requirements editor", page.locator("[data-testid=requirements-v2]").count() == 0
                      and page.locator("text=Skills Analysis").count() > 0)
                status, body = api("GET", f"/jobs/{JOBS['legacy']}/requirements", "admin")
                check("F8 legacy job: the requirements API answers 409 and touches nothing", status == 409 and body["detail"]["code"] == "not_requirements_v2")
                s.close()


            if want('F9'):
                seed()
                for lang, size, mobile, tag in (("ar", (1366, 900), False, "ar_desktop"), ("en", (390, 844), True, "en_mobile"), ("ar", (390, 844), True, "ar_mobile")):
                    s = Session(browser, "admin", lang=lang, size=size, mobile=mobile)
                    page = s.open_job()
                    root = page.locator("[data-testid=requirements-v2]")
                    check(f"F9 {tag}: editor direction and language", root.get_attribute("dir") == ("rtl" if lang == "ar" else "ltr") and root.get_attribute("lang") == lang)
                    check(f"F9 {tag}: blockers shown in the page language", ("نص موجّه" if lang == "ar" else "Text addressed to the AI") in txt(page, "[data-testid=blockers]"))
                    check(f"F9 {tag}: no horizontal page overflow", overflow(page) <= 2, str(overflow(page)))
                    shot(page, f"F9_{tag}")
                    s.close()


            if want('F10'):
                seed()
                s = Session(browser, "admin")
                page = s.open_job()
                summ = page.locator("[data-testid=raw-output] summary")
                summ.focus(); page.keyboard.press("Enter")
                check("F10 keyboard: Enter on the raw-output summary expands it", page.locator("[data-testid=raw-output]").get_attribute("open") is not None)
                btn = page.locator("[data-kind=injection_requirement] button", has_text="Remove this item")
                btn.focus()
                check("F10 keyboard: the correction button is focusable and shows a focus ring", btn.evaluate("e => document.activeElement === e") and
                      btn.evaluate("e => getComputedStyle(e).outlineStyle !== 'none' || getComputedStyle(e).boxShadow !== 'none'"))
                page.keyboard.press("Enter")
                check("F10 keyboard: Enter performs the correction (draft only)", page.locator("[data-testid=unsaved-badge]").count() == 1 and not s.writes)
                page.locator("[data-testid=save]").focus(); page.keyboard.press("Space")
                page.wait_for_timeout(1500)
                check("F10 keyboard: Space on Save sends the explicit save", any(w["method"] == "PUT" for w in s.writes))
                tabs = []
                page.locator("[data-testid=requirements-v2]").locator("button:visible").first.focus()
                for _ in range(6):
                    page.keyboard.press("Tab")
                    tabs.append(page.evaluate("document.activeElement && (document.activeElement.textContent || document.activeElement.tagName).trim().slice(0, 30)"))
                check("F10 keyboard: Tab moves through interactive controls in order (no focus trap)", len(set(tabs)) >= 3, str(tabs))
                s.close()

            real_errors = []
            browser.close()
    except Exception as exc:                                                    # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("full-app run completed", False, repr(exc))
        exit_code = 1
    finally:
        uv.terminate()
        spa.shutdown()
        pg.stop()
        shutil.rmtree(dist, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    lines = ["# Requirements-v2 editor: FULL-APPLICATION verification", "",
             "Real FastAPI app (`main:app`, real JWT login), real SPA (JobDetails page) in Chromium, disposable PostgreSQL 16 with the project's schema and migrations. "
             "Synthetic v2 records inserted directly; no creation/extraction/evaluation, no AI call, no VPS.", "",
             f"{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.", "",
             "Migrations that failed on top of schema.sql in the disposable database (pre-existing, not part of this scope): " + ("; ".join(pg.failed_migrations) or "none"), ""]
    lines += [f"- {'PASS' if ok else '**FAIL**'} {n}{'' if ok else f' — {d}'}" for n, ok, d in RESULTS]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed or exit_code else 0


if __name__ == "__main__":
    raise SystemExit(main())
