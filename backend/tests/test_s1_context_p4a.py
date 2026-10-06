"""
Architecture C P4a — S1 s1-6.0 experience contexts (settings), the fail-closed S2 view gate and S1 independence
from the qualifying-context analysis. Offline only: fake AI clients, no OpenAI, no database. All JDs SYNTHETIC.
"""
import ast
import asyncio
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.s1_requirements import assemble as asm
from services.s1_requirements import classifier as clf
from services.s1_requirements import repair as rp
from services.s1_requirements import schema as sc
from services.s1_requirements.criteria import enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.validator import validate_response

BACKEND = Path(__file__).resolve().parent.parent


class FakeClient:
    """Returns queued answers and records every request (messages included)."""

    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(copy.deepcopy(kw))
            item = self.items.pop(0)
            content, finish = item if isinstance(item, tuple) else (item, "stop")
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason=finish)], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def job(hints, years, lines):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "\n".join(lines)
    return a, jd, enumerate_experience_criteria("J1", a)


def hint_item(cid, spans, *, settings=(), duration="D1", ambiguity=(), hints=1, **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "targets": [{"hint": f"T{i}", "type": "role", "match": "exact", "jd_span": None}
                      for i in range(1, hints + 1)],
          "settings": [{"line": ln, "text": t} for ln, t in settings],
          "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def free_item(cid, spans, restrictions, *, duration="D1", ambiguity=(), **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
          "restrictions": [{"line": ln, "text": t, "kind": k} for ln, t, k in restrictions],
          "duration": duration, "ambiguity": list(ambiguity), "note": "n"}
    it.update(over)
    return it


def resp(*items):
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


def validate(jd, crits, raw):
    j = JDText(jd)
    return validate_response(raw, j, crits, {d: (ln, m) for d, ln, m in j.durations()})


def classify(jd, a, *answers, cache=None):
    client = FakeClient(*answers)
    return client, run(clf.classify_job("J1", jd, a, client=client, cache=cache))


def resolved(art, *contexts, provenance="recruiter_confirmed"):
    """EXPLICIT test-written resolution (stand-in for P4c); never derived from art.settings."""
    eff = sc.EffectiveContext("identified" if contexts else "none", tuple(contexts), provenance)
    detail = "recruiter" if provenance != "jd_verified" else ("agreed" if contexts else "agreed_none")
    return dataclasses.replace(art, context_resolution=sc.ContextResolution("resolved", detail, eff))


AUD = "Internal Auditor"
L_ONE = "Minimum 5 years as an Internal Auditor in Islamic banking institutions in the GCC region"
ONE_JD = ["Requirements", "- " + L_ONE + "."]
L_GOV = "Minimum 5 years as an Internal Auditor in government entities"
L_SEP = "All of this experience must have been gained on infrastructure projects"
SEP_JD = ["Requirements", "- " + L_GOV + ".", "- Bachelor's degree in Accounting.", "- " + L_SEP + "."]


# ── A. schema v3 ─────────────────────────────────────────────────────────────

def _span(line, start, text):
    return sc.Span(line, start, start + len(text), text)


class TestSchemaV3:
    def test_versions(self):
        assert (sc.S1_SCHEMA, sc.S1_VERSION, sc.S1_PROMPT_VERSION, sc.S1_INPUT_VERSION) == (
            "s1_requirement_spec_v3", "1.5.0", "s1-6.0", "s1-in-1")
        assert sc.MAX_SETTINGS == 5
        assert "ambiguous_context_scope" in sc.AMBIGUITY_CODES
        assert sc.REASON_KINDS["ambiguous_context_scope"] == "ambiguity"            # business/ambiguity, not failure
        assert sc.RESTRICTION_KINDS == ("role", "function", "context", "vague")
        assert not hasattr(sc, "RESTRICTION_SECTOR")
        fields = [f.name for f in dataclasses.fields(sc.S1Artifact)]
        assert "settings" in fields and "setting" not in fields and "context_resolution" in fields

    def _art(self, settings=(), **kw):
        return sc.S1Artifact(criterion_id="c", job_id="j", source_path="p", display_text="d", requirement_text="r",
                             required=True, spec_status="resolved", policy="explicit_role",
                             settings=tuple(settings), **kw)

    def test_settings_invariants(self):
        a = sc.Setting("GCC region", "jd_asserted", _span(2, 30, "GCC region"))
        b = sc.Setting("infrastructure projects", "jd_asserted", _span(4, 3, "infrastructure projects"))
        assert self._art([a, b]).settings == (a, b)
        with pytest.raises(ValueError, match="ordered"):
            self._art([b, a])
        with pytest.raises(ValueError, match="overlap"):
            self._art([a, sc.Setting("region", "jd_asserted", _span(2, 34, "region"))])
        many = [sc.Setting(f"x{i}", "jd_asserted", _span(1, i * 10, f"x{i}")) for i in range(6)]
        with pytest.raises(ValueError, match="at most 5"):
            self._art(many)
        assert len(self._art(many[:5]).settings) == 5

    def test_v2_is_never_read_as_v3(self):
        d = self._art().to_dict()
        assert d["_schema"] == "s1_requirement_spec_v3" and d["settings"] == [] and "setting" not in d
        assert sc.S1Artifact.from_dict(d) == self._art()
        v2 = {**d, "_schema": "s1_requirement_spec_v2"}
        with pytest.raises(ValueError, match="not an s1_requirement_spec_v3"):
            sc.S1Artifact.from_dict(v2)
        legacy = {k: v for k, v in d.items() if k != "settings"}
        legacy["setting"] = None
        with pytest.raises(ValueError, match="never a single setting"):
            sc.S1Artifact.from_dict(legacy)

    def test_context_resolution_invariants(self):
        E, R = sc.EffectiveContext, sc.ContextResolution
        ok = R("resolved", "recruiter", E("identified", ("GCC region",), "recruiter_edited"))
        assert ok.semantic_dict() == {"status": "resolved", "detail": "recruiter",
                                      "effective": {"state": "identified", "contexts": ["GCC region"],
                                                    "provenance": "recruiter_edited"}}
        R("resolved", "agreed_none", E("none", (), "jd_verified"))
        R("unconfirmed", "unassessed")
        for bad in (lambda: R("resolved", "agreed"),                                         # resolved needs effective
                    lambda: R("unconfirmed", "uncertain", E("none", (), "jd_verified")),     # unconfirmed has none
                    lambda: R("resolved", "disagreement", E("none", (), "jd_verified")),
                    lambda: R("unconfirmed", "agreed"),
                    lambda: R("resolved", "agreed", E("none", (), "jd_verified")),
                    lambda: R("resolved", "agreed_none", E("identified", ("x",), "jd_verified")),
                    lambda: R("resolved", "recruiter", E("none", (), "jd_verified")),
                    lambda: R("resolved", "agreed", E("identified", ("x",), "recruiter_edited")),
                    lambda: E("identified", (), "jd_verified"),
                    lambda: E("none", ("x",), "jd_verified"),
                    lambda: E("identified", (" ",), "jd_verified"),
                    lambda: E("identified", ("x",), "original_ai"),
                    lambda: R("maybe", "agreed")):
            with pytest.raises(ValueError):
                bad()

    def test_resolution_roundtrip_and_hash(self):
        plain = self._art()
        r = resolved(plain, "GCC region")
        back = sc.S1Artifact.from_dict(r.to_dict())
        assert back == r and back.context_resolution.effective.contexts == ("GCC region",)
        # the effective context is part of the semantics: it changes content hash / spec version
        assert r.spec_version != plain.spec_version
        assert resolved(plain, "GCC region").spec_version != resolved(plain, "MENA region").spec_version
        # the audit record is not semantics
        rec = dataclasses.replace(r, context_resolution=dataclasses.replace(r.context_resolution, record={"a": 1}))
        assert rec.spec_version == r.spec_version


# ── B/C/D/F. validator ───────────────────────────────────────────────────────

class TestValidatorSettings:
    def test_one_contiguous_context_stays_one_setting(self):
        a, jd, crits = job([AUD], 5, ONE_JD)
        raw = resp(hint_item(crits[0].criterion_id, [(2, L_ONE)],
                             settings=[(2, "Islamic banking institutions in the GCC region")]))
        v = validate(jd, crits, raw)
        assert v.ok, v.errors
        (pc,) = v.results.values()
        assert [s.text for s in pc.settings] == ["Islamic banking institutions in the GCC region"]
        _, out = classify(jd, a, raw)
        (art,) = out.artifacts
        assert [s.text for s in art.settings] == ["Islamic banking institutions in the GCC region"]
        assert art.spec_status == "resolved" and art.field_provenance["settings"] == "jd_asserted"
        assert art.context_resolution is None                    # S1 never resolves the qualifying context

    def test_or_inside_one_context(self):
        line = "Minimum 5 years as an Internal Auditor in banks or insurance companies"
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + line + "."])
        _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, line)],
                                                settings=[(2, "banks or insurance companies")])))
        assert [s.text for s in out.artifacts[0].settings] == ["banks or insurance companies"]

    def test_two_independent_and_contexts_in_separate_sentence(self):
        a, jd, crits = job([AUD], 5, SEP_JD)
        cid = crits[0].criterion_id
        raw = resp(hint_item(cid, [(4, L_SEP), (2, L_GOV)],
                             settings=[(4, "infrastructure projects"), (2, "government entities")]))
        _, out = classify(jd, a, raw)
        (art,) = out.artifacts
        assert art.spec_status == "resolved"
        # deterministic JD order, AND semantics (both kept)
        assert [(s.text, s.jd_span.line) for s in art.settings] == [("government entities", 2),
                                                                    ("infrastructure projects", 4)]
        assert [s.line for s in art.requirement_spans] == [2, 4]
        assert art.requirement_text == L_GOV + "\n" + L_SEP

    def test_separate_sentence_needs_its_context_to_attach(self):
        a, jd, crits = job([AUD], 5, SEP_JD)
        v = validate(jd, crits, resp(hint_item(crits[0].criterion_id, [(2, L_GOV), (4, L_SEP)],
                                               settings=[(2, "government entities")])))
        assert not v.ok and any("contains no anchor" in e for e in v.errors)

    def test_about_us_before_the_requirement_never_attaches(self):
        lines = ["We are a leading audit firm in the GCC region.", "Requirements", "- " + L_GOV + "."]
        a, jd, crits = job([AUD], 5, lines)
        v = validate(jd, crits, resp(hint_item(
            crits[0].criterion_id, [(3, L_GOV), (1, "We are a leading audit firm in the GCC region")],
            settings=[(3, "government entities"), (1, "GCC region")])))
        assert not v.ok and any("contains no anchor" in e for e in v.errors)

    def test_context_sentence_outside_the_window_never_attaches(self):
        lines = ["Requirements", "- " + L_GOV + ".", "- a.", "- b.", "- c.", "- " + L_SEP + "."]
        a, jd, crits = job([AUD], 5, lines)
        v = validate(jd, crits, resp(hint_item(crits[0].criterion_id, [(2, L_GOV), (6, L_SEP)],
                                               settings=[(2, "government entities"), (6, "infrastructure projects")])))
        assert not v.ok and any("contains no anchor" in e for e in v.errors)

    def test_context_line_is_never_a_continuation_source(self):
        lines = ["Requirements", "- " + L_GOV + ".", "- x.", "- " + L_SEP + ".", "- Location: Riyadh office."]
        a, jd, crits = job([AUD], 5, lines)
        v = validate(jd, crits, resp(hint_item(
            crits[0].criterion_id, [(2, L_GOV), (4, L_SEP), (5, "Location: Riyadh office")],
            settings=[(4, "infrastructure projects")])))
        assert not v.ok and any("Location: Riyadh office" in e and "no anchor" in e for e in v.errors)

    @pytest.mark.parametrize("settings, frag", [
        ([(2, "Islamic banking institutions in the GCC region"), (2, "GCC region")], "overlap"),
        ([(2, "GCC region"), (2, "GCC region")], "duplicate context"),
        ([(2, "5 years as an Internal Auditor")], "must not include the duration"),
        ([(2, "Internal Auditor in Islamic")], "overlaps the target"),
        ([(1, "Requirements")], "not inside"),
        ([(2, "Gulf region")], "not verbatim"),
    ])
    def test_rejections(self, settings, frag):
        a, jd, crits = job([AUD], 5, ONE_JD)
        v = validate(jd, crits, resp(hint_item(crits[0].criterion_id, [(2, L_ONE)], settings=settings)))
        assert not v.ok and any(frag in e for e in v.errors), v.errors

    def test_at_most_five(self):
        line = "Minimum 5 years as an Internal Auditor in alpha, beta, gamma, delta, epsilon and zeta entities"
        a, jd, crits = job([AUD], 5, ["Requirements", "- " + line + "."])
        six = [(2, w) for w in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")]
        v = validate(jd, crits, resp(hint_item(crits[0].criterion_id, [(2, line)], settings=six)))
        assert not v.ok and any("at most 5 settings" in e for e in v.errors)
        assert validate(jd, crits, resp(hint_item(crits[0].criterion_id, [(2, line)], settings=six[:5]))).ok

    def test_legacy_single_setting_is_an_error_not_ignored(self):
        a, jd, crits = job([AUD], 5, ONE_JD)
        it = hint_item(crits[0].criterion_id, [(2, L_ONE)])
        del it["settings"]
        it["setting"] = {"line": 2, "text": "GCC region"}
        v = validate(jd, crits, resp(it))
        assert not v.ok and any('never a single "setting"' in e for e in v.errors)
        it["setting"] = None                                         # a null legacy key carries no context
        assert validate(jd, crits, resp(it)).ok
        it["settings"] = {"line": 2, "text": "GCC region"}           # not a list
        v = validate(jd, crits, resp(it))
        assert not v.ok and any("settings must be a list" in e for e in v.errors)

    def test_ambiguous_context_scope(self):
        line = "Minimum 5 years as an Internal Auditor in government entities or as a Tax Accountant"
        a, jd, crits = job([AUD, "Tax Accountant"], 5, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        bad = hint_item(cid, [(2, line)], hints=2, settings=[(2, "government entities")],
                        ambiguity=["ambiguous_context_scope"])
        v = validate(jd, crits, resp(bad))
        assert not v.ok and any("ambiguous_context_scope means" in e for e in v.errors)
        good = hint_item(cid, [(2, line)], hints=2, ambiguity=["ambiguous_context_scope"])
        _, out = classify(jd, a, resp(good))
        (art,) = out.artifacts
        assert art.settings == () and art.spec_status == "needs_confirmation"
        assert [(r.code, r.kind) for r in art.reasons] == [("ambiguous_context_scope", "ambiguity")]
        assert "ambiguous_relevance" not in [r.code for r in art.reasons]

    def test_hintless_context_restrictions(self):
        line = "Minimum 4 years of internal audit experience in government entities on infrastructure projects"
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        _, out = classify(jd, a, resp(free_item(cid, [(2, line)], [
            (2, "infrastructure projects", "context"), (2, "internal audit", "function"),
            (2, "government entities", "context")])))
        (art,) = out.artifacts
        assert art.policy == "functional" and [t.text for t in art.targets] == ["internal audit"]
        assert [s.text for s in art.settings] == ["government entities", "infrastructure projects"]
        # contexts only -> sector policy with several AND contexts (no longer a validation failure)
        line2 = "Minimum 4 years of experience in government entities on infrastructure projects"
        a2, jd2, crits2 = job([], 4, ["Requirements", "- " + line2 + "."])
        _, out2 = classify(jd2, a2, resp(free_item(crits2[0].criterion_id, [(2, line2)], [
            (2, "government entities", "context"), (2, "infrastructure projects", "context")])))
        art2 = out2.artifacts[0]
        assert out2.status == "ok" and art2.policy == "sector" and art2.spec_status == "resolved"
        assert [s.text for s in art2.settings] == ["government entities", "infrastructure projects"]
        assert art2.audit["ai"]["relevance_basis"] == "sector"

    def test_hintless_never_takes_settings_or_sector_kind(self):
        line = "Minimum 4 years of experience in government entities"
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        for it, frag in ((free_item(cid, [(2, line)], [(2, "government entities", "sector")]), "kind"),
                         (free_item(cid, [(2, line)], [(2, "government entities", "context")],
                                    settings=[{"line": 2, "text": "government entities"}]), "return no settings")):
            v = validate(jd, crits, resp(it))
            assert not v.ok and any(frag in e for e in v.errors), v.errors


# ── G. repair never broadens by dropping a context ──────────────────────────

class TestRepairKeepsContexts:
    def _two(self):
        return job([AUD], 5, SEP_JD)

    def test_valid_context_cannot_be_dropped(self):
        a, jd, crits = self._two()
        cid = crits[0].criterion_id
        spans = [(2, L_GOV), (4, L_SEP)]
        main = hint_item(cid, spans, settings=[(2, "government entities"), (4, "infrastructure projects"),
                                               (2, "governmnt")])                       # S2 invalid (not verbatim)
        only_a = hint_item(cid, spans, settings=[(2, "government entities")])
        _, out = classify(jd, a, resp(main), resp(only_a))
        assert out.artifacts[0].spec_status == "failed_validation"
        assert out.meta["repair_merge"]["kept_main_settings"] == [cid]
        fixed = hint_item(cid, spans, settings=[(2, "government entities"), (4, "infrastructure projects")])
        # dropping the invalid one without a replacement is still refused (it may have been a real context) ...
        _, out = classify(jd, a, resp(main), resp(fixed))
        assert out.artifacts[0].spec_status == "failed_validation"
        # ... while a corrected replacement is taken
        repl = hint_item(cid, spans, settings=[(2, "government entities"), (4, "infrastructure projects"),
                                               (4, "experience")])
        _, out = classify(jd, a, resp(main), resp(repl))
        assert out.artifacts[0].spec_status == "resolved"
        assert [s.text for s in out.artifacts[0].settings] == ["government entities", "experience",
                                                               "infrastructure projects"]

    def test_unit_contexts_preserved(self):
        A, B = {"line": 2, "text": "government entities"}, {"line": 4, "text": "infrastructure projects"}
        whole = {"line": 2, "text": "Islamic banking institutions in the GCC region"}
        part = {"line": 2, "text": "GCC region"}
        assert not rp.contexts_preserved([A, B], [A], set())                 # valid B dropped
        assert rp.contexts_preserved([A, B], [B, A], set())                   # order irrelevant
        assert rp.contexts_preserved([whole, part], [whole], {0, 1})          # merge into one contiguous phrase
        assert not rp.contexts_preserved([whole, part], [part], {0, 1})       # narrowing to a fragment
        assert not rp.contexts_preserved([A, {"line": 2, "text": "x"}], [A], {1})   # invalid dropped, not replaced
        assert rp.contexts_preserved([A, {"line": 2, "text": "x"}], [A, B], {1})

    def test_overlapping_contexts_may_merge_into_one(self):
        a, jd, crits = job([AUD], 5, ONE_JD)
        cid = crits[0].criterion_id
        main = hint_item(cid, [(2, L_ONE)], settings=[(2, "Islamic banking institutions in the GCC region"),
                                                      (2, "GCC region")])
        merged = hint_item(cid, [(2, L_ONE)], settings=[(2, "Islamic banking institutions in the GCC region")])
        _, out = classify(jd, a, resp(main), resp(merged))
        assert out.artifacts[0].spec_status == "resolved" and len(out.artifacts[0].settings) == 1
        narrowed = hint_item(cid, [(2, L_ONE)], settings=[(2, "GCC region")])
        _, out = classify(jd, a, resp(main), resp(narrowed))
        assert out.artifacts[0].spec_status == "failed_validation"

    def test_legacy_single_setting_cannot_be_repaired_away(self):
        a, jd, crits = job([AUD], 5, ONE_JD)
        cid = crits[0].criterion_id
        main = hint_item(cid, [(2, L_ONE)])
        del main["settings"]
        main["setting"] = {"line": 2, "text": "GCC region"}
        _, out = classify(jd, a, resp(main), resp(hint_item(cid, [(2, L_ONE)])))
        assert out.artifacts[0].spec_status == "failed_validation"
        _, out = classify(jd, a, resp(main), resp(hint_item(cid, [(2, L_ONE)], settings=[(2, "GCC region")])))
        assert out.artifacts[0].spec_status == "resolved"

    def test_ambiguous_scope_may_remove_contexts(self):
        line = "Minimum 5 years as an Internal Auditor in government entities or as a Tax Accountant"
        a, jd, crits = job([AUD, "Tax Accountant"], 5, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        main = hint_item(cid, [(2, line)], hints=2, settings=[(2, "government entities")],
                         ambiguity=["ambiguous_context_scope"])
        rep = hint_item(cid, [(2, line)], hints=2, ambiguity=["ambiguous_context_scope"])
        _, out = classify(jd, a, resp(main), resp(rep))
        art = out.artifacts[0]
        assert art.spec_status == "needs_confirmation" and art.settings == ()
        assert [r.code for r in art.reasons] == ["ambiguous_context_scope"]

    def test_restriction_contexts_cannot_be_dropped(self):
        line = "Minimum 4 years of internal audit experience in government entities on infrastructure projects"
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        good = [(2, "internal audit", "function"), (2, "government entities", "context"),
                (2, "infrastructure projects", "context")]
        main = free_item(cid, [(2, line)], good + [(2, "internal", "nonsense")])           # R3 malformed kind
        drop = free_item(cid, [(2, line)], good[:2])                                       # drops a valid context
        _, out = classify(jd, a, resp(main), resp(drop))
        assert out.artifacts[0].spec_status == "failed_validation"
        assert out.meta["repair_merge"]["kept_main_restrictions"] == [cid]

    def test_invalid_or_unknown_kind_context_restriction_must_be_replaced(self):
        line = "Minimum 4 years of internal audit experience in government entities on infrastructure projects"
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        cid = crits[0].criterion_id
        fn, gov, infra = ((2, "internal audit", "function"), (2, "government entities", "context"),
                          (2, "infrastructure projects", "context"))
        # an invalid (not verbatim) context may not simply disappear in the repair
        main = free_item(cid, [(2, line)], [fn, gov, (2, "infrastructure project", "context")])
        _, out = classify(jd, a, resp(main), resp(free_item(cid, [(2, line)], [fn, gov])))
        assert out.artifacts[0].spec_status == "failed_validation"
        _, out = classify(jd, a, resp(main), resp(free_item(cid, [(2, line)], [fn, gov, infra])))
        assert out.artifacts[0].spec_status == "resolved" and len(out.artifacts[0].settings) == 2
        # an entry of unknown kind (e.g. the retired wire kind "sector") must reappear with its text
        main = free_item(cid, [(2, line)], [fn, gov, (2, "infrastructure projects", "sector")])
        _, out = classify(jd, a, resp(main), resp(free_item(cid, [(2, line)], [fn, gov])))
        assert out.artifacts[0].spec_status == "failed_validation"
        _, out = classify(jd, a, resp(main), resp(free_item(cid, [(2, line)], [fn, gov, infra])))
        assert out.artifacts[0].spec_status == "resolved"

    def test_material_lock_scoped_repair_and_withdrawal_unchanged(self):
        src = (BACKEND / "services" / "s1_requirements" / "repair.py").read_text(encoding="utf-8")
        for name in ("_material_lock_holds", "plan_withdrawal", "apply_withdrawal", "_merge_pairs",
                     "normalize_duplicate_representations"):
            assert f"def {name}(" in src


# ── compound_requirement vs ambiguous_context_scope (distinct reasons, never coupled) ──────────────────

L_COMPOUND = "5 years of experience, including 2 years in the GCC"
L_COMPOUND_ROLE = "Minimum 5 years of experience as an Internal Auditor, including 2 years in the GCC region"
L_ALT = "Minimum 4 years as an Accountant in a Big Four firm or as an Internal Auditor"
L_BOTH = "8 years of overall experience, including 3 years as an Accountant in a Big Four firm or as an Internal Auditor"


def _compound_free():
    a, jd, crits = job([], 5, ["Requirements", "- " + L_COMPOUND + "."])
    _, out = classify(jd, a, resp(free_item(crits[0].criterion_id, [(2, L_COMPOUND)], [])))
    return out.artifacts[0]


def _compound_role():
    a, jd, crits = job([AUD], 5, ["Requirements", "- " + L_COMPOUND_ROLE + "."])
    _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, L_COMPOUND_ROLE)])))
    return out.artifacts[0]


def _alternative():
    a, jd, crits = job(["Accountant", AUD], 4, ["Requirements", "- " + L_ALT + "."])
    _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, L_ALT)], hints=2,
                                            ambiguity=["ambiguous_context_scope"])))
    return out.artifacts[0]


class TestCompoundIsNotContextAmbiguity:
    @pytest.mark.parametrize("make", [_compound_free, _compound_role])
    def test_compound_requirement_only(self, make):
        art = make()
        assert art.spec_status == "needs_confirmation" and art.settings == ()
        assert [r.code for r in art.reasons] == ["compound_requirement"]
        assert "ambiguous_context_scope" not in [r.code for r in art.reasons]
        assert art.required_years is None                         # N is never collapsed for a compound

    def test_alternative_specific_context_is_ambiguous_scope_only(self):
        art = _alternative()
        assert art.spec_status == "needs_confirmation" and art.settings == ()
        assert [(r.code, r.field) for r in art.reasons] == [("ambiguous_context_scope", "ai")]
        assert "compound_requirement" not in [r.code for r in art.reasons]

    def test_reasons_are_not_coupled(self):
        # each reason comes from its own source: compound from the deterministic duration parser, the context
        # scope only from the model's ambiguity report; both appear only when both genuinely apply
        a, jd, crits = job(["Accountant", AUD], 8, ["Requirements", "- " + L_BOTH + "."])
        _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, L_BOTH)], hints=2,
                                                ambiguity=["ambiguous_context_scope"])))
        art = out.artifacts[0]
        assert sorted((r.code, r.field) for r in art.reasons) == [("ambiguous_context_scope", "ai"),
                                                                  ("compound_requirement", "required_years")]
        tree = ast.parse((BACKEND / "services" / "s1_requirements" / "assemble.py").read_text(encoding="utf-8"))
        made = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Reason"
                and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "AMB_AMBIGUOUS_CONTEXT_SCOPE"]
        assert made == []                                         # assembly never derives the context-scope code
        vsrc = (BACKEND / "services" / "s1_requirements" / "validator.py").read_text(encoding="utf-8")
        assert "compound" not in vsrc.lower()                     # nor does the validator tie it to compounds

    @pytest.mark.parametrize("make", [_compound_free, _compound_role, _alternative])
    def test_both_fail_closed(self, make):
        art = make()
        for rr in (True, False):
            with pytest.raises(asm.S1ViewError):
                asm.s2_views(art, require_resolved=rr)                                # no resolution
            with pytest.raises(asm.S1ViewError):
                asm.s2_views(resolved(art, provenance="jd_verified"), require_resolved=rr)
        with pytest.raises(asm.S1ViewError):
            asm.s2_views(resolved(art, "GCC region"), require_resolved=True)       # still needs_confirmation
        if any(r.code == "compound_requirement" for r in art.reasons):
            with pytest.raises(asm.S1ViewError, match="compound requirement"):      # never, not even a preview
                asm.s2_views(resolved(art, "GCC region"), require_resolved=False)


# ── J. fail-closed S2 views ─────────────────────────────────────────────────

def _art_one():
    a, jd, crits = job([AUD], 5, ONE_JD)
    _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, L_ONE)],
                                            settings=[(2, "Islamic banking institutions in the GCC region")])))
    return out.artifacts[0]


class TestFailClosedViews:
    @pytest.mark.parametrize("rr", [True, False])
    def test_missing_resolution_blocks(self, rr):
        art = _art_one()
        assert art.spec_status == "resolved" and art.settings
        with pytest.raises(asm.S1ViewError) as e:
            asm.s2_views(art, require_resolved=rr)
        assert e.value.code == "context_unresolved"

    @pytest.mark.parametrize("rr", [True, False])
    @pytest.mark.parametrize("detail", ["disagreement", "uncertain", "unassessed", "qc_failed", "stale",
                                        "s1_unavailable"])
    def test_unconfirmed_blocks(self, rr, detail):
        art = dataclasses.replace(_art_one(), context_resolution=sc.ContextResolution("unconfirmed", detail))
        with pytest.raises(asm.S1ViewError) as e:
            asm.s2_views(art, require_resolved=rr)
        assert e.value.code == "context_unconfirmed"

    def test_gate_checks_status_even_if_the_invariant_is_bypassed(self):
        cr = sc.ContextResolution("unconfirmed", "disagreement")
        object.__setattr__(cr, "effective", sc.EffectiveContext("none", (), "jd_verified"))   # forged object
        art = dataclasses.replace(_art_one(), context_resolution=cr)
        for rr in (True, False):
            with pytest.raises(asm.S1ViewError) as e:
                asm.s2_views(art, require_resolved=rr)
            assert e.value.code == "context_unconfirmed"

    @pytest.mark.parametrize("rr", [True, False])
    def test_multi_context_blocks(self, rr):
        art = resolved(_art_one(), "Islamic banking institutions", "GCC region")
        with pytest.raises(asm.S1ViewError) as e:
            asm.s2_views(art, require_resolved=rr)
        assert e.value.code == "multi_context_unsupported"

    def test_view_uses_effective_context_never_s1_settings(self):
        art = _art_one()
        (v,) = asm.s2_views(resolved(art, "GCC region"))
        assert v.setting == "GCC region"                                   # not S1's own reading
        (v,) = asm.s2_views(resolved(art))                                 # recruiter "none" governs
        assert v.setting is None
        (v,) = asm.s2_views(resolved(art, "Islamic banking institutions in the GCC region", provenance="jd_verified"))
        assert v.setting == "Islamic banking institutions in the GCC region"
        assert v.spec_version == resolved(art, "Islamic banking institutions in the GCC region",
                                          provenance="jd_verified").spec_version

    def test_ambiguous_scope_needs_a_recruiter_resolution(self):
        line = "Minimum 5 years as an Internal Auditor in government entities or as a Tax Accountant"
        a, jd, crits = job([AUD, "Tax Accountant"], 5, ["Requirements", "- " + line + "."])
        _, out = classify(jd, a, resp(hint_item(crits[0].criterion_id, [(2, line)], hints=2,
                                                ambiguity=["ambiguous_context_scope"])))
        art = out.artifacts[0]
        for rr in (True, False):
            with pytest.raises(asm.S1ViewError) as e:
                asm.s2_views(resolved(art, provenance="jd_verified"), require_resolved=rr)
            assert e.value.code == "context_scope_ambiguous"
        with pytest.raises(asm.S1ViewError):                                # still needs_confirmation
            asm.s2_views(resolved(art, "government entities"))
        (v,) = asm.s2_views(resolved(art, "government entities"), require_resolved=False)
        assert v.setting == "government entities"

    def test_sector_and_pure_duration_never_lose_a_context(self):
        line = "Minimum 4 years of experience in government entities"
        a, jd, crits = job([], 4, ["Requirements", "- " + line + "."])
        _, out = classify(jd, a, resp(free_item(crits[0].criterion_id, [(2, line)],
                                                [(2, "government entities", "context")])))
        sector = out.artifacts[0]
        with pytest.raises(asm.S1ViewError, match="needs exactly one effective context"):
            asm.s2_views(resolved(sector))
        (v,) = asm.s2_views(resolved(sector, "government entities"))
        assert (v.policy, v.targets, v.setting) == ("sector", ("government entities",), None)
        line2 = "Minimum 4 years of professional experience"
        a2, jd2, crits2 = job([], 4, ["Requirements", "- " + line2 + "."])
        _, out2 = classify(jd2, a2, resp(free_item(crits2[0].criterion_id, [(2, line2)], [])))
        pure = out2.artifacts[0]
        assert pure.policy == "pure_duration"
        with pytest.raises(asm.S1ViewError, match="pure-duration"):
            asm.s2_views(resolved(pure, "GCC region"))
        assert asm.s2_views(resolved(pure))[0].setting is None

    def test_failures_never_get_a_view_even_when_resolved(self):
        a, jd, crits = job([AUD], 5, ONE_JD)
        _, out = classify(jd, a, resp({"criteria": "x"}), resp({"criteria": "x"}))
        art = out.artifacts[0]
        assert art.spec_status == "failed_validation" and art.policy is None and art.settings == ()
        for rr in (True, False):
            with pytest.raises(asm.S1ViewError):
                asm.s2_views(resolved(art), require_resolved=rr)

    def test_compound_and_unverified_still_block_first(self):
        src = (BACKEND / "services" / "s1_requirements" / "assemble.py").read_text(encoding="utf-8")
        body = src[src.index("def s2_views("):]
        assert body.index("BIZ_COMPOUND_REQUIREMENT") < body.index("_context_gate(art)") < body.index("allowed:")

    def test_no_bypass_parameter(self):
        import inspect
        assert list(inspect.signature(asm.s2_views).parameters) == ["art", "require_resolved"]
        tree = ast.parse((BACKEND / "services" / "s1_requirements" / "assemble.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "s2_views")
        assert not any(isinstance(n, ast.Attribute) and n.attr == "settings" for n in ast.walk(fn))
        gate = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_context_gate")
        assert not any(isinstance(n, ast.Attribute) and n.attr == "settings" for n in ast.walk(gate))


# ── K. S1 independence from the qualifying-context analysis ─────────────────

BASE = {"experience": {"minimum_years": 5, "relevant_roles": [AUD, "Tax Accountant"],
                       "requirement_type": "required"},
        "domain_knowledge": ["auditing"], "other_requirements": ["Fluent English"]}
IND_JD = "\n".join(["Requirements",
                    "- Minimum 5 years as an Internal Auditor or Tax Accountant in government entities.",
                    "- Fluent English."])
QC_VARIANTS = {
    "identified_analysis": {"state": "identified", "contexts": ["government entities"], "source": "analysis"},
    "identified_other": {"state": "identified", "contexts": ["GCC region", "listed banks"], "source": "analysis"},
    "none_analysis": {"state": "none", "contexts": [], "source": "analysis"},
    "uncertain": {"state": "uncertain", "contexts": ["government entities"], "source": "analysis"},
    "recruiter_identified": {"state": "identified", "contexts": ["ministries"], "source": "recruiter"},
    "recruiter_none": {"state": "none", "contexts": [], "source": "recruiter"},
    "malformed_str": "government entities",
    "malformed_obj": {"state": 7, "contexts": None},
}
AUDIT_VARIANTS = {
    "audit_current": {"schema": "qc_audit_v1", "current": {"source": "analysis", "jd_sha256": "a" * 64,
                                                           "prompt_version": "qc-1"}, "latest_run": None},
    "audit_recruiter": {"schema": "qc_audit_v1", "current": {"source": "recruiter", "provenance": "recruiter_edited",
                                                             "user_id": "u", "changed_at": "t"}},
    "audit_latest_run": {"schema": "qc_audit_v1", "current": None,
                         "latest_run": {"status": "ok", "result": {"state": "identified",
                                                                   "contexts": ["government entities"]}}},
    "audit_failed_run": {"schema": "qc_audit_v1", "latest_run": {"status": "failed_technical", "error": "x"}},
    "audit_malformed": ["not", "an", "object"],
}


def _variants():
    out = {"absent": copy.deepcopy(BASE)}
    for k, qc in QC_VARIANTS.items():
        a = copy.deepcopy(BASE)
        a["experience"]["qualifying_context"] = copy.deepcopy(qc)
        out[f"qc_{k}"] = a
    for k, au in AUDIT_VARIANTS.items():
        a = copy.deepcopy(BASE)
        a["qualifying_context_audit"] = copy.deepcopy(au)
        out[k] = a
        both = copy.deepcopy(a)
        both["experience"]["qualifying_context"] = copy.deepcopy(QC_VARIANTS["recruiter_identified"])
        out[f"{k}+qc"] = both
    return out


VARIANTS = _variants()


def _oracle(analysis):
    (c,) = enumerate_experience_criteria("J1", analysis)
    return resp(hint_item(c.criterion_id, [(2, "Minimum 5 years as an Internal Auditor or Tax Accountant in "
                                               "government entities")], hints=2,
                          settings=[(2, "government entities")]))


def _observe(analysis, cache=None):
    crits = enumerate_experience_criteria("J1", analysis)
    req = clf.build_request(JDText(IND_JD), crits)
    client = FakeClient(_oracle(analysis))
    out = run(clf.classify_job("J1", IND_JD, analysis, client=client, cache=cache))
    return {"criteria": [dataclasses.asdict(c) for c in crits], "ids": [c.criterion_id for c in crits],
            "user_message": req.user_message, "system": clf.S1_SYSTEM_PROMPT, "input_hash": req.input_hash,
            "cache_key": clf.s1_cache_key(req),
            "messages": [r["messages"] for r in client.requests], "calls": len(client.requests),
            "cache_hit": out.meta.get("cache_hit"),
            "artifacts": [a.to_dict() for a in out.artifacts]}


class TestIndependence:
    def test_variants_cover_the_required_space(self):
        assert len(VARIANTS) == 1 + len(QC_VARIANTS) + 2 * len(AUDIT_VARIANTS)
        assert {"identified", "none", "uncertain"} <= {q.get("state") for q in QC_VARIANTS.values()
                                                        if isinstance(q, dict)}
        assert {"analysis", "recruiter"} <= {q.get("source") for q in QC_VARIANTS.values() if isinstance(q, dict)}

    @pytest.mark.parametrize("name", sorted(VARIANTS))
    def test_qc_never_changes_s1_input_prompt_or_cache_identity(self, name):
        base, other = _observe(VARIANTS["absent"]), _observe(VARIANTS[name])
        for k in ("criteria", "ids", "user_message", "system", "input_hash", "cache_key", "messages", "calls",
                  "cache_hit", "artifacts"):
            assert other[k] == base[k], (name, k)
        assert "qualifying" not in other["user_message"] and "qualifying" not in json.dumps(other["messages"])

    def test_cache_hit_behaviour_identical(self):
        cache = clf.InMemoryS1Cache()
        first = _observe(VARIANTS["absent"], cache=cache)
        assert first["calls"] == 1 and first["cache_hit"] is False
        for name, a in VARIANTS.items():
            client = FakeClient()                                           # any call would fail (empty queue)
            out = run(clf.classify_job("J1", IND_JD, a, client=client, cache=cache))
            assert client.requests == [] and out.meta["cache_hit"] is True, name
            assert [x.semantic_dict() for x in out.artifacts] == [
                sc.S1Artifact.from_dict(d).semantic_dict() for d in first["artifacts"]], name
        assert len(cache.store) == 1

    def test_repair_call_messages_identical(self):
        def two_calls(analysis):
            (c,) = enumerate_experience_criteria("J1", analysis)
            bad = resp(hint_item(c.criterion_id, [(2, "Minimum 5 years as an Internal Auditor")], hints=2,
                                 duration="D9"))
            client = FakeClient(bad, _oracle(analysis))
            run(clf.classify_job("J1", IND_JD, analysis, client=client))
            return [r["messages"] for r in client.requests]
        base = two_calls(VARIANTS["absent"])
        assert len(base) == 2
        for name in ("qc_identified_analysis", "qc_recruiter_none", "audit_current+qc", "audit_malformed"):
            assert two_calls(VARIANTS[name]) == base, name

    def test_control_non_qc_fields_do_change_the_request(self):
        base = _observe(VARIANTS["absent"])
        years = copy.deepcopy(BASE)
        years["experience"]["minimum_years"] = 7
        roles = copy.deepcopy(BASE)
        roles["experience"]["relevant_roles"] = [AUD]
        for a in (years, roles):
            crits = enumerate_experience_criteria("J1", a)
            req = clf.build_request(JDText(IND_JD), crits)
            assert (req.user_message != base["user_message"] or [c.criterion_id for c in crits] != base["ids"])
            assert clf.s1_cache_key(req) != base["cache_key"] or [c.criterion_id for c in crits] != base["ids"]
        r = clf.build_request(JDText(IND_JD), enumerate_experience_criteria("J1", roles))
        assert r.user_message != base["user_message"] and clf.s1_cache_key(r) != base["cache_key"]

    # static guards: the S1 interpretation path never reads the qualifying-context service, keys or agreement
    S1_PATH = ("classifier.py", "criteria.py", "validator.py", "repair.py", "jd_text.py", "durations.py")

    @pytest.mark.parametrize("fname", S1_PATH)
    def test_static_guard(self, fname):
        src = (BACKEND / "services" / "s1_requirements" / fname).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        for m in mods:
            for banned in ("qualifying_context", "context_agreement", "agreement", "resolver"):
                assert banned not in m, (fname, m)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docs]                                   # documentation may name the rule
        for s_ in strings:
            assert "qualifying_context" not in s_ and "context_resolution" not in s_, (fname, s_[:80])
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & {"qualifying_context", "context_resolution", "ContextResolution"}, fname

    def test_entry_points_take_no_qc_parameter(self):
        import inspect
        assert list(inspect.signature(clf.classify_job).parameters) == [
            "job_id", "jd_text", "analysis_json", "recruiter_fields", "client", "model", "cache"]
        assert list(inspect.signature(clf.build_request).parameters) == ["jd", "criteria"]
        assert list(inspect.signature(enumerate_experience_criteria).parameters) == ["job_id", "analysis_json"]

    def test_mutation_check_detects_a_leak(self, monkeypatch):
        """The independence tests are able to fail: leak the QC object into the request and they see it."""
        orig = clf.build_request

        def leaky(jd, criteria, _a={}):
            req = orig(jd, criteria)
            req.payload["leak"] = _a.get("qc")
            return req
        base = _observe(VARIANTS["absent"])
        monkeypatch.setattr(clf, "build_request", leaky)
        leaky.__defaults__[0]["qc"] = QC_VARIANTS["identified_analysis"]
        other = _observe(VARIANTS["qc_identified_analysis"])
        assert other["user_message"] != base["user_message"] and other["cache_key"] != base["cache_key"]
        assert other["messages"] != base["messages"]


# ── B. prompt semantics ─────────────────────────────────────────────────────

class TestPromptS16:
    def test_semantics(self):
        p = clf.S1_SYSTEM_PROMPT
        for frag in ("geographic scope", "organisation type", "sector or domain", "project type", "work setting",
                     "the location of this vacancy", "duties or responsibilities of this job",
                     "\"multinational\", \"international\" or \"global\" when they describe the hiring company",
                     "\"dynamic\", \"fast-paced\", \"multicultural team\"", "ONE contiguous restriction is ONE context",
                     "Never split an \"or\"", "ambiguous_context_scope", "\"settings\": a list of 0 to 5 contexts",
                     "PART DURATION", "do NOT report \"ambiguous_context_scope\" for this",
                     "Never for a part of the experience with its own duration (that is a compound requirement)"):
            assert frag in p, frag
        scope_rule = next(ln for ln in p.splitlines() if ln.strip().startswith("- AMBIGUOUS SCOPE"))
        assert "including" not in scope_rule and "overall" not in scope_rule     # compounds are not context ambiguity
        assert "Never use the hiring company's name, a location" not in p
        assert "a location" not in p

    def test_prompt_examples_avoid_the_p4_fixture_vocabulary(self):
        p = clf.S1_SYSTEM_PROMPT.lower()
        for word in ("gcc", "islamic", "government entities", "infrastructure", "multinational organisations"):
            assert word not in p, word
