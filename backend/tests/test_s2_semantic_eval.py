"""Offline tests for the read-only S2 evaluation harness (fake client, synthetic CV)."""
import asyncio
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests.test_s2_experience import CV, CV_S0, FakeClient, res, s2_resp

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
    for sql in (ev.SAMPLE_IDS_SQL, ev.TEXT_SQL):
        assert sql.strip().upper().startswith("SELECT")
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE|GRANT)\b", sql.upper())
        assert "candidate_name" not in sql and "candidate_email" not in sql
    src = (BACKEND / "scripts" / "s2_semantic_eval.py").read_text(encoding="utf-8")
    assert "conn.execute" not in src and "executemany" not in src
