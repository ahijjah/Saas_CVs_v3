"""
Audit evidence of the s1a-1.3 target-basis Stage B (real model run on the VPS, 2026-10-08): the recorded results file
(scripts/s1_eval_results/pass_a/s1a-1.3_target_basis_r1.results.json, SYNTHETIC JDs and model outputs only) is
SHA-pinned, and its 220 recorded raw answers replay EXACTLY through the code: under the s1a-1.3 contract (the run's
own) and under the current contract (s1a-1.4 changed the prompt only). The diagnosis it supports is pinned too:
every wrong answer was a first answer "restrictions: [] + total_experience", accepted without repair.
Offline only: scripted clients, no model call, no network, no database.
"""
import asyncio
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
RESULTS = SCRIPTS / "s1_eval_results" / "pass_a"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", SCRIPTS / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
ctx = ev.ctx

FILE = RESULTS / "s1a-1.3_target_basis_r1.results.json"
SHA = "3524eb85b94dbb403ba5214a6b844e22ec8f98396ff30315e54ca01f9fe0f6d9"
D = json.loads(FILE.read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in json.loads(ev.FIXTURES["target_basis"].read_text(encoding="utf-8"))["cases"]}
KEYS = ("target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability", "failure_rate", "repair_rate",
        "unstable_cases", "outcomes", "hard", "unsafe", "misses", "failures", "main_calls", "repair_calls",
        "target_expansion", "s1a13")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestEvidence:
    def test_pinned_and_described(self):
        assert hashlib.sha256(FILE.read_bytes()).hexdigest() == SHA
        m = D["meta"]
        assert (m["mode"], m["prompt_version"], m["prompt_sha256"], m["model"], m["temperature"], m["runs"]) == (
            "real", "s1a-1.3", "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d", "gpt-4o-mini", 0.0, 5)
        assert (m["fixture"], m["fixture_version"], m["fixture_sha256"]) == (
            "target_basis", "s1a-basis-2", ev.FIXTURE_SHA256["target_basis"])
        assert len(D["records"]) == 220 and len({r["case"] for r in D["records"]}) == 44
        log = (RESULTS / "PASS_A_EVAL_LOG.md").read_text(encoding="utf-8")
        assert FILE.name in log and SHA in log

    def test_recorded_outcome(self):
        s = D["summary"]
        assert (s["target_accuracy"], s["policy_accuracy"], s["target_basis_accuracy"], s["stability"]) == (
            0.863, 0.8402, 0.7717, 0.9091)
        assert s["hard"] == {"unsafe_target_policy_loss": 35, "independence_failures": 0}
        assert s["outcomes"] == {"ok": 219, "failed_validation": 1, "failed_technical": 0}
        assert D["gates"]["all_decided_pass"] is False

    def test_every_wrong_answer_is_a_first_answer_total_experience(self):
        wrong = [(r, o) for r in D["records"] for o, c in zip(r["criteria"], r["checks"])
                 if c["ok"] and not (c["targets"] and c["policy"] and c["basis"] and not c["unsafe"])]
        assert len(wrong) == 50
        assert {o["basis"] for _, o in wrong} == {"total_experience"}
        assert not any(r["repair_used"] for r, _ in wrong)
        assert all(json.loads(r["raw"][0]["content"])["criteria"][0]["restrictions"] == [] for r, _ in wrong)
        lost = Counter(r["case"] for r, o in wrong if CASES[r["case"]]["gold"]["target_basis"] == "targets")
        assert lost == {"BE12": 5, "BE14": 5, "BE17": 5, "BE21": 5, "BA22": 5, "BA11": 4, "BA21": 1}


class TestExactReplay:
    @pytest.mark.parametrize("version", ["s1a-1.3", None])          # the run's own contract, and the current one
    def test_replays_exactly(self, version):
        records = []
        for r in D["records"]:
            case = CASES[r["case"]]
            obs = run(ev.run_case(case, ctx.ScriptedClient(*[c["content"] for c in r["raw"]]), version))
            assert obs["criteria"] == r["criteria"], (r["case"], r["run"])
            assert (obs["repair_used"], obs["outcome_detail"], len(obs["calls"])) == (
                r["repair_used"], r["outcome_detail"], len(r["calls"])), (r["case"], r["run"])
            records.append({"case": r["case"], "family": r["family"], "lang": r["lang"], "run": r["run"], **obs,
                            **ev.score(case, obs)})
        s = ev.summarize(records)
        for k in KEYS:
            assert json.loads(json.dumps(s[k])) == D["summary"][k], k
        assert ev.evaluate_gates(s)["gates"] == D["gates"]["gates"]
