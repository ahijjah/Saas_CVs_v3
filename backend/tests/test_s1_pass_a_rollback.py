"""
Rollback s1a-1.2 (Option D, withdrawn) -> s1a-1.1 (active, a BASELINE CANDIDATE, not an approved version).

Verifies, offline only (scripted clients, no model call, no network, no database, no held-out fixture):
  1. the active Pass A prompt, request bytes and cache identity are exactly s1a-1.1's (values pinned from the
     s1a-1.1 commit);
  2. no runtime code requires, reads, checks or merges "names_role_or_work";
  3. the s1a-1.1 answer wire is unchanged (a stray field is tolerated and ignored, never required);
  4. the recorded real s1a-1.1 MAIN answers replay through the restored runtime to exactly the recorded
     observations and summary (original extraction / validation / repair behaviour);
  5. strict F5, F6 and the downstream fail-closed blocks are unchanged;
  6. the harness keeps the frozen target-basis fixture infrastructure and repair_rate (reported only), with the
     s1a-1.1 gate set and the zero-unsafe hard gate unchanged;
  7. the three recorded MAIN result files and the withdrawn s1a-1.2 prompt are preserved for audit (SHA-pinned).
Since S1-A-1.3 the ACTIVE prompt is s1a-1.3; s1a-1.1 stays pinned and runnable by explicit version under its own
contract (pass_a.CONTRACTS["s1a-1.1"]), so points 1 and 4 now verify s1a-1.1 BY VERSION: its prompt bytes, request
bytes and cache identity, and the exact replay of its recorded MAIN runs.
"""
import ast
import asyncio
import copy
import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_requirements.jd_text import JDText
from services.s1_two_pass import assemble as asm
from services.s1_two_pass import pass_a as pa
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a
from services.s1_two_pass import schema as sc

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
PKG = BACKEND / "services" / "s1_two_pass"
RESULTS = SCRIPTS / "s1_eval_results" / "pass_a"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", SCRIPTS / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
ctx = ev.ctx

S1A_11 = "952299303431f68d62b9544d6897baa488855c37c22d0fd2890789b15d463e11"
S1A_12 = "f7ec01e2816744322205a889e2834270a30decea625a84355fa47c70e416afd3"
TARGET_BASIS_FIXTURE = "02a452bba2de4374f394db5a9d4d11065ab80f70749f3337be5550007d304293"
TARGET_BASIS_V2 = "2daadcb18350dc908fb48ab6081c8ba2fda010f78c2fbb9c55233f780d2ea3e0"
# pinned from the s1a-1.1 commit (b0fa6eb) for MAIN case CM30
CM30_INPUT_HASH = "86bd6b6012e176afa19b55820f6bb5cacc21fac3ca1f00e8574bf71353f4c426"
CM30_CACHE_KEY = "e8b167ddf7d0fe45aac5594d6064b01a42fcd2247ba9f6056462e3159d3b2f6b"
CM30_CALL_SHA = "678b04ab46cc4db1bbffcfe51fc4fc0936fde4a20781e6e4ccde2a912511574f"
RECORDED = {   # file -> (sha256, prompt version, fingerprint, unsafe loss)
    "s1a-1.0_main_r1.results.json": ("64bf800b6422094dc7e3f14e6a3d4b243085c3c61f6750430a09f4382c4c4b46",
                                     "s1a-1.0", "4caabb71429c", 18),
    "s1a-1.1_main_r1.results.json": ("82ac8ab35be2479e6e7564f3e469d0e3789188e6615c250d9b42f6edad5e50d0",
                                     "s1a-1.1", "952299303431", 1),
    "s1a-1.2_main_r1.results.json": ("289b827f383cd65e6160fee20e80c61ff6a8419f9e81cc65d96e18382960a72d",
                                     "s1a-1.2", "f7ec01e28167", 9),
}
MAIN = json.loads((SCRIPTS / "s1_eval_fixtures" / "s1_ctx_main_cases.json").read_text(encoding="utf-8"))
MAIN_BY = {c["id"]: c for c in MAIN["cases"]}
TB = json.loads((SCRIPTS / "s1_eval_fixtures" / "s1a_target_basis_cases.json").read_text(encoding="utf-8"))
NRW = "names_role_or_work"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def recorded(name):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.items.pop(0)),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def main_job(case_id):
    case = MAIN_BY[case_id]
    return case, ctx.case_jd(case), ctx.case_analysis(case), ctx.case_criteria(case)


def cm30_answer(**extra):
    case, jd, a, crits = main_job("CM30")
    it = {"criterion_id": crits[0].criterion_id, "requirement_spans": [{"line": 4, "text": case["jd_lines"][3]}],
          "restrictions": [{"line": 4, "text": "project management", "kind": "function"}],
          "target_basis": "targets", "duration": "D1", "ambiguity": [], "note": "n", **extra}
    return case, jd, a, json.dumps({"criteria": [it]})


# ── 1. s1a-1.1 prompt and request identity, by explicit version ─────────────

S1A_11_FILE = prompt_a.PROMPT_DIR / "s1a-1.1.txt"


class TestS1a11Pinned:
    def test_version_file_and_hash(self):
        assert sc.S1A_PROMPT_VERSION == "s1a-1.3"                     # S1-A-1.3: no longer the active prompt
        assert prompt_a.PROMPT_SHA256["s1a-1.1"] == prompt_a.pass_a_prompt_sha256("s1a-1.1") == S1A_11
        assert hashlib.sha256(S1A_11_FILE.read_bytes()).hexdigest() == S1A_11
        assert prompt_a.pass_a_prompt_fingerprint("s1a-1.1") == "952299303431"
        assert prompt_a.load_pass_a_prompt("s1a-1.1").encode("utf-8") == S1A_11_FILE.read_bytes()
        assert NRW not in prompt_a.load_pass_a_prompt("s1a-1.1") and NRW not in prompt_a.load_pass_a_prompt()
        assert pa.pass_a_contract("s1a-1.1") == pa.CONTRACTS["s1a-1.1"] and not pa.CONTRACTS["s1a-1.1"].where_evidence
        assert pa.CONTRACTS["s1a-1.1"].basis_message == pa.BASIS_REREAD_MESSAGE

    def test_request_bytes_and_cache_key_match_the_s1a_1_1_commit(self):
        case, jd, a, crits = main_job("CM30")
        req = pa.build_pass_a_request(JDText(jd), crits)
        assert req.input_hash == CM30_INPUT_HASH                     # the request payload is unchanged by S1-A-1.3
        assert pa.pass_a_cache_key(req, prompt_version="s1a-1.1") == CM30_CACHE_KEY
        assert pa.pass_a_cache_key(req) != CM30_CACHE_KEY             # the s1a-1.3 key is another key
        call = pr.build_pass_a_call(req, prompt_version="s1a-1.1")
        assert hashlib.sha256(json.dumps(call, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == CM30_CALL_SHA
        assert call["messages"][0]["content"].encode("utf-8") == S1A_11_FILE.read_bytes()

    def test_harness_pins(self):
        assert (ev.PINNED["prompt_version"], ev.PINNED["prompt_fingerprint"], ev.PINNED["prompt_sha256"]) == (
            "s1a-1.3", "0cf68cadc53d", prompt_a.PROMPT_SHA256["s1a-1.3"])
        assert ev.check_pins(ev.FIXTURES["main"], "main") == []
        assert ev.check_pins(ev.FIXTURES["target_basis"], "target_basis") == []


# ── 2. no runtime dependency on the withdrawn field ─────────────────────────

class TestNoRuntimeDependency:
    @pytest.mark.parametrize("name", ["schema.py", "pass_a.py", "pass_a_runner.py", "assemble.py", "pass_b.py",
                                      "__init__.py"])
    def test_runtime_modules_never_mention_it(self, name):
        text = (PKG / name).read_text(encoding="utf-8")
        for frag in (NRW, "nrw", "NRW_"):
            assert frag not in text, (name, frag)

    def test_prompt_module_mentions_it_only_in_its_docstring(self):
        tree = ast.parse((PKG / "prompt_a.py").read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree)
        assert NRW in doc and "WITHDRAWN" in doc and "BASELINE CANDIDATE, NOT an approved version" in doc
        code = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None),
                                                                                    ast.Constant))]
        assert NRW not in "\n".join(ast.unparse(n) for n in code)

    def test_interfaces_carry_no_withdrawn_field(self):
        # S1-A-1.3 added where_evidence (audit evidence), nothing of Option D
        assert [f.name for f in dataclasses.fields(pa.PassACriterion)] == ["parsed", "target_basis", "where_evidence"]
        for attr in ("SCOPE_NAMES_ROLE_OR_WORK", "FIELD_NAMES_ROLE_OR_WORK", "NRW_REQUIRED_MESSAGE",
                     "NRW_DISAGREE_MESSAGE"):
            assert not hasattr(pa, attr), attr

    def test_harness_no_longer_scores_or_gates_it(self):
        src = (SCRIPTS / "s1_pass_a_eval.py").read_text(encoding="utf-8")
        assert "nrw" not in src and "names_role_or_work_accuracy" not in src
        assert set(ev.THRESHOLDS) == {"target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability",
                                      "failure_rate"}


# ── 3. the s1a-1.1 wire is unchanged ────────────────────────────────────────

class TestWire:
    def test_answer_without_the_field_validates_with_one_call(self):
        case, jd, a, raw = cm30_answer()
        client = FakeClient(raw)
        res = run(pr.run_pass_a_job(ctx.case_job_id(case), jd, a, client=client))
        assert res.status == "ok" and len(client.requests) == 1 and not res.outcome.meta["repair_used"]
        assert "nrw_disagreements" not in res.outcome.meta
        assert NRW not in res.frozen[0].artefact.audit

    @pytest.mark.parametrize("value", [True, False, "junk", None])
    def test_stray_field_is_ignored_never_required_never_checked(self, value):
        case, jd, a, plain = cm30_answer()
        _, _, _, stray = cm30_answer(**{NRW: value})
        outs = []
        for raw in (plain, stray):
            client = FakeClient(raw)
            res = run(pr.run_pass_a_job(ctx.case_job_id(case), jd, a, client=client))
            assert res.status == "ok" and len(client.requests) == 1
            outs.append([f.artefact.to_dict() for f in res.frozen])
        assert outs[0] == outs[1]

    def test_the_s1a_1_2_cm02_failure_shape_now_validates(self):
        # s1a-1.2 failed CM02 4/5 on its own field; under s1a-1.1 the same structured answer is simply valid
        case, jd, a, crits = main_job("CM02")
        it = {"criterion_id": crits[0].criterion_id, "requirement_spans": [{"line": 2, "text": case["jd_lines"][1]}],
              NRW: False, "restrictions": [{"line": 2, "text": "procurement", "kind": "function"}],
              "target_basis": "targets", "duration": "D1", "ambiguity": [], "note": "n"}
        client = FakeClient(json.dumps({"criteria": [it]}))
        res = run(pr.run_pass_a_job(ctx.case_job_id(case), jd, a, client=client))
        assert res.status == "ok" and len(client.requests) == 1
        assert [t.text for t in res.frozen[0].frame.targets] == ["procurement"]


# ── 4. recorded real s1a-1.1 MAIN answers replay exactly ────────────────────

class TestRecordedReplay:
    def test_every_recorded_s1a_1_1_run_reproduces_its_observation(self):
        d = recorded("s1a-1.1_main_r1.results.json")
        assert d["meta"]["prompt_version"] == "s1a-1.1" and d["meta"]["prompt_sha256"] == S1A_11
        records = []
        for r in d["records"]:
            case = MAIN_BY[r["case"]]
            obs = run(ev.run_case(case, ctx.ScriptedClient(*[c["content"] for c in r["raw"]]), "s1a-1.1"))
            # S1-A-1.3 observes where_evidence too; s1a-1.1 never produces it
            assert all(o.pop("where_evidence") in ([], None) for o in obs["criteria"])
            assert obs["criteria"] == r["criteria"], (r["case"], r["run"])
            assert obs["repair_used"] == r["repair_used"] and len(obs["calls"]) == len(r["calls"])
            records.append({"case": r["case"], "family": r["family"], "lang": r["lang"], "run": r["run"], **obs,
                            **ev.score(case, obs)})
        assert len(records) == 220
        s, o = ev.summarize(records), d["summary"]
        for k in ("target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability", "failure_rate",
                  "unstable_cases", "outcomes", "hard", "unsafe", "main_calls", "repair_calls", "target_expansion",
                  "misses", "failures"):
            assert json.loads(json.dumps(s[k])) == o[k], k       # recorded file went through JSON
        assert s["hard"]["unsafe_target_policy_loss"] == 1 and s["unsafe"][0]["case"] == "CM30"
        assert s["repair_rate"] == 0.0
        assert ev.evaluate_gates(s)["gates"] == d["gates"]["gates"]       # same verdicts: still NOT accepted
        assert d["gates"]["all_decided_pass"] is False


# ── 5. downstream fail-closed protections unchanged ─────────────────────────

class TestFailClosed:
    def test_strict_f5_default(self):
        assert sc.DEFAULT_ABSENT_POLICY == "strict"

    def test_recorded_cm30_run_5_stays_out_of_any_s2_view(self):
        d = recorded("s1a-1.1_main_r1.results.json")
        (r,) = [x for x in d["records"] if x["case"] == "CM30" and x["run"] == 5]
        case, jd, a, crits = main_job("CM30")
        pb = json.dumps({"criteria": [{"criterion_id": crits[0].criterion_id, "contexts": [], "context_spans": [],
                                       "target_gap": []}]})
        out = asm.run_scripted_job(ctx.case_job_id(case), jd, a, r["raw"][0]["content"], pb)
        (art,) = out.artifacts
        assert (art.target_state, art.spec_status) == ("target_absent_claimed", "needs_confirmation")
        for rr in (True, False):
            with pytest.raises(asm.S1V4ViewError) as e:
                asm.s2_views_v4(art, require_resolved=rr)
            assert e.value.code == "target_unconfirmed"

    @pytest.mark.parametrize("basis", ["unspecified", "total_experience", "setting_only"])
    def test_f6_still_blocks_repair_only_no_target_readings(self, basis):
        assert basis in pr.NO_TARGET_BASES
        case, jd, a, crits = main_job("CM30")
        cid = crits[0].criterion_id
        main = {"criterion_id": cid, "requirement_spans": [{"line": 4, "text": case["jd_lines"][3]}],
                "restrictions": [{"line": 4, "text": "project management", "kind": "function"}],
                "target_basis": "targets", "duration": "D1", "ambiguity": [], "note": "n"}
        rep = {**main, "restrictions": [{"line": 4, "text": "relevant", "kind": "vague"}] if basis == "unspecified"
               else [], "target_basis": basis}
        val = pa.PassAValidation(False, ["b"], {}, [pa.ScopedError(cid, ("target_basis",), "b")])
        _, _, f6 = pr.merge_pass_a(json.dumps({"criteria": [main]}), json.dumps({"criteria": [rep]}), val, crits)
        assert f6 and pr.F6_ERROR in f6[0]


# ── 6. harness: frozen fixture infrastructure kept, gates unchanged ─────────

class TestHarness:
    @pytest.mark.parametrize("fixture,version", [("main", None), ("target_basis", None), ("main", "s1a-1.1"),
                                                 ("target_basis@s1a-basis-1", "s1a-1.1")])
    def test_oracle_scores_perfectly(self, fixture, version):
        # current: MAIN + s1a-basis-2 under s1a-1.3; replay: MAIN + the archived s1a-basis-1 under s1a-1.1
        path = ev.FIXTURES[fixture] if fixture in ev.FIXTURES else ev.ARCHIVED_FIXTURES[fixture][0]
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        recs = run(ev.run_all(cases, runs=2, client_for=lambda c: ctx.ScriptedClient(ev.oracle_response(c)),
                              prompt_version=version))
        s = ev.summarize(recs)
        for k in ("target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability"):
            assert s[k] == 1.0, k
        assert s["failure_rate"] == 0.0 and s["repair_rate"] == 0.0 and s["hard"]["unsafe_target_policy_loss"] == 0
        g = ev.evaluate_gates(s)
        assert g["all_decided_pass"] and g["undecided"] == []

    def test_oracle_answers_carry_no_withdrawn_field(self):
        for case in MAIN["cases"] + TB["cases"]:
            for it in json.loads(ev.oracle_response(case))["criteria"]:
                assert NRW not in it

    def test_gate_set_is_the_s1a_1_1_set_and_zero_unsafe_still_fails(self):
        s = {"hard": {"unsafe_target_policy_loss": 1, "independence_failures": 0}, "target_accuracy": 1.0,
             "policy_accuracy": 1.0, "target_basis_accuracy": 1.0, "stability": 1.0, "failure_rate": 0.0,
             "repair_rate": 0.9}
        g = ev.evaluate_gates(s)
        assert set(g["gates"]) == {"hard:unsafe_target_policy_loss", "hard:independence_failures", "target_accuracy",
                                   "policy_accuracy", "target_basis_accuracy", "stability", "failure_rate"}
        assert g["gates"]["hard:unsafe_target_policy_loss"] is False and g["all_decided_pass"] is False
        assert ev.THRESHOLDS == {"target_accuracy": 0.95, "policy_accuracy": 0.95, "target_basis_accuracy": 0.95,
                                 "stability": 0.95, "failure_rate": 0.05}

    def test_repair_rate_is_reported_never_gated(self):
        s = {"hard": {"unsafe_target_policy_loss": 0, "independence_failures": 0}, "target_accuracy": 1.0,
             "policy_accuracy": 1.0, "target_basis_accuracy": 1.0, "stability": 1.0, "failure_rate": 0.0,
             "repair_rate": 1.0}
        assert ev.evaluate_gates(s)["all_decided_pass"] is True
        assert "repair_rate" in (SCRIPTS / "s1_pass_a_eval.py").read_text(encoding="utf-8")

    def test_fixture_choice_cli_and_no_heldout(self, tmp_path):
        assert set(ev.FIXTURES) == {"main", "target_basis"}
        # the s1a-1.1 target-basis fixture is archived byte for byte (reproducibility), never selectable
        path, sha = ev.ARCHIVED_FIXTURES["target_basis@s1a-basis-1"]
        assert sha == TARGET_BASIS_FIXTURE and hashlib.sha256(path.read_bytes()).hexdigest() == TARGET_BASIS_FIXTURE
        assert hashlib.sha256(ev.FIXTURES["target_basis"].read_bytes()).hexdigest() == ev.FIXTURE_SHA256["target_basis"]
        assert ev.FIXTURE_SHA256["target_basis"] == TARGET_BASIS_V2
        assert not any("heldout" in str(p) for p in ev.FIXTURES.values())
        assert ev.main(["--out", str(tmp_path / "o"), "--fixture", "target_basis", "--mode", "oracle",
                        "--runs", "1"]) == 0
        meta = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))["meta"]
        assert (meta["fixture"], meta["prompt_version"], meta["fixture_sha256"]) == (
            "target_basis", "s1a-1.3", TARGET_BASIS_V2)
        with pytest.raises(SystemExit):
            ev.main(["--out", str(tmp_path / "h"), "--fixture", "heldout"])

    def test_real_mode_still_refused_without_confirmation(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ctx, "make_real_client", lambda: (_ for _ in ()).throw(AssertionError("client")))
        assert ev.main(["--out", str(tmp_path), "--mode", "real", "--fixture", "target_basis"]) == 2
        assert "--confirm-real" in capsys.readouterr().out


# ── 7. audit preservation ───────────────────────────────────────────────────

class TestAudit:
    @pytest.mark.parametrize("name", sorted(RECORDED))
    def test_recorded_results_preserved_and_pinned(self, name):
        sha, version, fingerprint, unsafe = RECORDED[name]
        assert hashlib.sha256((RESULTS / name).read_bytes()).hexdigest() == sha
        d = recorded(name)
        assert (d["meta"]["prompt_version"], d["meta"]["prompt_fingerprint"], d["meta"]["mode"]) == (
            version, fingerprint, "real")
        assert d["meta"]["fixture"] == "main" and d["meta"]["fixture_sha256"] == ctx.FIXTURE_SHA256["main"]
        assert d["summary"]["hard"]["unsafe_target_policy_loss"] == unsafe and len(d["records"]) == 220

    def test_log_states_each_status(self):
        log = (RESULTS / "PASS_A_EVAL_LOG.md").read_text(encoding="utf-8")
        assert "**s1a-1.1 — BASELINE CANDIDATE, NOT AN APPROVED VERSION (superseded as the active prompt" in log
        assert "**s1a-1.3 — CURRENT ACTIVE PROMPT. CANDIDATE PENDING REAL EVALUATION (Stage B)" in log
        assert "**s1a-1.2 (Option D) — WITHDRAWN.**" in log and "**s1a-1.0 — FAILED.**" in log
        for name, (sha, *_rest) in RECORDED.items():
            assert name in log and sha in log, name

    def test_withdrawn_prompt_kept_loadable_only_by_explicit_version(self):
        p12 = prompt_a.load_pass_a_prompt("s1a-1.2")
        assert hashlib.sha256(p12.encode("utf-8")).hexdigest() == prompt_a.PROMPT_SHA256["s1a-1.2"] == S1A_12
        assert p12 != prompt_a.load_pass_a_prompt() and p12 != prompt_a.load_pass_a_prompt("s1a-1.1")
        start, end = p12.index("\n\n1a NAMES_ROLE_OR_WORK"), p12.index("\n\n2 TARGETS")
        assert (p12[:start] + p12[end:]).replace('"names_role_or_work": true, ', "") == (
            prompt_a.load_pass_a_prompt("s1a-1.1"))

    def test_no_production_code_reads_the_results_directory(self):
        # docstrings may point a reader at the log; no code (outside tests) may reference the directory
        for sub in ("services", "workers", "routers"):
            for p in (BACKEND / sub).rglob("*.py"):
                tree = ast.parse(p.read_text(encoding="utf-8"))
                if (tree.body and isinstance(tree.body[0], ast.Expr)
                        and isinstance(getattr(tree.body[0], "value", None), ast.Constant)):
                    tree.body = tree.body[1:]
                assert "s1_eval_results" not in ast.unparse(tree), p
