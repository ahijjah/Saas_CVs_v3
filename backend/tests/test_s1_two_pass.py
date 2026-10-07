"""
S1 two-pass foundation (s1_requirement_spec_v4), Step 1: Pass A / Pass B wire schemas and strict validators,
deterministic assembly, fail-closed rules F1-F6, structural conflicts, fail-closed S2 views and the independence
of both passes from the qualifying-context analysis. Offline only: scripted answers, no client, no model call,
no database. All JDs SYNTHETIC.
"""
import ast
import copy
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from services.s1_requirements import schema as v3
from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_two_pass import assemble as asm
from services.s1_two_pass import pass_a as pa
from services.s1_two_pass import pass_b as pb
from services.s1_two_pass import schema as sc
from services.qualifying_context.agreement import resolve_context
from services.qualifying_context.runner import jd_sha256

BACKEND = Path(__file__).resolve().parent.parent
PKG = BACKEND / "services" / "s1_two_pass"

AUD = "Internal Auditor"
L_ROLE = "Minimum 5 years as an Internal Auditor in Islamic banking institutions in the GCC region"
L_FUNC = "Minimum 4 years of internal audit experience in government entities"
L_TOTAL = "Minimum 4 years of professional experience"
L_SETTING = "Minimum 4 years of experience in government entities"
L_TWO = "Minimum 6 years of project controls experience on infrastructure projects"
L_TWO_SEP = "Experience with government entities is required for this experience"
L_ALT = "Minimum 4 years as an Accountant in a Big Four firm or as an Internal Auditor"
L_SOFT = "Minimum 4 years as an Internal Auditor, preferably within the energy sector"
L_COMPOUND = "5 years of experience, including 2 years in the GCC"
L_GAP = "Minimum 4 years of project management experience"
L_VAGUE = "Minimum 4 years of relevant experience"


# ── scripted builders ────────────────────────────────────────────────────────

def job(hints, years, lines):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "\n".join(lines)
    return a, jd, enumerate_experience_criteria("J1", a)


def hinted(cid, spans, *, hints=1, basis="targets", duration="D1", ambiguity=(), **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "targets": [{"hint": f"T{i}", "type": "role", "match": "exact", "jd_span": None}
                      for i in range(1, hints + 1)],
          "target_basis": basis, "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def free(cid, spans, restrictions, *, basis, duration="D1", ambiguity=(), **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "restrictions": [{"line": ln, "text": t, "kind": k} for ln, t, k in restrictions],
          "target_basis": basis, "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def ctx(line, text, scope="all", applies_to=("T1",)):
    return {"line": line, "text": text, "scope": scope, "applies_to": list(applies_to)}


def ctx_item(cid, contexts=(), *, spans=(), gap=(), **over):
    it = {"criterion_id": cid, "contexts": list(contexts),
          "context_spans": [{"line": ln, "text": t} for ln, t in spans],
          "target_gap": [{"line": ln, "text": t} for ln, t in gap]}
    it.update(over)
    return it


def resp(*items):
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


def two_pass(a, jd, raw_a, raw_b=None, **kw):
    return asm.run_scripted_job("J1", jd, a, raw_a, raw_b, **kw)


def only(result):
    (art,) = result.artifacts
    return art


def qc_analysis(a, jd, state, contexts=(), *, source="analysis", provenance=None, jd_hash="current"):
    """analysis_json with a stored qualifying context (hash of the CURRENT JD unless told otherwise)."""
    out = copy.deepcopy(a)
    out["experience"]["qualifying_context"] = {"state": state, "contexts": list(contexts), "source": source}
    cur = {"source": source}
    if jd_hash is not None:
        cur["jd_sha256"] = jd_sha256(jd) if jd_hash == "current" else jd_hash
    if provenance:
        cur["provenance"] = provenance
    out["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": cur, "latest_run": {"status": "ok"}}
    return out


def resolve(art, analysis, jd):
    return asm.with_resolution(art, resolve_context(asm.s1_reading(art), analysis, jd))


# common jobs
def role_job():
    a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
    return a, jd, crits[0].criterion_id


def role_art(contexts=None, **kw):
    a, jd, cid = role_job()
    if contexts is None:
        contexts = [ctx(2, "Islamic banking institutions in the GCC region")]
    return a, jd, only(two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])), resp(ctx_item(cid, contexts)), **kw))


# ── A. schema v4 ─────────────────────────────────────────────────────────────

def _span(line, start, text):
    return v3.Span(line, start, start + len(text), text)


class TestSchemaV4:
    def test_versions_and_vocabulary(self):
        assert (sc.S1V4_SCHEMA, sc.S1V4_VERSION) == ("s1_requirement_spec_v4", "2.0.0")
        assert (sc.S1A_INPUT_VERSION, sc.S1B_INPUT_VERSION) == ("s1a-in-1", "s1b-in-1")
        assert (sc.S1A_PROMPT_VERSION, sc.S1B_PROMPT_VERSION, sc.PROMPT_PENDING) == ("s1a-1.1", "s1b-1.0", "pending")
        assert sc.TARGET_BASES == ("targets", "total_experience", "setting_only", "unspecified")
        assert sc.PASS_A_RESTRICTION_KINDS == ("role", "function", "vague")
        assert sc.CONTEXT_SCOPES == ("all", "one_alternative", "part_duration", "softened")
        assert sc.DEFAULT_ABSENT_POLICY == "strict"
        assert sc.V4_REASON_KINDS["target_unconfirmed"] == "business"
        assert sc.V4_REASON_KINDS["context_structure_conflict"] == "business"
        assert sc.V4_REASON_KINDS["ambiguous_context_scope"] == "ambiguity"
        assert sc.V4_REASON_KINDS["compound_requirement"] == "business"

    def test_v3_kept_for_audit_and_replay(self):
        # the v3 package is untouched: its versions and prompt are those of the last committed s1-6.1 state
        assert (v3.S1_SCHEMA, v3.S1_VERSION, v3.S1_PROMPT_VERSION, v3.S1_INPUT_VERSION) == (
            "s1_requirement_spec_v3", "1.5.1", "s1-6.1", "s1-in-1")

    def _art(self, **kw):
        base = dict(criterion_id="c", job_id="j", source_path="p", display_text="d", requirement_text="r",
                    required=True, spec_status="resolved", target_state="target_fixed", context_pass="ok",
                    policy="explicit_role", target_basis="targets")
        base.update(kw)
        return sc.S1ArtifactV4(**base)

    def test_invariants(self):
        gcc = _span(2, 10, "GCC region")
        cand = sc.ContextCandidate("GCC region", gcc, "all", ("T1",))
        soft = sc.ContextCandidate("GCC region", gcc, "softened", ("T1",))
        setting = v3.Setting("GCC region", "jd_asserted", gcc)
        assert self._art(context_candidates=(cand,), settings=(setting,)).settings == (setting,)
        R = sc.ReasonV4
        bad = [
            dict(settings=(setting,)),                                              # setting without candidate
            dict(context_candidates=(soft,), settings=(setting,),
                 spec_status="needs_confirmation", reasons=(R("ambiguous_context_scope"),)),  # non-"all" setting
            dict(target_state="target_failed"),                                     # failed state, ok status
            dict(spec_status="failed_technical", reasons=(R("ai_unavailable"),), context_pass="skipped"),
            dict(spec_status="failed_technical", target_state="target_failed", reasons=(R("ai_unavailable"),),
                 context_pass="skipped"),                                           # failed with a policy
            dict(spec_status="failed_technical", target_state="target_failed", reasons=(R("ai_unavailable"),),
                 policy=None, target_basis=None),                                   # failed with a context pass
            dict(target_state="target_absent_claimed", target_basis="total_experience", policy="pure_duration"),
            dict(target_state="target_unspecified", target_basis="unspecified", policy="pure_duration"),
            dict(reasons=(R("target_unconfirmed"),)),                               # resolved with a reason
            dict(spec_status="needs_confirmation"),                                 # needs a reason
            dict(spec_status="needs_confirmation", reasons=(R("validation_failed"),)),
            dict(target_basis=None), dict(policy=None), dict(target_state="maybe"), dict(context_pass="maybe"),
            dict(target_basis="nothing"),
            dict(context_candidates=tuple(sc.ContextCandidate(f"x{i}", _span(1, i * 5, f"x{i}"), "all", ())
                                          for i in range(6))),
        ]
        for kw in bad:
            with pytest.raises(ValueError):
                self._art(**kw)
        with pytest.raises(ValueError):
            sc.ContextCandidate("x", gcc, "everywhere", ())
        with pytest.raises(ValueError):
            sc.ReasonV4("not_a_code")
        failed = self._art(spec_status="failed_technical", target_state="target_failed", policy=None,
                           target_basis=None, context_pass="skipped", reasons=(R("ai_unavailable"),), retryable=True)
        assert failed.policy is None and failed.targets == ()

    def test_roundtrip_hash_and_schema_guard(self):
        a, jd, art = role_art()
        d = art.to_dict()
        assert d["_schema"] == "s1_requirement_spec_v4"
        assert sc.S1ArtifactV4.from_dict(d) == art
        assert art.spec_version.startswith("s1v4-2.0.0-")
        r = resolve(art, qc_analysis(a, jd, "identified", ["Islamic banking institutions in the GCC region"]), jd)
        assert r.context_resolution.detail == "agreed"
        assert sc.S1ArtifactV4.from_dict(r.to_dict()) == r
        assert r.spec_version != art.spec_version                     # the effective context is semantics
        rec = dataclasses.replace(r, context_resolution=dataclasses.replace(r.context_resolution, record={}))
        assert rec.spec_version == r.spec_version                     # the audit record is not
        for other in ("s1_requirement_spec_v3", "s1_requirement_spec_v2", None):
            with pytest.raises(ValueError, match="not an s1_requirement_spec_v4"):
                sc.S1ArtifactV4.from_dict({**d, "_schema": other})


# ── B. Pass A wire + validator ───────────────────────────────────────────────

def validate_a(jd, crits, raw):
    req = pa.build_pass_a_request(JDText(jd), crits)
    return pa.validate_pass_a(raw, JDText(jd), crits, req.durations)


class TestPassA:
    def test_hinted_ok(self):
        a, jd, cid = role_job()
        v = validate_a(jd, enumerate_experience_criteria("J1", a), resp(hinted(cid, [(2, L_ROLE)])))
        assert v.ok, v.errors
        assert v.results[cid].target_basis == "targets" and v.results[cid].parsed.settings == ()

    @pytest.mark.parametrize("key,value", [("settings", [{"line": 2, "text": "GCC region"}]),
                                           ("setting", "GCC region"), ("contexts", [{"line": 2, "text": "GCC"}]),
                                           ("context_spans", [{"line": 2, "text": "GCC region"}]),
                                           ("target_gap", [{"line": 2, "text": "GCC region"}])])
    def test_never_returns_contexts(self, key, value):
        a, jd, cid = role_job()
        v = validate_a(jd, enumerate_experience_criteria("J1", a), resp(hinted(cid, [(2, L_ROLE)], **{key: value})))
        assert not v.ok and v.results == {}
        assert any(f"remove the key {key!r}" in e for e in v.errors)

    def test_empty_forbidden_lists_are_tolerated(self):
        a, jd, cid = role_job()
        v = validate_a(jd, enumerate_experience_criteria("J1", a), resp(hinted(cid, [(2, L_ROLE)], settings=[])))
        assert v.ok, v.errors

    @pytest.mark.parametrize("kind", ["context", "sector", "setting"])
    def test_context_restriction_kinds_rejected(self, kind):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        raw = resp(free(crits[0].criterion_id, [(2, L_SETTING)], [(2, "government entities", kind)],
                        basis="setting_only"))
        v = validate_a(jd, crits, raw)
        assert not v.ok and any(f"kind {kind!r} is not allowed" in e for e in v.errors)

    @pytest.mark.parametrize("basis", [None, "", "pure_duration", "none"])
    def test_target_basis_required(self, basis):
        a, jd, cid = role_job()
        item = hinted(cid, [(2, L_ROLE)])
        if basis is None:
            del item["target_basis"]
        else:
            item["target_basis"] = basis
        v = validate_a(jd, enumerate_experience_criteria("J1", a), resp(item))
        assert not v.ok and any("target_basis is required" in e for e in v.errors)

    @pytest.mark.parametrize("line,restr,basis,ok", [
        (L_FUNC, [(2, "internal audit", "function")], "targets", True),
        (L_FUNC, [(2, "internal audit", "function")], "total_experience", False),
        (L_FUNC, [(2, "internal audit", "function")], "setting_only", False),
        (L_VAGUE, [(2, "relevant", "vague")], "unspecified", True),
        (L_VAGUE, [(2, "relevant", "vague")], "total_experience", False),
        (L_TOTAL, [], "total_experience", True),
        (L_SETTING, [], "setting_only", True),
        (L_TOTAL, [], "targets", False),            # F2: an empty list never implies a basis, in either direction
        (L_TOTAL, [], "unspecified", False),
    ])
    def test_basis_must_match_the_typed_restrictions(self, line, restr, basis, ok):
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        v = validate_a(jd, crits, resp(free(crits[0].criterion_id, [(2, line)], restr, basis=basis)))
        assert v.ok is ok, v.errors
        if not ok:
            # s1a-1.1: never a basis prescribed from the model's own restrictions
            assert any("and the restrictions disagree" in e and "Re-read the requirement statement" in e
                       for e in v.errors)
            assert not any("expected one of" in e for e in v.errors)

    def test_hinted_basis_is_targets_only(self):
        a, jd, cid = role_job()
        v = validate_a(jd, enumerate_experience_criteria("J1", a),
                       resp(hinted(cid, [(2, L_ROLE)], basis="total_experience")))
        assert not v.ok

    def test_setting_only_derives_sector(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        v = validate_a(jd, crits, resp(free(crits[0].criterion_id, [(2, L_SETTING)], [], basis="setting_only")))
        r = v.results[crits[0].criterion_id]
        assert r.parsed.policy == "sector" and r.parsed.relevance_basis == "sector"

    def test_job_is_all_or_nothing(self):
        a, jd, crits = job([AUD], 4, ["Requirements", "- " + L_ALT + ".", "- " + L_TOTAL + "."])
        assert len(crits) >= 1
        good = [hinted(c.criterion_id, [(2, L_ALT)]) for c in crits]
        good[-1]["target_basis"] = "nothing"
        v = validate_a(jd, crits, resp(*good))
        assert not v.ok and v.results == {}

    def test_input_kinds_match_v3(self):
        from services.s1_requirements.classifier import INPUT_KINDS
        assert pa.INPUT_KINDS == INPUT_KINDS

    def test_request_shape_and_cache_key(self):
        a, jd, cid = role_job()
        crits = enumerate_experience_criteria("J1", a)
        req = pa.build_pass_a_request(JDText(jd), crits)
        assert set(req.payload) == {"s1a_input_version", "jd_lines", "duration_candidates", "criteria"}
        assert set(req.payload["criteria"][0]) == {"criterion_id", "kind", "has_years", "target_hints"}
        assert req.user_message.startswith("INPUT:\n")
        k = pa.pass_a_cache_key(req)
        assert k == pa.pass_a_cache_key(pa.build_pass_a_request(JDText(jd), crits))
        assert k != pa.pass_a_cache_key(req, prompt_fingerprint="x") != pa.pass_a_cache_key(req, model="m")
        assert k != pb.pass_b_cache_key(pb.build_pass_b_request(JDText(jd), []))


# ── C. Pass B wire + validator ───────────────────────────────────────────────

def frames_for(a, jd, raw_a):
    crits = enumerate_experience_criteria("J1", a)
    j = JDText(jd)
    req = pa.build_pass_a_request(j, crits)
    va = pa.validate_pass_a(raw_a, j, crits, req.durations)
    assert va.ok, va.errors
    return [asm.freeze_targets(c, va.results[c.criterion_id], j, req.durations, run={}, recruiter_fields={}).frame
            for c in crits]


def validate_b(a, jd, raw_a, raw_b):
    fr = frames_for(a, jd, raw_a)
    return pb.validate_pass_b(raw_b, JDText(jd), fr)


class TestPassB:
    def _role(self):
        a, jd, cid = role_job()
        return a, jd, cid, resp(hinted(cid, [(2, L_ROLE)]))

    def test_ok(self):
        a, jd, cid, ra = self._role()
        v = validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(2, "Islamic banking institutions in the GCC region")])))
        assert v.ok, v.errors
        (c,) = v.results[cid].contexts
        assert (c.scope, c.applies_to) == ("all", ("T1",)) and v.results[cid].target_gap == ()

    @pytest.mark.parametrize("key", ["targets", "restrictions", "target_basis", "policy", "settings", "setting"])
    def test_cannot_touch_targets(self, key):
        a, jd, cid, ra = self._role()
        v = validate_b(a, jd, ra, resp(ctx_item(cid, **{key: []})))
        assert not v.ok and any("targets are fixed input" in e for e in v.errors)

    @pytest.mark.parametrize("key", ["contexts", "context_spans", "target_gap"])
    def test_required_lists(self, key):
        a, jd, cid, ra = self._role()
        item = ctx_item(cid)
        del item[key]
        v = validate_b(a, jd, ra, resp(item))
        assert not v.ok and any(f"{key} is required" in e for e in v.errors)

    @pytest.mark.parametrize("contexts,frag", [
        ([ctx(2, "GCC regions")], "not verbatim"),
        ([ctx(1, "Requirements")], "not inside this criterion's requirement statement"),
        ([ctx(2, "Internal Auditor in Islamic")], "overlaps a target"),
        ([ctx(2, "5 years as")], "must not include the duration"),
        ([ctx(2, "GCC region"), ctx(2, "the GCC region")], "overlaps or repeats another context"),
        ([ctx(2, "GCC region", applies_to=("T9",))], "unknown target ids"),
        ([ctx(2, "GCC region", applies_to=("T1", "T1"))], "repeats a target id"),
        ([ctx(2, "GCC region", applies_to=())], "applies to every target"),
        ([ctx(2, "GCC region", scope="one_alternative")], "proper subset"),
        ([ctx(2, "GCC region", scope="softened", applies_to=())], "must name the targets"),
        ([{"line": 2, "text": "GCC region", "applies_to": ["T1"]}], "scope is required"),
        ([{"line": 2, "text": "GCC region", "scope": "all"}], "applies_to is required"),
        ([{"line": 2, "text": "GCC region", "scope": "nearby", "applies_to": ["T1"]}], "scope is required"),
        ([{"line": "2", "text": "GCC region", "scope": "all", "applies_to": ["T1"]}], "must be an object"),
    ])
    def test_context_rules(self, contexts, frag):
        a, jd, cid, ra = self._role()
        v = validate_b(a, jd, ra, resp(ctx_item(cid, contexts)))
        assert not v.ok and any(frag in e for e in v.errors), v.errors

    def test_at_most_five(self):
        line = "Minimum 5 years as an Internal Auditor in aa, bb, cc, dd, ee, ff"
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        v = validate_b(a, jd, resp(hinted(cid, [(2, line)])),
                       resp(ctx_item(cid, [ctx(2, w) for w in ("aa", "bb", "cc", "dd", "ee", "ff")])))
        assert not v.ok and any("at most 5" in e for e in v.errors)

    def test_no_targets_means_empty_applies_to(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        cid = crits[0].criterion_id
        ra = resp(free(cid, [(2, L_SETTING)], [], basis="setting_only"))
        assert validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(2, "government entities", applies_to=())]))).ok
        v = validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(2, "government entities")])))
        assert not v.ok and any("unknown target ids" in e or "must be []" in e for e in v.errors)

    def test_context_span_window_and_anchor(self):
        lines = ["Requirements", "- " + L_TWO + ".", "- Bachelor's degree in Engineering.", "- " + L_TWO_SEP + "."]
        a, jd, crits = job([], 6, lines)
        cid = crits[0].criterion_id
        ra = resp(free(cid, [(2, L_TWO)], [(2, "project controls", "function")], basis="targets"))
        ok = ctx_item(cid, [ctx(2, "infrastructure projects", applies_to=("J1",)),
                            ctx(4, "government entities", applies_to=("J1",))], spans=[(4, L_TWO_SEP)])
        v = validate_b(a, jd, ra, resp(ok))
        assert v.ok, v.errors
        assert [s.line for s in v.results[cid].context_spans] == [4]
        # a context sentence that carries no context of this criterion
        v = validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(2, "infrastructure projects", applies_to=("J1",))],
                                                spans=[(4, L_TWO_SEP)])))
        assert not v.ok and any("must hold a context" in e for e in v.errors)
        # a context on a line that is not a declared context span
        v = validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(4, "government entities", applies_to=("J1",))])))
        assert not v.ok
        # outside the window / before the requirement
        far = lines[:3] + ["- x.", "- y.", "- z.", "- " + L_TWO_SEP + "."]
        a2, jd2, crits2 = job([], 6, far)
        ra2 = resp(free(crits2[0].criterion_id, [(2, L_TWO)], [(2, "project controls", "function")], basis="targets"))
        v = validate_b(a2, jd2, ra2, resp(ctx_item(crits2[0].criterion_id,
                                                   [ctx(7, "government entities", applies_to=("J1",))],
                                                   spans=[(7, L_TWO_SEP)])))
        assert not v.ok and any("not within 3 lines after" in e for e in v.errors)
        before = ["Requirements", "- " + L_TWO_SEP + ".", "- " + L_TWO + "."]
        a3, jd3, crits3 = job([], 6, before)
        ra3 = resp(free(crits3[0].criterion_id, [(3, L_TWO)], [(3, "project controls", "function")], basis="targets"))
        v = validate_b(a3, jd3, ra3, resp(ctx_item(crits3[0].criterion_id,
                                                   [ctx(2, "government entities", applies_to=("J1",))],
                                                   spans=[(2, L_TWO_SEP)])))
        assert not v.ok

    @pytest.mark.parametrize("gap,frag", [
        ([(2, "Internal Auditor")], "already a target"),
        ([(2, "GCC region")], "is a context, not a missing target"),
        ([(2, "5 years")], "must not include the duration"),
        ([(1, "Requirements")], "not inside"),
        ([(2, "auditors")], "not verbatim"),
    ])
    def test_target_gap_rules(self, gap, frag):
        a, jd, cid, ra = self._role()
        v = validate_b(a, jd, ra, resp(ctx_item(cid, [ctx(2, "GCC region")], gap=gap)))
        assert not v.ok and any(frag in e for e in v.errors), v.errors

    @pytest.mark.parametrize("excluded,ok", [([], True), (None, True),
                                             ([{"text": "dynamic", "why": "generic_environment"}], True),
                                             ([{"text": "dynamic", "why": "because"}], False),
                                             ([{"why": "other"}], False), ("dynamic", False)])
    def test_excluded_shape(self, excluded, ok):
        a, jd, cid, ra = self._role()
        v = validate_b(a, jd, ra, resp(ctx_item(cid, excluded=excluded)))
        assert v.ok is ok

    def test_one_result_per_criterion(self):
        a, jd, cid, ra = self._role()
        assert not validate_b(a, jd, ra, resp()).ok
        assert not validate_b(a, jd, ra, resp(ctx_item(cid), ctx_item(cid))).ok
        assert not validate_b(a, jd, ra, resp(ctx_item(cid), ctx_item("other"))).ok
        assert not validate_b(a, jd, ra, "not json").ok
        assert not validate_b(a, jd, ra, json.dumps([])).ok

    def test_request_carries_only_frozen_frames(self):
        a, jd, cid, ra = self._role()
        fr = frames_for(a, jd, ra)
        req = pb.build_pass_b_request(JDText(jd), fr)
        assert set(req.payload) == {"s1b_input_version", "jd_lines", "criteria"}
        assert set(req.payload["criteria"][0]) == {"criterion_id", "target_basis", "targets", "requirement_spans",
                                                   "duration"}
        assert req.payload["criteria"][0]["targets"][0]["id"] == "T1"


# ── D. assembly, F1-F6, structural conflicts ────────────────────────────────

class TestFailClosed:
    def test_f1_technical_failure_is_never_no_target(self):
        a, jd, cid = role_job()
        r = two_pass(a, jd, None, pass_a_failure="ai_unavailable")
        art = only(r)
        assert (art.spec_status, art.target_state, art.context_pass) == ("failed_technical", "target_failed",
                                                                         "skipped")
        assert art.policy is None and art.targets == () and art.settings == () and art.context_candidates == ()
        assert r.pass_b == {"status": "skipped"}
        assert asm.s1_reading(art)["status"] == "unavailable"
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(art, require_resolved=False)
        assert e.value.code == "failed"

    @pytest.mark.parametrize("raw", ["not json", resp(), resp({"criterion_id": "x"})])
    def test_f1_invalid_pass_a_fails_every_criterion(self, raw):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + ".", "- " + L_TOTAL + "."])
        r = two_pass(a, jd, raw, resp())
        assert r.artifacts and all(x.spec_status == "failed_validation" and x.target_state == "target_failed"
                                   for x in r.artifacts)
        assert r.pass_a["status"] == "validation_failed" and r.pass_a["errors"]

    def test_f1_failed_target_even_with_a_recruiter_context(self):
        a, jd, cid = role_job()
        art = only(two_pass(a, jd, None, pass_a_failure="ai_unavailable"))
        r = resolve(art, qc_analysis(a, jd, "identified", ["GCC region"], source="recruiter",
                                     provenance="recruiter_edited"), jd)
        assert r.context_resolution.status == "resolved"
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(r, require_resolved=False)
        assert e.value.code == "failed"

    def test_f2_empty_list_never_means_pure_duration(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_GAP + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_GAP)], [], basis="targets")), resp(ctx_item(cid))))
        assert art.spec_status == "failed_validation" and art.policy is None

    def _total(self, policy="strict", contexts=(), gap=(), line=L_TOTAL, recruiter=None):
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        return a, jd, only(two_pass(a, jd, resp(free(cid, [(2, line)], [], basis="total_experience")),
                                    resp(ctx_item(cid, contexts, gap=gap)), absent_policy=policy,
                                    recruiter_fields=recruiter))

    def test_f5_strict_default_never_auto_resolves_a_no_target_reading(self):
        a, jd, art = self._total()
        assert (art.spec_status, art.target_state, art.policy) == ("needs_confirmation", "target_absent_claimed",
                                                                   "pure_duration")
        assert [(x.code, x.field) for x in art.reasons] == [("target_unconfirmed", "target_basis")]
        r = resolve(art, qc_analysis(a, jd, "none"), jd)
        assert r.context_resolution.detail == "agreed_none"
        for rr in (True, False):
            with pytest.raises(asm.S1V4ViewError) as e:
                asm.s2_views_v4(r, require_resolved=rr)
            assert e.value.code == "target_unconfirmed"

    def test_f3_corroborated_total_experience(self):
        a, jd, art = self._total("corroborated")
        assert (art.spec_status, art.target_state) == ("resolved", "target_absent_corroborated")
        r = resolve(art, qc_analysis(a, jd, "none"), jd)
        (view,) = asm.s2_views_v4(r)
        assert view.policy == "pure_duration" and view.setting is None and view.targets == ()

    def test_f3_gap_defeats_corroboration(self):
        a, jd, art = self._total("corroborated", gap=[(2, "project management")], line=L_GAP)
        assert art.target_state == "target_absent_claimed" and art.spec_status == "needs_confirmation"
        assert {x.code for x in art.reasons} == {"target_unconfirmed"}
        assert art.audit["context_pass"]["target_gap"][0]["text"] == "project management"
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(art, require_resolved=False)
        assert e.value.code == "target_unconfirmed"

    def test_f3_total_experience_with_a_context_is_a_conflict(self):
        line = "Minimum 4 years of professional experience in government entities"
        a, jd, art = self._total("corroborated", contexts=[ctx(2, "government entities", applies_to=())], line=line)
        assert art.target_state == "target_absent_claimed"
        assert {x.code for x in art.reasons} == {"context_structure_conflict", "target_unconfirmed"}

    def test_f3_corroborated_setting_only(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_SETTING)], [], basis="setting_only")),
                            resp(ctx_item(cid, [ctx(2, "government entities", applies_to=())])),
                            absent_policy="corroborated"))
        assert (art.spec_status, art.target_state, art.policy) == ("resolved", "target_absent_corroborated", "sector")
        r = resolve(art, qc_analysis(a, jd, "identified", ["government entities"]), jd)
        (view,) = asm.s2_views_v4(r)
        assert view.policy == "sector" and view.targets == ("government entities",) and view.setting is None

    def test_f3_setting_only_without_an_all_context_is_a_conflict(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        cid = crits[0].criterion_id
        for b in (ctx_item(cid), ctx_item(cid, [ctx(2, "government entities", "softened", ())])):
            art = only(two_pass(a, jd, resp(free(cid, [(2, L_SETTING)], [], basis="setting_only")), resp(b),
                                absent_policy="corroborated"))
            assert "context_structure_conflict" in {x.code for x in art.reasons}
            assert art.target_state == "target_absent_claimed" and art.spec_status == "needs_confirmation"

    @pytest.mark.parametrize("prov", ["recruiter_confirmed", "recruiter_edited"])
    def test_f4_recruiter_target_basis(self, prov):
        a, jd, art = self._total(recruiter={"experience.target_basis": prov})
        assert (art.spec_status, art.target_state) == ("resolved", "recruiter_set")
        assert art.field_provenance["target_basis"] == prov
        r = resolve(art, qc_analysis(a, jd, "none"), jd)
        assert asm.s2_views_v4(r)[0].policy == "pure_duration"

    def test_f4_recruiter_field_is_validated(self):
        with pytest.raises(ValueError):
            asm.split_recruiter_fields({"experience.target_basis": "jd_verified"})
        with pytest.raises(ValueError):
            asm.split_recruiter_fields({"experience.unknown": "recruiter_edited"})

    def test_f4_recruiter_targets_keep_the_gap_as_audit_only(self):
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_ROLE + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])),
                            resp(ctx_item(cid, [ctx(2, "GCC region")], gap=[(2, "Islamic banking")])),
                            recruiter_fields={"experience.relevant_roles": "recruiter_edited"}))
        assert "target_unconfirmed" not in {x.code for x in art.reasons}
        assert art.audit["target_gap_under_recruiter_targets"] == ["Islamic banking"]

    def test_unknown_absent_policy_rejected(self):
        a, jd, cid = role_job()
        with pytest.raises(ValueError):
            two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])), resp(ctx_item(cid)), absent_policy="lenient")

    def test_unspecified_target_never_gets_a_view(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_VAGUE + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_VAGUE)], [(2, "relevant", "vague")], basis="unspecified")),
                            resp(ctx_item(cid))))
        assert art.target_state == "target_unspecified" and art.spec_status == "needs_confirmation"
        r = resolve(art, qc_analysis(a, jd, "none"), jd)
        for rr in (True, False):
            with pytest.raises(asm.S1V4ViewError) as e:
                asm.s2_views_v4(r, require_resolved=rr)
            assert e.value.code == "target_unconfirmed"

    @pytest.mark.parametrize("failure", ["technical", "validation"])
    def test_pass_b_failure_keeps_targets_and_is_never_no_context(self, failure):
        a, jd, cid = role_job()
        kw = {"pass_b_failure": "ai_unavailable"} if failure == "technical" else {}
        raw_b = None if failure == "technical" else resp(ctx_item(cid, [ctx(2, "nowhere at all")]))
        r = two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])), raw_b, **kw)
        art = only(r)
        assert art.context_pass == f"failed_{failure}" and [t.text for t in art.targets] == [AUD]
        assert art.settings == () and art.context_candidates == ()
        assert asm.s1_reading(art)["status"] == "unavailable"
        rr = resolve(art, qc_analysis(a, jd, "none"), jd)
        assert (rr.context_resolution.status, rr.context_resolution.detail) == ("unconfirmed", "s1_unavailable")
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(rr, require_resolved=False)
        assert e.value.code == "context_unavailable"
        # even a recruiter context does not create a view without the context pass
        rec = resolve(art, qc_analysis(a, jd, "none", source="recruiter", provenance="recruiter_edited"), jd)
        with pytest.raises(asm.S1V4ViewError):
            asm.s2_views_v4(rec, require_resolved=False)

    def test_assemble_v4_status_consistency_guard(self):
        a, jd, cid = role_job()
        crits = enumerate_experience_criteria("J1", a)
        j = JDText(jd)
        req = pa.build_pass_a_request(j, crits)
        va = pa.validate_pass_a(resp(hinted(cid, [(2, L_ROLE)])), j, crits, req.durations)
        ft = asm.freeze_targets(crits[0], va.results[cid], j, req.durations, run={}, recruiter_fields={})
        with pytest.raises(ValueError):
            asm.assemble_v4(ft, None, "ok", run={})


class TestStructure:
    def test_all_scope_context_is_the_s1_setting(self):
        a, jd, art = role_art()
        assert art.spec_status == "resolved" and art.target_state == "target_fixed"
        assert [s.text for s in art.settings] == ["Islamic banking institutions in the GCC region"]
        assert art.field_provenance["settings"] == "jd_asserted"

    def test_one_alternative_is_ambiguous_scope_and_never_applied(self):
        a, jd, crits = job(["Accountant", AUD], 4, ["Requirements", "- " + L_ALT + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_ALT)], hints=2)),
                            resp(ctx_item(cid, [ctx(2, "Big Four firm", "one_alternative", ("T1",))]))))
        assert art.spec_status == "needs_confirmation" and art.settings == ()
        assert [x.code for x in art.reasons] == ["ambiguous_context_scope"]
        assert [t.text for t in art.targets] == ["Accountant", AUD]                 # both targets kept
        assert [c.scope for c in art.context_candidates] == ["one_alternative"]

    def test_softened_is_ambiguous_scope(self):
        a, jd, crits = job([AUD], 4, ["Requirements", "- " + L_SOFT + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_SOFT)])),
                            resp(ctx_item(cid, [ctx(2, "energy sector", "softened")]))))
        assert [x.code for x in art.reasons] == ["ambiguous_context_scope"] and art.settings == ()

    def test_part_duration_without_compound_is_a_conflict(self):
        a, jd, art = role_art([ctx(2, "GCC region", "part_duration")])
        assert [x.code for x in art.reasons] == ["context_structure_conflict"] and art.settings == ()

    def test_compound_part_duration_is_compound_only(self):
        a, jd, crits = job([], 5, ["Requirements", "- " + L_COMPOUND + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_COMPOUND)], [], basis="total_experience")),
                            resp(ctx_item(cid, [ctx(2, "GCC", "part_duration", ())]))))
        codes = {x.code for x in art.reasons}
        assert "compound_requirement" in codes and "context_structure_conflict" not in codes
        assert "ambiguous_context_scope" not in codes and art.settings == ()
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(art, require_resolved=False)
        assert e.value.code == "compound_requirement"

    def test_compound_with_an_all_context_blocks_and_keeps_audit(self):
        line = "Minimum 5 years of experience as an Internal Auditor, including 2 years in the GCC region"
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, line)])), resp(ctx_item(cid, [ctx(2, "GCC region")]))))
        assert "compound_requirement" in {x.code for x in art.reasons}
        assert art.settings == () and art.audit["compound_context"] == ["GCC region"]
        r = resolve(art, qc_analysis(a, jd, "identified", ["GCC region"], source="recruiter",
                                     provenance="recruiter_edited"), jd)
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(r, require_resolved=False)
        assert e.value.code == "compound_requirement"

    def test_target_gap_with_fixed_targets(self):
        a, jd, art = role_art([ctx(2, "GCC region")])
        a, jd, cid = role_job()
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])),
                            resp(ctx_item(cid, [ctx(2, "GCC region")], gap=[(2, "Islamic banking")]))))
        assert [(x.code, x.field) for x in art.reasons] == [("target_unconfirmed", "targets")]
        assert art.target_state == "target_fixed" and art.spec_status == "needs_confirmation"

    def test_separate_sentence_context_and_two_and_contexts(self):
        lines = ["Requirements", "- " + L_TWO + ".", "- Bachelor's degree in Engineering.", "- " + L_TWO_SEP + "."]
        a, jd, crits = job([], 6, lines)
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_TWO)], [(2, "project controls", "function")],
                                             basis="targets")),
                            resp(ctx_item(cid, [ctx(2, "infrastructure projects", applies_to=("J1",)),
                                                ctx(4, "government entities", applies_to=("J1",))],
                                          spans=[(4, L_TWO_SEP)]))))
        assert art.spec_status == "resolved"
        assert [s.text for s in art.settings] == ["infrastructure projects", "government entities"]
        assert [s.line for s in art.requirement_spans] == [2, 4] and "government entities" in art.requirement_text
        r = resolve(art, qc_analysis(a, jd, "identified", ["infrastructure projects", "government entities"]), jd)
        assert r.context_resolution.detail == "agreed"
        with pytest.raises(asm.S1V4ViewError) as e:                       # never flattened into one setting
            asm.s2_views_v4(r, require_resolved=False)
        assert e.value.code == "multi_context_unsupported"

    def test_excluded_is_audit_only(self):
        a, jd, cid = role_job()
        base = two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])), resp(ctx_item(cid, [ctx(2, "GCC region")])))
        exc = two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])),
                       resp(ctx_item(cid, [ctx(2, "GCC region")],
                                     excluded=[{"text": "Islamic banking", "why": "other"}], note="x")))
        assert only(base).content_hash == only(exc).content_hash
        assert only(exc).audit["context_pass"]["excluded"] == [{"text": "Islamic banking", "why": "other"}]


# ── E. S2 views (fail closed) ────────────────────────────────────────────────

class TestViews:
    def test_view_uses_the_effective_context_never_s1_text(self):
        a, jd, art = role_art([ctx(2, "the GCC region")])
        r = resolve(art, qc_analysis(a, jd, "identified", ["GCC region"]), jd)
        assert r.context_resolution.detail == "agreed"
        (v,) = asm.s2_views_v4(r)
        assert v.setting == "GCC region" and v.targets == (AUD,) and v.policy == "explicit_role"
        assert v.spec_version == r.spec_version

    def test_missing_resolution_blocks_regardless_of_require_resolved(self):
        a, jd, art = role_art()
        for rr in (True, False):
            with pytest.raises(asm.S1V4ViewError) as e:
                asm.s2_views_v4(art, require_resolved=rr)
            assert e.value.code == "context_unresolved"

    @pytest.mark.parametrize("analysis_kind", ["absent", "uncertain", "stale", "disagreement"])
    def test_unconfirmed_blocks_regardless_of_require_resolved(self, analysis_kind):
        a, jd, art = role_art()
        analysis = {"absent": a, "uncertain": qc_analysis(a, jd, "uncertain"),
                    "stale": qc_analysis(a, jd, "identified", ["Islamic banking institutions in the GCC region"],
                                         jd_hash="0" * 64),
                    "disagreement": qc_analysis(a, jd, "identified", ["GCC region"])}[analysis_kind]
        r = resolve(art, analysis, jd)
        assert r.context_resolution.status == "unconfirmed"
        for rr in (True, False):
            with pytest.raises(asm.S1V4ViewError) as e:
                asm.s2_views_v4(r, require_resolved=rr)
            assert e.value.code == "context_unconfirmed"

    def test_recruiter_answers_ambiguous_scope(self):
        a, jd, crits = job(["Accountant", AUD], 4, ["Requirements", "- " + L_ALT + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_ALT)], hints=2)),
                            resp(ctx_item(cid, [ctx(2, "Big Four firm", "one_alternative", ("T1",))]))))
        agreed_none = resolve(art, qc_analysis(a, jd, "none"), jd)
        assert agreed_none.context_resolution.detail == "disagreement"   # a scoped context is never "none"
        rec = resolve(art, qc_analysis(a, jd, "identified", ["Big Four firm"], source="recruiter",
                                       provenance="recruiter_edited"), jd)
        views = asm.s2_views_v4(rec)
        assert [v.setting for v in views] == ["Big Four firm"]

    def test_recruiter_does_not_answer_other_reasons(self):
        a, jd, cid = role_job()
        art = only(two_pass(a, jd, resp(hinted(cid, [(2, L_ROLE)])),
                            resp(ctx_item(cid, [ctx(2, "GCC region", "softened")], gap=[(2, "Islamic banking")]))))
        rec = resolve(art, qc_analysis(a, jd, "identified", ["GCC region"], source="recruiter",
                                       provenance="recruiter_edited"), jd)
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(rec, require_resolved=False)
        assert e.value.code == "target_unconfirmed"

    def test_pure_duration_cannot_carry_a_context(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_TOTAL + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_TOTAL)], [], basis="total_experience")),
                            resp(ctx_item(cid)), absent_policy="corroborated"))
        rec = resolve(art, qc_analysis(a, jd, "identified", ["professional"], source="recruiter",
                                       provenance="recruiter_edited"), jd)
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(rec)
        assert e.value.code == "context_structure_conflict"

    def test_sector_needs_an_effective_context(self):
        a, jd, crits = job([], 4, ["Requirements", "- " + L_SETTING + "."])
        cid = crits[0].criterion_id
        art = only(two_pass(a, jd, resp(free(cid, [(2, L_SETTING)], [], basis="setting_only")),
                            resp(ctx_item(cid, [ctx(2, "government entities", applies_to=())])),
                            absent_policy="corroborated"))
        rec = resolve(art, qc_analysis(a, jd, "none", source="recruiter", provenance="recruiter_edited"), jd)
        assert rec.context_resolution.detail == "recruiter"
        with pytest.raises(asm.S1V4ViewError) as e:
            asm.s2_views_v4(rec)
        assert e.value.code == "context_structure_conflict"

    def test_no_bypass_parameter(self):
        assert list(inspect.signature(asm.s2_views_v4).parameters) == ["art", "require_resolved"]


# ── F. independence of both passes ───────────────────────────────────────────

def qc_variants(a, jd):
    base = copy.deepcopy(a)
    out = {"absent": base}
    for name, (state, ctxs, src) in {"identified": ("identified", ["GCC region"], "analysis"),
                                     "identified_other": ("identified", ["MENA"], "analysis"),
                                     "none": ("none", [], "analysis"), "uncertain": ("uncertain", [], "analysis"),
                                     "recruiter": ("identified", ["GCC region"], "recruiter"),
                                     "recruiter_none": ("none", [], "recruiter")}.items():
        out[name] = qc_analysis(a, jd, state, ctxs, source=src, provenance="recruiter_edited"
                                if src == "recruiter" else None)
    g = copy.deepcopy(base)
    g["experience"]["qualifying_context"] = "garbage"
    g["qualifying_context_audit"] = ["garbage"]
    out["garbage"] = g
    m = qc_analysis(a, jd, "identified", ["GCC region"])
    m["qualifying_context_audit"]["latest_run"] = {"status": "failed_technical", "result": {"state": "none"}}
    m["qualifying_context_audit"]["current"]["provenance"] = "recruiter_confirmed"
    out["audit_mutated"] = m
    only_audit = copy.deepcopy(base)
    only_audit["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": None,
                                              "latest_run": {"status": "failed_validation"}}
    out["audit_only"] = only_audit
    return out


class TestIndependence:
    def _job(self):
        a, jd, cid = role_job()
        return a, jd, cid, resp(hinted(cid, [(2, L_ROLE)])), resp(ctx_item(cid, [ctx(2, "GCC region")]))

    def test_variants_cover_the_required_space(self):
        a, jd, *_ = self._job()
        assert len(qc_variants(a, jd)) == 10

    def test_pass_a_input_and_cache_identity_ignore_qc(self):
        a, jd, *_ = self._job()
        base = pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", a))
        for name, v in qc_variants(a, jd).items():
            req = pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", v))
            assert req.user_message == base.user_message, name
            assert req.input_hash == base.input_hash and pa.pass_a_cache_key(req) == pa.pass_a_cache_key(base)

    def test_pass_b_input_and_cache_identity_ignore_qc(self):
        a, jd, cid, ra, rb = self._job()
        base = two_pass(a, jd, ra, rb)
        for name, v in qc_variants(a, jd).items():
            r = two_pass(v, jd, ra, rb)
            assert r.pass_a["input_hash"] == base.pass_a["input_hash"], name
            assert r.pass_b["input_hash"] == base.pass_b["input_hash"], name

    def test_artefacts_identical_across_qc_variants(self):
        a, jd, cid, ra, rb = self._job()
        base = [x.to_dict() for x in two_pass(a, jd, ra, rb).artifacts]
        for name, v in qc_variants(a, jd).items():
            assert [x.to_dict() for x in two_pass(v, jd, ra, rb).artifacts] == base, name

    def test_only_the_resolution_differs(self):
        a, jd, cid, ra, rb = self._job()
        art = only(two_pass(a, jd, ra, rb))
        outs = {name: resolve(art, v, jd) for name, v in qc_variants(a, jd).items()}
        for name, r in outs.items():
            assert dataclasses.replace(r, context_resolution=None) == art, name
        assert len({r.context_resolution.detail for r in outs.values()}) > 1

    def test_control_non_qc_fields_do_change_the_input(self):
        a, jd, cid, ra, rb = self._job()
        base = pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", a))
        b = copy.deepcopy(a)
        b["experience"]["minimum_years"] = 6
        c = copy.deepcopy(a)
        c["experience"]["relevant_roles"] = ["Auditor"]
        for v in (b, c):
            assert pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", v)).input_hash != \
                base.input_hash
        assert pa.build_pass_a_request(JDText(jd + "\n- x."), enumerate_experience_criteria("J1", a)).input_hash \
            != base.input_hash

    def test_pass_b_input_changes_only_with_the_frozen_targets(self):
        a, jd, crits = job(["Accountant", AUD], 4, ["Requirements", "- " + L_ALT + "."])
        cid = crits[0].criterion_id
        one = two_pass(a, jd, resp(hinted(cid, [(2, L_ALT)], hints=2)), resp(ctx_item(cid)))
        two = two_pass(a, jd, resp(hinted(cid, [(2, L_ALT)], hints=2, ambiguity=["ambiguous_relevance"])),
                       resp(ctx_item(cid)))
        assert one.pass_b["input_hash"] == two.pass_b["input_hash"]      # ambiguity is not a target
        fr = frames_for(a, jd, resp(hinted(cid, [(2, L_ALT)], hints=2)))
        k = pb.pass_b_cache_key(pb.build_pass_b_request(JDText(jd), fr))
        changed = [dataclasses.replace(f, targets=f.targets[:1]) for f in fr]
        assert pb.pass_b_cache_key(pb.build_pass_b_request(JDText(jd), changed)) != k

    def test_mutation_check_detects_a_leak(self, monkeypatch):
        a, jd, cid, ra, rb = self._job()
        real = pa.build_pass_a_request
        v = qc_variants(a, jd)["identified"]

        def leaky(j, crits):
            req = real(j, crits)
            req.payload["leak"] = v["experience"].get("qualifying_context")
            return req
        clean = real(JDText(jd), enumerate_experience_criteria("J1", a)).input_hash
        monkeypatch.setattr(pa, "build_pass_a_request", leaky)
        assert pa.build_pass_a_request(JDText(jd), enumerate_experience_criteria("J1", v)).input_hash != clean

    def test_entry_points_take_no_qc_parameter(self):
        assert list(inspect.signature(pa.build_pass_a_request).parameters) == ["jd", "criteria"]
        assert list(inspect.signature(pb.build_pass_b_request).parameters) == ["jd", "frames"]
        assert list(inspect.signature(pa.validate_pass_a).parameters) == ["raw", "jd", "criteria", "durations"]
        assert list(inspect.signature(pb.validate_pass_b).parameters) == ["raw", "jd", "frames"]
        assert [f.name for f in dataclasses.fields(pb.TargetFrame)] == [
            "criterion_id", "target_basis", "targets", "requirement_spans", "duration_span"]

    @pytest.mark.parametrize("fname", ["__init__.py", "schema.py", "pass_a.py", "pass_b.py", "assemble.py"])
    def test_static_guard(self, fname):
        path = PKG / fname
        text = path.read_text(encoding="utf-8")
        assert "qualifying_context" not in text and "candidate_qc" not in text
        mods = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                mods |= {x.name for x in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        for m in mods:
            for banned in ("qualifying_context", "openai", "ai_service", "llm_", "s1_requirements.classifier",
                           "s1_requirements.repair", "s2_experience", "routers", "workers", "database",
                           "sqlalchemy", "criteria_matcher", "deterministic_scoring", "config"):
                assert banned not in m, (fname, m)
        if fname in ("pass_a.py", "pass_b.py"):
            assert "context_resolution" not in text and "resolve_context" not in text

    def test_nothing_in_production_imports_the_two_pass_package(self):
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            root = BACKEND / sub
            for p in root.rglob("*.py") if root.exists() else []:
                if PKG in p.parents:
                    continue
                if "s1_two_pass" in p.read_text(encoding="utf-8"):
                    hits.append(str(p))
        assert hits == []
