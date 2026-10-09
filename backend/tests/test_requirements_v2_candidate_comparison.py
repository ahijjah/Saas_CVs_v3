"""Offline tests for the v2-2 comparison run: explicit candidate selection (baseline stays the default), prompt metadata in every
record, the reserve-before-call gate for the longer prompt, and the comparison report with its three-way diagnostics.
No model call, no network, no key. Passing says nothing about how a model performs with v2-2."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
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


ex = _load("req_v2_run_c", "requirements_v2_extraction_run.py")
cp = _load("req_v2_cmp_c", "requirements_v2_extraction_compare.py")
ev = ex.ev
CASES = ev.load_cases()
BY_ID = {c["id"]: c for c in CASES}
BASE_DIR = BACKEND / "benchmark_results" / "requirements_v2" / "baseline_v2-1"
FAKE_KEY = "sk-TESTKEY0123456789abcdefghijkl"


def _git_has(commit):
    try:
        subprocess.run(["git", "-C", str(ex.REPO), "cat-file", "-e", commit], check=True, capture_output=True)
        return True
    except Exception:
        return False


needs_candidate_commit = pytest.mark.skipif(not _git_has("916d1058"), reason="candidate commit not in this checkout")


class Fake:
    def __init__(self, script=None, completion=500, prompt=5200):
        self.calls, self.script, self.completion, self.prompt = [], script or {}, completion, prompt

    def __call__(self, request, timeout):
        n = len(self.calls) + 1
        self.calls.append(request)
        step = self.script.get(n)
        if isinstance(step, ex.ApiError):
            raise step
        step = step or {}
        case = next(c for c in CASES if c["jd"] in request["messages"][-1]["content"])
        return {"raw": step.get("raw", json.dumps(ev.reference_response(case), ensure_ascii=False)), "finish_reason": "stop",
                "model": ex.RUN_MODEL, "usage": {"prompt_tokens": self.prompt, "completion_tokens": self.completion}}


# ══ 1. explicit candidate selection; the baseline stays the default ═══════════════════════════════════════════════════
def test_baseline_is_the_default_and_builds_exactly_the_pinned_request():
    assert ex.SPECS["baseline"] is ex.BASELINE_SPEC and set(ex.SPECS) == {"baseline", "v2-2"}
    for c in CASES:
        built = ex.BASELINE_SPEC.build(c["jd"], c["job_metadata"])
        from services.requirements_v2.extraction.prompt import build_request
        assert built == build_request(c["jd"], c["job_metadata"], model=ex.RUN_MODEL)
    assert ex.BASELINE_SPEC.version == "criteria_extraction_v2-1" and ex.BASELINE_SPEC.sha256 == ex.BASELINE_V21_SHA256


def test_candidate_changes_only_the_system_prompt():
    for c in CASES:
        b, n = ex.BASELINE_SPEC.build(c["jd"], c["job_metadata"]), ex.CANDIDATE_SPEC.build(c["jd"], c["job_metadata"])
        assert b["messages"][1] == n["messages"][1]                                  # identical user message
        assert b["messages"][0]["content"] != n["messages"][0]["content"]
        assert n["messages"][0]["content"] == (BACKEND / "prompt_candidates" / "criteria_extraction_v2-2" / "criteria_extraction_v2-2.txt").read_text(encoding="utf-8")
        for k in ("model", "temperature", "max_tokens", "response_format"):
            assert b[k] == n[k]
        assert (n["model"], n["temperature"], n["max_tokens"], n["response_format"]) == ("gpt-4o-mini-2024-07-18", 0.1, 6000, {"type": "json_object"})


def test_cli_default_is_baseline_and_v2_2_is_explicit(capsys):
    assert ex.main(["--preflight"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["prompt_key"] == "baseline" and d["prompt_version"] == "criteria_extraction_v2-1" and d["candidate_commit"] is None
    if _git_has("916d1058"):
        assert ex.main(["--preflight", "--prompt", "v2-2"]) == 0
        d = json.loads(capsys.readouterr().out)
        assert (d["prompt_key"], d["prompt_version"], d["prompt_sha256"], d["candidate_commit"]) == ("v2-2", "criteria_extraction_v2-2", ex.CANDIDATE_V22_SHA256, "916d1058")
    with pytest.raises(SystemExit):
        ex.main(["--preflight", "--prompt", "v2-3"])


# ══ 2. the candidate hash / manifest / commit are verified; the baseline's frozen checks are kept ═══════════════════════
@needs_candidate_commit
def test_candidate_verifies_against_its_commit_and_manifest():
    assert ex.verify_candidate() == []
    manifest = json.loads((ex.CANDIDATE_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == ex.CANDIDATE_V22_SHA256 == hashlib.sha256((ex.CANDIDATE_DIR / "criteria_extraction_v2-2.txt").read_bytes()).hexdigest()
    assert ex.load_candidate_v22() == (ex.CANDIDATE_DIR / "criteria_extraction_v2-2.txt").read_text(encoding="utf-8")


def test_candidate_load_refuses_a_changed_text_or_manifest(tmp_path, monkeypatch):
    orig, d = ex.CANDIDATE_DIR, tmp_path / "cand"
    shutil.copytree(orig, d)
    monkeypatch.setattr(ex, "CANDIDATE_DIR", d)
    assert ex.load_candidate_v22()
    (d / "criteria_extraction_v2-2.txt").write_text((d / "criteria_extraction_v2-2.txt").read_text(encoding="utf-8") + "\nextra", encoding="utf-8")
    with pytest.raises(ex.CandidateIntegrityError):
        ex.load_candidate_v22()
    shutil.copy(orig / "criteria_extraction_v2-2.txt", d / "criteria_extraction_v2-2.txt")
    m = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    m["sha256"] = "0" * 64                                                          # manifest edited together with nothing else
    (d / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ex.CandidateIntegrityError):
        ex.load_candidate_v22()


def test_candidate_verification_detects_changed_added_or_uncommitted_files(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    d = tmp_path / "backend" / "prompt_candidates" / "criteria_extraction_v2-2"
    shutil.copytree(ex.CANDIDATE_DIR, d)
    git("add", "."); git("commit", "-qm", "cand")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for name, val in (("REPO", tmp_path), ("CANDIDATE_COMMIT", sha), ("CANDIDATE_DIR", d)):
        monkeypatch.setattr(ex, name, val)
    assert ex.verify_candidate() == []
    (d / "CHANGES.md").write_text("edited", encoding="utf-8")
    assert any("differs" in p for p in ex.verify_candidate()) and any("uncommitted" in p for p in ex.verify_candidate())
    git("commit", "-qam", "edit")
    assert any("differs" in p for p in ex.verify_candidate())
    (d / "NEW.txt").write_text("x", encoding="utf-8"); git("add", "."); git("commit", "-qm", "add")
    assert any("added to the candidate" in p for p in ex.verify_candidate())


def test_both_prompts_keep_the_frozen_file_check(monkeypatch):
    monkeypatch.setattr(ex, "verify_frozen", lambda: ["differs from 059c56b: backend/services/requirements_v2/x.py"])
    monkeypatch.setattr(ex, "verify_candidate", lambda: [])
    for spec in (ex.BASELINE_SPEC, ex.CANDIDATE_SPEC):
        pf = ex.preflight_offline(CASES, spec)
        assert not pf["ok"] and any("differs from 059c56b" in p for p in pf["problems"])
    monkeypatch.setattr(ex, "verify_frozen", lambda: [])
    monkeypatch.setattr(ex, "verify_candidate", lambda: ["differs from 916d1058: x"])
    assert not ex.preflight_offline(CASES, ex.CANDIDATE_SPEC)["ok"]
    assert ex.preflight_offline(CASES, ex.BASELINE_SPEC)["ok"]                       # the baseline never depends on the candidate


def test_the_limits_settings_and_model_are_unchanged():
    p = ev.PLAN
    assert (p["max_calls"], p["runs_per_case"], p["max_total_tokens"], p["max_cost_usd"], p["wall_clock_limit_s"], p["per_call_timeout_s"], p["retries"],
            p["max_completion_tokens_per_call"]) == (24, 2, 200_000, 0.25, 1800, 90, 0, 6000)
    assert ex.RUN_MODEL == "gpt-4o-mini-2024-07-18" and (ex.PRICE_IN, ex.PRICE_OUT) == (0.15e-6, 0.60e-6)


# ══ 3. prompt version and hash are in every record and in the run metadata ═══════════════════════════════════════════════
@pytest.mark.parametrize("spec_key", ["baseline", "v2-2"])
def test_every_call_record_and_the_run_metadata_carry_the_selected_prompt(tmp_path, spec_key):
    spec = ex.SPECS[spec_key]
    out = tmp_path / "out"
    seen = {}

    class Probe(Fake):
        def __call__(self, request, timeout):
            if not self.calls:
                seen["meta_before_first_call"] = json.loads((out / "meta.json").read_text(encoding="utf-8"))     # written before any call
            return super().__call__(request, timeout)
    summary = ex.execute(CASES, Probe(script={4: ex.ApiError("auth", 401)}), out, count_fn=lambda m: 5300, spec=spec)
    assert summary["stopped_by"] == "auth_or_model_error" and summary["calls_made"] == 4                         # a partial run
    recs = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 4
    for r in recs:
        assert (r["prompt_version"], r["prompt_sha256"], r["prompt_key"]) == (spec.version, spec.sha256, spec.key) and r["prompt_source"]
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert seen["meta_before_first_call"] == meta
    assert (meta["prompt_version"], meta["prompt_sha256"], meta["model"]) == (spec.version, spec.sha256, "gpt-4o-mini-2024-07-18")
    assert meta["settings"] == {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}}
    assert meta["limits"]["max_calls"] == 24 and meta["limits"]["max_total_tokens"] == 200_000 and meta["frozen_benchmark_commit"] == "059c56b"
    assert meta["candidate_commit"] == ("916d1058" if spec_key == "v2-2" else None)
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert (run["prompt_version"], run["prompt_sha256"]) == (spec.version, spec.sha256)
    scored = ex.score(CASES, out)
    assert scored["run_meta"]["prompt_sha256"] == spec.sha256


def test_the_default_execute_is_the_baseline(tmp_path):
    ex.execute(CASES, Fake(), tmp_path / "o", count_fn=lambda m: 2600)
    recs = [json.loads(x) for x in (tmp_path / "o" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 24 and {r["prompt_version"] for r in recs} == {"criteria_extraction_v2-1"}


def test_candidate_runs_send_the_candidate_prompt_and_quarantine_its_echo(tmp_path):
    f = Fake()
    ex.execute(CASES, f, tmp_path / "o", count_fn=lambda m: 5300, spec=ex.CANDIDATE_SPEC)
    assert len(f.calls) == 24 and all(c["messages"][0]["content"] == ex.CANDIDATE_SPEC.text() for c in f.calls)
    rule_line = next(ln for ln in ex.CANDIDATE_SPEC.leak_lines() if '"' in ln)                 # JSON escapes the quotes in the raw text
    g = Fake(script={2: {"raw": json.dumps({"x": rule_line})}})
    s = ex.execute(CASES, g, tmp_path / "p", count_fn=lambda m: 5300, spec=ex.CANDIDATE_SPEC)
    assert s["stopped_by"] == "quarantine_system_prompt_text" and len(g.calls) == 2
    assert all("ADDITIONAL EXAMPLES" not in ln for ln in ex.CANDIDATE_SPEC.leak_lines())


# ══ 4. reservations for the longer prompt; the shared gate; the limits stay practical ═══════════════════════════════════
def test_reserve_is_input_plus_the_full_output_allowance_for_the_candidate():
    count = lambda m: 5300
    plan = ev.PLAN
    need = 5300 + 6000
    assert ex.gate(0, plan["max_total_tokens"] - need, 0.0, 0.0, 5300, plan) is None
    assert ex.gate(0, plan["max_total_tokens"] - need + 1, 0.0, 0.0, 5300, plan) == "token_budget_reserve"
    assert ex.gate(0, 0, plan["max_cost_usd"] - 0.0001, 0.0, 5300, plan) == "cost_budget_reserve"
    assert ex.gate(24, 0, 0.0, 0.0, 5300, plan) == "max_calls"
    assert ex.gate(0, 0, 0.0, plan["wall_clock_limit_s"] - plan["per_call_timeout_s"] + 1, 5300, plan) == "wall_clock"
    assert ex.gate(0, 0, 0.0, 0.0, 5300, plan, stop_file=True) == "manual_stop"


@pytest.mark.parametrize("usage", ["expected", "tight_cap", "cap"])
def test_simulation_and_execution_agree_because_they_share_one_gate(tmp_path, usage):
    count = lambda m: 5300
    plan = dict(ev.PLAN, max_total_tokens={"expected": 200_000, "tight_cap": 60_000, "cap": 200_000}[usage])
    comp = 6000 if usage == "cap" else 700
    sim = ex.simulate_calls(CASES, ex.CANDIDATE_SPEC, count, lambda c, r, e: (5100, comp), lambda c, r: 5.0, plan)
    f = Fake(completion=comp, prompt=5100)
    run = ex.execute(CASES, f, tmp_path / "o", count_fn=count, spec=ex.CANDIDATE_SPEC, plan=plan)
    assert (sim["calls_made"], sim["tokens"], sim["stopped_by"]) == (run["calls_made"], run["actual_tokens"], run["stopped_by"])
    assert run["actual_tokens"] <= plan["max_total_tokens"]
    if usage == "expected":
        assert run["calls_made"] == 24 and run["stopped_by"] is None


def test_assessment_reproduces_the_real_baseline_and_shows_the_candidate_fits():
    baseline = ex.load_baseline_calls()
    assert len(baseline) == 24
    count, method = ex.make_counter()
    a0 = ex.budget_assessment(CASES, ex.BASELINE_SPEC, count, method, baseline=baseline)
    A = list(a0["scenarios"].values())[0]
    real_tokens = sum(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in baseline)
    if method.startswith("heuristic"):                                              # the projection method reproduces what the VPS really used
        assert abs(A["tokens"] - real_tokens) / real_tokens < 0.01 and A["calls_made"] == 24
        assert 0.85 < a0["calibration_from_baseline"]["min"] <= a0["calibration_from_baseline"]["max"] < 0.92
    a2 = ex.budget_assessment(CASES, ex.CANDIDATE_SPEC, count, method, baseline=baseline)
    lp = a2["limits_practical"]
    assert lp["all_24_calls_A_expected"] and lp["all_24_calls_B_growth"]
    assert lp["token_headroom_pct_A"] > 20 and lp["token_headroom_pct_B"] > 20
    assert lp["cost_usd_B"] < 0.25 * 0.2 and lp["wall_clock_s_B"] < 1800 * 0.25
    assert lp["stress_C_stopped_by"] == "token_budget_reserve" and lp["stress_C_calls_before_reserve_stop"] < 24     # the cap still binds, by reservation
    lo, hi = a2["executor_reserve_range_tokens"]
    assert 11_000 < lo <= hi < 15_000
    assert all(r["executor_reserve_tokens_per_call"] > b["executor_reserve_tokens_per_call"] for r, b in zip(a2["per_case"], a0["per_case"]))


def test_preflight_reports_key_presence_only_and_checks_the_network_without_sending_anything(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    opened = []

    class Sock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ex.socket, "create_connection", lambda addr, timeout=None: opened.append(addr) or Sock())
    assert ex.main(["--preflight", "--check-network"]) == 0
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["openai_key_present"] is True and FAKE_KEY not in out and d["network"] == {"host": "api.openai.com", "reachable": True}
    assert opened == [("api.openai.com", 443)]
    monkeypatch.setattr(ex.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    assert ex.check_network()["reachable"] is False
    monkeypatch.delenv("OPENAI_API_KEY")
    ex.main(["--preflight"])
    assert json.loads(capsys.readouterr().out)["openai_key_present"] is False


# ══ 5. comparison report: gates untouched, diagnostics separate ═════════════════════════════════════════════════════════
def test_baseline_data_is_intact_and_rescoring_reproduces_the_saved_results():
    m = json.loads((BASE_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in m["files"].items():
        assert hashlib.sha256((BASE_DIR / name).read_bytes()).hexdigest() == digest
    assert (m["prompt_version"], m["prompt_sha256"]) == ("criteria_extraction_v2-1", ex.BASELINE_V21_SHA256)
    rep = cp.compare(CASES, BASE_DIR, BASE_DIR)
    chk = rep["sides"]["baseline"]["saved_results_check"]
    assert chk == {"gates_match": True, "summary_match": True, "consistency_match": True}          # the operator's results.json is reproduced exactly
    assert all(g["change"] == "same" for g in rep["gates"]) and rep["regressions"]["gates"] == []
    failing = sorted(g["gate"].split()[0] for g in rep["gates"] if g["baseline"] is False)
    assert failing == ["G1", "G10", "G12", "G4", "G5", "G7", "G8", "G9"]


def test_diagnostics_never_change_a_gate_or_a_score():
    runs = cp.answers_of(cp.load_records(BASE_DIR))
    before = copy.deepcopy(runs)
    s1 = cp.rescore(CASES, runs)
    cp.diagnose_runs(CASES, runs)
    s2 = cp.rescore(CASES, runs)
    assert runs == before and s1["gates"] == s2["gates"] and s1["summary"] == s2["summary"] and s1["consistency"] == s2["consistency"]


def test_baseline_unmatched_items_split_into_the_three_causes():
    d = cp.diagnose_runs(CASES, cp.answers_of(cp.load_records(BASE_DIR)))
    t1, t2 = d["run1"]["totals"], d["run2"]["totals"]
    assert (t1["truly_omitted"], t1["present_invalid_evidence"], t1["valid_rejected_by_matching"]) == (12, 5, 4)
    assert (t2["truly_omitted"], t2["present_invalid_evidence"], t2["valid_rejected_by_matching"]) == (12, 4, 2)
    for t in (t1, t2):
        assert t["expected_items"] == t["matched"] + t["truly_omitted"] + t["present_invalid_evidence"] + t["valid_rejected_by_matching"]
    cond = d["run1"]["totals"]
    assert cond["conditions_expected"] == 22 and cond["conditions_routed"] + cond["conditions_wrong_list"] + cond["conditions_omitted"] \
        + cond["conditions_invalid_evidence"] + cond["conditions_as_scored_item"] == 22
    b10 = d["run1"]["per_case"]["B10_ar_preferred_only"]["items"]
    assert len(b10["present_invalid_evidence"]) == 4 and not b10["truly_omitted"]                  # present, not omitted
    assert {x["subtype"] for x in b10["present_invalid_evidence"]} == {"source_text_not_found_in_job_description", "over_wide_span_joins_other_text"}
    b04 = d["run1"]["per_case"]["B04_en_preferred_only"]["items"]
    assert len(b04["truly_omitted"]) == 4 and not b04["present_invalid_evidence"]                   # genuinely omitted
    b06 = d["run1"]["per_case"]["B06_en_injection"]["items"]
    assert {x["subtype"] for x in b06["valid_rejected_by_matching"]} == {"sub_span_of_expected_evidence", "list_marker_prefix"}
    assert [x["returned"] for x in b06["extra_split_alternative_half"]] == ["Java"]


def _one_case(cid, mutate):
    case = BY_ID[cid]
    r = ev.reference_response(case)
    mutate(r)
    res = cp.parse_response(json.dumps(r, ensure_ascii=False), case["jd"], "stop")
    assert res.ok
    return cp.diagnose_items(case, res), ev.score_case(case, json.dumps(r, ensure_ascii=False))


def test_synthetic_causes_are_classified_independently():
    def drop_docker(r):
        r["categories"]["skills"] = [i for i in r["categories"]["skills"] if i["text"] != "Docker"]
    d, sc = _one_case("B06_en_injection", drop_docker)
    assert [x["expected"] for x in d["truly_omitted"]] == ["Docker"] and not d["present_invalid_evidence"] and not d["valid_rejected_by_matching"]

    def heading_joined(r):
        for i in r["categories"]["skills"]:
            if i["text"] == "Docker":
                i["source_text"] = "Preferred:\n- Docker."                                      # in the JD, but joins the heading to the entry
    d, sc = _one_case("B06_en_injection", heading_joined)
    assert [x["subtype"] for x in d["present_invalid_evidence"]] == ["over_wide_span_joins_other_text"] and not d["truly_omitted"]

    def not_in_jd(r):
        for i in r["categories"]["skills"]:
            if i["text"] == "Docker":
                i["source_text"] = "Preferred: Docker"                                           # not contiguous in the JD
    d, sc = _one_case("B06_en_injection", not_in_jd)
    assert [x["subtype"] for x in d["present_invalid_evidence"]] == ["source_text_not_found_in_job_description"]

    def bullet(r):
        for i in r["categories"]["skills"]:
            if i["text"] == "Docker":
                i["source_text"] = "- Docker."
    d, sc = _one_case("B06_en_injection", bullet)
    assert [x["subtype"] for x in d["valid_rejected_by_matching"]] == ["list_marker_prefix"] and "Docker" in sc["missing"]    # the scorer still counts it missing

    def split_or(r):
        r["categories"]["skills"] = [i for i in r["categories"]["skills"] if i["text"] != "Python or Java"] + [
            {"text": t, "importance": "required", "importance_cue": None, "source_text": "Python or Java", "origin": "stated", "alternatives": None, "experience": None}
            for t in ("Python", "Java")]
    d, sc = _one_case("B06_en_injection", split_or)
    assert [x["returned"] for x in d["extra_split_alternative_half"]] == ["Java"] and sc["precision"] < 1.0


def test_comparison_flags_regressions_and_keeps_gates_frozen(tmp_path):
    cand = tmp_path / "cand"
    cand.mkdir()
    recs = cp.load_records(BASE_DIR)
    for r in recs:
        if r["case"] == "B04_en_preferred_only":                                              # the candidate "fixes" B04 (oracle answer)
            r["raw"] = json.dumps(ev.reference_response(BY_ID[r["case"]]), ensure_ascii=False)
        if r["case"] == "B07_ar_accountant" and r["run"] == "run1":                           # and loses an item the baseline had
            raw = json.loads(r["raw"])
            raw["categories"]["skills"] = [i for i in raw["categories"]["skills"] if "Excel" not in i["text"]]
            r["raw"] = json.dumps(raw, ensure_ascii=False)
        r.update(prompt_version="criteria_extraction_v2-2", prompt_sha256=ex.CANDIDATE_V22_SHA256)
    (cand / "calls.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    (cand / "meta.json").write_text(json.dumps({"prompt_version": "criteria_extraction_v2-2", "prompt_sha256": ex.CANDIDATE_V22_SHA256, "model": ex.RUN_MODEL}), encoding="utf-8")
    rep = cp.compare(CASES, BASE_DIR, cand)
    b04 = next(r for r in rep["per_case"] if r["case"] == "B04_en_preferred_only")
    assert b04["baseline_run1"]["recall"] == 0.0 and b04["candidate_run1"]["recall"] == 1.0
    lost = rep["regressions"]["items_matched_in_baseline_not_in_candidate"]["run1"]
    assert any("B07_ar_accountant" in x and "Excel" in x for x in lost)
    assert rep["sides"]["candidate"]["meta"]["prompt_version"] == "criteria_extraction_v2-2" and rep["sides"]["candidate"]["meta"]["per_call_prompt_records"]
    assert rep["sides"]["baseline"]["meta"]["prompt_version"] == "criteria_extraction_v2-1"
    md = cp.render_markdown(rep)
    for needle in ("criteria_extraction_v2-2", "criteria_extraction_v2-1", "truly omitted", "present, invalid evidence", "valid, rejected by benchmark matching", "G12"):
        assert needle in md
    out = tmp_path / "rep"
    assert cp.main(["--candidate", str(cand), "--out", str(out)]) == 0 and (out / "comparison.md").exists() and (out / "comparison.json").exists()


def test_compare_module_never_imports_a_model_client_or_network():
    import ast
    for f in ("requirements_v2_extraction_compare.py",):
        tree = ast.parse((SCRIPTS / f).read_text(encoding="utf-8"))
        mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not mods & {"openai", "httpx", "requests", "aiohttp", "socket", "sqlalchemy", "database"}


def test_comparison_plan_states_the_unchanged_limits_hashes_and_caveats():
    md = (BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_comparison" / "PLAN.md").read_text(encoding="utf-8")
    for needle in (ex.CANDIDATE_V22_SHA256, "916d1058", "059c56b", "200,000", "$0.25", "1800 s", "gpt-4o-mini-2024-07-18", "none is raised",
                   "do not raise a limit without approval", "never change a score or gate", "held-out", "No model call has been made",
                   "truly omitted", "present, invalid evidence", "valid, rejected by benchmark matching"):
        assert needle in md, needle
    a = ex.budget_assessment(CASES, ex.CANDIDATE_SPEC, *ex.make_counter(), baseline=ex.load_baseline_calls())
    assert (round(a["limits_practical"]["token_headroom_pct_A"]), round(a["limits_practical"]["token_headroom_pct_B"])) == (32, 29) \
        or ex.make_counter()[1].startswith("tiktoken")                              # the table in the plan is the sandbox projection
