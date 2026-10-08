"""
S1-A-1.3 — target-basis classification correction (prompt s1a-1.3, contract where_evidence), Stage A offline checks.

  1. contract: valid setting evidence is accepted (English and Arabic, proclitic boundaries); every contradictory
     combination is rejected (setting_only without evidence, evidence with any other basis or a hinted criterion,
     evidence overlapping a restriction or the duration, outside the requirement span, not verbatim, too many,
     overlapping each other, not a list);
  2. the basis follows from the typed answer and is checked on the RAW answer (never masked by another error);
  3. repair: the neutral four-way message, where_evidence follows the basis, the where guard (a repair never turns
     a main where_evidence span into a role / function), F6 and the restriction locks unchanged, functional targets
     protected;
  4. where_evidence is preserved on the S1-A result (PassACriterion -> TargetFrame) and never enters the Pass B input;
  5. s1a-1.1 stays reproducible under its own contract;
  6. the deterministic outcome of the S1-A-1.3 review paths A-U under the new contract;
  7. the reporting-only harness extensions (confusion, where_evidence, conversions, slices, per-case stability).
Offline only: scripted clients, no model call, no network, no database, no held-out fixture.
"""
import asyncio
import copy
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
from services.s1_two_pass import pass_b as pb
from services.s1_two_pass import prompt_a

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("s1_pass_a_eval", SCRIPTS / "s1_pass_a_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
ctx = ev.ctx

PROMPT = prompt_a.load_pass_a_prompt()
C11, C13 = pa.CONTRACTS["s1a-1.1"], pa.CONTRACTS["s1a-1.3"]

# SYNTHETIC statements (not fixture wording)
EN_SET = "Minimum 5 years of experience with logistics carriers"
EN_SET_W = "logistics carriers"
EN_COMB = "Minimum 4 years of credit analysis experience with logistics carriers"
EN_VAGUE_SET = "Minimum 4 years of relevant experience with logistics carriers"
EN_TOTAL = "Minimum 6 years of overall experience"
EN_VAGUE = "Minimum 6 years of comparable relevant experience"
AR_SET = "خبرة 5 سنوات بقطاع التأمين الصحي"
AR_SET_W = "قطاع التأمين الصحي"
AR_COMB = "خبرة 4 سنوات في تحليل الائتمان لدى شركات النقل"
AR_ADJ = "خبرة قانونية لا تقل عن 5 سنوات"


class FakeClient:
    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.items.pop(0)),
                                                            finish_reason="stop")], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def job(hints, years, line, *, ar=False):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "\n".join(["المتطلبات" if ar else "Requirements", "- " + line + "."])
    return a, jd, enumerate_experience_criteria("J1", a)


def free(cid, line, restrictions=(), *, basis, where=None, duration="D1", ambiguity=(), **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": 2, "text": line}],
          "restrictions": [{"line": 2, "text": t, "kind": k} for t, k in restrictions],
          "target_basis": basis, "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    if where is not None:
        it["where_evidence"] = [{"line": 2, "text": w} if isinstance(w, str) else w for w in where]
    it.update(over)
    return it


def resp(*items):
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


def validate(a, jd, raw, contract=None):
    crits = enumerate_experience_criteria("J1", a)
    req = pa.build_pass_a_request(JDText(jd), crits)
    return pa.validate_pass_a(raw, JDText(jd), crits, req.durations, contract=contract)


def pass_a_job(a, jd, *answers, prompt_version=None):
    client = FakeClient(*answers)
    return client, run(pr.run_pass_a_job("J1", jd, a, client=client, prompt_version=prompt_version))


def note(client):
    return client.requests[1]["messages"][-1]["content"]


# ── 1. the contract: valid evidence accepted, contradictions rejected ───────

class TestContractAccepts:
    @pytest.mark.parametrize("line,where,ar", [
        (EN_SET, EN_SET_W, False),
        (EN_SET, "logistics carriers", False),
        (AR_SET, AR_SET_W, True),                       # starts right after the attached proclitic ب
        (AR_SET, "بقطاع التأمين الصحي", True),           # or includes it
    ])
    def test_setting_only_with_verbatim_evidence(self, line, where, ar):
        a, jd, crits = job([], 5, line, ar=ar)
        v = validate(a, jd, resp(free(crits[0].criterion_id, line, basis="setting_only", where=[where])))
        assert v.ok, v.errors
        r = v.results[crits[0].criterion_id]
        assert r.target_basis == "setting_only" and r.parsed.policy == "sector"
        assert [w.text for w in r.where_evidence] == [where] and r.parsed.settings == () and r.parsed.targets == ()

    def test_vague_word_may_accompany_the_where_limit(self):
        a, jd, crits = job([], 4, EN_VAGUE_SET)
        v = validate(a, jd, resp(free(crits[0].criterion_id, EN_VAGUE_SET, [("relevant", "vague")],
                                      basis="setting_only", where=[EN_SET_W])))
        assert v.ok, v.errors and v.results[crits[0].criterion_id].parsed.policy == "sector"

    @pytest.mark.parametrize("line,restr,basis,ar", [
        (EN_COMB, [("credit analysis", "function")], "targets", False),
        (EN_TOTAL, [], "total_experience", False),
        (EN_VAGUE, [("relevant", "vague")], "unspecified", False),
        (AR_COMB, [("تحليل الائتمان", "function")], "targets", True),
        (AR_ADJ, [("قانونية", "function")], "targets", True),       # the Arabic adjective form names the work
    ])
    @pytest.mark.parametrize("where", [None, []])
    def test_other_bases_without_evidence(self, line, restr, basis, ar, where):
        a, jd, crits = job([], 4, line, ar=ar)
        v = validate(a, jd, resp(free(crits[0].criterion_id, line, restr, basis=basis, where=where)))
        assert v.ok, v.errors and v.results[crits[0].criterion_id].where_evidence == ()


class TestContractRejects:
    def _errs(self, line, restr, basis, where, *, ar=False, hints=(), **over):
        a, jd, crits = job(hints, 5, line, ar=ar)
        cid = crits[0].criterion_id
        if hints:
            it = {"criterion_id": cid, "requirement_spans": [{"line": 2, "text": line}],
                  "targets": [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}],
                  "target_basis": basis, "duration": "D1", "ambiguity": [], "note": "n", **over}
            if where is not None:
                it["where_evidence"] = [{"line": 2, "text": w} for w in where]
        else:
            it = free(cid, line, restr, basis=basis, where=where, **over)
        v = validate(a, jd, resp(it))
        assert not v.ok and v.results == {}
        return v

    def test_setting_only_without_evidence(self):
        v = self._errs(EN_SET, [], "setting_only", None)
        (e,) = [x for x in v.scoped if "target_basis" in x.scopes]
        assert set(e.scopes) == {"target_basis", "restrictions", "where_evidence"}
        assert pa.BASIS_NEUTRAL_MESSAGE in e.message

    @pytest.mark.parametrize("basis", ["total_experience", "unspecified"])
    def test_evidence_with_a_no_where_basis(self, basis):
        restr = [("relevant", "vague")] if basis == "unspecified" else []
        line = EN_VAGUE_SET if basis == "unspecified" else EN_SET
        v = self._errs(line, restr, basis, [EN_SET_W])
        assert any("the restrictions and where_evidence disagree" in e for e in v.errors)

    def test_evidence_with_a_named_function(self):
        v = self._errs(EN_COMB, [("credit analysis", "function")], "targets", [EN_SET_W])
        (e,) = v.scoped
        assert e.scopes == ("where_evidence",)                  # the function and its basis stay: only drop it
        assert "never changes a role or work target" in e.message

    def test_setting_only_with_a_named_function(self):
        v = self._errs(EN_COMB, [("credit analysis", "function")], "setting_only", [EN_SET_W])
        assert any("disagree" in e for e in v.errors)

    def test_evidence_on_a_hinted_criterion(self):
        v = self._errs("Minimum 5 years as an Internal Auditor with logistics carriers", [], "targets",
                       [EN_SET_W], hints=["Internal Auditor"])
        assert any("target_hints has no where_evidence" in e for e in v.errors)

    def test_evidence_overlapping_a_restriction(self):
        v = self._errs(EN_SET, [("logistics carriers", "vague")], "setting_only", [EN_SET_W])
        assert any("the same words are never both what and where" in e for e in v.errors)

    def test_evidence_overlapping_the_duration(self):
        v = self._errs(EN_SET, [], "setting_only", ["5 years of experience"])
        assert any("must not include the duration" in e for e in v.errors)

    @pytest.mark.parametrize("where,ar", [("logistics carrier", False), ("ogistics carriers", False),
                                          ("لقطاع التأمين الصحي", True), ("طاع التأمين", True)])
    def test_evidence_not_verbatim_whole_words(self, where, ar):
        v = self._errs(AR_SET if ar else EN_SET, [], "setting_only", [where], ar=ar)
        assert any("where_evidence[0]" in e and "not verbatim" in e for e in v.errors)

    def test_evidence_outside_the_requirement_span(self):
        a, jd, crits = job([], 5, EN_SET)
        jd = jd + "\n- We serve logistics carriers across the country."
        it = free(crits[0].criterion_id, EN_SET, basis="setting_only", where=[{"line": 3, "text": EN_SET_W}])
        v = validate(a, jd, resp(it))
        assert not v.ok and any("not inside one of this criterion's requirement_spans" in e for e in v.errors)

    def test_evidence_spans_overlap_each_other(self):
        v = self._errs(EN_SET, [], "setting_only", [EN_SET_W, "carriers"])
        assert any("overlap; quote each limit once" in e for e in v.errors)

    def test_too_many_evidence_spans(self):
        line = "Minimum 5 years of experience with aa, bb, cc, dd, ee and ff"
        v = self._errs(line, [], "setting_only", ["with aa", "bb", "cc", "dd", "ee", "ff"])
        assert any("at most 5 where_evidence spans" in e for e in v.errors)

    @pytest.mark.parametrize("value", [{"line": 2, "text": EN_SET_W}, EN_SET_W])
    def test_evidence_must_be_a_list(self, value):
        a, jd, crits = job([], 5, EN_SET)
        v = validate(a, jd, resp(free(crits[0].criterion_id, EN_SET, basis="setting_only", where_evidence=value)))
        assert not v.ok and any("where_evidence must be a list" in e for e in v.errors)

    def test_disagreement_reported_even_with_another_error(self):
        # the basis check runs on the raw answer: an unrelated invalid span does not mask it (s1a-1.2 gap)
        a, jd, crits = job([], 5, EN_SET)
        it = free(crits[0].criterion_id, EN_SET, basis="setting_only")
        it["requirement_spans"] = [{"line": 2, "text": "not in the jd at all"}]
        v = validate(a, jd, resp(it))
        assert any("disagree" in e for e in v.errors) and any("not verbatim" in e for e in v.errors)


# ── 2. repair ───────────────────────────────────────────────────────────────

class TestRepair:
    def test_neutral_message_names_all_four_bases_and_no_context_vocabulary(self):
        m = pa.BASIS_NEUTRAL_MESSAGE
        for b in ('"targets"', '"setting_only"', '"unspecified"', '"total_experience"'):
            assert m.count(b) == 1, b
        assert "where_evidence" in m and "context" not in m.lower() and "settings" not in m.lower()
        assert pa.BASIS_REREAD_MESSAGE != m and C13.basis_message == m and C11.basis_message == pa.BASIS_REREAD_MESSAGE

    def test_missing_evidence_repaired_from_the_repair_answer(self):
        a, jd, crits = job([], 5, EN_SET)
        cid = crits[0].criterion_id
        client, res = pass_a_job(a, jd, resp(free(cid, EN_SET, basis="setting_only")),
                                 resp(free(cid, EN_SET, basis="setting_only", where=[EN_SET_W])))
        assert res.status == "ok" and res.outcome.meta["outcome"] == "repaired"
        f = res.frozen[0]
        assert f.frame.target_basis == "setting_only" and [w.text for w in f.frame.where_evidence] == [EN_SET_W]
        assert {"criterion_id": cid, "field": "where_evidence"} in res.outcome.meta["repair_merge"]["taken"]
        n = note(client)
        assert pa.BASIS_NEUTRAL_MESSAGE in n and pr.BASIS_AGREE_WHERE in n and "context" not in n.lower()

    def test_bad_evidence_span_fixed_by_the_repair_alone(self):
        a, jd, crits = job([], 5, EN_SET)
        cid = crits[0].criterion_id
        _, res = pass_a_job(a, jd, resp(free(cid, EN_SET, basis="setting_only", where=["logistics carrier"])),
                            resp(free(cid, EN_SET, basis="setting_only", where=[EN_SET_W])))
        assert res.status == "ok" and [w.text for w in res.frozen[0].frame.where_evidence] == [EN_SET_W]

    def test_evidence_on_a_function_answer_is_dropped_and_the_function_kept(self):
        a, jd, crits = job([], 4, EN_COMB)
        cid = crits[0].criterion_id
        main = free(cid, EN_COMB, [("credit analysis", "function")], basis="targets", where=[EN_SET_W])
        _, res = pass_a_job(a, jd, resp(main), resp(free(cid, EN_COMB, [("credit analysis", "function")],
                                                          basis="targets", where=[])))
        assert res.status == "ok"
        f = res.frozen[0]
        assert [t.text for t in f.frame.targets] == ["credit analysis"] and f.frame.where_evidence == ()

    @pytest.mark.parametrize("line,where,ar", [(EN_SET, EN_SET_W, False), (AR_SET, AR_SET_W, True)])
    def test_where_guard_blocks_setting_to_function_conversion(self, line, where, ar):
        # the main answer gave the words as where_evidence (and, inconsistently, a vague word): the repair may
        # not turn the evidenced words into a function
        a, jd, crits = job([], 5, line, ar=ar)
        cid = crits[0].criterion_id
        main = free(cid, line, basis="total_experience", where=[where])
        rep = free(cid, line, [(where, "function")], basis="targets", where=[])
        _, res = pass_a_job(a, jd, resp(main), resp(rep))
        assert res.status == "failed" and res.outcome.reason == "validation_failed"
        assert res.outcome.meta["outcome"] == "where_guard_rejected"
        assert any(pr.WHERE_GUARD_ERROR in e for e in res.outcome.errors["repair_errors"])
        assert all(x.target_state == "target_failed" and x.policy is None for x in res.failed)

    def test_where_guard_does_not_block_a_different_function(self):
        a, jd, crits = job([], 4, EN_COMB)
        cid = crits[0].criterion_id
        main = free(cid, EN_COMB, basis="targets", where=[EN_SET_W])     # says targets, forgot the function
        rep = free(cid, EN_COMB, [("credit analysis", "function")], basis="targets", where=[])
        _, res = pass_a_job(a, jd, resp(main), resp(rep))
        # the function is not the evidenced words: the guard allows it, F6 is not involved (targets)
        assert res.status == "ok" and [t.text for t in res.frozen[0].frame.targets] == ["credit analysis"]
        assert res.frozen[0].frame.where_evidence == ()

    def test_residual_self_consistent_setting_only_that_omits_a_named_function(self):
        # NOT preventable structurally (documented residual): the answer agrees with itself, so no check sees the
        # omitted work; it is measured by the unsafe gate and blocked downstream (strict F5: no S2 view)
        a, jd, crits = job([], 4, EN_COMB)
        _, res = pass_a_job(a, jd, resp(free(crits[0].criterion_id, EN_COMB, basis="setting_only",
                                                where=[EN_SET_W])))
        assert res.status == "ok" and res.frozen[0].frame.target_basis == "setting_only"
        art = asm.assemble_v4(res.frozen[0], None, "skipped", run={})
        assert art.target_state == "target_absent_claimed"
        with pytest.raises(asm.S1V4ViewError):
            asm.s2_views_v4(art, require_resolved=False)

    def test_functional_target_is_never_repaired_into_a_setting(self):
        a, jd, crits = job([], 4, EN_COMB)
        cid = crits[0].criterion_id
        main = free(cid, EN_COMB, [("credit analysis", "function")], basis="setting_only", where=[EN_SET_W])
        rep = free(cid, EN_COMB, [], basis="setting_only", where=[EN_SET_W])
        _, res = pass_a_job(a, jd, resp(main), resp(rep))
        # the restriction lock keeps the function; the merged answer still disagrees: fail closed, never sector
        assert res.status == "failed" and res.outcome.reason == "validation_failed"

    @pytest.mark.parametrize("main_basis", ["total_experience", "unspecified", "targets"])
    def test_f6_unchanged_repair_never_sole_source_of_setting_only(self, main_basis):
        a, jd, crits = job([], 5, EN_VAGUE_SET)
        cid = crits[0].criterion_id
        restr = {"total_experience": [], "unspecified": [("relevant", "vague")], "targets": []}[main_basis]
        main = free(cid, EN_VAGUE_SET, restr, basis=main_basis, where=[EN_SET_W] if main_basis != "targets" else [])
        rep = free(cid, EN_VAGUE_SET, restr, basis="setting_only", where=[EN_SET_W])
        _, res = pass_a_job(a, jd, resp(main), resp(rep))
        assert res.status == "failed" and res.outcome.meta["outcome"] == "f6_rejected"

    def test_merge_where_follows_the_basis_only(self):
        a, jd, crits = job([], 5, EN_SET)
        cid = crits[0].criterion_id
        main = free(cid, EN_SET, basis="setting_only", where=[EN_SET_W], duration="D9")       # duration error only
        rep = free(cid, EN_SET, basis="setting_only", where=["experience"])
        val = pa.validate_pass_a(resp(main), JDText(jd), crits, pa.build_pass_a_request(JDText(jd), crits).durations)
        assert not val.ok and all("where_evidence" not in e.scopes and "target_basis" not in e.scopes
                                  for e in val.scoped)
        merged, info, viol = pr.merge_pass_a(resp(main), resp(rep), val, crits, jd=JDText(jd))
        it = json.loads(merged)["criteria"][0]
        assert it["where_evidence"] == main["where_evidence"] and not viol       # not in scope: the main is kept
        assert {"criterion_id": cid, "field": "where_evidence"} not in info["taken"]

    def test_s1a_1_1_merge_has_no_where_rules(self):
        a, jd, crits = job([], 5, EN_SET)
        cid = crits[0].criterion_id
        main = free(cid, EN_SET, basis="total_experience", where=[EN_SET_W])
        rep = free(cid, EN_SET, [(EN_SET_W, "function")], basis="targets")
        val = pa.PassAValidation(False, ["b"], {}, [pa.ScopedError(cid, ("target_basis", "restrictions"), "b")])
        _, info, viol = pr.merge_pass_a(resp(main), resp(rep), val, crits, jd=JDText(jd), contract=C11)
        assert viol == [] and "where_guard_violations" not in info


# ── 3. preserved for reconciliation, never a Pass B input ───────────────────

class TestPreservation:
    def _frozen(self):
        a, jd, crits = job([], 5, EN_SET)
        _, res = pass_a_job(a, jd, resp(free(crits[0].criterion_id, EN_SET, basis="setting_only",
                                                where=[EN_SET_W])))
        return a, jd, res

    def test_on_the_frozen_s1a_result(self):
        _, jd, res = self._frozen()
        (f,) = res.frozen
        (w,) = f.frame.where_evidence
        assert (w.line, w.text) == (2, EN_SET_W) and JDText(jd).line(2)[w.start:w.end] == EN_SET_W
        assert f.artefact.settings == () and f.artefact.policy == "sector"

    def test_never_in_the_pass_b_input(self):
        a, jd, res = self._frozen()
        (f,) = res.frozen
        bare = pb.TargetFrame(f.frame.criterion_id, f.frame.target_basis, f.frame.targets,
                              f.frame.requirement_spans, f.frame.duration_span)
        assert f.frame.to_payload() == bare.to_payload() and "where_evidence" not in f.frame.to_payload()
        assert pb.build_pass_b_request(JDText(jd), [f.frame]).input_hash == (
            pb.build_pass_b_request(JDText(jd), [bare]).input_hash)

    def test_assembly_is_unchanged_by_the_evidence(self):
        # the frozen frame carries the evidence into assemble_v4 (the reconciliation step), which does not use it
        # yet: same artefact as a frame without it
        a, jd, res = self._frozen()
        (f,) = res.frozen
        bare = asm.FrozenTarget(f.criterion, f.artefact, pb.TargetFrame(
            f.frame.criterion_id, f.frame.target_basis, f.frame.targets, f.frame.requirement_spans,
            f.frame.duration_span))
        a1 = asm.assemble_v4(f, None, "skipped", run={})
        a2 = asm.assemble_v4(bare, None, "skipped", run={})
        assert a1.to_dict() == a2.to_dict()


# ── 4. s1a-1.1 reproducible under its own contract ──────────────────────────

class TestS1a11Contract:
    def test_unanchored_setting_only_still_valid_under_s1a_1_1(self):
        a, jd, crits = job([], 5, EN_SET)
        raw = resp(free(crits[0].criterion_id, EN_SET, basis="setting_only"))
        assert validate(a, jd, raw, C11).ok and not validate(a, jd, raw).ok

    def test_s1a_1_1_ignores_the_field_entirely(self):
        a, jd, crits = job([], 5, EN_SET)
        raw = resp(free(crits[0].criterion_id, EN_SET, basis="total_experience", where=["junk"]))
        v = validate(a, jd, raw, C11)
        assert v.ok and v.results[crits[0].criterion_id].where_evidence == ()

    def test_s1a_1_1_run_uses_its_prompt_and_message(self):
        a, jd, crits = job([], 5, EN_SET)
        cid = crits[0].criterion_id
        client, res = pass_a_job(a, jd, resp(free(cid, EN_SET, [(EN_SET_W, "vague")], basis="setting_only")),
                                 resp(free(cid, EN_SET, [(EN_SET_W, "vague")], basis="unspecified")),
                                 prompt_version="s1a-1.1")
        assert client.requests[0]["messages"][0]["content"] == prompt_a.load_pass_a_prompt("s1a-1.1")
        assert pa.BASIS_REREAD_MESSAGE in note(client) and pr.BASIS_AGREE in note(client)
        assert res.outcome.meta["prompt_version"] == "s1a-1.1" and res.outcome.meta["outcome"] == "f6_rejected"

    def test_unknown_version_has_no_contract(self):
        for v in ("s1a-1.0", "s1a-1.2", "s1a-9.9"):
            with pytest.raises(ValueError):
                pa.pass_a_contract(v)


# ── 5. the S1-A-1.3 review paths under the new contract ─────────────────────

TB = {c["id"]: c for c in json.loads(ev.FIXTURES["target_basis"].read_text(encoding="utf-8"))["cases"]}


def _fixture_answer(case_id, restrictions, basis, where=None, amb=()):
    c = TB[case_id]
    durs = {m.text: did for did, _, m in JDText(ctx.case_jd(c)).durations()}
    it = {"criterion_id": ctx.case_criteria(c)[0].criterion_id, "requirement_spans": c["oracle"]["requirement_spans"],
          "restrictions": [{"line": 2, "text": t, "kind": k} for t, k in restrictions], "target_basis": basis,
          "duration": durs[c["oracle"]["duration"]], "ambiguity": list(amb), "note": "n"}
    if where is not None:
        it["where_evidence"] = [{"line": 2, "text": w} for w in where]
    return json.dumps({"criteria": [it]}, ensure_ascii=False)


S = "the hospitality sector"
B = "شركات المقاولات"
PATHS = [
    # id, case, main, repair, expected (outcome, basis, unsafe)
    ("A0", "BE07", ([], "setting_only", [S]), None, ("ok", "setting_only", False)),
    ("A", "BE07", ([], "setting_only"), ([], "setting_only", [S]), ("ok", "setting_only", False)),
    ("B", "BE07", ([], "total_experience"), None, ("ok", "total_experience", True)),        # residual
    ("C", "BE07", ([(S, "vague")], "unspecified"), None, ("ok", "unspecified", True)),      # residual
    ("D", "BE07", ([(S, "function")], "targets"), None, ("ok", "targets", False)),           # residual (narrowing)
    ("E", "BE07", ([(S, "vague")], "setting_only", [S]), ([], "setting_only", [S]), ("validation", None, False)),
    ("G", "BE07", ([(S, "function")], "setting_only"), ([], "setting_only", [S]), ("validation", None, False)),
    ("H", "BE07", ([(S, "function")], "setting_only"), ([(S, "function")], "targets", []), ("ok", "targets", False)),
    ("H*", "BE07", ([], "total_experience", [S]), ([(S, "function")], "targets", []), ("validation", None, False)),
    ("I", "BE07", ([], "targets"), ([], "setting_only", [S]), ("validation", None, False)),
    ("J", "BA16", ([(B, "function")], "setting_only"), ([], "setting_only", [B]), ("validation", None, False)),
    ("J*", "BA16", ([], "setting_only", [B]), None, ("ok", "setting_only", False)),
    ("L*", "BA16", ([(B, "function")], "setting_only", [B]), ([(B, "function")], "targets", []),
     ("validation", None, False)),
    ("N", "BA22", ([], "total_experience"), None, ("ok", "total_experience", True)),         # residual
    ("P", "BA22", ([("إدارية", "function")], "targets"), None, ("ok", "targets", False)),
    ("Q", "BE05", ([], "total_experience"), None, ("ok", "total_experience", False)),
    ("S", "BE05", ([("relevant", "vague")], "unspecified", None, ["ambiguous_relevance"]), None,
     ("ok", "unspecified", False)),
    ("U", "BA04", ([], "setting_only"), ([], "total_experience", []), ("validation", None, False)),
]


class TestReviewPaths:
    @pytest.mark.parametrize("pid,case,main,rep,expected", PATHS, ids=[p[0] for p in PATHS])
    def test_path(self, pid, case, main, rep, expected):
        raws = [_fixture_answer(case, *main)] + ([_fixture_answer(case, *rep)] if rep else [])
        obs = run(ev.run_case(TB[case], ctx.ScriptedClient(*raws)))
        (o,) = obs["criteria"]
        (c,) = ev.score(TB[case], obs)["checks"]
        assert (o["outcome"], o["basis"], c["unsafe"]) == expected, (obs["outcome_detail"], obs["errors"])
        if pid in ("H*", "L*"):
            assert obs["outcome_detail"] == "where_guard_rejected"


# ── 6. reporting-only harness extensions ────────────────────────────────────

def _rec(case, run_no, lang, family, criteria, golds, *, job="ok", repair=False, errors=None, detail=None):
    obs = {"job_outcome": job, "reason": None, "criteria": criteria, "calls": [{"call": "main"}], "raw": [],
           "repair_used": repair, "outcome_detail": detail, "errors": errors or {"errors": [], "repair_errors": []}}
    checks = [ev.check(g, o) for g, o in zip(golds, criteria)]
    return {"case": case, "run": run_no, "lang": lang, "family": family, **obs, "checks": checks,
            "gold": [{"targets": g["targets"], "basis": g["basis"], "policy": g["policy"]} for g in golds]}


def O(basis, policy, *targets, where=()):
    return {"criterion_id": "c", "outcome": "ok", "basis": basis, "policy": policy, "status": "x", "reasons": [],
            "targets": [{"text": t, "type": k} for t, k in targets], "where_evidence": list(where)}


FAILED = {"criterion_id": "c", "outcome": "validation", "basis": None, "policy": None, "status": "failed_validation",
          "reasons": [], "targets": None, "where_evidence": None}
GS = {"targets": [], "basis": "setting_only", "policy": "sector"}
GT = {"targets": [], "basis": "total_experience", "policy": "pure_duration"}
GF = {"targets": [("credit analysis", "function")], "basis": "targets", "policy": "functional"}


class TestReporting:
    def _summary(self):
        recs = [
            _rec("S1", 1, "en", "setting_only", [O("setting_only", "sector", where=["x"])], [GS]),
            _rec("S1", 2, "en", "setting_only", [O("total_experience", "pure_duration")], [GS]),
            _rec("S2", 1, "ar", "setting_only", [O("targets", "functional", ("x", "function"))], [GS], repair=True,
                 errors={"errors": ["criterion c: where_evidence ..."], "repair_errors": []}),
            _rec("S2", 2, "ar", "setting_only", [FAILED], [GS], job="failed", repair=True, detail="where_guard_rejected",
                 errors={"errors": [], "repair_errors": ["criterion c: " + pr.WHERE_GUARD_ERROR]}),
            _rec("T1", 1, "en", "total", [O("setting_only", "sector", where=["y"])], [GT]),
            _rec("T1", 2, "en", "total", [O("setting_only", "sector", where=["y"])], [GT]),
            _rec("F1", 1, "ar", "fn", [O("unspecified", "pure_duration")], [GF]),
            _rec("F1", 2, "ar", "fn", [O("targets", "functional", ("credit analysis", "function"))], [GF]),
        ]
        return ev.summarize(recs)

    def test_confusion_and_slices(self):
        r = self._summary()["s1a13"]
        assert r["basis_confusion"] == {"setting_only": {"failed_validation": 1, "setting_only": 1, "targets": 1,
                                                         "total_experience": 1},
                                        "targets": {"targets": 1, "unspecified": 1},
                                        "total_experience": {"setting_only": 2}}
        assert r["by_gold_basis"]["setting_only"]["target_basis_accuracy"] == round(1 / 3, 4)
        assert r["by_gold_basis"]["setting_only"]["failures"] == 1
        assert r["by_language"]["en"]["unsafe"] == 1 and r["by_language"]["ar"]["unsafe"] == 1
        assert set(r["by_group"]) == {"setting_only", "total", "fn"}

    def test_where_evidence_conversions_unsafe_and_repairs(self):
        s = self._summary()
        r = s["s1a13"]
        w = r["where_evidence"]
        assert (w["setting_only_gold_ok_runs"], w["with_evidence"]) == (3, 1)
        assert [(x["case"], x["run"]) for x in w["missing"]] == [("S1", 2), ("S2", 1)]
        assert [(x["case"], x["run"]) for x in w["unexpected"]] == [("T1", 1), ("T1", 2)]
        assert [(x["case"], x["run"], x["main"], x["after_repair"]) for x in w["invalid_runs"]] == [
            ("S2", 1, True, False), ("S2", 2, False, True)]
        assert r["sector_to_function"]["count"] == 1 and r["sector_to_function"]["after_repair"] == 1
        assert r["unsafe_breakdown"] == {"policy_downgrades": 2, "target_losses": 1}
        assert s["hard"]["unsafe_target_policy_loss"] == 2           # the gate itself is unchanged
        assert r["repairs"] == {"jobs_repaired": 2, "repaired_ok": 1, "repaired_failed": 1,
                                "failure_details": {"where_guard_rejected": 1}}

    def test_stability_by_case(self):
        st = self._summary()["s1a13"]["stability_by_case"]
        assert st["T1"] == {"runs": 2, "distinct_signatures": 1, "stable": True}
        assert st["S1"]["stable"] is False and st["F1"]["distinct_signatures"] == 2

    def test_reporting_never_gates(self):
        s = self._summary()
        assert set(ev.evaluate_gates(s)["gates"]) == {"hard:unsafe_target_policy_loss", "hard:independence_failures",
                                                      "target_accuracy", "policy_accuracy", "target_basis_accuracy",
                                                      "stability", "failure_rate"}
        assert ev.THRESHOLDS == {"target_accuracy": 0.95, "policy_accuracy": 0.95, "target_basis_accuracy": 0.95,
                                 "stability": 0.95, "failure_rate": 0.05}
        md = ev.render_markdown({k: "x" for k in ("prompt_version", "prompt_fingerprint", "s1_version", "model",
                                                  "temperature", "mode", "runs", "fixture", "fixture_version",
                                                  "fixture_sha256")}, s, ev.evaluate_gates(s))
        assert "## S1-A-1.3 reporting (not a gate)" in md and "basis confusion" in md


# ── 7. prompt s1a-1.3 ───────────────────────────────────────────────────────

class TestPromptS1a13:
    def test_pinned(self):
        assert prompt_a.PROMPT_SHA256["s1a-1.3"] == (
            "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d")
        assert pa.pass_a_contract() is C13 and C13.where_evidence

    def test_only_the_conflicting_rules_changed(self):
        p11 = prompt_a.load_pass_a_prompt("s1a-1.1")

        def sec(p, a, b):
            return p[p.index(a):p.index(b)]
        assert sec(PROMPT, "1 REQUIREMENT_SPANS", "4 RESTRICTIONS") == sec(p11, "1 REQUIREMENT_SPANS", "4 RESTRICTIONS")
        assert sec(PROMPT, "6 DURATION", "7 AMBIGUITY") == sec(p11, "6 DURATION", "7 AMBIGUITY")
        assert PROMPT[PROMPT.index("SECURITY RULES"):] == p11[p11.index("SECURITY RULES"):]

    @pytest.mark.parametrize("rule", [
        "Decide WHAT first; look at WHERE only when the statement names no role and no work.",
        "X is WHERE when it only says in which place, sector or industry, or for which kind of employer or client",
        "If you cannot tell whether X names work or only where, return X as a function and report "
        "\"ambiguous_relevance\"; never drop it.",
        "Return [] ONLY when the requirement statement names no role and no work and has no vague word.",
        "limits neither which work counts nor where or for whom it was gained",
        "A working atmosphere (e.g. \"experience under tight deadlines\") is not a where limit.",
        "ARABIC \"خبرة\" WITH AN ADJECTIVE",
        "When it only describes the experience itself",
        "A where or for whom limit never changes this",
    ])
    def test_rules_present(self, rule):
        assert rule in PROMPT

    @pytest.mark.parametrize("gone", [
        "Such a statement is never restrictions [].",
        "Decide the basis from the role and work words ONLY.",
        "Do not quote or describe that limit.",
        "names WHAT position or work counts",
    ])
    def test_contradictions_removed(self, gone):
        assert gone not in PROMPT and gone in prompt_a.load_pass_a_prompt("s1a-1.1")

    def test_examples_are_not_fixture_wording(self):
        new = PROMPT[PROMPT.index("4 RESTRICTIONS"):PROMPT.index("6 DURATION")].lower()
        new += PROMPT[PROMPT.index("OUTPUT:"):PROMPT.index("SECURITY RULES")].lower()
        for name in ("s1_ctx_main_cases.json", "s1_boundary_cases.json", "s1a_target_basis_cases_v2.json"):
            data = json.loads((SCRIPTS / "s1_eval_fixtures" / name).read_text(encoding="utf-8"))
            for case in data["cases"]:
                for line in case["jd_lines"]:
                    t = line.strip("- .").lower()
                    assert len(t) < 15 or t not in new, (name, case["id"], line)
        for phrase in ("pharmaceutical", "قطاع الاتصالات", "similar experience", "ذات علاقة", "tight deadlines",
                       "freight forwarding", "shipping companies", "خبرة تسويقية"):
            for name in ("s1_ctx_main_cases.json", "s1a_target_basis_cases_v2.json"):
                assert phrase not in (SCRIPTS / "s1_eval_fixtures" / name).read_text(encoding="utf-8").lower()
