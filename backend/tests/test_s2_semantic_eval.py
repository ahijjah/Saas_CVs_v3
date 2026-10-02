"""Offline tests for the read-only S2 evaluation harness (fake client, synthetic CV)."""
import asyncio
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests.test_s2_experience import CV, CV_S0, FakeClient, res, s0_doc, s0_entry, s0_resp, s2_resp

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("s2_semantic_eval", BACKEND / "scripts" / "s2_semantic_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_fixture_criteria_are_valid_and_diverse():
    crit = ev.load_criteria(ev.DEFAULT_CRITERIA)
    assert 8 <= len(crit) <= 12
    assert {sp.policy for sp, _ in crit} == {"explicit_role", "functional", "sector"}
    assert len({sp.criterion_id for sp, _ in crit}) == len(crit)
    assert any(sp.setting for sp, _ in crit) and any(sp.required_years is None for sp, _ in crit)


def test_source_span_must_be_verbatim(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"criteria": [{"criterion_id": "X", "policy": "functional", "required_years": 2,
                                           "criterion_text": "2 years in sales", "targets": ["sales"],
                                           "source_spans": ["marketing"]}]}))
    with pytest.raises(ValueError, match="not verbatim"):
        ev.load_criteria(p)


def test_spread_sample_round_robins_jobs():
    rows = [{"application_id": f"a{i}", "job_id": j} for i, j in enumerate("AAAABBC")]
    got = [r["application_id"] for r in ev.spread_sample(rows, 5, 3)]
    assert got == ["a0", "a4", "a6", "a1", "a5"]
    assert len(ev.spread_sample(rows, 50, 1)) == 3


def _spec(policy="explicit_role", targets=("Project Manager",), setting=None, text="5 years as a Project Manager"):
    return ev.RequirementSpec(policy=policy, required_years=5, targets=targets, setting=setting,
                              criterion_id="C", criterion_text=text, source_spans=targets)


@pytest.mark.parametrize("row, flag", [
    ({"label": "qualifying", "title": "Chef", "quotes": ["Cooked meals"], "basis": "title"}, ev.FLAG_FALSE_Q),
    ({"label": "related", "title": "Driver", "entry_text": "Drove trucks"}, ev.FLAG_FALSE_R),
    ({"label": "related", "title": "Senior Project Manager", "quotes": ["Project Manager"]}, ev.FLAG_R_UNDERCALL),
    ({"label": "not_relevant", "title": "Project Manager", "quotes": ["x"]}, ev.FLAG_NR_RELEVANT),
    ({"label": "insufficient", "title": "Project Manager"}, ev.FLAG_INS_EVIDENCE),
    ({"label": "not_relevant", "title": "Clerk", "quotes": ["helped the Project Manager"]}, ev.FLAG_QUOTE),
    ({"label": "qualifying", "title": "Project Manager", "quotes": ["Project Manager"],
      "reason": "No evidence of delivery"}, ev.FLAG_REASON),
])
def test_review_flags(row, flag):
    sp = _spec()
    m = ev.Matcher(sp, ["project management"])
    assert flag in ev.review_flags(m, sp, row)


def test_supporting_role_title_not_flagged_as_undercall():
    sp = _spec()
    flags = ev.review_flags(ev.Matcher(sp, []), sp, {"label": "related", "title": "Assistant Project Manager",
                                                     "quotes": ["Assistant Project Manager"]})
    assert ev.FLAG_R_UNDERCALL not in flags


def test_setting_flag():
    sp = _spec("functional", ("customer service",), "call center", "2 years customer service in a call center")
    row = {"label": "qualifying", "title": "Agent", "quotes": ["customer service"], "entry_text": "customer service"}
    assert ev.FLAG_SETTING in ev.review_flags(ev.Matcher(sp, []), sp, row)


def _args(tmp_path, **kw):
    base = dict(cache_dir=None, concurrency=1, price_in=0.15, price_out=0.60)
    base.update(kw)
    return Namespace(**base)


def test_run_eval_end_to_end_offline(tmp_path, monkeypatch):
    sp = ev.RequirementSpec(policy="explicit_role", required_years=5, targets=("Programme Manager",),
                            criterion_id="C1", criterion_text="5 years as Programme Manager",
                            source_spans=("Programme Manager",), spec_version="t")
    s2_first_bad = s2_resp(res("E1", "qualifying", [(3, "Programme Manager")], basis="title"))  # E2/E3 missing
    s2_good = s2_resp(
        res("E1", "qualifying", [(3, "Programme Manager")], basis="title"),
        res("E2", "related", [(11, "Organised logistics and meeting minutes")]),
        res("E3", "insufficient", [(12, "Consultant")], basis="title", missing=["responsibilities"]))
    client = FakeClient(CV_S0, s2_first_bad, s2_good)
    monkeypatch.setattr(ev.llm_call, "create_client", lambda: client)
    sample = [{"application_id": "app-1", "job_id": "j1", "job_title": "PM", "extracted_text": CV}]
    out = tmp_path / "out"
    out.mkdir()
    summary = run(ev.run_eval(sample, [(sp, ["project"])], _args(tmp_path), out))

    assert summary["classifications"] == 3 and summary["s2_repaired"] == 1 and summary["s2_failed"] == 0
    assert summary["label_distribution"] == {"qualifying": 1, "related": 1, "not_relevant": 0, "insufficient": 1}
    assert summary["api"]["s0"]["calls"] == 1 and summary["api"]["s2"] == {
        **summary["api"]["s2"], "calls": 2, "repair_calls": 1}
    pairs = [json.loads(x) for x in (out / "pairs.jsonl").read_text().splitlines()]
    e2 = next(p for p in pairs if p["entry_id"] == "E2")
    assert e2["title"] == "Project Assistant" and e2["quotes"] == ["Organised logistics and meeting minutes"]
    for f in ("report.md", "review.csv", "summary.json", "s2_results.jsonl", "s0_summary.jsonl"):
        assert (out / f).exists()
    assert "## Review queue (flagged)" in (out / "report.md").read_text()

    # second run: everything from the file cache, no new calls
    client2 = FakeClient()
    monkeypatch.setattr(ev.llm_call, "create_client", lambda: client2)
    again = run(ev.run_eval(sample, [(sp, ["project"])], _args(tmp_path), out))
    assert client2.requests == [] and again["api"]["s0"]["calls"] == again["api"]["s2"]["calls"] == 0
    assert again["api"]["s0_cache_hits"] == 1 and again["api"]["s2_cache_hits"] == 1
    assert again["label_distribution"] == summary["label_distribution"]


def test_readonly_connection_is_verified(monkeypatch):
    class Conn:
        async def fetchval(self, q):
            return "off"

        async def close(self):
            self.closed = True

    async def fake_connect(dsn, server_settings):
        assert server_settings["default_transaction_read_only"] == "on"
        return Conn()

    import types
    monkeypatch.setitem(__import__("sys").modules, "asyncpg", types.SimpleNamespace(connect=fake_connect))
    with pytest.raises(RuntimeError, match="not read-only"):
        run(ev.ReadOnlyDB.connect("postgresql://x"))


def test_harness_sql_is_select_only():
    import re
    for sql in (ev.SAMPLE_IDS_SQL, ev.TEXT_SQL, ev.JOB_APPS_SQL):
        assert sql.strip().upper().startswith("SELECT")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT)\b", sql.upper())
        assert "candidate_name" not in sql and "candidate_email" not in sql
    src = (BACKEND / "scripts" / "s2_semantic_eval.py").read_text(encoding="utf-8")
    assert "conn.execute" not in src and "executemany" not in src


# ═════════════════════════════════════════════════════════════════════════════
# Real-job mode: JOB-2026-0031 (offline: fake DB, no AI)
# ═════════════════════════════════════════════════════════════════════════════

JOB = "JOB-2026-0031"
JOB_FIXTURE = BACKEND / "scripts" / "s2_eval_fixtures" / "job_2026_0031.json"
REAL_TEXT = ("Minimum 5 years of experience in a relevant role "
             "(Construction Project Manager or Assistant Project Manager)")
CPM_CV = """EXPERIENCE
Assistant Project Manager
BuildCo Construction
Jan 2019 - Dec 2021
- Coordinated subcontractors and site schedules for commercial buildings
Site Engineer
Delta Contracting LLC
Mar 2015 - Dec 2018
- Supervised concrete works on residential towers"""
CPM_S0 = s0_resp([
    s0_entry("A1", title=(2, "Assistant Project Manager"), employer=(3, "BuildCo Construction"),
             header=(2, 3, 4), body=[(5, 5)]),
    s0_entry("A2", title=(6, "Site Engineer"), employer=(7, "Delta Contracting LLC"),
             header=(6, 7, 8), body=[(9, 9)])])


def test_real_job_fixture_is_exact():
    data = json.loads(JOB_FIXTURE.read_text(encoding="utf-8"))
    assert (data["job_code"], data["spec_version"]) == (JOB, "eval-job-2026-0031-1")
    (c,) = data["criteria"]
    assert {k: c[k] for k in ("criterion_id", "policy", "required_years", "criterion_text", "targets",
                              "setting", "source_spans")} == {
        "criterion_id": "JOB-2026-0031.experience.1", "policy": "explicit_role", "required_years": 5,
        "criterion_text": REAL_TEXT,
        "targets": ["Construction Project Manager", "Assistant Project Manager"], "setting": "construction",
        "source_spans": ["Construction Project Manager", "Assistant Project Manager"]}
    ((sp, _),) = ev.load_criteria(JOB_FIXTURE)
    assert (sp.criterion_id, sp.spec_version, sp.required_years, sp.setting) == (
        "JOB-2026-0031.experience.1", "eval-job-2026-0031-1", 5, "construction")


def test_fixture_scope_rules(tmp_path):
    ev.check_fixture_scope(JOB_FIXTURE, JOB)                                   # matching: ok
    with pytest.raises(ValueError, match="generic or mismatched"):
        ev.check_fixture_scope(ev.DEFAULT_CRITERIA, JOB)                       # generic refused
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"job_code": "JOB-2026-0099", "criteria": []}))
    with pytest.raises(ValueError, match="JOB-2026-0099"):
        ev.check_fixture_scope(other, JOB)                                     # mismatched refused
    with pytest.raises(ValueError, match="--job-code JOB-2026-0031"):
        ev.check_fixture_scope(JOB_FIXTURE, None)                              # job fixture needs its job


def test_job_sql_filters_by_job_code():
    assert "WHERE j.job_code = $1" in ev.JOB_APPS_SQL
    assert "extraction_status = 'done'" in ev.TEXT_SQL and "length(f.extracted_text) DESC" in ev.TEXT_SQL


class FakeDB:
    """Answers the harness's SELECTs from memory and records every query."""

    def __init__(self, apps, texts):
        self.apps, self.texts, self.queries, self.closed = apps, texts, [], False

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        if sql is ev.JOB_APPS_SQL:
            return [r for r in self.apps if r["job_code"] == args[0]]
        if sql is ev.TEXT_SQL:
            return [{"application_id": i, "extracted_text": self.texts[i], "job_id": "j31",
                     "job_title": "Construction_Project_Manager"} for i in args[0] if i in self.texts]
        raise AssertionError(f"unexpected SQL {sql[:60]!r}")

    async def close(self):
        self.closed = True


def _apps():
    base = {"job_id": "j31", "job_title": "Construction_Project_Manager", "job_code": JOB}
    return [
        {**base, "application_id": "a-ok", "files": 1, "done_files": 1, "max_text_len": len(CPM_CV)},
        {**base, "application_id": "b-nofile", "files": 0, "done_files": 0, "max_text_len": 0},
        {**base, "application_id": "c-pending", "files": 1, "done_files": 0, "max_text_len": 0},
        {**base, "application_id": "d-short", "files": 1, "done_files": 1, "max_text_len": 20},
        {**base, "job_code": "JOB-2026-0001", "application_id": "z-other-job", "files": 1, "done_files": 1,
         "max_text_len": len(CPM_CV)},
    ]


def test_load_job_sample_filters_job_and_excludes_unusable():
    db = FakeDB(_apps(), {"a-ok": CPM_CV, "z-other-job": CPM_CV})
    sample, excluded, total = run(ev.load_job_sample(db, JOB, 100))
    assert [a["application_id"] for a in sample] == ["a-ok"] and total == 4
    assert {e["application_id"]: e["reason"] for e in excluded} == {
        "b-nofile": "no_files", "c-pending": "extraction_not_done", "d-short": "text_shorter_than_100_chars"}
    assert db.queries[0][1] == (JOB,)
    assert db.queries[1][1] == (["a-ok"],)                    # texts fetched only for usable ids


def test_s2_request_for_real_spec_masks_years_threshold_and_dates():
    ((sp, _),) = ev.load_criteria(JOB_FIXTURE)
    doc = s0_doc(CPM_CV, CPM_S0)
    req = ev.s2.build_request(doc, sp, CPM_CV)
    crit = req.payload["criterion"]
    assert crit["criterion_text"] == ("Minimum [N] years of experience in a relevant role "
                                      "(Construction Project Manager or Assistant Project Manager)")
    assert crit["setting"] == "construction" and crit["policy"] == "explicit_role"
    assert "required_years" not in req.user_message and "5 years" not in req.user_message
    assert {ln["text"] for e in req.payload["entries"] for ln in e["lines"] if ln["line"] in (4, 8)} == {"[dates]"}
    for leaked in ("2019", "2021", "2015", "2018"):
        assert leaked not in req.user_message


def _dry_args(tmp_path, *extra):
    return ev.build_parser().parse_args(
        ["--out", str(tmp_path / "out"), "--job-code", JOB, "--criteria", str(JOB_FIXTURE),
         "--min-text-chars", "100", "--dry-run", *extra])


def _no_ai(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry run must not create an OpenAI client")
    monkeypatch.setattr(ev.llm_call, "create_client", boom)
    monkeypatch.setattr(ev.s2, "_get_client", boom)
    monkeypatch.setattr(ev.s0, "_get_client", boom)


def test_dry_run_no_ai_no_writes(tmp_path, monkeypatch, capsys):
    _no_ai(monkeypatch)
    db = FakeDB(_apps(), {"a-ok": CPM_CV})

    async def fake_connect():
        return db
    monkeypatch.setattr(ev, "connect_db", fake_connect)
    assert run(ev.main_async(_dry_args(tmp_path))) == 0
    out = capsys.readouterr().out
    for frag in (f"job_code: {JOB}", "usable CVs selected: 1", "excluded: 3", "excluded 1 x no_files",
                 "a-ok", "Minimum [N] years of experience", '"setting": "construction"',
                 "required_years=5 (S4/S5 only — not sent to S2)", "S2 version 1.1.0 · prompt s2-2",
                 "entries are produced by S0 at run time", "L4: [dates]", "S0: 1 main + at most 1 repairs",
                 "S2: at most 1 main", "no OpenAI calls were made"):
        assert frag in out, frag
    assert "Coordinated subcontractors" not in out                  # no CV body text without S0 cache
    assert not (tmp_path / "out").exists()                          # nothing written
    assert db.closed and all(sql.strip().upper().startswith("SELECT") for sql, _ in db.queries)


def test_dry_run_shows_real_entries_when_s0_cached(tmp_path, monkeypatch, capsys):
    _no_ai(monkeypatch)
    doc = s0_doc(CPM_CV, CPM_S0)                                    # built offline with the fake client
    cache = tmp_path / "cache" / "s0"
    cache.mkdir(parents=True)
    (cache / f"{ev.s0.s0_cache_key(CPM_CV)}.json").write_text(json.dumps(doc.to_dict()))

    async def fake_connect():
        return FakeDB(_apps(), {"a-ok": CPM_CV})
    monkeypatch.setattr(ev, "connect_db", fake_connect)
    run(ev.main_async(_dry_args(tmp_path, "--cache-dir", str(tmp_path / "cache"))))
    out = capsys.readouterr().out
    assert "from cached S0 (validated, 2 entries)" in out
    assert '"title": "Assistant Project Manager"' in out and '"text": "[dates]"' in out
    assert "2019" not in out.split("S2 request for application")[1].split("date masking preview")[0]


@pytest.mark.parametrize("criteria, extra, msg", [
    (ev.DEFAULT_CRITERIA, [], "generic or mismatched"),
    (JOB_FIXTURE, ["--application-id", "x"], "mutually exclusive"),
])
def test_refusals_happen_before_any_db_access(tmp_path, monkeypatch, criteria, extra, msg):
    async def no_db():
        raise AssertionError("must refuse before connecting")
    monkeypatch.setattr(ev, "connect_db", no_db)
    args = ev.build_parser().parse_args(["--out", str(tmp_path), "--job-code", JOB, "--criteria", str(criteria),
                                         "--dry-run", *extra])
    with pytest.raises(SystemExit, match=msg):
        run(ev.main_async(args))
