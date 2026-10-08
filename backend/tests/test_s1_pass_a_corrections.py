"""
Pass A corrections after the s1a-1.0 real MAIN forensics (prompt s1a-1.1, repair message, F6, expansion
diagnostic). The recorded s1a-1.0 answers of the failing MAIN cases (CM09, CM10, CM19, CM21, CM23, CM26, CM30;
expansion shapes CM13, CM38-CM40) are replayed through the CURRENT code with a scripted client.
Since S1-A-1.3 the current prompt is s1a-1.3: the prompt and repair-message assertions of this file pin s1a-1.1
EXPLICITLY (loaded / run by version under its own contract); the recorded replays run through the current code.
Offline only: no model call, no network, no database; the held-out fixture is never read.
"""
import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_two_pass import assemble as asm
from services.s1_two_pass import pass_a as pa
from services.s1_two_pass import pass_a_runner as pr
from services.s1_two_pass import prompt_a

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", SCRIPTS / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
ctx = ev.ctx

MAIN = json.loads((SCRIPTS / "s1_eval_fixtures" / "s1_ctx_main_cases.json").read_text(encoding="utf-8"))
BY = {c["id"]: c for c in MAIN["cases"]}
PROMPT = prompt_a.load_pass_a_prompt("s1a-1.1")          # the s1a-1.1 corrections, pinned by version
OLD = prompt_a.load_pass_a_prompt("s1a-1.0")
C11 = pa.CONTRACTS["s1a-1.1"]


class FakeClient:
    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            content = self.items.pop(0)
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def answer(case_id, *items):
    """A recorded per-criterion answer (without criterion_id) for a MAIN case, as raw JSON."""
    crits = ctx.case_criteria(BY[case_id])
    return json.dumps({"criteria": [{"criterion_id": c.criterion_id, "note": "recorded", **it}
                                    for c, it in zip(crits, items)]}, ensure_ascii=False)


def pass_a(case_id, *raws, prompt_version=None):
    case = BY[case_id]
    client = FakeClient(*raws)
    res = run(pr.run_pass_a_job(ctx.case_job_id(case), ctx.case_jd(case), ctx.case_analysis(case), client=client,
                                prompt_version=prompt_version))
    return client, res


def span(case_id, line):
    return [{"line": line, "text": BY[case_id]["jd_lines"][line - 1]}]


def R(line, text, kind):
    return {"line": line, "text": text, "kind": kind}


def item(case_id, line, restrictions, basis):
    return {"requirement_spans": span(case_id, line), "restrictions": restrictions, "target_basis": basis,
            "duration": "D1", "ambiguity": []}


def note(client):
    return client.requests[1]["messages"][-1]["content"]


def observed(res):
    return ev.observe(None, res, [])["criteria"][0]


# recorded s1a-1.0 answers (results of the real MAIN run, verbatim apart from criterion_id / note)
CM09_MAIN = item("CM09", 2, [R(2, "public sector", "vague")], "setting_only")
CM09_REP_UNSPEC = item("CM09", 2, [R(2, "public sector", "vague")], "unspecified")
CM09_REP_LEGAL = item("CM09", 2, [R(2, "legal experience", "function"), R(2, "public sector", "vague")], "targets")
CM10_MAIN = item("CM10", 2, [R(2, "الشؤون القانونية", "function"), R(2, "القطاع الحكومي", "vague")], "setting_only")
CM10_REP = item("CM10", 2, [R(2, "الشؤون القانونية", "function")], "total_experience")
CM19_MAIN = item("CM19", 2, [R(2, "non-governmental organisations", "vague")], "setting_only")
CM19_REP1 = item("CM19", 2, [R(2, "fundraising", "function"), R(2, "non-governmental organisations", "vague")],
                 "unspecified")
CM19_REP2 = item("CM19", 2, [R(2, "fundraising", "function"), R(2, "non-governmental organisations", "vague")],
                 "targets")
CM21_MAIN = item("CM21", 2, [R(2, "infrastructure projects", "function")], "targets")
CM23_RUN2 = item("CM23", 2, [], "total_experience")
CM26_RUN1 = item("CM26", 2, [], "total_experience")
CM30_MAIN = item("CM30", 4, [], "total_experience")


# ── 1. prompt s1a-1.1 ────────────────────────────────────────────────────────

def _sec(p, a, b):
    return p[p.index(a):p.index(b)]


class TestPromptS1a11:
    def test_only_sections_4_5_and_the_closing_example_changed(self):
        for a, b in (("You read the EXPERIENCE", "4 RESTRICTIONS"), ("6 DURATION", "OUTPUT:")):
            assert _sec(PROMPT, a, b) == _sec(OLD, a, b)
        assert PROMPT[PROMPT.index("SECURITY RULES"):] == OLD[OLD.index("SECURITY RULES"):]
        assert _sec(PROMPT, "OUTPUT:", '{"line": 13') == _sec(OLD, "OUTPUT:", '{"line": 13')

    def test_where_phrases_are_never_restrictions(self):
        s4 = _sec(PROMPT, "4 RESTRICTIONS", "5 TARGET_BASIS")
        assert ("WHERE IS NOT A RESTRICTION: words that only say where, for whom or under what circumstances the "
                "experience was gained") in s4
        assert 'Never return them as "vague", "function" or "role", and never inside the quoted text of a role or ' \
               'function' in s4
        assert 'stop before such words (e.g. "payroll administration experience for retail clients" -> ' \
               '"payroll administration")' in s4

    def test_vague_is_narrowed(self):
        s4 = _sec(PROMPT, "4 RESTRICTIONS", "5 TARGET_BASIS")
        assert ('vague     ONLY a word such as "relevant", "related", "similar" or "in the field" that asks for '
                'relevant experience WITHOUT saying relevant to what. Nothing else is vague.') in s4
        assert "or the like" not in s4

    def test_unsure_function_rule_removed(self):
        assert "If you are unsure whether a phrase names work, return it as a function restriction." in OLD
        assert "unsure whether a phrase names work" not in PROMPT

    def test_x_experience_names_the_work(self):
        s4 = _sec(PROMPT, "4 RESTRICTIONS", "5 TARGET_BASIS")
        assert ('in "<X> experience", "experience in <X>", "experience of <X>" and the Arabic "خبرة ... في <X>", '
                'X names the work: return X as a function') in s4
        assert "Such a statement is never restrictions []." in s4

    def test_setting_only_only_without_role_or_work(self):
        s5 = _sec(PROMPT, "5 TARGET_BASIS", "6 DURATION")
        assert ('"setting_only"      no target_hints and restrictions []: the statement names no role and no work '
                'at all') in s5
        assert "Decide the basis from the role and work words ONLY." in s5
        assert 'when the statement names any role or work, the basis is "targets", whatever else it says' in s5
        assert 'never "setting_only"' in s5

    def test_closing_example_is_a_no_hints_function(self):
        out = PROMPT[PROMPT.index("OUTPUT:"):PROMPT.index("SECURITY RULES")]
        last = out[out.rindex('{"criterion_id"'):]
        assert '"restrictions": [{"line": 13, "text": "verbatim work words only", "kind": "function"}]' in last
        assert '"target_basis": "targets"' in last
        assert '"restrictions": []' not in out and "total_experience" not in out

    def test_new_examples_avoid_main_fixture_targets(self):
        # the text NEW in s1a-1.1 (sections 4-5, closing example) quotes no MAIN gold target and no MAIN requirement
        # line (no tuning leakage); the inherited s1-5.2 illustrations are unchanged and checked by test_s1_pass_a
        p = (_sec(PROMPT, "4 RESTRICTIONS", "6 DURATION") + PROMPT[PROMPT.index('{"line": 13'):
                                                                    PROMPT.index("SECURITY RULES")]).lower()
        for case in MAIN["cases"]:
            for g in ev.case_golds(case):
                for text, _ in g["targets"]:
                    if len(text) > 3:
                        assert text.lower() not in p, (case["id"], text)
            for line in case["jd_lines"]:
                assert line.strip("- .").lower() not in p or len(line) < 15, (case["id"], line)
        assert "project management" not in p


# ── 2. repair message ───────────────────────────────────────────────────────

class TestRepairMessage:
    def test_mismatch_never_prescribes_a_basis_from_restrictions(self):
        case = BY["CM09"]
        crits = ctx.case_criteria(case)
        jd = ctx.JDText(ctx.case_jd(case))
        v = pa.validate_pass_a(answer("CM09", CM09_MAIN), jd, crits, pa.build_pass_a_request(jd, crits).durations,
                               contract=C11)
        (e,) = [x for x in v.scoped if "target_basis" in x.scopes]
        assert "expected one of" not in e.message and "unspecified']" not in e.message
        assert pa.BASIS_REREAD_MESSAGE in e.message and "and the restrictions disagree" in e.message
        assert set(e.scopes) == {"target_basis", "restrictions"}

    def test_hinted_mismatch_message(self):
        case = BY["CM01"]
        crits = ctx.case_criteria(case)
        jd = ctx.JDText(ctx.case_jd(case))
        it = json.loads(ev.oracle_response(case))
        it["criteria"][0]["target_basis"] = "total_experience"
        v = pa.validate_pass_a(json.dumps(it), jd, crits, pa.build_pass_a_request(jd, crits).durations)
        assert any(pa.BASIS_HINTED_MESSAGE in e for e in v.errors)
        assert not any("Re-read" in e for e in v.errors)

    def test_the_note_sent_to_the_model(self):
        client, _ = pass_a("CM19", answer("CM19", CM19_MAIN), answer("CM19", CM19_REP2), prompt_version="s1a-1.1")
        n = note(client)
        assert pa.BASIS_REREAD_MESSAGE in n and "expected one of" not in n
        assert client.requests[0]["messages"][0]["content"] == PROMPT          # the pinned s1a-1.1 prompt was sent


# ── 3. F6 strengthened + recorded failure replays ───────────────────────────

class TestRecordedReplays:
    def test_cm09_repair_only_unspecified_is_now_rejected(self):
        # s1a-1.0 accepted this (unspecified / pure_duration, the target "legal" lost); now: fail closed
        client, res = pass_a("CM09", answer("CM09", CM09_MAIN), answer("CM09", CM09_REP_UNSPEC))
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert res.outcome.meta["outcome"] == "f6_rejected"
        assert any(pr.F6_ERROR in e and "'unspecified'" in e for e in res.outcome.errors["repair_errors"])
        assert all(a.target_state == "target_failed" and a.policy is None for a in res.failed)
        assert "unspecified" in pr.NO_TARGET_BASES

    def test_cm09_repair_that_restores_the_function_still_passes(self):
        _, res = pass_a("CM09", answer("CM09", CM09_MAIN), answer("CM09", CM09_REP_LEGAL))
        o = observed(res)
        assert res.status == "ok" and o["basis"] == "targets" and [t["text"] for t in o["targets"]] == [
            "legal experience"]

    def test_cm10_repair_only_total_experience_rejected(self):
        _, res = pass_a("CM10", answer("CM10", CM10_MAIN), answer("CM10", CM10_REP))
        assert res.status == "failed" and res.outcome.meta["outcome"] == "f6_rejected"

    def test_cm19_inconsistent_repair_fails_closed(self):
        _, res = pass_a("CM19", answer("CM19", CM19_MAIN), answer("CM19", CM19_REP1))
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert all(a.target_state == "target_failed" for a in res.failed)

    def test_cm19_repair_with_the_function_passes(self):
        _, res = pass_a("CM19", answer("CM19", CM19_MAIN), answer("CM19", CM19_REP2))
        o = observed(res)
        assert res.status == "ok" and [t["text"] for t in o["targets"]] == ["fundraising"]
        assert ev.check(ev.case_golds(BY["CM19"])[0], o)["unsafe"] is False

    def test_same_declared_unspecified_survives_a_repair(self):
        # F6 only blocks a no-target basis introduced by the repair alone
        main = item("CM09", 2, [R(2, "relevant", "vague")], "unspecified")
        main["duration"] = "D9"
        rep = item("CM09", 2, [R(2, "relevant", "vague")], "unspecified")
        crits = ctx.case_criteria(BY["CM09"])
        val = pa.PassAValidation(False, ["d"], {}, [pa.ScopedError(crits[0].criterion_id, ("duration",), "d")])
        _, _, f6 = pr.merge_pass_a(answer("CM09", main), answer("CM09", rep), val, crits)
        assert f6 == []
        rep["target_basis"] = "unspecified"
        main["target_basis"] = "total_experience"
        val = pa.PassAValidation(False, ["b"], {}, [pa.ScopedError(crits[0].criterion_id, ("target_basis",), "b")])
        _, _, f6 = pr.merge_pass_a(answer("CM09", main), answer("CM09", rep), val, crits)
        assert len(f6) == 1 and "'unspecified' only in the repair" in f6[0]

    @pytest.mark.parametrize("case_id,raw_item", [("CM21", CM21_MAIN), ("CM23", CM23_RUN2), ("CM26", CM26_RUN1),
                                                  ("CM30", CM30_MAIN)])
    def test_first_answer_losses_are_measured_unsafe(self, case_id, raw_item):
        # these validate (a wrong but consistent answer cannot be detected by the validator): the harness counts
        # them as unsafe target loss, which is the hard gate of the next real run
        _, res = pass_a(case_id, answer(case_id, raw_item))
        assert res.status == "ok" and res.outcome.meta["repair_used"] is False
        c = ev.check(ev.case_golds(BY[case_id])[0], observed(res))
        assert c["unsafe"] is True and c["targets_lost"]

    @pytest.mark.parametrize("case_id,raw_item", [("CM23", CM23_RUN2), ("CM26", CM26_RUN1), ("CM30", CM30_MAIN)])
    def test_no_target_claims_stay_fail_closed_downstream(self, case_id, raw_item):
        # strict F5: a declared total_experience never resolves and never gets an S2 view
        case = BY[case_id]
        crits = ctx.case_criteria(case)
        pb = json.dumps({"criteria": [{"criterion_id": c.criterion_id, "contexts": [], "context_spans": [],
                                       "target_gap": []} for c in crits]})
        out = asm.run_scripted_job(ctx.case_job_id(case), ctx.case_jd(case), ctx.case_analysis(case),
                                   answer(case_id, raw_item), pb)
        (art,) = out.artifacts
        assert art.target_state == "target_absent_claimed" and art.spec_status == "needs_confirmation"
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(art, require_resolved=False)
        assert e.value.code == "target_unconfirmed"

    def test_cm21_wrong_target_is_caught_by_a_pass_b_gap(self):
        # the one unsafe shape that is target_fixed: only Pass B's required target_gap can stop it downstream
        case = BY["CM21"]
        (c,) = ctx.case_criteria(case)
        pb = json.dumps({"criteria": [{"criterion_id": c.criterion_id, "contexts": [], "context_spans": [],
                                       "target_gap": [{"line": 2, "text": "project controls"}]}]})
        out = asm.run_scripted_job(ctx.case_job_id(case), ctx.case_jd(case), ctx.case_analysis(case),
                                   answer("CM21", CM21_MAIN), pb)
        (art,) = out.artifacts
        assert art.target_state == "target_fixed" and "target_unconfirmed" in {r.code for r in art.reasons}
        with pytest.raises(asm.S1V4ViewError):
            asm.s2_views_v4(art, require_resolved=False)


# ── 4. expansion diagnostic (reporting only) ────────────────────────────────

class TestExpansionDiagnostic:
    @pytest.mark.parametrize("gold,obs,extra", [
        ("إدارة المخاطر", "إدارة المخاطر لدى المؤسسات المالية", ["المؤسسات", "المالية"]),     # CM13
        ("محاسب", "كمحاسب في البنوك", ["البنوك"]),                                             # CM38
        ("الائتمان", "الائتمان بالقطاع المصرفي", ["بالقطاع", "المصرفي"]),                      # CM39
        ("المشتريات", "المشتريات لدى الجهات الحكومية", ["الجهات", "الحكومية"]),               # CM40
        ("الصيانة", "في الصيانة", []),                                                         # CM23: leading في
        ("legal", "legal experience", []),                                                     # CM09 run 3
        ("التدقيق الداخلي", "في التدقيق الداخلي", []),
        ("project controls", "infrastructure projects", []),                                   # not carried: lost
        ("fundraising", "fundraising", []),
    ])
    def test_expansion(self, gold, obs, extra):
        assert ev.expansion(gold, obs) == extra

    def test_recorded_shapes_are_reported_but_never_gate(self):
        recs = []
        for cid, text in (("CM13", "إدارة المخاطر لدى المؤسسات المالية"), ("CM39", "الائتمان بالقطاع المصرفي"),
                          ("CM40", "المشتريات لدى الجهات الحكومية")):
            g = ev.case_golds(BY[cid])
            o = [{"outcome": "ok", "basis": "targets", "policy": "functional",
                  "targets": [{"text": text, "type": "function"}]}]
            ch = [ev.check(x, y) for x, y in zip(g, o)]
            recs.append({"case": cid, "run": 1, "job_outcome": "ok", "criteria": o, "checks": ch, "gold": g,
                         "calls": [], "repair_used": False, "pass": True})
        s = ev.summarize(recs)
        assert s["target_expansion"]["criterion_runs"] == 3 and s["target_expansion"]["cases"] == ["CM13", "CM39",
                                                                                                     "CM40"]
        assert s["target_expansion"]["rate"] == 1.0
        # existing metrics unchanged: these still count as correct targets and are not unsafe
        assert s["target_accuracy"] == 1.0 and s["hard"]["unsafe_target_policy_loss"] == 0
        g = ev.evaluate_gates(s)
        assert not any("expansion" in k for k in g["gates"]) and g["all_decided_pass"]
        assert "Target expansion (diagnostic only, not a gate)" in ev.render_markdown(
            {k: "x" for k in ("prompt_version", "prompt_fingerprint", "s1_version", "model", "temperature", "mode",
                              "runs", "fixture", "fixture_version", "fixture_sha256")}, s, g)

    def test_oracle_has_no_expansion(self):
        recs = run(ev.run_all(MAIN["cases"], runs=1, client_for=lambda c: ctx.ScriptedClient(ev.oracle_response(c))))
        s = ev.summarize(recs)
        assert s["target_expansion"] == {"criterion_runs": 0, "rate": 0.0, "cases": [], "details": []}

    def test_harness_pins_the_current_prompt(self):
        # since S1-A-1.3 the harness pins s1a-1.3; s1a-1.1 stays pinned in the prompt module for replay
        assert ev.PINNED["prompt_version"] == "s1a-1.3"
        assert ev.PINNED["prompt_sha256"] == prompt_a.PROMPT_SHA256["s1a-1.3"]
        assert prompt_a.PROMPT_SHA256["s1a-1.1"] == "952299303431f68d62b9544d6897baa488855c37c22d0fd2890789b15d463e11"
        assert ev.check_pins(SCRIPTS / "s1_eval_fixtures" / "s1_ctx_main_cases.json") == []
