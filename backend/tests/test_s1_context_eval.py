"""Offline tests for the S1 s1-6 context evaluation harness and its fixtures (no API, no database)."""
import ast
import asyncio
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("s1_context_eval", BACKEND / "scripts" / "s1_context_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

MAIN, _ = ev.load_fixture("main")
HELD, _ = ev.load_fixture("heldout")
MAIN_BY = {c["id"]: c for c in MAIN["cases"]}
HELD_BY = {c["id"]: c for c in HELD["cases"]}
FIX = BACKEND / "scripts" / "s1_eval_fixtures"
QC_FIX = BACKEND / "scripts" / "qc_eval_fixtures"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def records_for(cases, plan=None, runs=1):
    """plan: case id -> list (per run) of raw answers; default: the oracle answer."""
    plan, k = plan or {}, {}

    def client_for(case):
        i = k.get(case["id"], 0)
        k[case["id"]] = i + 1
        items = plan.get(case["id"])
        return ev.ScriptedClient(*(items[i] if items else [ev.oracle_response(case)]))
    return run(ev.run_all(cases, runs=runs, client_for=client_for))


def with_settings(case, *per_criterion):
    """The oracle answer with the given settings / context restrictions per criterion (None = unchanged)."""
    d = json.loads(ev.oracle_response(case))
    for it, new in zip(d["criteria"], per_criterion):
        if new is None:
            continue
        if "settings" in it:
            it["settings"] = [{"line": ln, "text": t} for ln, t in new]
        else:
            it["restrictions"] = [r for r in it["restrictions"] if r["kind"] != "context"] + [
                {"line": ln, "text": t, "kind": "context"} for ln, t in new]
    return json.dumps(d, ensure_ascii=False)


# ── fixtures ────────────────────────────────────────────────────────────────

REQUIRED_MAIN = {"geographic", "multinational_positive", "multinational_negative", "government", "banking",
                 "sector", "project_type", "organisation_type", "two_and", "or_phrase", "generic_environment",
                 "about_us", "new_job_duty", "job_location", "preferred", "separate_sentence", "role_specific",
                 "alternative_scope", "arabic_prefix", "contiguous", "split", "compound", "tools"}


class TestFixtures:
    def test_sizes_versions_and_ids(self):
        assert MAIN["fixture_version"] == "s1-ctx-main-1" and 40 <= len(MAIN["cases"]) <= 45
        assert HELD["fixture_version"] == "s1-ctx-heldout-1" and 20 <= len(HELD["cases"]) <= 25
        ids = [c["id"] for c in MAIN["cases"] + HELD["cases"]]
        assert len(ids) == len(set(ids))

    def test_family_coverage(self):
        assert {c["family"] for c in MAIN["cases"]} == REQUIRED_MAIN
        held = {c["family"] for c in HELD["cases"]}
        assert held >= REQUIRED_MAIN - {"banking", "separate_sentence"}       # covered by two_and / sector cases
        for fx in (MAIN, HELD):
            cases = fx["cases"]
            assert sum(c["lang"] == "ar" for c in cases) >= 5 and sum(c["lang"] == "en" for c in cases) >= 15
            pol = [cr["expected"]["polarity"] for c in cases for cr in c["criteria"]]
            assert pol.count("positive") >= 10 and pol.count("negative") >= 7 and pol.count("scope") >= 2
            assert pol.count("compound") >= 1
            exp = [cr["expected"]["settings"] for c in cases for cr in c["criteria"]]
            assert any(len(s) == 2 for s in exp)                                     # AND
            assert any(isinstance(x, str) and " or " in x for s in exp for x in s)   # OR inside one context
            assert any(len(c["criteria"]) == 2 for c in cases)                       # role-specific, 2 criteria

    def test_labels_are_complete(self):
        for c in MAIN["cases"] + HELD["cases"]:
            assert len(c["criteria"]) == len(ev.case_criteria(c)), c["id"]
            for cr in c["criteria"]:
                e = cr["expected"]
                assert set(e) == {"settings", "status", "reasons_include", "polarity"}, c["id"]
                assert e["polarity"] in ("positive", "negative", "neutral", "scope", "compound")
                assert (e["polarity"] == "positive") == bool(e["settings"]), c["id"]
                scope = "ambiguous_context_scope" in e["reasons_include"]
                compound = "compound_requirement" in e["reasons_include"]
                assert (e["polarity"] == "scope") == scope and (e["polarity"] == "compound") == compound, c["id"]
                assert scope == ("ambiguous_context_scope" in cr["oracle"]["ambiguity"]), c["id"]
                if scope or compound:
                    assert e["status"] == "needs_confirmation" and e["settings"] == [], c["id"]

    def test_contiguous_and_split_pairs(self):
        assert MAIN_BY["CM41"]["criteria"][0]["expected"]["settings"] == [
            "Islamic banking institutions in the GCC region"]
        assert sorted(MAIN_BY["CM42"]["criteria"][0]["expected"]["settings"]) == [
            "GCC region", "Islamic banking institutions"]
        assert HELD_BY["CX20"]["criteria"][0]["expected"]["settings"] == ["family vineyards in volcanic regions"]

    def test_compound_stays_blocked(self):
        # pre-exposure correction: a compound requirement is NOT ambiguous_context_scope
        for c in (MAIN_BY["CM43"], HELD_BY["CX22"]):
            cr = c["criteria"][0]
            e = cr["expected"]
            assert e == {"settings": [], "status": "needs_confirmation", "reasons_include": ["compound_requirement"],
                         "polarity": "compound"}
            assert cr["oracle"]["ambiguity"] == [] and cr["oracle"]["settings"] == []
            (r,) = records_for([c])
            (o,) = r["criteria"]
            assert r["pass"] and o["reasons"] == ["compound_requirement"] and o["settings"] == []
            assert o["view"] != "VIEW"

    @pytest.mark.parametrize("name", ["main", "heldout"])
    def test_oracle_scores_100_percent_and_offline_hard_gates_pass(self, name):
        fx = MAIN if name == "main" else HELD
        records = records_for(fx["cases"])
        s = ev.summarize(records, fx["cases"], independence_failures=len(ev.independence_probe(fx["cases"])))
        assert s["pass_runs"] == len(fx["cases"]) and s["settings_accuracy"] == 1.0
        assert s["outcomes"]["failed_validation"] == 0 and s["outcomes"]["failed_technical"] == 0
        assert all(v == 0 for k, v in s["hard"].items() if v != "not_available"), s["hard"]
        g = ev.evaluate_gates(s, name)
        assert g["all_decided_pass"] and g["undecided"] == ["hard:false_agreed", "hard:false_agreed_none",
                                                              "stability"]


# ── pins and leakage ────────────────────────────────────────────────────────

class TestPins:
    def test_fixture_sha_pins(self):
        for name, path in ev.FIXTURES.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == ev.FIXTURE_SHA256[name], name
            assert ev.check_pins(name, path) == []

    def test_config_pins(self):
        from services.s1_requirements import classifier as clf, schema as sc
        assert ev.PINNED == {"prompt_version": sc.S1_PROMPT_VERSION, "prompt_fingerprint": clf.prompt_fingerprint(),
                             "s1_version": sc.S1_VERSION, "model": sc.S1_MODEL, "temperature": clf.S1_TEMPERATURE,
                             "max_tokens": sc.S1_MAX_TOKENS}
        assert ev.PINNED["prompt_version"] == "s1-6.1" and ev.PINNED["temperature"] == 0.0
        assert ev.CLIENT_MAX_RETRIES == 0 and ev.DEFAULT_RUNS == 5

    def test_changed_fixture_or_prompt_is_refused(self, tmp_path, monkeypatch):
        p = tmp_path / "x.json"
        p.write_bytes(ev.FIXTURES["main"].read_bytes() + b" ")
        assert any("sha256" in x for x in ev.check_pins("main", p))
        monkeypatch.setattr(ev.clf, "prompt_fingerprint", lambda: "000000000000")
        assert any("prompt_fingerprint" in x for x in ev.check_pins("main", ev.FIXTURES["main"]))

    def test_heldout_documents_single_use(self):
        assert "permanently exposed" in HELD["_comment"] and "AT MOST ONCE" in HELD["_comment"]
        doc = ev.__doc__
        assert "permanently exposed" in doc and "tune on the MAIN fixture only" in doc

    @staticmethod
    def _words(s):
        return " ".join(re.findall(r"\w+", ev.normalize(s)))

    def _contains(self, hay, phrase):
        p = self._words(phrase)
        return bool(p) and f" {p} " in f" {self._words(hay)} "

    @staticmethod
    def _texts(path):
        d = json.loads(path.read_text(encoding="utf-8"))
        out = []
        for c in d["cases"]:
            out += c.get("jd_lines", [])
            out += list((c.get("analysis") or c.get("analysis_stub") or {}).get("relevant_roles") or [])
        return "\n".join(out)

    @staticmethod
    def _phrases(fx, contexts_only=False):
        out = set()
        for c in fx["cases"]:
            if not contexts_only:
                out |= set(c["analysis"]["relevant_roles"])
            for cr in c["criteria"]:
                o = cr["oracle"]
                out |= {x["text"] for x in o.get("settings", [])}
                out |= {r["text"] for r in o.get("restrictions", [])
                        if r["kind"] != "vague" and (not contexts_only or r["kind"] == "context")}
        return out

    def test_heldout_vocabulary_is_unseen(self):
        from services.s1_requirements.classifier import S1_SYSTEM_PROMPT
        corpora = {"main": self._texts(ev.FIXTURES["main"]),
                   "s1_boundary": self._texts(FIX / "s1_boundary_cases.json"),
                   "s1_heldout": self._texts(FIX / "s1_heldout_cases.json"),
                   "qc_main": self._texts(QC_FIX / "qc_main_cases.json"),
                   "qc_heldout": self._texts(QC_FIX / "qc_heldout_cases.json"),
                   "prompt": S1_SYSTEM_PROMPT}
        leaks = [(p, k) for p in sorted(self._phrases(HELD)) for k, v in corpora.items() if self._contains(v, p)]
        assert leaks == []

    def test_main_contexts_are_not_prompt_examples(self):
        from services.s1_requirements.classifier import S1_SYSTEM_PROMPT
        assert [p for p in self._phrases(MAIN, contexts_only=True) if self._contains(S1_SYSTEM_PROMPT, p)] == []

    def test_frozen_qc_artifacts_untouched(self):
        assert hashlib.sha256((QC_FIX / "prompts" / "candidate_qc-1.txt").read_bytes()).hexdigest() == (
            "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df")
        for name, prefix in (("qc_main_cases.json", "c5fcd502"), ("qc_heldout_cases.json", "d94a39f3")):
            assert hashlib.sha256((QC_FIX / name).read_bytes()).hexdigest().startswith(prefix), name


# ── safety ──────────────────────────────────────────────────────────────────

class TestSafety:
    def _boom(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("real client created")
        monkeypatch.setattr(ev, "make_real_client", boom)
        monkeypatch.setattr(ev.llm_call, "create_client", boom)

    def test_offline_modes_never_create_a_real_client(self, tmp_path, monkeypatch):
        self._boom(monkeypatch)
        assert ev.main(["--out", str(tmp_path / "d")]) == 0
        assert not (tmp_path / "d").exists()
        assert ev.main(["--out", str(tmp_path / "o"), "--mode", "oracle", "--runs", "1",
                        "--cases", "CM01,CM20,CM36"]) == 0
        res = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
        assert res["summary"]["pass_runs"] == 3 and res["meta"]["mode"] == "oracle"
        assert res["meta"]["fixture_sha256"] == ev.FIXTURE_SHA256["main"]

    def test_real_mode_refusals(self, tmp_path, monkeypatch, capsys):
        self._boom(monkeypatch)
        assert ev.main(["--out", str(tmp_path / "r"), "--mode", "real"]) == 2
        assert "--confirm-real" in capsys.readouterr().out
        assert ev.main(["--out", str(tmp_path / "r"), "--mode", "real", "--fixture", "heldout",
                        "--confirm-real"]) == 2
        assert "--allow-heldout" in capsys.readouterr().out
        monkeypatch.setattr(ev, "FIXTURE_SHA256", {**ev.FIXTURE_SHA256, "main": "0" * 64})
        assert ev.main(["--out", str(tmp_path / "r"), "--mode", "real", "--confirm-real"]) == 2
        assert "pins do not match" in capsys.readouterr().out
        assert not (tmp_path / "r").exists()

    def test_real_client_is_zero_retry_and_lazy(self):
        tree = ast.parse((BACKEND / "scripts" / "s1_context_eval.py").read_text(encoding="utf-8"))
        top = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names} | {
            n.module or "" for n in tree.body if isinstance(n, ast.ImportFrom)}
        assert not any(m.startswith("openai") or m == "config" for m in top)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_real_client")
        src = ast.unparse(fn)
        assert "max_retries=CLIENT_MAX_RETRIES" in src and "AsyncOpenAI" in src
        for banned in ("database", "sqlalchemy", "routers", "workers", "s2_experience", "llm_criteria_mapper",
                       "qualifying_context"):
            assert not any(banned in m for m in top), banned

    def test_calls_are_recorded_with_model_and_temperature(self):
        (r,) = records_for([MAIN_BY["CM01"]])
        assert r["calls"] == [{"call": "main", "model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 4000,
                               "finish_reason": "stop"}]
        assert r["raw"][0]["content"] == ev.oracle_response(MAIN_BY["CM01"])


# ── scoring and gates ───────────────────────────────────────────────────────

class TestScoring:
    def test_canon_and_set_match(self):
        assert ev.canon_context("in the GCC region") == ev.canon_context("GCC region") == "gcc region"
        assert ev.canon_context("لدى المؤسسات المالية") == ev.canon_context("المؤسسات المالية")
        assert ev.settings_match(["A x", "B y"], ["b y", "the a x"])                # order-free, AND set
        assert not ev.settings_match(["A x", "B y"], ["A x"])
        assert not ev.settings_match(["A x in B y"], ["A x", "B y"])                # contiguous != split
        assert not ev.settings_match(["A x", "B y"], ["A x in B y"])
        assert not ev.settings_match([], None)                                      # a failure is not "none"
        assert ev.settings_match([{"one_of": ["بالقطاع المصرفي", "القطاع المصرفي"]}], ["القطاع المصرفي"])

    def test_false_none_and_false_context(self):
        cases = [MAIN_BY["CM01"], MAIN_BY["CM24"]]
        plan = {"CM01": [[with_settings(MAIN_BY["CM01"], [])]],
                "CM24": [[with_settings(MAIN_BY["CM24"], [(2, "fast-paced, dynamic environment")])]]}
        s = ev.summarize(records_for(cases, plan), cases)
        assert s["false_none_rate"] == 1.0 and s["false_context_rate"] == 1.0 and s["settings_accuracy"] == 0.0
        assert not ev.evaluate_gates(s, "main")["all_decided_pass"]

    def test_split_answer_for_contiguous_case_is_wrong(self):
        c = MAIN_BY["CM41"]
        (r,) = records_for([c], {"CM41": [[with_settings(c, [(2, "Islamic banking institutions"), (2, "GCC region")])]]})
        assert r["criteria"][0]["outcome"] == "ok" and not r["pass"]

    def test_failures_are_failures_never_none(self):
        c = MAIN_BY["CM24"]
        (r,) = records_for([c], {"CM24": [["not json", "still not json"]]})
        o = r["criteria"][0]
        assert o["outcome"] == "validation" and o["settings"] is None and not o["failed_as_none"]
        s = ev.summarize([r], [c])
        assert s["false_context_rate"] is None and s["failure_rate"] == 1.0 and s["hard"]["failed_artifact_treated_as_none"] == 0
        assert not ev.evaluate_gates(s, "main")["gates"]["failure_rate"]

    def test_technical_failure_recorded(self):
        c = MAIN_BY["CM01"]
        (r,) = records_for([c], {"CM01": [[RuntimeError("down")]]})
        assert r["criteria"][0]["outcome"] == "technical" and r["criteria"][0]["settings"] is None
        assert r["status_reason"] == "ai_unavailable" and not r["pass"]

    def test_stability(self):
        c = MAIN_BY["CM01"]
        plan = {"CM01": [[ev.oracle_response(c)], [with_settings(c, [(2, "GCC")])]]}
        s = ev.summarize(records_for([c], plan, runs=2), [c])
        assert s["stability"] == 0.0 and s["unstable_cases"] == ["CM01"]
        s2 = ev.summarize(records_for([c], runs=2), [c])
        assert s2["stability"] == 1.0 and ev.evaluate_gates(s2, "main")["gates"]["stability"] is True

    def test_role_specific_scored_per_criterion(self):
        c = MAIN_BY["CM36"]
        wrong = with_settings(c, [(2, "high-rise projects")], [(3, "Draftsperson")])  # context on the wrong role
        (r,) = records_for([c], {"CM36": [[wrong]]})
        assert not r["pass"]

    def test_gate_thresholds(self):
        assert ev.GATES == {"main": {"settings_accuracy": 0.85, "false_none_rate": 0.05, "false_context_rate": 0.10},
                            "heldout": {"settings_accuracy": 0.80, "false_none_rate": 0.05,
                                        "false_context_rate": 0.10}}
        assert (ev.STABILITY_MIN, ev.FAILURE_RATE_MAX, ev.JOINT_AGREEMENT_SOFT) == (0.90, 0.05, 0.70)
        s = {"settings_accuracy": 0.84, "false_none_rate": 0.0, "false_context_rate": 0.0, "stability": 0.95,
             "failure_rate": 0.0, "hard": {"x": 0, "false_agreed": "not_available"}}
        assert not ev.evaluate_gates(s, "main")["gates"]["settings_accuracy"]
        assert ev.evaluate_gates(s, "heldout")["gates"]["settings_accuracy"]
        assert ev.evaluate_gates({**s, "hard": {"x": 1}}, "heldout")["all_decided_pass"] is False

    def test_unconfirmed_context_view_is_a_hard_gate(self, monkeypatch):
        c = MAIN_BY["CM01"]
        monkeypatch.setattr(ev.asm, "s2_views", lambda art, require_resolved=True: ["leak"])
        (r,) = records_for([c])
        assert ev.summarize([r], [c])["hard"]["unconfirmed_context_yielding_s2_view"] == 1

    def test_independence_probe_detects_a_leak(self, monkeypatch):
        assert ev.independence_probe(MAIN["cases"]) == []
        orig = ev.clf.build_request
        seen = {}

        def leaky(jd, criteria):
            req = orig(jd, criteria)
            req.payload["n"] = seen.setdefault("n", 0)
            seen["n"] += 1
            return req
        monkeypatch.setattr(ev.clf, "build_request", leaky)
        assert ev.independence_probe([MAIN_BY["CM01"]]) != []


class TestDiagnosticGroups:
    def test_groups_are_reported_not_gated(self):
        recs = records_for(MAIN["cases"])
        s = ev.summarize(recs, MAIN["cases"])
        g = s["diagnostic_groups"]
        assert set(g) == {"alternative_scope", "softened_qualifier", "compound", "or_phrase", "generic_environment",
                          "and_multiple_contexts", "same_line_context", "arabic"}
        assert all(v["rate"] == 1.0 for v in g.values())
        assert g["same_line_context"]["criterion_runs"] >= 15 and g["arabic"]["criterion_runs"] == 9
        gates = ev.evaluate_gates(s, "main")["gates"]
        assert not any(k in gates for k in g)                       # diagnostics are never acceptance gates

