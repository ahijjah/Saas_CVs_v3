"""
Offline tests for the Pass A evaluation harness (scripts/s1_pass_a_eval.py): gold derivation from the unchanged
MAIN fixture, the oracle self-check, exact metric arithmetic on hand-built records, scripted-answer mutations,
gates, and the real-mode guards. No API, no database; the held-out fixture is never read.
"""
import asyncio
import builtins
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", SCRIPTS / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
ctx = ev.ctx

MAIN_PATH = SCRIPTS / "s1_eval_fixtures" / "s1_ctx_main_cases.json"
MAIN = json.loads(MAIN_PATH.read_text(encoding="utf-8"))
BY = {c["id"]: c for c in MAIN["cases"]}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def records_for(cases, plan=None, runs=1):
    """plan: case id -> list (per run) of answer lists; default: the Pass A oracle answer."""
    plan, k = plan or {}, {}

    def client_for(case):
        i = k.get(case["id"], 0)
        k[case["id"]] = i + 1
        items = plan.get(case["id"])
        answers = items[min(i, len(items) - 1)] if items else [ev.oracle_response(case)]
        return ctx.ScriptedClient(*answers)
    return run(ev.run_all(cases, runs=runs, client_for=client_for))


def _hintless_function_case():
    for c in MAIN["cases"]:
        if len(c["criteria"]) == 1 and "restrictions" in c["criteria"][0]["oracle"]:
            g = ev.case_golds(c)[0]
            if g["basis"] == "targets" and len(g["targets"]) == 1 and g["targets"][0][1] == "function":
                return c
    raise AssertionError("no single hint-less function case in MAIN")


def _hinted_role_case():
    for c in MAIN["cases"]:
        if len(c["criteria"]) == 1 and "targets" in c["criteria"][0]["oracle"]:
            g = ev.case_golds(c)[0]
            if g["policy"] == "explicit_role" and len(g["targets"]) == 1:
                return c
    raise AssertionError("no single hinted role case in MAIN")


# ── fixture is reused unchanged; held-out is never touched ──────────────────

class TestFixture:
    def test_main_fixture_unchanged_and_pinned(self):
        assert hashlib.sha256(MAIN_PATH.read_bytes()).hexdigest() == ctx.FIXTURE_SHA256["main"]
        assert ev.FIXTURE == "main" and ctx.FIXTURES["main"] == MAIN_PATH

    def test_gold_derivation_does_not_mutate_the_fixture(self):
        before = copy.deepcopy(MAIN)
        for c in MAIN["cases"]:
            ev.case_golds(c)
            ev.oracle_response(c)
        assert MAIN == before

    def test_heldout_is_never_read(self, tmp_path, monkeypatch):
        held = str(ctx.FIXTURES["heldout"])
        real_open, real_read = builtins.open, Path.read_text

        def guard_open(f, *a, **k):
            assert str(f) != held, "held-out opened"
            return real_open(f, *a, **k)

        def guard_read(self, *a, **k):
            assert str(self) != held, "held-out read"
            return real_read(self, *a, **k)
        monkeypatch.setattr(builtins, "open", guard_open)
        monkeypatch.setattr(Path, "read_text", guard_read)
        assert ev.main(["--out", str(tmp_path / "o"), "--mode", "oracle", "--runs", "1",
                        "--cases", "CM01"]) == 0
        with pytest.raises(SystemExit):
            ev.main(["--out", str(tmp_path / "h"), "--fixture", "heldout"])

    def test_gold_matches_the_v3_gold_where_both_define_it(self):
        # the existing combined-harness gold (role/function targets + policy) agrees with the Pass A gold
        for c in MAIN["cases"]:
            for spec, g in zip(c["criteria"], ev.case_golds(c)):
                rf, pol = ctx.gold_targets(spec)
                if "restrictions" in spec["oracle"]:
                    assert [t for t, _ in g["targets"]] == [t for t, _ in rf], c["id"]
                assert g["policy"] == pol, c["id"]

    def test_gold_basis_rules(self):
        spec = lambda o: {"oracle": o}                                                     # noqa: E731
        R = lambda *ks: {"restrictions": [{"line": 1, "text": f"x{i}", "kind": k} for i, k in enumerate(ks)]}  # noqa
        assert ev.pass_a_gold(spec(R("function", "context")), [])["basis"] == "targets"
        assert ev.pass_a_gold(spec(R("role", "function")), [])["policy"] == "mixed"
        assert ev.pass_a_gold(spec(R("vague", "context")), []) == {"targets": [], "basis": "unspecified",
                                                                   "policy": "pure_duration"}
        assert ev.pass_a_gold(spec(R("context")), []) == {"targets": [], "basis": "setting_only", "policy": "sector"}
        assert ev.pass_a_gold(spec(R()), []) == {"targets": [], "basis": "total_experience",
                                                 "policy": "pure_duration"}
        hinted = {"targets": [{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "function"}]}
        assert ev.pass_a_gold(spec(hinted), ["A", "b"]) == {"targets": [("A", "role"), ("b", "function")],
                                                            "basis": "targets", "policy": "mixed"}

    def test_oracle_answers_carry_no_context(self):
        for c in MAIN["cases"]:
            raw = ev.oracle_response(c)
            for it in json.loads(raw)["criteria"]:
                assert "settings" not in it and "setting" not in it
                assert all(r["kind"] in ("role", "function", "vague") for r in it.get("restrictions", []))
                assert "ambiguous_context_scope" not in it["ambiguity"]
                assert it["target_basis"] in ("targets", "unspecified", "setting_only", "total_experience")


# ── oracle self-check ───────────────────────────────────────────────────────

class TestOracle:
    def test_every_main_case_scores_perfectly(self):
        recs = records_for(MAIN["cases"], runs=2)
        s = ev.summarize(recs)
        assert all(r["pass"] for r in recs), [r["case"] for r in recs if not r["pass"]]
        for k in ("target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability"):
            assert s[k] == 1.0, k
        assert s["failure_rate"] == 0.0 and s["hard"]["unsafe_target_policy_loss"] == 0
        assert s["repair_calls"] == 0 and s["main_calls"] == 2 * len(MAIN["cases"])
        assert s["criterion_runs"] == 2 * sum(len(c["criteria"]) for c in MAIN["cases"])
        g = ev.evaluate_gates(s)
        assert g["all_decided_pass"] and g["undecided"] == []

    def test_independence_probe_clean(self):
        assert ev.independence_probe(MAIN["cases"]) == []

    def test_cli_oracle_writes_results(self, tmp_path):
        assert ev.main(["--out", str(tmp_path / "o"), "--mode", "oracle", "--runs", "2"]) == 0
        data = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
        assert data["meta"]["stage"] == "pass_a" and data["meta"]["prompt_version"] == "s1a-1.3"
        assert data["meta"]["fixture_sha256"] == ctx.FIXTURE_SHA256["main"]
        assert data["gates"]["all_decided_pass"] is True
        assert (tmp_path / "o" / "report.md").read_text(encoding="utf-8").startswith("# S1 Pass A")


# ── exact metric arithmetic on hand-built records ───────────────────────────

def _rec(case, run, criteria, golds, *, job="ok"):
    obs = {"job_outcome": job, "reason": None, "criteria": criteria, "calls": [{"call": "main"}], "raw": [],
           "repair_used": False}
    checks = [ev.check(g, o) for g, o in zip(golds, criteria)]
    return {"case": case, "family": "f", "lang": "en", "run": run, **obs, "checks": checks,
            "gold": golds, "pass": all(c["ok"] and c["targets"] and c["policy"] and c["basis"] for c in checks)}


GF = {"targets": [("payroll administration", "function")], "basis": "targets", "policy": "functional"}
GR = {"targets": [("Internal Auditor", "role")], "basis": "targets", "policy": "explicit_role"}
GS = {"targets": [], "basis": "setting_only", "policy": "sector"}


def O(basis, policy, *targets, outcome="ok"):
    return {"outcome": outcome, "basis": basis, "policy": policy,
            "targets": None if outcome != "ok" else [{"text": t, "type": k} for t, k in targets]}


FAIL = O(None, None, outcome="validation")


class TestMetricArithmetic:
    def test_exact_rates(self):
        recs = [
            _rec("A", 1, [O("targets", "functional", ("payroll administration", "function"))], [GF]),   # all ok
            _rec("A", 2, [O("targets", "functional", ("payroll administration", "function"))], [GF]),
            _rec("B", 1, [O("targets", "explicit_role", ("Internal Auditor", "function"))], [GR]),     # type
            _rec("B", 2, [O("targets", "explicit_role", ("Internal Auditor", "role"))], [GR]),         # unstable
            _rec("C", 1, [O("total_experience", "pure_duration")], [GF]),                              # unsafe
            _rec("C", 2, [FAIL], [GF], job="failed"),                                                  # failure
            _rec("D", 1, [O("setting_only", "sector")], [GS]),
            _rec("D", 2, [O("total_experience", "pure_duration")], [GS]),                              # unsafe
        ]
        s = ev.summarize(recs)
        assert s["criterion_runs"] == 8 and s["outcomes"] == {"ok": 7, "failed_validation": 1, "failed_technical": 0}
        assert s["failure_rate"] == round(1 / 8, 4)
        # 7 ok runs: role/function targets ok in A1, A2, B2, D1 and D2 (no targets on either side; the sector ->
        # pure-duration loss of D2 is caught by policy, basis and the unsafe gate) -> 5/7
        assert s["target_accuracy"] == round(5 / 7, 4)
        # policy ok: A1, A2, B1, B2, D1 -> 5/7
        assert s["policy_accuracy"] == round(5 / 7, 4)
        # basis ok: A1, A2, B1, B2, D1 -> 5/7
        assert s["target_basis_accuracy"] == round(5 / 7, 4)
        assert s["hard"]["unsafe_target_policy_loss"] == 2
        assert {(x["case"], x["run"]) for x in s["unsafe"]} == {("C", 1), ("D", 2)}
        assert s["stability"] == 0.25 and s["unstable_cases"] == ["B", "C", "D"]
        g = ev.evaluate_gates(s)["gates"]
        assert g["hard:unsafe_target_policy_loss"] is False and g["failure_rate"] is False
        assert g["target_accuracy"] is False and g["stability"] is False

    def test_failed_runs_are_never_unsafe_and_never_scored(self):
        c = ev.check(GF, FAIL)
        assert c == {"ok": False, "targets": False, "policy": False, "basis": False, "unsafe": False,
                     "targets_lost": [], "policy_downgraded": False, "expanded": [], "sector_to_function": False}
        s = ev.summarize([_rec("X", 1, [FAIL], [GF], job="failed")])
        assert s["target_accuracy"] is None and s["failure_rate"] == 1.0
        assert ev.evaluate_gates(s)["gates"]["target_accuracy"] is False

    @pytest.mark.parametrize("gold,obs,unsafe", [
        (GF, O("targets", "functional", ("payroll administration", "function")), False),
        (GF, O("targets", "functional", ("payroll", "function")), False),          # narrower word run: carried
        (GF, O("targets", "explicit_role", ("payroll administration", "role")), False),  # type flip: not a loss
        (GF, O("targets", "functional", ("translation", "function")), True),      # different target: lost
        (GF, O("setting_only", "sector"), True),                                   # downgrade to sector
        (GR, O("unspecified", "pure_duration"), True),
        (GS, O("setting_only", "sector"), False),
        (GS, O("total_experience", "pure_duration"), True),                        # sector -> pure duration
        (GS, O("targets", "functional", ("x", "function")), False),                # narrowing is not unsafe
    ])
    def test_unsafe_definition(self, gold, obs, unsafe):
        assert ev.check(gold, obs)["unsafe"] is unsafe

    def test_target_equality_is_one_to_one_and_typed(self):
        g = [("Laboratory Technician", "role"), ("laboratory testing", "function")]
        assert ev._targets_equal(g, [{"text": "laboratory testing", "type": "function"},
                                     {"text": "Laboratory Technician", "type": "role"}])
        assert not ev._targets_equal(g, [{"text": "Laboratory Technician", "type": "role"}])
        assert not ev._targets_equal(g, [{"text": "Laboratory Technician", "type": "role"},
                                         {"text": "Laboratory Technician", "type": "role"}])
        assert not ev._targets_equal(g, [{"text": "Laboratory Technician", "type": "function"},
                                         {"text": "laboratory testing", "type": "role"}])
        assert ev._targets_equal([], [])

    def test_stability_needs_two_runs(self):
        s = ev.summarize([_rec("A", 1, [O("targets", "functional", ("payroll administration", "function"))], [GF])])
        assert s["stability"] is None
        g = ev.evaluate_gates(s)
        assert g["gates"]["stability"] is None and g["undecided"] == ["stability"]

    @pytest.mark.parametrize("metric,value,ok", [
        ("target_accuracy", 0.95, True), ("target_accuracy", 0.9499, False),
        ("policy_accuracy", 0.95, True), ("policy_accuracy", 0.9499, False),
        ("target_basis_accuracy", 0.95, True), ("target_basis_accuracy", 0.9499, False),
        ("stability", 0.95, True), ("stability", 0.9499, False),
        ("failure_rate", 0.05, True), ("failure_rate", 0.0501, False),
    ])
    def test_threshold_boundaries(self, metric, value, ok):
        s = {"hard": {"unsafe_target_policy_loss": 0, "independence_failures": 0}, "target_accuracy": 1.0,
             "policy_accuracy": 1.0, "target_basis_accuracy": 1.0, "stability": 1.0, "failure_rate": 0.0}
        s[metric] = value
        assert ev.evaluate_gates(s)["gates"][metric] is ok

    def test_hard_gates(self):
        s = {"hard": {"unsafe_target_policy_loss": 1, "independence_failures": 0}, "target_accuracy": 1.0,
             "policy_accuracy": 1.0, "target_basis_accuracy": 1.0, "stability": 1.0, "failure_rate": 0.0}
        g = ev.evaluate_gates(s)
        assert g["gates"]["hard:unsafe_target_policy_loss"] is False and g["all_decided_pass"] is False
        s["hard"] = {"unsafe_target_policy_loss": 0, "independence_failures": 2}
        assert ev.evaluate_gates(s)["all_decided_pass"] is False


# ── scripted mutations through the real runner ──────────────────────────────

def _answer(case, mutate):
    data = json.loads(ev.oracle_response(case))
    mutate(data["criteria"][0])
    return json.dumps(data, ensure_ascii=False)


class TestScriptedMutations:
    def test_dropped_function_is_unsafe_target_loss(self):
        c = _hintless_function_case()

        def drop(it):
            it["restrictions"] = [r for r in it["restrictions"] if r["kind"] != "function"]
            it["target_basis"] = "total_experience"
        recs = records_for([c], {c["id"]: [[_answer(c, drop)]]})
        s = ev.summarize(recs)
        assert s["hard"]["unsafe_target_policy_loss"] == 1 and s["failure_rate"] == 0.0
        assert s["target_accuracy"] == 0.0 and s["policy_accuracy"] == 0.0 and s["target_basis_accuracy"] == 0.0
        assert ev.evaluate_gates(s)["gates"]["hard:unsafe_target_policy_loss"] is False

    def test_type_flip_costs_target_and_policy_accuracy_but_is_not_unsafe(self):
        c = _hinted_role_case()

        def flip(it):
            it["targets"][0]["type"] = "function"
        s = ev.summarize(records_for([c], {c["id"]: [[_answer(c, flip)]]}))
        assert s["target_accuracy"] == 0.0 and s["policy_accuracy"] == 0.0
        assert s["target_basis_accuracy"] == 1.0 and s["hard"]["unsafe_target_policy_loss"] == 0

    def test_failures_counted(self):
        c = _hinted_role_case()
        recs = records_for([c], {c["id"]: [[RuntimeError("down")], ["{}", "{}"]]}, runs=2)
        s = ev.summarize(recs)
        assert s["outcomes"] == {"ok": 0, "failed_validation": 1, "failed_technical": 1}
        assert s["failure_rate"] == 1.0 and s["hard"]["unsafe_target_policy_loss"] == 0
        assert [f["reason"] for f in s["failures"]] == ["ai_unavailable", "validation_failed"]

    def test_instability_across_runs(self):
        c = _hinted_role_case()

        def flip(it):
            it["targets"][0]["type"] = "function"
        recs = records_for([c], {c["id"]: [[ev.oracle_response(c)], [_answer(c, flip)]]}, runs=2)
        s = ev.summarize(recs)
        assert s["stability"] == 0.0 and s["unstable_cases"] == [c["id"]]

    def test_repair_is_recorded(self):
        c = _hinted_role_case()

        def no_basis(it):
            del it["target_basis"]
        recs = records_for([c], {c["id"]: [[_answer(c, no_basis), ev.oracle_response(c)]]})
        s = ev.summarize(recs)
        assert s["repair_calls"] == 1 and s["target_accuracy"] == 1.0
        assert recs[0]["repair_used"] is True


# ── real-mode guards (no client is ever created here) ────────────────────────

class TestGuards:
    def test_real_needs_confirmation(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ctx, "make_real_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
        assert ev.main(["--out", str(tmp_path), "--mode", "real"]) == 2
        assert "--confirm-real" in capsys.readouterr().out

    def test_pins_hold_now(self):
        assert ev.check_pins(MAIN_PATH) == []

    def test_pin_mismatch_refuses(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ctx, "make_real_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
        monkeypatch.setitem(ev.PINNED, "prompt_fingerprint", "000000000000")
        assert ev.main(["--out", str(tmp_path), "--mode", "real", "--confirm-real"]) == 2
        assert "pins do not match" in capsys.readouterr().out

    def test_fixture_sha_mismatch_refuses(self, monkeypatch):
        monkeypatch.setitem(ev.FIXTURE_SHA256, "main", "0" * 64)          # the harness pins its own fixtures
        assert any("sha256" in p for p in ev.check_pins(MAIN_PATH))

    def test_dry_run_and_oracle_never_build_a_real_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ctx, "make_real_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
        assert ev.main(["--out", str(tmp_path / "d")]) == 0
        assert ev.main(["--out", str(tmp_path / "o"), "--mode", "oracle", "--runs", "1", "--cases", "CM01"]) == 0

    def test_stage_switch_in_the_existing_harness(self, tmp_path, capsys):
        assert ctx.main(["--stage", "pass_a", "--out", str(tmp_path / "d")]) == 0
        assert '"stage": "pass_a"' in capsys.readouterr().out
        assert ctx.main(["--stage", "pass_b", "--out", str(tmp_path / "x")]) == 2
        with pytest.raises(SystemExit):
            ctx.main(["--stage", "pass_a", "--out", str(tmp_path / "h"), "--allow-heldout"])

    def test_pinned_values(self):
        assert ev.PINNED == {"prompt_version": "s1a-1.3", "prompt_fingerprint": "0cf68cadc53d",
                             "prompt_sha256": "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d",
                             "s1_version": "2.0.0", "model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 4000}
        assert ev.THRESHOLDS == {"target_accuracy": 0.95, "policy_accuracy": 0.95, "target_basis_accuracy": 0.95,
                                 "stability": 0.95, "failure_rate": 0.05}
        assert ctx.CLIENT_MAX_RETRIES == 0
