"""
Option D (Pass A prompt s1a-1.2): the required structured judgement "names_role_or_work", its deterministic
AGREEMENT check with restrictions / target_basis (criteria without hints), the single repair, fail-closed paths,
hint authority, note staying audit-only, and the harness on the committed target-basis fixture.
Offline only: scripted clients, no model call, no network, no database; held-out fixtures are never read.
"""
import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
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

P12 = prompt_a.load_pass_a_prompt()
P11 = prompt_a.load_pass_a_prompt("s1a-1.1")
TB = json.loads((SCRIPTS / "s1_eval_fixtures" / "s1a_target_basis_cases.json").read_text(encoding="utf-8"))
TB_BY = {c["id"]: c for c in TB["cases"]}


class FakeClient:
    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            item = self.items.pop(0)
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def job(hints, years, line):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "Requirements\n- " + line + "."
    return a, jd, enumerate_experience_criteria("J1", a)


def ans(cid, line, *, nrw, restrictions=None, basis, targets=None, note="n", **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": 2, "text": line}]}
    if nrw is not ...:
        it["names_role_or_work"] = nrw
    if targets is not None:
        it["targets"] = targets
    else:
        it["restrictions"] = [{"line": 2, "text": t, "kind": k} for t, k in (restrictions or [])]
    it.update({"target_basis": basis, "duration": "D1", "ambiguity": [], "note": note})
    it.update(over)
    return json.dumps({"criteria": [it]}, ensure_ascii=False)


def validate(a, jd, crits, raw):
    j = JDText(jd)
    return pa.validate_pass_a(raw, j, crits, pa.build_pass_a_request(j, crits).durations)


def pass_a(a, jd, *raws, **kw):
    client = FakeClient(*raws)
    return client, run(pr.run_pass_a_job("J1", jd, a, client=client, **kw))


def note_of(client):
    return client.requests[1]["messages"][-1]["content"]


HINT = [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}]
L_PM = "At least 6 years of project management experience"           # the CM30 shape (MAIN wording)
L_TOT = "Minimum 5 years of professional experience"
L_REL = "Minimum 4 years of relevant experience"
L_SET = "3 years of experience in the hospitality sector"
L_ROLE = "Minimum 5 years of experience as a Site Supervisor"


# ── prompt s1a-1.2 ──────────────────────────────────────────────────────────

class TestPrompt:
    def test_version_pin(self):
        assert hashlib.sha256(P12.encode("utf-8")).hexdigest() == prompt_a.PROMPT_SHA256["s1a-1.2"] == (
            "f7ec01e2816744322205a889e2834270a30decea625a84355fa47c70e416afd3")
        assert prompt_a.pass_a_prompt_fingerprint() == "f7ec01e28167"

    def test_only_change_from_s1a_1_1_is_the_field(self):
        start = P12.index("\n\n1a NAMES_ROLE_OR_WORK")
        end = P12.index("\n\n2 TARGETS")
        stripped = (P12[:start] + P12[end:]).replace('"names_role_or_work": true, ', "")
        assert stripped == P11

    def test_definition(self):
        s = P12[P12.index("1a NAMES_ROLE_OR_WORK"):P12.index("2 TARGETS")]
        assert "REQUIRED for every criterion, true or false, given BEFORE targets / restrictions" in s
        assert "true   the statement explicitly names a role or position the experience must be AS, or the work, " \
               "function, discipline or professional activity it must be IN" in s
        assert "false  the statement only asks for total, general, professional or relevant experience, or only " \
               "says where or for whom it was gained, without naming the role or the work" in s
        assert "Judge the requirement statement itself, never the target_hints" in s
        assert 'For criteria WITH target_hints, target_basis stays "targets" whatever this judgement says.' in s

    def test_field_precedes_targets_and_restrictions_in_every_example(self):
        out = P12[P12.index("OUTPUT:"):P12.index("SECURITY RULES")]
        examples = out.split('{"criterion_id"')[1:]
        assert len(examples) == 4
        for e in examples:
            i = e.index('"names_role_or_work": true')
            j = e.index('"targets"') if '"targets"' in e else e.index('"restrictions"')
            assert i < j
        assert '"names_role_or_work": false' not in out          # no no-target template in the examples

    def test_still_free_of_context_vocabulary(self):
        for frag in ("sector", "context", "settings", "qualifying"):
            assert frag not in P12.lower()


# ── validator: agreement (criteria without hints) ───────────────────────────

class TestAgreement:
    def test_cm30_contradiction_is_an_error_that_prescribes_nothing(self):
        a, jd, crits = job([], 6, L_PM)
        cid = crits[0].criterion_id
        v = validate(a, jd, crits, ans(cid, L_PM, nrw=True, basis="total_experience"))
        assert not v.ok
        (e,) = [x for x in v.scoped if pa.NRW_DISAGREE_MESSAGE in x.message]
        assert set(e.scopes) == {"names_role_or_work", "target_basis", "restrictions"}
        assert "expected one of" not in e.message and "project management" not in e.message

    def test_inverse_contradiction(self):
        a, jd, crits = job([], 6, L_PM)
        cid = crits[0].criterion_id
        v = validate(a, jd, crits, ans(cid, L_PM, nrw=False, restrictions=[("project management", "function")],
                                       basis="targets"))
        assert not v.ok and any(pa.NRW_DISAGREE_MESSAGE in e for e in v.errors)

    @pytest.mark.parametrize("line,nrw,restrictions,basis", [
        (L_PM, True, [("project management", "function")], "targets"),
        (L_ROLE, True, [("Site Supervisor", "role")], "targets"),
        (L_TOT, False, [], "total_experience"),
        (L_SET, False, [], "setting_only"),
        (L_REL, False, [("relevant", "vague")], "unspecified"),
    ])
    def test_consistent_answers_validate(self, line, nrw, restrictions, basis):
        a, jd, crits = job([], 5, line)
        cid = crits[0].criterion_id
        amb = ["ambiguous_relevance"] if basis == "unspecified" else []
        v = validate(a, jd, crits, ans(cid, line, nrw=nrw, restrictions=restrictions, basis=basis, ambiguity=amb))
        assert v.ok, v.errors
        assert v.results[cid].names_role_or_work is nrw and v.results[cid].target_basis == basis

    @pytest.mark.parametrize("line,restrictions,basis", [
        (L_TOT, [], "total_experience"), (L_SET, [], "setting_only"),
        (L_REL, [("relevant", "vague")], "unspecified")])
    def test_true_without_a_target_is_rejected_for_every_no_target_basis(self, line, restrictions, basis):
        a, jd, crits = job([], 5, line)
        cid = crits[0].criterion_id
        amb = ["ambiguous_relevance"] if basis == "unspecified" else []
        v = validate(a, jd, crits, ans(cid, line, nrw=True, restrictions=restrictions, basis=basis, ambiguity=amb))
        assert not v.ok and any(pa.NRW_DISAGREE_MESSAGE in e for e in v.errors)

    @pytest.mark.parametrize("value", [..., None, "true", "false", 1, 0, [], {}])
    def test_missing_or_non_boolean(self, value):
        a, jd, crits = job([], 6, L_PM)
        cid = crits[0].criterion_id
        v = validate(a, jd, crits, ans(cid, L_PM, nrw=value, restrictions=[("project management", "function")],
                                       basis="targets"))
        assert not v.ok and any(pa.NRW_REQUIRED_MESSAGE in e for e in v.errors)
        (e,) = [x for x in v.scoped if pa.NRW_REQUIRED_MESSAGE in x.message]
        assert e.scopes == ("names_role_or_work",)


# ── hinted criteria: hints stay authoritative, the judgement is audit only ──

class TestHinted:
    @pytest.mark.parametrize("nrw", [True, False])
    def test_judgement_never_changes_hint_targets(self, nrw):
        a, jd, crits = job(["Site Supervisor"], 5, L_ROLE)
        cid = crits[0].criterion_id
        client, res = pass_a(a, jd, ans(cid, L_ROLE, nrw=nrw, targets=HINT, basis="targets"))
        assert res.status == "ok" and res.outcome.meta["calls"] == 1 and not res.outcome.meta["repair_used"]
        (f,) = res.frozen
        assert [t.text for t in f.frame.targets] == ["Site Supervisor"] and f.frame.target_basis == "targets"
        assert f.artefact.audit["names_role_or_work"] is nrw
        assert res.outcome.meta["nrw_disagreements"] == []

    def test_recruiter_confirmed_roles_stay_governing(self):
        a, jd, crits = job(["Site Supervisor"], 5, L_ROLE)
        cid = crits[0].criterion_id
        _, res = pass_a(a, jd, ans(cid, L_ROLE, nrw=False, targets=HINT, basis="targets"),
                        recruiter_fields={"experience.relevant_roles": "recruiter_confirmed"})
        (f,) = res.frozen
        assert [(t.text, t.provenance) for t in f.frame.targets] == [("Site Supervisor", "recruiter_confirmed")]

    def test_hinted_still_requires_the_field(self):
        a, jd, crits = job(["Site Supervisor"], 5, L_ROLE)
        cid = crits[0].criterion_id
        v = validate(a, jd, crits, ans(cid, L_ROLE, nrw=..., targets=HINT, basis="targets"))
        assert not v.ok and any(pa.NRW_REQUIRED_MESSAGE in e for e in v.errors)

    def test_hinted_basis_rule_unchanged(self):
        a, jd, crits = job(["Site Supervisor"], 5, L_ROLE)
        cid = crits[0].criterion_id
        v = validate(a, jd, crits, ans(cid, L_ROLE, nrw=False, targets=HINT, basis="total_experience"))
        assert any(pa.BASIS_HINTED_MESSAGE in e for e in v.errors)
        assert not any(pa.NRW_DISAGREE_MESSAGE in e for e in v.errors)


# ── repair and fail-closed behaviour ────────────────────────────────────────

class TestRepair:
    def _pm(self):
        a, jd, crits = job([], 6, L_PM)
        return a, jd, crits[0].criterion_id

    def test_cm30_contradiction_repaired_together(self):
        a, jd, cid = self._pm()
        client, res = pass_a(a, jd, ans(cid, L_PM, nrw=True, basis="total_experience"),
                             ans(cid, L_PM, nrw=True, restrictions=[("project management", "function")],
                                 basis="targets"))
        assert res.status == "ok" and res.outcome.meta["repair_used"] is True
        (f,) = res.frozen
        assert [t.text for t in f.frame.targets] == ["project management"] and f.frame.target_basis == "targets"
        assert res.outcome.meta["nrw_disagreements"] == [cid]
        taken = {t["field"] for t in res.outcome.meta["repair_merge"]["taken"] if t["criterion_id"] == cid}
        assert {"restrictions", "target_basis", "names_role_or_work"} <= taken
        n = note_of(client)
        assert pa.NRW_DISAGREE_MESSAGE in n and "expected one of" not in n
        for w in ("context", "sector", "project management"):
            assert w not in n.split("Never return a policy:")[1].lower()

    def test_contradiction_kept_by_the_repair_fails_closed(self):
        a, jd, cid = self._pm()
        bad = ans(cid, L_PM, nrw=True, basis="total_experience")
        _, res = pass_a(a, jd, bad, bad)
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert all(x.target_state == "target_failed" and x.policy is None for x in res.failed)

    def test_repair_may_resolve_toward_no_target_but_never_alone(self):
        # code never decides which side is right: a repair that keeps the main's own declared basis and changes the
        # judgement is accepted (strict F5 keeps it out of any S2 view downstream) ...
        a, jd, cid = self._pm()
        _, res = pass_a(a, jd, ans(cid, L_PM, nrw=True, basis="total_experience"),
                        ans(cid, L_PM, nrw=False, basis="total_experience"))
        assert res.status == "ok" and res.frozen[0].frame.target_basis == "total_experience"
        assert res.frozen[0].artefact.audit["names_role_or_work"] is False
        # ... but a no-target basis introduced by the repair alone is still F6-rejected
        _, res = pass_a(a, jd, ans(cid, L_PM, nrw=False, restrictions=[("project management", "function")],
                                   basis="targets"),
                        ans(cid, L_PM, nrw=False, basis="total_experience"))
        assert res.status == "failed" and res.outcome.meta["outcome"] == "f6_rejected"

    def test_inverse_contradiction_repaired(self):
        a, jd, cid = self._pm()
        _, res = pass_a(a, jd, ans(cid, L_PM, nrw=False, restrictions=[("project management", "function")],
                                   basis="targets"),
                        ans(cid, L_PM, nrw=True, restrictions=[("project management", "function")], basis="targets"))
        assert res.status == "ok" and res.frozen[0].artefact.audit["names_role_or_work"] is True

    def test_missing_field_repaired_or_failed(self):
        a, jd, cid = self._pm()
        good = ans(cid, L_PM, nrw=True, restrictions=[("project management", "function")], basis="targets")
        missing = ans(cid, L_PM, nrw=..., restrictions=[("project management", "function")], basis="targets")
        _, res = pass_a(a, jd, missing, good)
        assert res.status == "ok" and res.outcome.meta["repair_used"]
        _, res = pass_a(a, jd, missing, missing)
        assert res.status == "failed" and res.outcome.reason == "validation_failed"

    def test_no_repair_when_consistent(self):
        a, jd, cid = self._pm()
        client, res = pass_a(a, jd, ans(cid, L_PM, nrw=True, restrictions=[("project management", "function")],
                                        basis="targets"))
        assert res.status == "ok" and len(client.requests) == 1 and res.outcome.meta["nrw_disagreements"] == []


# ── English / Arabic work-vs-setting contrasts (committed target-basis fixture, unchanged) ─────

def _tb_answer(case, nrw, job_id="J1"):
    jd = "\n".join(case["jd_lines"])
    a = {"experience": copy.deepcopy(case["analysis"])}
    (crit,) = enumerate_experience_criteria(job_id, a)
    durs = {m.text: did for did, _, m in JDText(jd).durations()}
    o = {**copy.deepcopy(case["oracle"]), "criterion_id": crit.criterion_id, "names_role_or_work": nrw}
    o["duration"] = durs[o["duration"]]
    return a, jd, json.dumps({"criteria": [o]}, ensure_ascii=False)


class TestContrasts:
    @pytest.mark.parametrize("pair", [("BE15", "BE16"), ("BE17", "BE18"), ("BE19", "BE09"), ("BA12", "BA07"),
                                      ("BA13", "BA08"), ("BA18", "BA09"), ("BA21", "BA01")])
    def test_gold_judgement_validates_and_flipped_judgement_is_caught(self, pair):
        for cid in pair:
            case = TB_BY[cid]
            g = case["gold"]["names_role_or_work"]
            a, jd, raw = _tb_answer(case, g)
            _, res = pass_a(a, jd, raw)
            assert res.status == "ok" and res.outcome.validation.results[
                res.frozen[0].criterion.criterion_id].names_role_or_work is g
            a, jd, flipped = _tb_answer(case, not g)
            _, res = pass_a(a, jd, flipped, flipped)
            assert res.status == "failed" and res.outcome.meta["nrw_disagreements"], cid

    def test_pairs_really_differ(self):
        assert TB_BY["BE15"]["gold"]["names_role_or_work"] and not TB_BY["BE16"]["gold"]["names_role_or_work"]
        assert TB_BY["BA12"]["gold"]["names_role_or_work"] and not TB_BY["BA07"]["gold"]["names_role_or_work"]


# ── note stays audit only ───────────────────────────────────────────────────

class TestNoteAuditOnly:
    def test_note_never_changes_validation(self):
        a, jd, crits = job([], 5, L_TOT)
        cid = crits[0].criterion_id
        base = validate(a, jd, crits, ans(cid, L_TOT, nrw=False, basis="total_experience", note="n"))
        work = validate(a, jd, crits, ans(cid, L_TOT, nrw=False, basis="total_experience",
                                          note="The requirement specifies experience in project management."))
        assert base.ok and work.ok and base.errors == work.errors == []
        assert base.results[cid].target_basis == work.results[cid].target_basis

    def test_validator_and_runner_never_read_note(self):
        for name in ("pass_a.py", "pass_a_runner.py"):
            src = (BACKEND / "services" / "s1_two_pass" / name).read_text(encoding="utf-8")
            assert '["note"]' not in src and 'get("note")' not in src, name

    def test_judgement_is_audit_not_semantics(self):
        a, jd, crits = job(["Site Supervisor"], 5, L_ROLE)
        cid = crits[0].criterion_id
        arts = []
        for nrw in (True, False):
            _, res = pass_a(a, jd, ans(cid, L_ROLE, nrw=nrw, targets=HINT, basis="targets"))
            f = res.frozen[0]
            pb = json.dumps({"criteria": [{"criterion_id": cid, "contexts": [], "context_spans": [],
                                           "target_gap": []}]})
            v4 = asm.assemble_v4(f, asm.validate_pass_b(pb, JDText(jd), [f.frame]).results[cid], "ok", run={})
            arts.append(v4)
        assert arts[0].content_hash == arts[1].content_hash
        assert arts[0].audit["names_role_or_work"] is True and arts[1].audit["names_role_or_work"] is False


# ── harness on the target-basis fixture ─────────────────────────────────────

class TestHarness:
    def test_target_basis_oracle_scores_perfectly(self):
        recs = run(ev.run_all(TB["cases"], runs=2, client_for=lambda c: ctx.ScriptedClient(ev.oracle_response(c))))
        s = ev.summarize(recs)
        for k in ("names_role_or_work_accuracy", "target_accuracy", "target_basis_accuracy", "policy_accuracy",
                  "stability"):
            assert s[k] == 1.0, k
        assert s["failure_rate"] == 0.0 and s["nrw_disagreement_rate"] == 0.0 and s["repair_rate"] == 0.0
        assert s["gold_basis_distribution"] == {"setting_only": 20, "targets": 44, "total_experience": 16,
                                                "unspecified": 8}
        assert ev.evaluate_gates(s)["all_decided_pass"]

    def test_disagreement_repair_and_failure_rates(self):
        cases = [TB_BY["BE11"], TB_BY["BE01"]]                     # tax advisory (work) / professional (none)
        plan = {}
        for case in cases:
            g = case["gold"]["names_role_or_work"]
            _, _, good = _tb_answer(case, g, ctx.case_job_id(case))
            _, _, flipped = _tb_answer(case, not g, ctx.case_job_id(case))
            plan[case["id"]] = [flipped, good] if case["id"] == "BE11" else [flipped, flipped]

        def client_for(case):
            return ctx.ScriptedClient(*plan[case["id"]])
        s = ev.summarize(run(ev.run_all(cases, runs=1, client_for=client_for)))
        assert s["nrw_disagreement_rate"] == 1.0                   # both first answers contradicted themselves
        assert s["repair_rate"] == 1.0 and s["repair_calls"] == 2
        assert s["failure_rate"] == 0.5                             # BE01's repair kept the contradiction
        assert s["names_role_or_work_accuracy"] == 1.0 and s["outcomes"]["ok"] == 1

    def test_judgement_miss_is_counted(self):
        case = TB_BY["BE16"]                                         # setting only: gold false
        a, jd, raw = _tb_answer(case, False, ctx.case_job_id(case))
        o = json.loads(raw)
        o["criteria"][0]["names_role_or_work"] = True                # wrong judgement ...
        o["criteria"][0]["restrictions"] = [{"line": 2, "text": "insurance sector", "kind": "function"}]
        o["criteria"][0]["target_basis"] = "targets"                 # ... with a consistent (wrong) target
        s = ev.summarize(run(ev.run_all([case], runs=1, client_for=lambda c: ctx.ScriptedClient(json.dumps(o)))))
        assert s["names_role_or_work_accuracy"] == 0.0 and s["target_basis_accuracy"] == 0.0
        assert s["hard"]["unsafe_target_policy_loss"] == 0               # narrowing is never unsafe

    def test_fixture_choice_and_pins(self, tmp_path):
        assert set(ev.FIXTURES) == {"main", "target_basis"}
        assert not any("heldout" in str(p) for p in ev.FIXTURES.values())
        assert ev.check_pins(ev.FIXTURES["target_basis"], "target_basis") == []
        assert ev.check_pins(ev.FIXTURES["main"], "main") == []
        assert ev.main(["--out", str(tmp_path / "o"), "--fixture", "target_basis", "--mode", "oracle",
                        "--runs", "1"]) == 0
        data = json.loads((tmp_path / "o" / "results.json").read_text(encoding="utf-8"))
        assert data["meta"]["fixture"] == "target_basis" and data["meta"]["fixture_sha256"] == ev.FIXTURE_SHA256[
            "target_basis"]
        with pytest.raises(SystemExit):
            ev.main(["--out", str(tmp_path / "h"), "--fixture", "heldout"])

    def test_main_gates_not_weakened(self):
        assert ev.THRESHOLDS["target_accuracy"] == ev.THRESHOLDS["policy_accuracy"] == ev.THRESHOLDS[
            "target_basis_accuracy"] == ev.THRESHOLDS["stability"] == 0.95
        assert ev.THRESHOLDS["failure_rate"] == 0.05
        s = {"hard": {"unsafe_target_policy_loss": 1, "independence_failures": 0}, "target_accuracy": 1.0,
             "policy_accuracy": 1.0, "target_basis_accuracy": 1.0, "names_role_or_work_accuracy": 1.0,
             "stability": 1.0, "failure_rate": 0.0}
        assert ev.evaluate_gates(s)["all_decided_pass"] is False
