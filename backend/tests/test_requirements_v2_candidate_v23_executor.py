"""Offline tests for explicit `--prompt v2-3` selection in the benchmark executor and for the v2-3 versus v2-2 comparison (OR/AND structure, condition routing,
regressions elsewhere). No model call, no network, no key. Passing says nothing about how a model performs with v2-3: no v2-3 run exists, and every "run" in
these tests is a fake written by the tests."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"


def _load(name, filename):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


ex = _load("req_v2_run_v23", "requirements_v2_extraction_run.py")
cp = _load("req_v2_cmp_v23", "requirements_v2_extraction_compare.py")
ev = ex.ev
CASES = ev.load_cases()
V22_RUN = BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1"
BASE_RUN = BACKEND / "benchmark_results" / "requirements_v2" / "baseline_v2-1"
V23_SHA = "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"
V22_SHA = "40ea678b65a5782da3f74f1c0b52f4dbeb10cc369f78efd25f1e38827ff48dda"
V21_SHA = "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"
FAKE_KEY = "sk-TESTKEY0123456789abcdefghijkl"


def _git_has(commit):
    try:
        subprocess.run(["git", "-C", str(ex.REPO), "cat-file", "-e", commit], check=True, capture_output=True)
        return True
    except Exception:
        return False


needs_commit = pytest.mark.skipif(not _git_has("7d5c671") or not _git_has("916d1058"), reason="candidate commits not in this checkout")


class Fake:
    def __init__(self, script=None, completion=500, prompt=6300):
        self.calls, self.script, self.completion, self.prompt = [], script or {}, completion, prompt

    def __call__(self, request, timeout):
        n = len(self.calls) + 1
        self.calls.append(request)
        step = self.script.get(n)
        if isinstance(step, ex.ApiError):
            raise step
        step = step or {}
        case = next(c for c in CASES if c["jd"] in request["messages"][-1]["content"])
        return {"raw": step.get("raw", json.dumps(ev.reference_response(case), ensure_ascii=False)), "finish_reason": "stop", "model": ex.RUN_MODEL,
                "usage": {"prompt_tokens": self.prompt, "completion_tokens": self.completion}}


# ══ 1. explicit selection; baseline and v2-2 selection preserved ═══════════════════════════════════════════════════════════════
def test_three_prompts_are_selectable_and_the_default_is_still_the_baseline():
    assert set(ex.SPECS) == {"baseline", "v2-2", "v2-3"} and ex.SPECS["baseline"] is ex.BASELINE_SPEC and ex.SPECS["v2-2"] is ex.CANDIDATE_SPEC
    assert (ex.BASELINE_SPEC.version, ex.BASELINE_SPEC.sha256, ex.BASELINE_SPEC.commit) == ("criteria_extraction_v2-1", V21_SHA, None)
    assert (ex.CANDIDATE_SPEC.version, ex.CANDIDATE_SPEC.sha256, ex.CANDIDATE_SPEC.commit) == ("criteria_extraction_v2-2", V22_SHA, "916d1058")
    assert (ex.CANDIDATE_V23_SPEC.version, ex.CANDIDATE_V23_SPEC.sha256, ex.CANDIDATE_V23_SPEC.commit) == ("criteria_extraction_v2-3", V23_SHA, "7d5c671")
    assert ex.CANDIDATE_V23_SHA256 == V23_SHA and ex.CANDIDATE_V23_COMMIT == "7d5c671"
    assert ex.CANDIDATE_V22_SHA256 == V22_SHA and ex.CANDIDATE_COMMIT == "916d1058"


def test_the_cli_accepts_v2_3_and_nothing_else_unknown(capsys, monkeypatch):
    monkeypatch.setattr(ex, "verify_frozen", lambda: [])
    monkeypatch.setattr(ex, "verify_candidate", lambda: [])
    monkeypatch.setattr(ex, "verify_candidate_v23", lambda: [])
    assert ex.main(["--preflight", "--prompt", "v2-3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["prompt_key"] == "v2-3" and out["prompt_version"] == "criteria_extraction_v2-3" and out["prompt_sha256"] == V23_SHA and out["candidate_commit"] == "7d5c671"
    assert out["openai_key_present"] in (True, False) and "reservations_vs_v2_2" in out and out["assessment"]["limits_practical"]["all_24_calls_A_expected"]
    with pytest.raises(SystemExit):
        ex.main(["--preflight", "--prompt", "v2-4"])
    assert ex.main(["--preflight"]) == 0 and json.loads(capsys.readouterr().out)["prompt_key"] == "baseline"        # default unchanged
    assert ex.main(["--preflight", "--prompt", "v2-2"]) == 0 and json.loads(capsys.readouterr().out)["candidate_commit"] == "916d1058"


def test_a_v2_3_run_is_blocked_without_a_key_and_never_reaches_the_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ex, "verify_frozen", lambda: [])
    monkeypatch.setattr(ex, "verify_candidate", lambda: [])
    monkeypatch.setattr(ex, "verify_candidate_v23", lambda: [])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ex, "openai_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call attempted")))
    assert ex.main(["--run", "--prompt", "v2-3", "--out", str(tmp_path / "o")]) == 3
    assert "BLOCKED" in capsys.readouterr().out and not (tmp_path / "o").exists()


# ══ 2. commit 7d5c671, manifest and SHA-256 are verified; v2-2 and the frozen files stay verified ═══════════════════════════════
@needs_commit
def test_v2_3_verifies_against_its_commit_manifest_and_hash():
    assert ex.verify_candidate_v23() == [] and ex.verify_candidate() == [] and ex.verify_frozen() == []
    d = ex.CANDIDATE_V23_DIR
    manifest = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == V23_SHA == hashlib.sha256((d / "criteria_extraction_v2-3.txt").read_bytes()).hexdigest()
    assert manifest["base_sha256"] == V22_SHA and manifest["version"] == "criteria_extraction_v2-3" and manifest["frozen_baseline_commit"] == "059c56b"
    assert ex.load_candidate_v23() == (d / "criteria_extraction_v2-3.txt").read_text(encoding="utf-8")
    assert subprocess.run(["git", "-C", str(ex.REPO), "merge-base", "--is-ancestor", "7d5c671", "HEAD"]).returncode == 0


def test_v2_3_load_refuses_a_changed_text_a_changed_manifest_or_a_wrong_base(tmp_path, monkeypatch):
    orig, d = ex.CANDIDATE_V23_DIR, tmp_path / "cand"
    shutil.copytree(orig, d)
    monkeypatch.setattr(ex, "CANDIDATE_V23_DIR", d)
    assert ex.load_candidate_v23()
    f = d / "criteria_extraction_v2-3.txt"
    f.write_text(f.read_text(encoding="utf-8") + "\nextra", encoding="utf-8")
    with pytest.raises(ex.CandidateIntegrityError):
        ex.load_candidate_v23()
    shutil.copy(orig / "criteria_extraction_v2-3.txt", f)
    for key, bad in (("sha256", "0" * 64), ("base_sha256", V21_SHA), ("version", "criteria_extraction_v2-2"), ("frozen_baseline_commit", "deadbee")):
        m = json.loads((orig / "MANIFEST.json").read_text(encoding="utf-8"))
        m[key] = bad
        (d / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
        with pytest.raises(ex.CandidateIntegrityError):
            ex.load_candidate_v23()


def test_v2_3_directory_verification_detects_changed_added_or_uncommitted_files(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    d = tmp_path / "backend" / "prompt_candidates" / "criteria_extraction_v2-3"
    shutil.copytree(ex.CANDIDATE_V23_DIR, d)
    git("add", "."); git("commit", "-qm", "cand")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for name, val in (("REPO", tmp_path), ("CANDIDATE_V23_COMMIT", sha), ("CANDIDATE_V23_DIR", d)):
        monkeypatch.setattr(ex, name, val)
    assert ex.verify_candidate_v23() == []
    (d / "CHANGES.md").write_text("edited", encoding="utf-8")
    assert any("differs" in p for p in ex.verify_candidate_v23()) and any("uncommitted" in p for p in ex.verify_candidate_v23())
    git("commit", "-qam", "edit")
    assert any("differs" in p for p in ex.verify_candidate_v23())
    (d / "NEW.txt").write_text("x", encoding="utf-8"); git("add", "."); git("commit", "-qm", "add")
    assert any("added to the candidate" in p for p in ex.verify_candidate_v23())
    monkeypatch.setattr(ex, "CANDIDATE_V23_COMMIT", "0" * 40)
    assert any("missing or not an ancestor" in p for p in ex.verify_candidate_v23())


def test_a_v2_3_preflight_checks_v2_3_v2_2_and_the_frozen_files_and_a_failure_in_any_blocks_it(monkeypatch):
    ok = lambda: []                                              # noqa: E731
    monkeypatch.setattr(ex, "verify_frozen", ok)
    monkeypatch.setattr(ex, "verify_candidate", ok)
    monkeypatch.setattr(ex, "verify_candidate_v23", ok)
    assert ex.preflight_offline(CASES, ex.CANDIDATE_V23_SPEC)["ok"]
    for name, msg in (("verify_frozen", "differs from 059c56b: x"), ("verify_candidate", "differs from 916d1058: x"), ("verify_candidate_v23", "differs from 7d5c671: x")):
        monkeypatch.setattr(ex, name, lambda m=msg: [m])
        pf = ex.preflight_offline(CASES, ex.CANDIDATE_V23_SPEC)
        assert not pf["ok"] and msg in pf["problems"], name
        monkeypatch.setattr(ex, name, ok)
    # v2-2 and the baseline never depend on the v2-3 directory
    monkeypatch.setattr(ex, "verify_candidate_v23", lambda: ["differs from 7d5c671: x"])
    assert ex.preflight_offline(CASES, ex.CANDIDATE_SPEC)["ok"] and ex.preflight_offline(CASES, ex.BASELINE_SPEC)["ok"]


# ══ 3. everything else is identical: cases, runs, model, settings, limits, stop conditions ═══════════════════════════════════════
def test_requests_differ_only_in_the_system_prompt():
    for c in CASES:
        r1, r2, r3 = (ex.SPECS[k].build(c["jd"], c["job_metadata"]) for k in ("baseline", "v2-2", "v2-3"))
        for r in (r1, r2, r3):
            assert (r["model"], r["temperature"], r["max_tokens"], r["response_format"]) == ("gpt-4o-mini-2024-07-18", 0.1, 6000, {"type": "json_object"})
        assert r2["messages"][1] == r3["messages"][1] == r1["messages"][1]                  # the user message is the same for every prompt
        assert r3["messages"][0]["content"] == ex.CANDIDATE_V23_SPEC.text() != r2["messages"][0]["content"]
        assert {k: v for k, v in r3.items() if k != "messages"} == {k: v for k, v in r2.items() if k != "messages"}
    assert ex.BASELINE_SPEC.build(CASES[0]["jd"], CASES[0]["job_metadata"]) == ex.build_request(CASES[0]["jd"], CASES[0]["job_metadata"], model=ex.RUN_MODEL)


def test_cases_runs_and_limits_are_unchanged():
    p = ev.PLAN
    assert (p["max_calls"], p["runs_per_case"], p["max_total_tokens"], p["max_cost_usd"], p["wall_clock_limit_s"], p["per_call_timeout_s"], p["retries"],
            p["max_completion_tokens_per_call"]) == (24, 2, 200_000, 0.25, 1800, 90, 0, 6000)
    assert len(CASES) == 12 and sorted(c["language"] for c in CASES) == ["ar"] * 6 + ["en"] * 6
    assert ex.RUN_MODEL == "gpt-4o-mini-2024-07-18" and (ex.PRICE_IN, ex.PRICE_OUT) == (0.15e-6, 0.60e-6)


def test_every_record_and_the_metadata_carry_v2_3_and_a_partial_run_keeps_them(tmp_path):
    spec = ex.CANDIDATE_V23_SPEC
    out = tmp_path / "out"
    summary = ex.execute(CASES, Fake(script={5: ex.ApiError("auth", 401)}), out, count_fn=lambda m: 8300, spec=spec)
    assert summary["stopped_by"] == "auth_or_model_error" and summary["calls_made"] == 5
    recs = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all((r["prompt_version"], r["prompt_sha256"], r["prompt_key"]) == ("criteria_extraction_v2-3", V23_SHA, "v2-3") for r in recs)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert (meta["prompt_version"], meta["prompt_sha256"], meta["candidate_commit"], meta["model"]) == ("criteria_extraction_v2-3", V23_SHA, "7d5c671", "gpt-4o-mini-2024-07-18")
    assert meta["settings"] == {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}} and meta["frozen_benchmark_commit"] == "059c56b"
    assert meta["limits"]["max_calls"] == 24 and meta["limits"]["max_total_tokens"] == 200_000 and meta["limits"]["wall_clock_limit_s"] == 1800
    assert ex.score(CASES, out)["run_meta"]["prompt_sha256"] == V23_SHA


def test_v2_3_sends_its_prompt_in_all_24_calls_in_the_planned_order_and_quarantines_an_echo(tmp_path):
    f = Fake()
    ex.execute(CASES, f, tmp_path / "o", count_fn=lambda m: 8300, spec=ex.CANDIDATE_V23_SPEC)
    assert len(f.calls) == 24 and all(c["messages"][0]["content"] == ex.CANDIDATE_V23_SPEC.text() for c in f.calls)
    order = [c["id"] for c in CASES] * 2
    assert [next(c["id"] for c in CASES if c["jd"] in call["messages"][-1]["content"]) for call in f.calls] == order
    line = next(ln for ln in ex.CANDIDATE_V23_SPEC.leak_lines() if '"' in ln)
    g = Fake(script={2: {"raw": json.dumps({"x": line})}})
    s = ex.execute(CASES, g, tmp_path / "p", count_fn=lambda m: 8300, spec=ex.CANDIDATE_V23_SPEC)
    assert s["stopped_by"] == "quarantine_system_prompt_text" and len(g.calls) == 2


def test_the_stop_conditions_are_the_same_for_v2_3(tmp_path):
    spec, count = ex.CANDIDATE_V23_SPEC, (lambda m: 8300)
    s = ex.execute(CASES, Fake(script={1: ex.ApiError("transport"), 2: ex.ApiError("transport")}), tmp_path / "a", count_fn=count, spec=spec)
    assert s["stopped_by"] == "two_consecutive_api_errors" and s["calls_made"] == 2
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "STOP").write_text("x")
    assert ex.execute(CASES, Fake(), tmp_path / "b", count_fn=count, spec=spec)["stopped_by"] == "manual_stop"
    s = ex.execute(CASES, Fake(prompt=5000, completion=6000), tmp_path / "c", count_fn=count, spec=spec)          # every call at the cap
    assert s["stopped_by"] == "token_budget_reserve" and s["actual_tokens"] + 0 <= ev.PLAN["max_total_tokens"]
    clock = iter(range(0, 100000, 400))
    s = ex.execute(CASES, Fake(), tmp_path / "d", count_fn=count, clock=lambda: next(clock), spec=spec)
    assert s["stopped_by"] == "wall_clock"


# ══ 4. reservations recalculated for the longer prompt ══════════════════════════════════════════════════════════════════════════
def test_v2_3_reserves_more_input_than_v2_2_by_the_added_text_and_the_output_allowance_is_unchanged():
    cmp_ = ex.reservation_comparison(CASES, ex.make_counter()[0], ex.CANDIDATE_V23_SPEC, ex.CANDIDATE_SPEC)
    added = [r["added_input_tokens"] for r in cmp_["per_case"].values()]
    assert len(added) == 12 and min(added) > 600 and max(added) < 1200 and max(added) - min(added) <= 2        # the same added text for every case
    assert cmp_["v2-3_worst_case_reserved_total"] - cmp_["v2-2_worst_case_reserved_total"] == sum(added) * 2
    count = ex.make_counter()[0]
    for c in CASES:
        req = ex.CANDIDATE_V23_SPEC.build(c["jd"], c["job_metadata"])
        need = count(req["messages"]) + ev.PLAN["max_completion_tokens_per_call"]
        # the gate refuses exactly when input + 6000 would pass the 200,000-token limit
        assert ex.gate(0, 200_000 - need + 1, 0.0, 0.0, count(req["messages"]), ev.PLAN) == "token_budget_reserve"
        assert ex.gate(0, 200_000 - need, 0.0, 0.0, count(req["messages"]), ev.PLAN) is None


def test_the_limits_stay_practical_for_the_longer_prompt_with_real_v2_2_usage_as_the_yardstick():
    count, method = ex.make_counter()
    a = ex.budget_assessment(CASES, ex.CANDIDATE_V23_SPEC, count, method)
    lp = a["limits_practical"]
    assert lp["all_24_calls_A_expected"] and lp["all_24_calls_B_growth"] and lp["cost_usd_B"] < 0.25 and lp["wall_clock_s_B"] < 1800
    assert lp["token_headroom_pct_A"] > 15 and lp["token_headroom_pct_B"] > 10                  # the token limit is the binding one: still a margin
    # the real v2-2 run used 133,517 tokens; v2-3 adds ~740 input tokens per call: the projection from that real yardstick
    recs = [json.loads(x) for x in (V22_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    used = sum(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in recs)
    assert used == 133_517
    cmp_ = ex.reservation_comparison(CASES, count, ex.CANDIDATE_V23_SPEC, ex.CANDIDATE_SPEC)
    added_actual = 24 * max(r["added_input_tokens"] for r in cmp_["per_case"].values())
    assert used + added_actual < 0.85 * 200_000
    # a stress run (every completion at the cap) still stops on the reserve, never above the limit
    st = a["scenarios"]["C stress: exact-count reserve, every completion at the 6000 cap, 60 s per call"]
    assert st["stopped_by"] == "token_budget_reserve" and st["tokens"] <= 200_000


# ══ 5. the comparison against v2-2: OR/AND structure, condition routing, regressions elsewhere ══════════════════════════════════
def _fake_v23_dir(tmp_path, mutate=None):
    d = tmp_path / "v23"
    d.mkdir()
    recs = [json.loads(x) for x in (V22_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    for r in recs:
        r["prompt_version"], r["prompt_sha256"] = "criteria_extraction_v2-3", V23_SHA
        if mutate:
            mutate(r)
    (d / "calls.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"prompt_version": "criteria_extraction_v2-3", "prompt_sha256": V23_SHA, "model": ex.RUN_MODEL,
                                             "settings": {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}}}), encoding="utf-8")
    return d


def test_the_routing_table_in_the_comparison_is_the_one_in_the_v2_3_prompt():
    text = (BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt").read_text(encoding="utf-8")
    rule9 = re.search(r"^9\. .*?(?=^10\. )", text, re.S | re.M).group(0)
    table = {}
    for ln in rule9.splitlines():
        m = re.match(r"\s+(non_scoreable_requirements|post_hiring_conditions|informational_items): (.*)", ln)
        if m:
            for label in re.findall(r"(?:^|, )([a-z_]+)(?: \([^)]*\))?(?=,|$|\.)", m.group(2).rstrip(".")):
                table[label] = m.group(1)
    assert table == cp.ROUTING


def test_the_stored_runs_keep_their_official_gates_and_the_scorer_is_unchanged():
    for d in (BASE_RUN, V22_RUN):
        saved = json.loads((d / "results.json").read_text(encoding="utf-8"))["gates"]
        runs = cp.answers_of(cp.load_records(d))
        assert cp.rescore(CASES, runs)["gates"] == saved
    assert ex.verify_frozen() == []                                              # parser, scorer, labels, matching, gates: byte-identical to 059c56b


def test_focus_report_for_an_identical_run_shows_no_change_anywhere(tmp_path):
    rep = cp.compare(CASES, V22_RUN, _fake_v23_dir(tmp_path), labels=("v2-2", "v2-3"), focus="structure-routing")
    F = rep["focus"]
    assert F["integrity"]["baseline"]["matches"] and F["integrity"]["candidate"]["matches"] and F["same_model_and_settings"]
    assert F["metrics_worse_than_baseline"] == [] and rep["regressions"]["gates"] == [] and all(g["change"] == "same" for g in rep["gates"])
    for name in ("baseline", "candidate"):
        assert F["sides"][name]["structure"]["run1"]["split_or_cases"] == ["B02_en_data_analyst", "B06_en_injection"]
    assert [d["runs"]["run1"]["delta"] for d in F["metric_deltas"] if d["metric"] == "alternatives exact"] == [0.0]
    md = cp.render_markdown(rep)
    for frag in ("v2-2 vs v2-3", "### OR/AND structure", "### Condition routing", "### Metrics everywhere", "synthetic-JD injection: not a v2-3 target; result kept",
                 "omissions / category assignment: not a v2-3 target; result kept", "split-OR guard issues"):
        assert frag in md, frag


def test_the_focus_report_reflects_a_structure_and_routing_change_and_flags_regressions(tmp_path):
    def mutate(r):
        if not r.get("raw") or r["run"] != "run1":
            return
        o = json.loads(r["raw"])
        for lst in cp.LISTS:                                                       # a list name written as a label becomes a proper label
            for cond in o.get(lst, []):
                if cond.get("category") in cp.LISTS:
                    cond["category"] = "background_check" if lst == "post_hiring_conditions" else "location"
        if r["case"].startswith("B02"):                                            # a split OR half is removed
            for cat in o["categories"].values():
                seen = set()
                cat[:] = [i for i in cat if not (i.get("alternatives") and (i["source_text"] in seen or seen.add(i["source_text"])))]
        if r["case"].startswith("B04"):                                            # a regression elsewhere: an item disappears
            for cat in o["categories"].values():
                cat[:] = cat[:-1] if cat else cat
        r["raw"] = json.dumps(o, ensure_ascii=False)
    rep = cp.compare(CASES, V22_RUN, _fake_v23_dir(tmp_path, mutate), labels=("v2-2", "v2-3"), focus="structure-routing")
    F = rep["focus"]
    b, c = F["sides"]["baseline"], F["sides"]["candidate"]
    assert c["routing_audit"]["run1"]["label_is_list_name"] < b["routing_audit"]["run1"]["label_is_list_name"]
    assert c["routing_audit"]["run2"] == b["routing_audit"]["run2"]                  # run 2 untouched
    assert c["split_or_extras"]["run1"] <= b["split_or_extras"]["run1"]
    assert "item recall" in F["metrics_worse_than_baseline"]                         # the item removed in B04 is a regression elsewhere, reported as such
    # diagnostics never move a gate
    saved = {g["gate"]: g["candidate"] for g in rep["gates"]}
    assert saved == rep["sides"]["candidate"]["scored"]["gates"]
    assert rep["sides"]["baseline"]["saved_results_check"]["gates_match"] is True


def test_the_focus_integrity_check_flags_a_run_that_is_not_v2_3_or_not_v2_2(tmp_path):
    rep = cp.compare(CASES, V22_RUN, V22_RUN, labels=("v2-2", "v2-3"), focus="structure-routing")             # the same v2-2 run as the "candidate"
    assert rep["focus"]["integrity"]["baseline"]["matches"] and not rep["focus"]["integrity"]["candidate"]["matches"]
    rep2 = cp.compare(CASES, BASE_RUN, _fake_v23_dir(tmp_path), labels=("v2-2", "v2-3"), focus="structure-routing")  # the v2-1 run as the "v2-2" side
    assert not rep2["focus"]["integrity"]["baseline"]["matches"]


def test_the_cli_defaults_to_the_recorded_v2_2_run_and_writes_the_report(tmp_path, capsys):
    out = tmp_path / "cmp"
    assert cp.main(["--candidate", str(_fake_v23_dir(tmp_path)), "--focus", "structure-routing", "--out", str(out)]) == 0
    md = (out / "comparison.md").read_text(encoding="utf-8")
    assert "v2-2 vs v2-3" in md and "### OR/AND structure" in md and "Metrics worse than v2-2: none." in md
    rep = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    assert rep["sides"]["baseline"]["dir"].endswith("v2-2_run1") and rep["labels"] == {"baseline": "v2-2", "candidate": "v2-3"}
    # without --focus the old report (baseline = v2-1) is unchanged
    capsys.readouterr()
    assert cp.main(["--candidate", str(V22_RUN)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "# requirements-v2 extraction: baseline vs candidate"


def test_nothing_in_the_registries_activation_or_migrations_knows_v2_3():
    for sub in ("routers", "workers", "services", "db"):
        for path in (BACKEND / sub).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sql", ".txt"} and "__pycache__" not in path.parts:
                assert "criteria_extraction_v2-3" not in path.read_text(encoding="utf-8", errors="ignore"), path
    assert "criteria_extraction_v2-3" not in (BACKEND / "main.py").read_text(encoding="utf-8")
