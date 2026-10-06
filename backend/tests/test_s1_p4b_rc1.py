"""
P4b RC1 — implementation fixes found by the real s1-6.0 MAIN evaluation (offline: recorded model outputs are
REPLAYED through the current code; no model call, no prompt change, no fixture change).

1. hint-less repair merge: invalid main ``settings`` are not authoritative, their contexts must reappear as
   ``context`` restrictions, and a repair is never the sole source of a context-only / total-experience reading;
2. repair guidance for a context outside the requirement spans (rule unchanged);
3. target / policy preservation metric of the context evaluation harness.
"""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from services.s1_requirements import classifier as clf
from services.s1_requirements import repair as rp
from services.s1_requirements.validator import CONTEXT_SPAN_GUIDANCE

BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("s1_context_eval", BACKEND / "scripts" / "s1_context_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

DATA = json.loads((BACKEND / "scripts" / "s1_eval_fixtures" / "p4b_main_recorded_regressions.json")
                  .read_text(encoding="utf-8"))
REC = {(r["case"], r["run"]): r for r in DATA["records"]}
CASES = {c["id"]: c for c in ev.load_fixture("main")[0]["cases"]}


def replay(case_id, *contents):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(ev.run_case(CASES[case_id], ev.ScriptedClient(*contents)))
    finally:
        loop.close()


def replay_record(case_id, run):
    return replay(case_id, *[c["content"] for c in REC[(case_id, run)]["raw"]])


def restr(obs):
    return [(x["text"], x["kind"]) for x in obs["criteria"][0]["restrictions"]]


class TestRecordedData:
    def test_provenance(self):
        m = DATA["source_meta"]
        assert (m["prompt_version"], m["prompt_fingerprint"], m["s1_version"], m["model"], m["temperature"],
                m["client_max_retries"], m["fixture"]) == ("s1-6.0", "5b4172f709b2", "1.5.0", "gpt-4o-mini", 0.0, 0,
                                                            "main")
        assert m["fixture_sha256"] == ev.FIXTURE_SHA256["main"]
        assert clf.prompt_fingerprint() == "5b4172f709b2"            # the prompt itself is unchanged

    def test_replay_without_repair_change_is_faithful(self):
        """Records the merge fix does not touch replay to exactly the recorded observation."""
        for key in (("CM21", 1), ("CM38", 1)):
            obs = replay_record(*key)
            o, old = obs["criteria"][0], REC[key]["criteria"][0]
            assert (o["outcome"], o["settings"], o["policy"]) == (old["outcome"], old["settings"], old["policy"])


# ── 1. hint-less repair merge ───────────────────────────────────────────────

class TestHintlessRepairMerge:
    @pytest.mark.parametrize("key, function, context", [
        (("CM02", 1), "procurement experience", "within Saudi Arabia"),
        (("CM03", 1), "في المراجعة الداخلية", "في دول مجلس التعاون الخليجي"),
        (("CM10", 1), "الشؤون القانونية", "في القطاع الحكومي"),
    ])
    def test_correct_repair_moving_settings_into_restrictions_is_taken(self, key, function, context):
        # real: main put the context in "settings" (invalid for a hint-less criterion), the repair moved it into
        # a typed context restriction next to the function; s1-6.0 kept the main settings and rejected it
        assert REC[key]["criteria"][0]["outcome"] == "validation"
        obs = replay_record(*key)
        o = obs["criteria"][0]
        assert o["outcome"] == "ok" and o["status"] == "resolved" and o["policy"] == "functional"
        assert (function, "function") in restr(obs) and (context, "context") in restr(obs)
        assert o["settings"] == [context]
        assert {"criterion_id": o["criterion_id"], "field": "restrictions"} in obs["repair_merge"]["taken"]
        merged = json.loads(rp.merge_repair(REC[key]["raw"][0]["content"], REC[key]["raw"][1]["content"],
                                            _scoped(key), ev.case_criteria(CASES[key[0]]))[0])
        assert "settings" not in merged["criteria"][0]               # the invalid field is never carried over

    @pytest.mark.parametrize("key", [("CM05", 1), ("CM05", 5), ("CM19", 1), ("CM21", 5), ("CM02", 4),
                                     ("CM09", 1), ("CM15", 1)])
    def test_repair_never_the_sole_source_of_a_context_only_reading(self, key):
        # real: the main answer had no restriction list; the repair returned ONLY a context, dropping the
        # function (CM09 "legal", CM15 "supply chain", CM02 "procurement", CM05 "HR administration", ...)
        rp_items = json.loads(REC[key]["raw"][1]["content"])["criteria"][0]["restrictions"]
        assert {x["kind"] for x in rp_items} == {"context"}
        obs = replay_record(*key)
        o = obs["criteria"][0]
        assert o["outcome"] == "validation" and o["settings"] is None and o["policy"] is None
        assert obs["repair_merge"]["kept_main_restrictions"] == [o["criterion_id"]]

    def test_project_management_never_becomes_resolved_pure_duration(self):
        # real CM30 (all 5 runs): "6 years of project management experience"; main had no restriction list, the
        # repair returned [] -> s1-6.0 produced a RESOLVED pure-duration (total experience) criterion
        key = ("CM30", 1)
        old = REC[key]["criteria"][0]
        assert (old["outcome"], old["status"], old["policy"]) == ("ok", "resolved", "pure_duration")
        assert json.loads(REC[key]["raw"][1]["content"])["criteria"][0]["restrictions"] == []
        o = replay_record(*key)["criteria"][0]
        assert o["outcome"] == "validation" and o["policy"] is None and o["status"] != "resolved"

    def test_main_settings_context_must_reappear(self):
        main = {"criterion_id": "c", "settings": [{"line": 2, "text": "in multinational companies"}]}
        fn = {"line": 2, "text": "HR administration", "kind": "function"}
        sc = {rp.SCOPE_SETTINGS, rp.SCOPE_RESTRICTIONS}
        lost = {"restrictions": [fn]}
        moved = {"restrictions": [fn, {"line": 2, "text": "gained in multinational companies", "kind": "context"}]}
        trimmed = {"restrictions": [fn, {"line": 2, "text": "multinational companies", "kind": "context"}]}
        other = {"restrictions": [fn, {"line": 2, "text": "companies", "kind": "context"}]}
        assert rp._merge_hintless(main, lost, sc, False) == (None, False)          # the context would be lost
        assert rp._merge_hintless(main, moved, sc, False)[1] is True               # longer phrase containing it
        assert rp._merge_hintless(main, trimmed, sc, False)[1] is True             # leading "in" only
        assert rp._merge_hintless(main, other, sc, False) == (None, False)         # material words dropped
        assert rp._merge_hintless(main, {"restrictions": [fn], "ambiguity": []}, sc, True)[1] is True  # scope code

    def test_same_context_is_closed_list_only(self):
        S = rp.same_context
        assert S({"line": 2, "text": "in the GCC region"}, {"line": 2, "text": "GCC region"})
        assert S({"line": 2, "text": "GCC region"}, {"line": 2, "text": "in the GCC region"})
        assert S({"line": 2, "text": "لدى الجهات الحكومية"}, {"line": 2, "text": "الجهات الحكومية"})
        assert not S({"line": 2, "text": "Islamic banks in the GCC"}, {"line": 2, "text": "GCC"})
        assert not S({"line": 2, "text": "GCC region"}, {"line": 3, "text": "GCC region"})
        assert not S({"line": 2, "text": "public sector"}, {"line": 2, "text": "private sector"})

    def test_role_or_function_never_silently_removed(self):
        main = {"restrictions": [{"line": 2, "text": "internal audit", "kind": "function"},
                                 {"line": 2, "text": "government entitie", "kind": "context"}]}     # R1 invalid
        drop = {"restrictions": [{"line": 2, "text": "government entities", "kind": "context"}]}
        keep = {"restrictions": [{"line": 2, "text": "internal audit", "kind": "function"},
                                 {"line": 2, "text": "government entities", "kind": "context"}]}
        sc = {f"{rp.SCOPE_RESTRICTION_PREFIX}R1"}
        assert rp._merge_hintless(main, drop, sc, False)[1] is False
        assert rp._merge_hintless(main, keep, sc, False)[1] is True

    @pytest.mark.parametrize("rep, taken", [
        ([], False), ([{"line": 2, "text": "relevant", "kind": "vague"}], False),
        ([{"line": 2, "text": "public sector", "kind": "context"}], False),
        ([{"line": 2, "text": "legal", "kind": "function"}, {"line": 2, "text": "public sector", "kind": "context"}],
         True),
        ([{"line": 2, "text": "Paralegal", "kind": "role"}], True),
    ])
    def test_first_answer_guard(self, rep, taken):
        assert rp._merge_restrictions({}, {"restrictions": rep}, {rp.SCOPE_RESTRICTIONS})[1] is taken

    def test_hint_criteria_settings_merge_unchanged(self):
        main = {"settings": [{"line": 2, "text": "GCC region"}, {"line": 2, "text": "x"}]}
        assert rp._merge_settings(main, {"settings": [{"line": 2, "text": "GCC region"}]},
                                  {f"{rp.SCOPE_SETTING_PREFIX}S1"}, False)[1] is False


def _scoped(key):
    from services.s1_requirements.jd_text import JDText
    from services.s1_requirements.validator import validate_response
    case = CASES[key[0]]
    j = JDText(ev.case_jd(case))
    return validate_response(REC[key]["raw"][0]["content"], j, ev.case_criteria(case),
                             {d: (ln, m) for d, ln, m in j.durations()}).scoped


# ── 2. repair guidance (rule unchanged) ─────────────────────────────────────

class TestContextSpanGuidance:
    def test_cm20_still_rejected_but_guided(self):
        key = ("CM20", 1)
        obs = replay_record(*key)
        assert obs["criteria"][0]["outcome"] == "validation"               # exactly as strict as before
        errs = obs["validation"]["errors"] + obs["validation"]["repair_errors"]
        assert errs and all("not inside one of this criterion's requirement_spans" in e for e in errs)
        assert all(CONTEXT_SPAN_GUIDANCE in e for e in errs)
        assert "add that sentence to requirement_spans" in CONTEXT_SPAN_GUIDANCE
        # the guidance reaches the repair request
        assert CONTEXT_SPAN_GUIDANCE in clf.repair_note(obs["validation"]["errors"])

    def test_cm20_repair_following_the_guidance_is_taken(self):
        main = REC[("CM20", 1)]["raw"][0]["content"]
        fixed = json.loads(main)
        fixed["criteria"][0]["requirement_spans"].append(
            {"line": 4, "text": "All of this experience must have been gained in the GCC region"})
        fixed["criteria"][0]["settings"] = [{"line": 4, "text": "GCC region"}]
        obs = replay("CM20", main, json.dumps(fixed))
        o = obs["criteria"][0]
        assert o["outcome"] == "ok" and o["settings"] == ["GCC region"] and [s["line"] for s in
                                                                             o["requirement_spans"]] == [2, 4]

    def test_no_automatic_span_addition(self):
        main = REC[("CM20", 1)]["raw"][0]["content"]
        obs = replay("CM20", main, main)                                     # a repair that repeats itself
        assert obs["criteria"][0]["outcome"] == "validation"

    def test_context_restriction_outside_spans_is_guided_too(self):
        from services.s1_requirements.jd_text import JDText
        from services.s1_requirements.validator import validate_response
        case = CASES["CM21"]
        j = JDText(ev.case_jd(case))
        crits = ev.case_criteria(case)
        raw = json.dumps({"criteria": [{
            "criterion_id": crits[0].criterion_id,
            "requirement_spans": [{"line": 2, "text": "Minimum 6 years of project controls experience on "
                                                      "infrastructure projects"}],
            "restrictions": [{"line": 2, "text": "project controls", "kind": "function"},
                             {"line": 3, "text": "government entities", "kind": "context"}],
            "duration": "D1", "ambiguity": [], "note": "n"}]})
        v = validate_response(raw, j, crits, {d: (ln, m) for d, ln, m in j.durations()})
        (err,) = [e for e in v.scoped if "not inside" in e.message]
        assert not v.ok and CONTEXT_SPAN_GUIDANCE in err.message and rp.SCOPE_SPANS in err.scopes


# ── 3. target / policy preservation metric ──────────────────────────────────

class TestTargetPolicyMetric:
    def _old(self):
        recs = [{"case": r["case"], "family": CASES[r["case"]]["family"], "lang": CASES[r["case"]]["lang"],
                 "run": r["run"], "repair_used": r["repair_used"], "calls": [], "criteria": r["criteria"]}
                for r in DATA["records"]]
        return ev.rescore(recs, list(CASES.values()))

    def test_old_unsafe_passes_are_exposed(self):
        recs = self._old()
        s = ev.summarize(recs, list(CASES.values()))
        tp = s["target_policy"]
        assert set(tp["cases"]) == {"CM02", "CM05", "CM09", "CM15", "CM21", "CM30", "CM38"}
        by = {(d["case"], d["run"]): d for d in tp["details"]}
        assert by[("CM30", 1)]["policy_downgraded"] and by[("CM30", 1)]["observed_policy"] == "pure_duration"
        assert by[("CM09", 1)]["targets_lost"] == ["legal"] and by[("CM15", 1)]["targets_lost"] == ["supply chain"]
        assert by[("CM05", 5)]["targets_lost"] == ["HR administration"] and by[("CM02", 4)]["repair_used"]
        assert by[("CM21", 1)]["targets_lost"] == ["project controls"] and not by[("CM21", 1)]["repair_used"]
        assert s["hard"]["unsafe_target_policy_loss"] == tp["unsafe_runs"] > 0
        assert tp["repair_target_loss_runs"] == sum(1 for d in tp["details"] if d["repair_used"])
        assert ev.evaluate_gates(s, "main")["gates"]["hard:unsafe_target_policy_loss"] is False
        # the CM30 run used to PASS on context alone; it no longer passes
        cm30 = next(r for r in recs if r["case"] == "CM30")
        assert cm30["checks"][0]["settings"] and not cm30["checks"][0]["targets"] and not cm30["pass"]

    def test_metric_is_separate_from_context_accuracy(self):
        recs = self._old()
        s = ev.summarize(recs, list(CASES.values()))
        cm09 = next(r for r in recs if r["case"] == "CM09")
        assert cm09["checks"][0]["settings"] is True and cm09["checks"][0]["targets"] is False
        assert "target_accuracy" in s["target_policy"] and "settings_accuracy" in s

    def test_gold_derivation_needs_no_fixture_change(self):
        assert ev.gold_targets(CASES["CM30"]["criteria"][0]) == ([("project management", "function")], "functional")
        assert ev.gold_targets(CASES["CM38"]["criteria"][0]) == (
            [("محاسب", "role"), ("التدقيق الداخلي", "function")], "mixed")
        assert ev.gold_targets(CASES["CM11"]["criteria"][0]) == ([], "explicit_role")
        assert ev._carries("محاسب", "كمحاسب") and ev._carries("الائتمان", "في الائتمان بالقطاع المصرفي")
        assert not ev._carries("legal", "public sector")

    def test_oracle_has_no_target_loss(self):
        recs = []
        for c in CASES.values():
            loop = asyncio.new_event_loop()
            try:
                obs = loop.run_until_complete(ev.run_case(c, ev.ScriptedClient(ev.oracle_response(c))))
            finally:
                loop.close()
            recs.append({"case": c["id"], "family": c["family"], "lang": c["lang"], "run": 1, **obs,
                         **ev.score(c, obs)})
        s = ev.summarize(recs, list(CASES.values()))
        assert s["target_policy"]["unsafe_runs"] == 0 and s["target_policy"]["target_accuracy"] == 1.0
        assert s["pass_runs"] == len(CASES)

    def test_rescore_cli_refuses_another_fixture_version(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"meta": {"fixture_sha256": "0" * 64}, "records": []}))
        assert ev.main(["--out", str(tmp_path / "o"), "--rescore", str(p)]) == 2

    def test_heldout_untouched(self):
        assert hashlib.sha256(ev.FIXTURES["heldout"].read_bytes()).hexdigest() == (
            "fb917b610c0bd31c1932818655a3392469a827df74a02fc3344e7e288c78589c") == ev.FIXTURE_SHA256["heldout"]
        assert "heldout" not in json.dumps(DATA["source_meta"])
