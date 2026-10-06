"""
S1 RequirementSpec foundation — offline tests (fake AI clients only; no OpenAI,
no database). All JD texts here are SYNTHETIC; the JOB-2026-0031 case uses the
real D-01 criterion text / analysis_json shape with a synthetic JD.
"""
import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.experience_accounting import RequirementSpec
from services.s1_requirements import assemble as asm
from services.s1_requirements import classifier as clf
from services.s1_requirements import schema as sc
from services.s1_requirements.criteria import (
    criterion_id, enumerate_experience_criteria, out_of_scope_items,
)
from services.s1_requirements.durations import parse_durations
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.validator import validate_response

BACKEND = Path(__file__).resolve().parent.parent


# ── fake AI client ───────────────────────────────────────────────────────────

class FakeClient:
    """Returns queued (content, finish_reason) items; an Exception item is raised."""

    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

        async def create(**kw):
            self.requests.append(kw)
            item = self.items.pop(0)
            if isinstance(item, Exception):
                raise item
            content, finish = item if isinstance(item, tuple) else (item, "stop")
            msg = SimpleNamespace(content=content)
            usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def run(coro):
    loop = asyncio.new_event_loop()          # never clear the main-thread loop
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def ai(*results) -> str:
    return json.dumps({"criteria": list(results)}, ensure_ascii=False)


def res(cid, policy, spans, targets=(), setting=None, duration=None, ambiguity=(), note="n"):
    return {"criterion_id": cid, "policy": policy,
            "requirement_spans": [{"line": ln, "text": t} for ln, t in spans],
            "targets": list(targets), "setting": setting, "duration": duration,
            "ambiguity": list(ambiguity), "note": note}


LEGACY_BASIS = {"explicit_role": "targets", "functional": "targets", "mixed": "targets", "sector": "sector",
                "pure_duration": "total_experience"}

# Reviewed word alignments for the legitimate equivalent mappings used by pre-s1-5 tests (hint, jd_span) ->
# alignment; the legacy fill adds them only to tests that do not state an alignment themselves.
TR, SA, FO, AB = "translation", "same", "form", "abbreviation"
TEST_ALIGNMENTS = {
    ("Construction Project Manager", "كمدير مشروع إنشائي"): [("Construction", "إنشائي", TR), ("Project", "مشروع", TR),
                                                         ("Manager", "كمدير", TR)],
    ("Construction Project Manager", "مدير مشروع إنشائي"): [("Construction", "إنشائي", TR), ("Project", "مشروع", TR),
                                                        ("Manager", "مدير", TR)],
    ("Assistant Project Manager", "مساعد مدير مشروع"): [("Assistant", "مساعد", TR), ("Project", "مشروع", TR),
                                                    ("Manager", "مدير", TR)],
    ("PM", "Project Manager"): [("PM", "Project Manager", AB)],
    ("software implementation", "implementing software"): [("software", "software", SA),
                                                           ("implementation", "implementing", FO)],
    ("database administration", "administering databases"): [("database", "databases", FO),
                                                              ("administration", "administering", FO)],
    ("Assistant Project Manager", "Assistant PM"): [("Assistant", "Assistant", SA), ("Project Manager", "PM", AB)],
    ("Site Engineer", "Site Engineer"): [("Site", "Site", SA), ("Engineer", "Engineer", SA)],
    ("HR Manager", "Human Resources Manager"): [("HR", "Human Resources", AB), ("Manager", "Manager", SA)],
}


def alignment(hint, jd_text):
    return [{"hint": h, "jd": j, "relation": r} for h, j, r in TEST_ALIGNMENTS[(hint, jd_text)]]


def _vague_restriction(it):
    for sp in it.get("requirement_spans") or []:
        for word in ("relevant", "related", "similar"):
            if isinstance(sp, dict) and word in (sp.get("text") or "").split():
                return {"line": sp["line"], "text": word, "kind": "vague"}
    return None


def with_match(raw, jd, analysis, job_id):
    """Test-only legacy fill for outputs written before the current contract (explicit values are kept):
    ``match`` on hint targets (pre-s1-3): jd_span -> equivalent; hint verbatim in a requirement span -> exact
    (none when the criterion reports ambiguous_relevance, i.e. the embedded case); otherwise none.
    ``relevance_basis`` on hint-less criteria (pre-s1-4) from their old model policy (LEGACY_BASIS).
    s1-6 wire adapter (pre-s1-6 outputs, only when the item has no "settings" key): a hint criterion's single
    ``setting`` becomes ``settings`` ([] for null, [setting] otherwise, unchanged content); restriction kind
    "sector" is the s1-6 wire kind "context". Semantics are unchanged; tests of the s1-6 wire contract itself
    write "settings" explicitly and are therefore never adapted."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        return raw
    j = JDText(jd)
    by_id = {c.criterion_id: c for c in enumerate_experience_criteria(job_id, analysis)}
    for it in data["criteria"]:
        c = by_id.get(it.get("criterion_id")) if isinstance(it, dict) else None
        if c is None or not isinstance(it.get("targets"), list):
            continue
        hints = {f"T{i}": h for i, h in enumerate(c.target_hints, 1)}
        if not hints and "relevance_basis" not in it and it.get("policy") in LEGACY_BASIS:
            # pre-s1-4 outputs: the old model policy of a hint-less criterion -> its relevance_basis
            basis = LEGACY_BASIS[it["policy"]]
            if basis == "total_experience" and "ambiguous_relevance" in (it.get("ambiguity") or []):
                basis = "unspecified"
            it["relevance_basis"] = basis
        if not hints and "restrictions" not in it and "relevance_basis" in it:
            # pre-s1-5 outputs: relevance_basis + JD targets + setting -> typed restrictions
            basis = it.pop("relevance_basis")
            rs = [{"line": t["line"], "text": t["text"], "kind": t.get("type")}
                  for t in it.pop("targets", None) or [] if isinstance(t, dict) and "line" in t]
            st = it.pop("setting", None)
            if isinstance(st, dict) and basis in ("targets", "sector"):
                rs.append({"line": st["line"], "text": st["text"], "kind": "context"})
            if basis == "unspecified" and _vague_restriction(it):
                rs.append(_vague_restriction(it))
            it["restrictions"] = rs
        if "settings" not in it:                     # s1-6 wire adapter (see docstring)
            st = it.pop("setting", None)
            if hints:
                it["settings"] = [] if st is None else [st]
            elif st is not None:
                it["setting"] = st                   # kept: hint-less criteria must still reject it
            for r in it.get("restrictions") or []:
                if isinstance(r, dict) and r.get("kind") == "sector":
                    r["kind"] = "context"
        req = [j.span_on_line(sp.get("line"), sp.get("text", "")) for sp in it.get("requirement_spans") or []
               if isinstance(sp, dict)]
        req = [r for r in req if r is not None]
        for t in it.get("targets") or []:
            if not isinstance(t, dict) or "hint" not in t or "match" in t or t.get("hint") not in hints:
                continue
            if t.get("jd_span") is not None:
                t["match"] = "equivalent"
                key = (hints[t["hint"]], (t["jd_span"] or {}).get("text") if isinstance(t["jd_span"], dict) else None)
                if "alignment" not in t and key in TEST_ALIGNMENTS:
                    t["alignment"], t.setdefault("jd_extra", [])[:] = alignment(*key), []
            elif any(s.within(r) for s in j.find(hints[t["hint"]]) for r in req):
                t["match"] = "none" if "ambiguous_relevance" in (it.get("ambiguity") or []) else "exact"
            else:
                t["match"] = "none"
    return json.dumps(data, ensure_ascii=False)


def classify(jd, analysis, *responses, job_id="J1", **kw):
    responses = [with_match(r, jd, analysis, job_id) if isinstance(r, str) else
                 (with_match(r[0], jd, analysis, job_id), r[1]) if isinstance(r, tuple) else r
                 for r in responses]
    client = FakeClient(*responses)
    return client, run(clf.classify_job(job_id, jd, analysis, client=client, **kw))


# ── s1-6 / P4a: S2 views are fail-closed on the qualifying context ──────────

def with_resolution(art, *contexts, provenance="recruiter_confirmed"):
    """P4c STAND-IN for view-shape tests: an EXPLICIT, test-written context resolution (recruiter provenance),
    never derived from art.settings. S1 itself never creates one, so without it no view exists."""
    eff = sc.EffectiveContext("identified" if contexts else "none", tuple(contexts), provenance)
    detail = "recruiter" if provenance != "jd_verified" else ("agreed" if contexts else "agreed_none")
    return dataclasses.replace(art, context_resolution=sc.ContextResolution("resolved", detail, eff))


def views(art, *contexts, require_resolved=True, provenance="recruiter_confirmed"):
    """The S1 artefact alone has NO view (context_unresolved, also for a permissive caller); the views of the
    same artefact once an explicit resolution with these effective contexts is attached."""
    assert art.context_resolution is None
    for rr in (True, False):
        with pytest.raises(asm.S1ViewError) as e:
            asm.s2_views(art, require_resolved=rr)
        assert e.value.code in (asm.VIEW_CONTEXT_UNRESOLVED, None)      # None: an earlier hard block
    return asm.s2_views(with_resolution(art, *contexts, provenance=provenance), require_resolved=require_resolved)


def setting_texts(art):
    return [x.text for x in art.settings]


# ── JOB-2026-0031 fixture (real D-01 criterion, synthetic JD) ───────────────

JOB31_ANALYSIS = {
    "experience": {"minimum_years": 5,
                   "relevant_roles": ["Construction Project Manager", "Assistant Project Manager"]},
    "domain_knowledge": ["commercial construction"],
    "other_requirements": ["Valid driving licence"],
}
JOB31_DISPLAY = ("Minimum 5 years of experience in a relevant role "
                 "(Construction Project Manager or Assistant Project Manager)")
JOB31_JD = """Construction Project Manager
About the role
We deliver commercial construction projects across the region.

Requirements
- Minimum 5 years of experience as a Construction Project Manager or Assistant Project Manager.
- Bachelor's degree in Civil Engineering.
- Valid driving licence."""
JOB31_REQ = "Minimum 5 years of experience as a Construction Project Manager or Assistant Project Manager"


def job31_cid(job_id="JOB-2026-0031"):
    (c,) = enumerate_experience_criteria(job_id, JOB31_ANALYSIS)
    return c.criterion_id


def job31_ok(cid, **over):
    r = res(cid, "explicit_role", [(6, JOB31_REQ)],
            targets=[{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "role"}], duration="D1")
    r.update(over)
    return r


class TestJob2026_0031:
    def test_fixture_result(self):
        cid = job31_cid()
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        assert out.status == "ok" and len(client.requests) == 1
        (art,) = out.artifacts                                   # ONE business criterion
        assert art.display_text == JOB31_DISPLAY
        assert art.spec_status == sc.STATUS_RESOLVED and art.reasons == ()
        assert art.policy == "explicit_role"
        assert [(t.text, t.type, t.provenance) for t in art.targets] == [
            ("Construction Project Manager", "role", "jd_verified"),
            ("Assistant Project Manager", "role", "jd_verified")]
        assert all(t.jd_span.line == 6 for t in art.targets)
        assert art.required_years.value == 5 and art.required_years.provenance == "jd_verified"
        assert art.required_years.jd_span.text == "5 years"
        assert art.settings == ()                                 # domain_knowledge is never a context
        assert art.requirement_text == JOB31_REQ
        assert art.context_resolution is None                     # S1 never resolves the qualifying context
        (view,) = views(art)                                      # ONE homogeneous S2 view (explicit resolution)
        assert view == RequirementSpec(
            policy="explicit_role", required_years=5.0,
            targets=("Construction Project Manager", "Assistant Project Manager"), setting=None,
            # s1-6: the spec version covers the effective context, so it is the RESOLVED artefact's
            spec_version=with_resolution(art).spec_version, criterion_id=cid, criterion_text=JOB31_REQ,
            source_spans=("Construction Project Manager", "Assistant Project Manager"))
        # no candidate evaluation: the request carries JD + criteria only
        payload = json.loads(client.requests[0]["messages"][1]["content"].split("\n", 1)[1])
        assert set(payload) == {"s1_input_version", "jd_lines", "duration_candidates", "criteria"}
        assert "commercial construction" not in json.dumps(payload["criteria"])
        assert out.out_of_scope == [{"source_path": "analysis_json.other_requirements[0]",
                                     "text": "Valid driving licence", "reason": "phase1_out_of_scope"}]

    def test_view_criterion_text_still_masks_with_unchanged_s2_masking(self):
        from services.s2_experience.masking import mask_threshold
        cid = job31_cid()
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        (view,) = views(out.artifacts[0])
        assert mask_threshold(view.criterion_text) == (
            "Minimum [N] years of experience as a Construction Project Manager or Assistant Project Manager")


# ── 1-2 enumeration ──────────────────────────────────────────────────────────

PARITY_CASES = [
    JOB31_ANALYSIS,
    {"experience": {"minimum_years": 3, "relevant_roles": [], "requirement_type": "preferred"}},
    {"experience": {"minimum_years": 2.5, "relevant_roles": ["Nurse"], "requirement_type": "required"}},
    {"experience": {"minimum_years": 0, "relevant_roles": ["Nurse", "", "Midwife", "Nurse"]}},
    {"experience": {"minimum_years": 4, "relevant_roles": ["A", "B", "C"]}},
    {"experience": {}}, {}, {"experience": None},
]


class TestEnumeration:
    @pytest.mark.parametrize("a", PARITY_CASES)
    def test_parity_with_flatten_criteria(self, a):
        from services.llm_criteria_mapper import _flatten_criteria
        mine = [(c.display_text, c.required) for c in enumerate_experience_criteria("J", a)]
        real = [(c["text"], c["required"]) for c in _flatten_criteria(a) if c["dimension"] == "experience"]
        assert mine == real

    def test_source_paths_and_hints(self):
        (c,) = enumerate_experience_criteria("J", JOB31_ANALYSIS)
        assert (c.source_path, c.kind, c.min_years, c.target_hints) == (
            "analysis_json.experience[years_and_roles]", "years_and_roles", 5,
            ("Construction Project Manager", "Assistant Project Manager"))
        (y,) = enumerate_experience_criteria("J", PARITY_CASES[1])
        assert (y.source_path, y.required, y.target_hints) == ("analysis_json.experience[years_only]", False, ())
        ro = enumerate_experience_criteria("J", PARITY_CASES[3])
        assert [(r.source_path, r.display_text, r.has_years) for r in ro] == [
            ("analysis_json.experience.relevant_roles[0]", "Nurse", False),
            ("analysis_json.experience.relevant_roles[2]", "Midwife", False),
            ("analysis_json.experience.relevant_roles[3]", "Nurse", False)]
        assert len({r.criterion_id for r in ro}) == 3            # duplicate role text, distinct paths

    def test_other_requirements_out_of_scope_only(self):
        a = {"other_requirements": ["5 years in the banking sector", ""]}
        assert enumerate_experience_criteria("J", a) == []
        assert out_of_scope_items(a) == [{"source_path": "analysis_json.other_requirements[0]",
                                          "text": "5 years in the banking sector", "reason": "phase1_out_of_scope"}]
        _, out = classify("x", a)                                  # no criteria -> no AI call
        assert out.status == "empty" and out.artifacts == [] and len(out.out_of_scope) == 1

    def test_stable_criterion_id(self):
        a = enumerate_experience_criteria("J1", JOB31_ANALYSIS)[0].criterion_id
        assert a == enumerate_experience_criteria("J1", dict(JOB31_ANALYSIS))[0].criterion_id
        assert a == criterion_id("J1", "analysis_json.experience[years_and_roles]", JOB31_DISPLAY)
        assert a != enumerate_experience_criteria("J2", JOB31_ANALYSIS)[0].criterion_id
        assert len(a) == 16


# ── 8-13 durations ───────────────────────────────────────────────────────────

def one(text):
    (m,) = parse_durations(text)
    return m


class TestDurations:
    @pytest.mark.parametrize("text, value, unit, years, bound, span", [
        ("Minimum 5 years of experience", 5, "years", 5, "exact", "5 years"),
        ("at least three years in sales", 3, "years", 3, "exact", "three years"),
        ("ten yrs experience", 10, "years", 10, "exact", "ten yrs"),
        ("1 year of experience", 1, "years", 1, "exact", "1 year"),
        ("five (5) years of experience", 5, "years", 5, "exact", "five (5) years"),
        ("twenty years", 20, "years", 20, "exact", "twenty years"),
    ])
    def test_english(self, text, value, unit, years, bound, span):
        m = one(text)
        assert (m.value, m.unit, m.years, m.bound, m.text) == (value, unit, years, bound, span)
        assert text[m.start:m.end] == m.text

    @pytest.mark.parametrize("text, years, bound, span", [
        ("خبرة 5 سنوات في القطاع المصرفي", 5, "exact", "5 سنوات"),
        ("خبرة لا تقل عن خمس سنوات", 5, "exact", "خمس سنوات"),
        ("خبرة لا تقل عن سنتين", 2, "exact", "سنتين"),
        ("خبرة عامين", 2, "exact", "عامين"),
        ("عشرة أعوام", 10, "exact", "عشرة أعوام"),
        ("سنة واحدة على الأقل", 1, "exact", "سنة واحدة"),
        ("ثلاث سنوات أو أكثر", 3, "at_least", "ثلاث سنوات أو أكثر"),
        ("سبع سنوات فأكثر", 7, "at_least", "سبع سنوات فأكثر"),
    ])
    def test_arabic(self, text, years, bound, span):
        m = one(text)
        assert (m.years, m.bound, m.text, m.unit) == (years, bound, span, "years")
        assert text[m.start:m.end] == span

    @pytest.mark.parametrize("text, value, unit, years", [
        ("18 months of call-centre experience", 18, "months", 1.5),
        ("6 months", 6, "months", 0.5),
        ("1.5 years", 1.5, "years", 1.5),
        ("خبرة شهرين", 2, "months", 0.1667),
        ("ستة أشهر", 6, "months", 0.5),
    ])
    def test_months_and_fractions(self, text, value, unit, years):
        m = one(text)
        assert (m.value, m.unit, m.years) == (value, unit, years)

    @pytest.mark.parametrize("text, lo, hi, span", [
        ("3-5 years experience", 3, 5, "3-5 years"),
        ("3 – 5 years", 3, 5, "3 – 5 years"),
        ("three to five years", 3, 5, "three to five years"),
        ("من 3 إلى 5 سنوات", 3, 5, "3 إلى 5 سنوات"),
        ("12-18 months", 1.0, 1.5, "12-18 months"),
    ])
    def test_ranges_keep_lower_bound(self, text, lo, hi, span):
        m = one(text)
        assert (m.years, m.upper_years, m.bound, m.is_range, m.text) == (lo, hi, "range", True, span)

    @pytest.mark.parametrize("text, span", [("5+ years", "5+ years"), ("10+ yrs of experience", "10+ yrs"),
                                            ("5 or more years", "5 or more years"), ("٥+ سنوات", "٥+ سنوات")])
    def test_plus_is_lower_bound(self, text, span):
        m = one(text)
        assert (m.bound, m.open_ended, m.text) == ("at_least", True, span)
        assert m.years in (5, 10)

    def test_arabic_indic_digits_keep_offsets(self):
        for text, span, years in [("خبرة ٥ سنوات", "٥ سنوات", 5), ("خبرة ۷ سال سنوات", "۷ سال سنوات", None),
                                  ("خبرة ١٠ سنوات", "١٠ سنوات", 10), ("٣-٥ سنوات", "٣-٥ سنوات", 3)]:
            found = parse_durations(text)
            if years is None:
                assert found == []                                  # digit not followed by a unit
                continue
            (m,) = found
            assert (m.text, m.years) == (span, years) and text[m.start:m.end] == span

    @pytest.mark.parametrize("text", ["Experience as a Site Engineer", "2019 - 2021", "Valid driving licence",
                                      "", None, "years of experience", "Level 5 certificate"])
    def test_no_duration(self, text):
        assert parse_durations(text) == []

    def test_multiple_in_order(self):
        got = [m.text for m in parse_durations("5 years overall, of which 2 years in banking")]
        assert got == ["5 years", "2 years"]


# ── 14-15 JD spans / lookup ─────────────────────────────────────────────────

class TestJDText:
    JD = "Title\n\n- Minimum 5 years as a  Project Manager.\n- Assistant Project Manager welcome\tteam"

    def test_stable_numbering_and_verbatim_spans(self):
        jd = JDText(self.JD)
        assert [d["line"] for d in jd.numbered()] == [1, 3, 4]       # blank line keeps its number
        sp = jd.span_on_line(3, "minimum 5 years as a project manager")
        assert sp.text == "Minimum 5 years as a  Project Manager"    # original text, double space kept
        assert jd.lines[2][sp.start:sp.end] == sp.text
        assert jd.span_on_line(3, "Senior Project Manager") is None
        assert jd.span_on_line(99, "x") is None
        assert jd.lines[3] == "- Assistant Project Manager welcome team"   # tab -> space

    def test_find_is_exact_and_word_bounded(self):
        jd = JDText(self.JD)
        assert [(s.line, s.text) for s in jd.find("Project Manager")] == [(3, "Project Manager"),
                                                                          (4, "Project Manager")]
        assert [s.line for s in jd.find("assistant project manager")] == [4]
        assert jd.find("Project Mgr") == [] and jd.find("Manage") == []      # no fuzzy / partial words

    def test_duration_candidates(self):
        jd = JDText(self.JD + "\n- 2-3 years in construction")
        assert [(d, ln, m.text) for d, ln, m in jd.durations()] == [("D1", 3, "5 years"), ("D2", 5, "2-3 years")]


# ── 3-7 policies via fake AI ────────────────────────────────────────────────

YEARS_ONLY = {"experience": {"minimum_years": 3, "relevant_roles": []}}


def only_cid(a, job_id="J1"):
    (c,) = enumerate_experience_criteria(job_id, a)
    return c.criterion_id


class TestPolicies:
    def test_typed_function_targets_from_hints(self):
        a = {"experience": {"minimum_years": 4, "relevant_roles": ["Quantity Surveying", "Cost Estimation"]}}
        jd = "- 4 years of experience in Quantity Surveying or Cost Estimation"
        cid = only_cid(a)
        _, out = classify(jd, a, ai(res(cid, "functional", [(1, jd[2:])],
                                        [{"hint": "T1", "type": "function"}, {"hint": "T2", "type": "function"}],
                                        duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.policy == "functional"
        assert {t.type for t in art.targets} == {"function"}
        (v,) = views(art)
        assert (v.policy, v.targets, v.required_years) == ("functional", ("Quantity Surveying", "Cost Estimation"), 4)

    def test_mixed_groups_into_two_homogeneous_views(self):
        jd = "Requirements\n- At least 3 years as a Procurement Officer or in contract management in the energy sector"
        cid = only_cid(YEARS_ONLY)
        line = jd.splitlines()[1][2:]
        _, out = classify(jd, YEARS_ONLY, ai(res(
            cid, "mixed", [(2, line)],
            [{"line": 2, "text": "Procurement Officer", "type": "role"},
             {"line": 2, "text": "contract management", "type": "function"}],
            setting={"line": 2, "text": "energy sector"}, duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved"
        assert [(t.target_id, t.provenance) for t in art.targets] == [("J1", "jd_asserted"), ("J2", "jd_asserted")]
        assert setting_texts(art) == ["energy sector"]
        # the view's setting is the EFFECTIVE context of the (explicit) resolution, never art.settings
        role, func = views(art, "energy sector")
        assert (role.policy, role.targets, func.policy, func.targets) == (
            "explicit_role", ("Procurement Officer",), "functional", ("contract management",))
        art_r = with_resolution(art, "energy sector")
        for v in (role, func):                                   # shared identity / N / setting / text
            assert (v.criterion_id, v.spec_version, v.required_years, v.setting, v.criterion_text) == (
                cid, art_r.spec_version, 3, "energy sector", line)

    def test_pure_duration(self):
        jd = "- 3+ years of professional experience"
        cid = only_cid(YEARS_ONLY)
        _, out = classify(jd, YEARS_ONLY, ai(res(cid, "pure_duration", [(1, "3+ years of professional experience")],
                                                 duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.required_years.parsed["bound"] == "at_least"
        (v,) = views(art)
        assert (v.policy, v.targets, v.setting, v.required_years) == ("pure_duration", (), None, 3)

    def test_sector(self):
        a = {"experience": {"minimum_years": 2, "relevant_roles": []}}
        jd = "- 2 years of experience in the banking sector"
        cid = only_cid(a)
        _, out = classify(jd, a, ai(res(cid, "sector", [(1, jd[2:])], setting={"line": 1, "text": "banking sector"},
                                        duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and setting_texts(art) == ["banking sector"]
        (v,) = views(art, "banking sector")
        assert (v.policy, v.targets, v.setting, v.source_spans) == ("sector", ("banking sector",), None,
                                                                    ("banking sector",))
        with pytest.raises(asm.S1ViewError):                      # a sector view never exists without its context
            asm.s2_views(with_resolution(art))

    def test_role_only_criteria_have_no_years(self):
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        jd = "- Experience as a Registered Nurse"
        cid = only_cid(a)
        _, out = classify(jd, a, ai(res(cid, "explicit_role", [(1, jd[2:])], [{"hint": "T1", "type": "role"}])))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.required_years is None and art.required is False
        (v,) = views(art)
        assert v.required_years is None


# ── 16-21 targets / setting / provenance / status ───────────────────────────

class TestAuthority:
    def test_target_absent_from_jd_needs_confirmation(self):
        jd = JOB31_JD.replace("Assistant Project Manager", "Deputy PM")
        cid = job31_cid()
        _, out = classify(jd, JOB31_ANALYSIS, ai(job31_ok(cid, requirement_spans=[
            {"line": 6, "text": "Minimum 5 years of experience as a Construction Project Manager or Deputy PM"}])),
            job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert art.spec_status == "needs_confirmation"
        assert [(r.code, r.kind, r.field) for r in art.reasons] == [("target_not_in_jd", "business", "targets.T2")]
        t2 = art.targets[1]
        assert (t2.text, t2.provenance, t2.jd_span) == ("Assistant Project Manager", "original_ai", None)
        with pytest.raises(asm.S1ViewError):
            asm.s2_views(with_resolution(art))
        (v,) = views(art, require_resolved=False)
        assert v.targets[1] == "Assistant Project Manager"

    def test_n_mismatch_and_n_not_in_jd(self):
        cid = job31_cid()
        jd = JOB31_JD.replace("Minimum 5 years", "Minimum 7 years")
        req = JOB31_REQ.replace("5 years", "7 years")
        _, out = classify(jd, JOB31_ANALYSIS, ai(job31_ok(cid, requirement_spans=[{"line": 6, "text": req}])),
                          job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert [r.code for r in art.reasons] == ["n_mismatch"]
        assert (art.required_years.value, art.required_years.provenance, art.required_years.analysis_hint) == (
            7, "jd_asserted", 5)
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid, duration=None)), job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert [r.code for r in art.reasons] == ["n_not_in_jd"]
        assert (art.required_years.value, art.required_years.provenance) == (5, "original_ai")

    def test_setting_accepted_only_inside_requirement_span(self):
        a = {"experience": {"minimum_years": 5, "relevant_roles": ["Site Engineer"]}}
        jd = "We build oil and gas plants.\n- Minimum 5 years of experience as a Site Engineer on oil and gas projects"
        req = "Minimum 5 years of experience as a Site Engineer on oil and gas projects"
        cid = only_cid(a)
        ok = res(cid, "explicit_role", [(2, req)], [{"hint": "T1", "type": "role", "jd_span": None}],
                 setting={"line": 2, "text": "oil and gas projects"}, duration="D1")
        _, out = classify(jd, a, ai(ok))
        assert setting_texts(out.artifacts[0]) == ["oil and gas projects"]
        assert out.artifacts[0].field_provenance["settings"] == "jd_asserted"
        # an About-us line cannot be added as a requirement span to obtain a context (V-anchor; s1-6: a context
        # sentence attaches only AFTER an anchored line)
        about = res(cid, "explicit_role", [(2, req), (1, "We build oil and gas plants")],
                    [{"hint": "T1", "type": "role"}], setting={"line": 1, "text": "oil and gas plants"},
                    duration="D1")
        _, out = classify(jd, a, ai(about), ai(about))
        assert out.artifacts[0].spec_status == "failed_validation"
        assert any("contains no anchor" in e for e in out.validation["errors"])
        bad = job31_ok(job31_cid(), settings=[{"line": 3, "text": "commercial construction"}])   # outside its spans
        val = validate_response(ai(bad), JDText(JOB31_JD), enumerate_experience_criteria("JOB-2026-0031",
                                JOB31_ANALYSIS), {did: (ln, m) for did, ln, m in JDText(JOB31_JD).durations()})
        assert not val.ok and any("settings[0]" in e and "not inside" in e for e in val.errors)
        cid = job31_cid()
        free = job31_ok(cid, settings=["construction"])                              # free text
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(free), ai(free), job_id="JOB-2026-0031")
        assert out.artifacts[0].spec_status == "failed_validation"

    def test_domain_knowledge_never_becomes_setting(self):
        cid = job31_cid()
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        assert art.settings == () and art.field_provenance["settings"] is None
        sent = client.requests[0]["messages"][1]["content"]
        assert "domain_knowledge" not in sent and "commercial construction\"" not in sent
        # a setting not in the JD at all (e.g. copied from domain_knowledge) is rejected
        bad = job31_ok(cid, setting={"line": 6, "text": "commercial construction"})
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(bad), ai(bad), job_id="JOB-2026-0031")
        assert out.artifacts[0].spec_status == "failed_validation"
        assert any("not verbatim" in e for e in out.validation["errors"])

    def test_explicit_recruiter_provenance(self):
        cid = job31_cid()
        jd = JOB31_JD.replace("Assistant Project Manager", "Deputy PM")
        _, out = classify(jd, JOB31_ANALYSIS, ai(job31_ok(cid, requirement_spans=[
            {"line": 6, "text": "Minimum 5 years of experience as a Construction Project Manager or Deputy PM"}])),
            job_id="JOB-2026-0031", recruiter_fields={"experience.relevant_roles": "recruiter_edited"})
        (art,) = out.artifacts
        assert art.spec_status == "resolved"                      # recruiter outranks the JD
        assert {t.provenance for t in art.targets} == {"recruiter_edited"}
        assert art.required_years.provenance == "jd_verified"    # years were not marked
        assert art.requirement_text == JOB31_DISPLAY and art.field_provenance["requirement_text"] == "recruiter_edited"
        with pytest.raises(ValueError):
            run(clf.classify_job("J", jd, JOB31_ANALYSIS, client=FakeClient(),
                                 recruiter_fields={"experience.relevant_roles": "jd_verified"}))
        with pytest.raises(ValueError):
            asm.check_recruiter_fields({"skills.required": "recruiter_edited"})

    def test_recruiter_provenance_never_inferred(self):
        edited = {**JOB31_ANALYSIS, "experience": {"minimum_years": 5, "relevant_roles": [
            "Construction Project Manager", "Assistant Project Manager"]},
            "original_analysis_json": {"experience": {"minimum_years": 3, "relevant_roles": ["PM"]}}}
        cid = job31_cid()
        _, out = classify(JOB31_JD, edited, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        provs = {t.provenance for t in art.targets} | {art.required_years.provenance} | {
            v for v in art.field_provenance.values() if isinstance(v, str)}
        assert not provs & set(sc.RECRUITER_PROVENANCES)
        assert art.audit["recruiter_fields"] == {}
        import inspect
        assert "original_analysis" not in inspect.signature(clf.classify_job).parameters
        src = "".join(p.read_text(encoding="utf-8") for p in (BACKEND / "services" / "s1_requirements").glob("*.py"))
        assert "original_analysis_json\"" not in src and "get(\"original_analysis_json" not in src


ELSEWHERE_JD = """Construction Project Manager
Reports to the Assistant Project Manager.

Requirements
- Minimum 5 years of experience in project management roles."""
ELSEWHERE_REQ = "Minimum 5 years of experience in project management roles"


def elsewhere(cid, t1=None, t2=None):
    tg = [{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "role"}]
    if t1:
        tg[0]["jd_span"] = t1
    if t2:
        tg[1]["jd_span"] = t2
    return res(cid, "explicit_role", [(5, ELSEWHERE_REQ)], tg, duration="D1")


ARABIC_JD = "المتطلبات\n- خبرة 5 سنوات كمدير مشروع إنشائي أو مساعد مدير مشروع"
ARABIC_REQ = "خبرة 5 سنوات كمدير مشروع إنشائي أو مساعد مدير مشروع"
T1_AR = {"line": 2, "text": "كمدير مشروع إنشائي"}
T2_AR = {"line": 2, "text": "مساعد مدير مشروع"}


def arabic(cid, t1, t2):
    return res(cid, "explicit_role", [(2, ARABIC_REQ)],
               [{"hint": "T1", "type": "role", "jd_span": t1}, {"hint": "T2", "type": "role", "jd_span": t2}],
               duration="D1")


class TestTargetProvenance:
    """jd_verified only inside the criterion's requirement spans; mapping -> jd_asserted."""

    def test_verbatim_inside_requirement_span_is_jd_verified(self):
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == ["jd_verified", "jd_verified"]
        req = art.requirement_spans[0]
        assert all(t.jd_span.within(req) for t in art.targets)

    def test_match_elsewhere_in_jd_is_not_jd_verified(self):
        jd = JDText(ELSEWHERE_JD)
        assert [s.line for s in jd.find("Construction Project Manager")] == [1]     # present, but outside
        assert [s.line for s in jd.find("Assistant Project Manager")] == [2]
        _, out = classify(ELSEWHERE_JD, JOB31_ANALYSIS, ai(elsewhere(job31_cid())), job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert [(t.text, t.provenance, t.jd_span) for t in art.targets] == [
            ("Construction Project Manager", "original_ai", None),
            ("Assistant Project Manager", "original_ai", None)]
        assert [(r.code, r.field) for r in art.reasons] == [("target_not_in_jd", "targets.T1"),
                                                            ("target_not_in_jd", "targets.T2")]
        assert art.spec_status == "needs_confirmation"
        assert art.required_years.provenance == "jd_verified"                       # N unaffected

    def test_valid_ai_mapping_is_jd_asserted(self):
        _, out = classify(ARABIC_JD, JOB31_ANALYSIS, ai(arabic(job31_cid(), T1_AR, T2_AR)), job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.reasons == ()
        assert [(t.text, t.provenance, t.jd_span.text, t.jd_span.line) for t in art.targets] == [
            ("Construction Project Manager", "jd_asserted", "كمدير مشروع إنشائي", 2),
            ("Assistant Project Manager", "jd_asserted", "مساعد مدير مشروع", 2)]   # text unchanged
        (v,) = views(art)
        assert v.targets == ("Construction Project Manager", "Assistant Project Manager")
        assert v.source_spans == ("كمدير مشروع إنشائي", "مساعد مدير مشروع")

    def test_partial_mapping(self):
        _, out = classify(ARABIC_JD, JOB31_ANALYSIS, ai(arabic(job31_cid(), T1_AR, None)), job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert [t.provenance for t in art.targets] == ["jd_asserted", "original_ai"]
        assert [(r.code, r.field) for r in art.reasons] == [("target_not_in_jd", "targets.T2")]
        assert art.spec_status == "needs_confirmation"

    @pytest.mark.parametrize("mapping, frag", [
        ({"line": 1, "text": "Construction Project Manager"}, "not inside"),     # elsewhere in the JD
        ({"line": 5, "text": "programme management roles"}, "not verbatim"),
        ({"line": 5, "text": "ject manag"}, "not verbatim"),
        ("project management roles", "must be an object"),
    ])
    def test_invalid_mapping_rejected(self, mapping, frag):
        v = _validate([elsewhere(job31_cid(), t1=mapping)], jd=ELSEWHERE_JD)
        assert not v.ok and any(frag in e for e in v.errors), v.errors

    def test_mapping_survives_cache_round_trip(self):
        cache = clf.InMemoryS1Cache()
        _, first = classify(ARABIC_JD, JOB31_ANALYSIS, ai(arabic(job31_cid(), T1_AR, T2_AR)),
                            job_id="JOB-2026-0031", cache=cache)
        client, second = classify(ARABIC_JD, JOB31_ANALYSIS, job_id="JOB-2026-0031", cache=cache)
        assert client.requests == [] and second.artifacts[0].content_hash == first.artifacts[0].content_hash
        assert [t.provenance for t in second.artifacts[0].targets] == ["jd_asserted", "jd_asserted"]

    @pytest.mark.parametrize("prov", ["recruiter_edited", "recruiter_confirmed"])
    def test_recruiter_targets_stay_authoritative(self, prov):
        _, out = classify(ELSEWHERE_JD, JOB31_ANALYSIS, ai(elsewhere(job31_cid())), job_id="JOB-2026-0031",
                          recruiter_fields={"experience.relevant_roles": prov})
        (art,) = out.artifacts
        assert {t.provenance for t in art.targets} == {prov}
        assert [t.jd_span for t in art.targets] == [None, None]                    # elsewhere match not used
        assert art.spec_status == "resolved" and art.reasons == ()
        assert art.field_provenance["targets"] == {"T1": prov, "T2": prov}
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031",
                          recruiter_fields={"experience.relevant_roles": prov})
        assert {t.provenance for t in out.artifacts[0].targets} == {prov}


# ── s1-2: equivalence-only target mapping ───────────────────────────────────

def _case(hints, line, *, years=5, maps=(), types=None, ambiguity=(), setting=None, extra_lines=()):
    """One criterion; JD = 'Requirements' + '- <line>' (+ continuation lines). Returns (jd, analysis, response)."""
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "\n".join(["Requirements", "- " + line, *extra_lines])
    types = types or ["role"] * len(hints)
    maps = list(maps) + [None] * (len(hints) - len(maps))
    tg = [{"hint": f"T{i}", "type": ty, "jd_span": ({"line": 2, "text": m} if m else None)}
          for i, (ty, m) in enumerate(zip(types, maps), 1)]
    policy = "explicit_role" if set(types) == {"role"} else "functional" if set(types) == {"function"} else "mixed"
    spans = [(2, line)] + [(3 + i, ln.strip()) for i, ln in enumerate(extra_lines)]
    r = res(only_cid(a), policy, spans, tg, setting=setting,
            duration="D1" if parse_durations(line) else None, ambiguity=ambiguity)
    return jd, a, r


def run_case(*args, **kw):
    jd, a, r = _case(*args, **kw)
    _, out = classify(jd, a, ai(r), ai(r))          # second response = identical repair
    return out


def prov(out):
    return [(t.target_id, t.provenance) for t in out.artifacts[0].targets]


def codes(out):
    return [r.code for r in out.artifacts[0].reasons]


def assert_form_candidate(art, mapped_text=None):
    """s1-5.2: a structurally valid mapping using a form pair is an audited candidate, never evidence."""
    (t,) = art.targets
    assert (t.provenance, t.jd_span) == ("original_ai", None) and art.spec_status == "needs_confirmation"
    assert art.field_provenance["targets"] == {t.target_id: "original_ai"}
    assert [(r.code, r.kind, r.field) for r in art.reasons if r.field == f"targets.{t.target_id}"] == [
        ("equivalence_unverified", "business", f"targets.{t.target_id}")]
    assert "target_not_in_jd" not in [r.code for r in art.reasons]
    (m,) = art.audit["target_mappings"]
    assert m["used"] is False and m["trust"] == "unverified_form" and "form" in m["relations"]
    assert m["match"] == "equivalent" and m["alignment"] and "jd_extra" in m
    assert {"mapped_text", "line", "start", "end"} <= set(m) and art.audit["review_required"] == []
    if mapped_text is not None:
        assert m["mapped_text"] == mapped_text
    for require_resolved in (True, False):
        with pytest.raises(asm.S1ViewError):
            asm.s2_views(art, require_resolved=require_resolved)
    return m


def assert_abbreviation_candidate(art):
    """s1-5.2.2: an abbreviation expansion the JD does not define is an audited candidate, never evidence."""
    (t,) = art.targets
    assert (t.provenance, t.jd_span) == ("original_ai", None) and art.spec_status == "needs_confirmation"
    assert [(r.code, r.kind) for r in art.reasons if r.field == f"targets.{t.target_id}"] == [
        ("equivalence_unverified", "business")]
    assert "not defined in the JD" in art.reasons[0].detail and "target_not_in_jd" not in [r.code for r in art.reasons]
    (m,) = art.audit["target_mappings"]
    assert m["used"] is False and m["trust"] == "unverified_abbreviation" and m["jd_definitions"] == []
    assert art.audit["review_required"] == []
    for require_resolved in (True, False):
        with pytest.raises(asm.S1ViewError):
            asm.s2_views(art, require_resolved=require_resolved)
    return m


def rejected(out, frag):
    """The equivalent claim was refused: failed_validation, or (s1-5.1) withdrawn after the repair -> the
    target is never jd_asserted and the criterion is never resolved."""
    art = out.artifacts[0]
    if not any(frag in e for e in out.validation["errors"]):
        return False
    if art.spec_status == "failed_validation":
        return True
    return (art.audit.get("alignment_withdrawn") is True and art.spec_status == "needs_confirmation"
            and all(t.provenance != "jd_asserted" for t in art.targets))


class TestS12EquivalenceMapping:
    # positive mappings: same role/function, only wording/language/form differs
    def test_arabic_equivalent_is_jd_asserted_and_resolved(self):
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع إنشائي",
                       maps=["كمدير مشروع إنشائي"])
        art = out.artifacts[0]
        assert prov(out) == [("T1", "jd_asserted")]
        assert art.spec_status == "resolved" and art.reasons == ()          # jd_asserted alone can resolve
        assert art.targets[0].text == "Construction Project Manager"
        assert art.audit["target_mappings"] == [{
            "target_id": "T1", "target_text": "Construction Project Manager", "mapped_text": "كمدير مشروع إنشائي",
            "line": 2, "start": art.targets[0].jd_span.start, "end": art.targets[0].jd_span.end, "used": True,
            "match": "equivalent", "alignment": alignment("Construction Project Manager", "كمدير مشروع إنشائي"),
            "jd_extra": [], "relations": ["translation"], "trust": "trust_bearing", "jd_definitions": []}]
        assert art.audit["review_required"] == ["T1"]

    def test_arabic_mapping_may_start_after_attached_letter(self):
        # s1-4: the span may include the attached ك or start right after it (orthographic boundary only)
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع إنشائي",
                       maps=["مدير مشروع إنشائي"])
        assert prov(out) == [("T1", "jd_asserted")] and out.artifacts[0].spec_status == "resolved"
        assert out.artifacts[0].targets[0].jd_span.text == "مدير مشروع إنشائي"
        # a span starting inside the word (not after a proclitic) is still not whole words
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع إنشائي",
                       maps=["دير مشروع إنشائي"])
        assert rejected(out, "whole words")

    def test_arabic_generic_project_manager_is_not_mapped(self):
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع")
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        assert out.artifacts[0].spec_status == "needs_confirmation"
        assert out.artifacts[0].audit["target_mappings"] == [] and out.artifacts[0].audit["review_required"] == []

    def test_pm_with_explicit_expansion(self):
        out = run_case(["PM"], "Minimum 5 years as a Project Manager (P.M.)", maps=["Project Manager"])
        assert prov(out) == [("T1", "jd_asserted")] and out.artifacts[0].spec_status == "resolved"
        verbatim = run_case(["PM"], "Minimum 5 years as a Project Manager (PM)")        # "PM" itself in span
        assert prov(verbatim) == [("T1", "jd_verified")]

    def test_ambiguous_pm_not_mapped(self):
        out = run_case(["PM"], "Minimum 5 years as a P.M.")
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]

    def test_software_implementation_grammatical_form(self):
        out = run_case(["software implementation"], "Minimum 3 years of experience implementing software",
                       years=3, maps=["implementing software"], types=["function"])
        assert_form_candidate(out.artifacts[0])                # s1-5.2: a form pair is never trust-bearing

    def test_enterprise_software_is_not_jd_asserted(self):
        # JD narrows with a platform qualifier: AI must not map (null -> target_not_in_jd)
        out = run_case(["software implementation"], "Minimum 3 years implementing enterprise software systems",
                       years=3, types=["function"])
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        # target embedded in a longer qualified phrase: verbatim -> jd_verified, but AI reports ambiguity
        line = "Minimum 3 years of experience in enterprise software implementation"
        out = run_case(["software implementation"], line, years=3, types=["function"],
                       ambiguity=["ambiguous_relevance"])
        assert prov(out) == [("T1", "jd_verified")] and codes(out) == ["ambiguous_relevance"]
        assert out.artifacts[0].spec_status == "needs_confirmation"
        # and a mapping onto the longer phrase is rejected (V-sub-b)
        out = run_case(["software implementation"], line, years=3, types=["function"],
                       maps=["enterprise software implementation"])
        assert rejected(out, "adds words to the target")

    def test_database_administration(self):
        out = run_case(["database administration"], "Minimum 4 years of experience administering databases",
                       years=4, maps=["administering databases"], types=["function"])
        assert_form_candidate(out.artifacts[0])
        out = run_case(["database administration"], "Minimum 4 years of experience in database support",
                       years=4, types=["function"])
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]

    @pytest.mark.parametrize("hint, line, longer", [
        ("Project Manager", "Minimum 5 years as a Construction Project Manager", "Construction Project Manager"),
        ("Accountant", "Minimum 5 years as a Senior Accountant", "Senior Accountant"),
        ("Project Manager", "Minimum 5 years as an Assistant Project Manager", "Assistant Project Manager"),
    ])
    def test_embedded_in_longer_title_is_ambiguity(self, hint, line, longer):
        out = run_case([hint], line, ambiguity=["ambiguous_relevance"])
        assert prov(out) == [("T1", "jd_verified")] and codes(out) == ["ambiguous_relevance"]
        assert out.artifacts[0].spec_status == "needs_confirmation"
        assert rejected(run_case([hint], line, maps=[longer]), "adds words to the target")       # V-sub-b

    def test_construction_pm_vs_generic_pm(self):
        out = run_case(["Construction Project Manager"], "Minimum 5 years as a Project Manager")
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        out = run_case(["Construction Project Manager"], "Minimum 5 years as a Project Manager",
                       maps=["Project Manager"])
        assert rejected(out, "drops words of the target")                                      # V-sub-a

    @pytest.mark.parametrize("hint, line, ty", [
        ("project management", "Minimum 5 years of experience in project coordination", "function"),
        ("Project Manager", "Minimum 5 years working with the project management team", "role"),
    ])
    def test_related_wording_is_not_mapped(self, hint, line, ty):
        out = run_case([hint], line, types=[ty])
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        assert out.artifacts[0].spec_status == "needs_confirmation"


class TestS12ValidatorGuards:
    def test_v_m9_hints_cannot_share_a_generic_phrase(self):
        out = run_case(["Project Manager", "Assistant Project Manager"],
                       "Minimum 5 years of experience in project management roles",
                       maps=["project management roles", "project management roles"])
        assert rejected(out, "mapped to overlapping phrases")

    def test_v_m8_word_limit(self):
        line = ("Minimum 5 years as one who supervises and engineers the daily technical works on large "
                "active building sites")
        out = run_case(["Site Engineer"], line,
                       maps=["one who supervises and engineers the daily technical works on large active "
                             "building sites"])
        assert rejected(out, "at most 12 words")

    def test_v_m8_no_duration_or_setting_in_mapping(self):
        out = run_case(["Site Supervisor"], "Minimum 5 years as a site foreman", maps=["5 years as a site foreman"])
        assert rejected(out, "must not include the duration")
        out = run_case(["Site Supervisor"], "Minimum 5 years as a site foreman on oil and gas projects",
                       maps=["site foreman on oil and gas projects"],
                       setting={"line": 2, "text": "oil and gas projects"})
        assert rejected(out, "must not include the context")

    def test_v_m10_mapping_cannot_take_another_hints_text(self):
        line = "Minimum 5 years as a Construction Project Manager or Assistant PM"
        hints = ["Construction Project Manager", "Assistant Project Manager"]
        out = run_case(hints, line, maps=[None, "Construction Project Manager"])
        assert rejected(out, "overlaps the verbatim text of hint T1")
        ok = run_case(hints, line, maps=[None, "Assistant PM"])
        # s1-5.2.2: "PM" is not defined in this JD, so the abbreviation mapping is only an unverified candidate
        assert prov(ok) == [("T1", "jd_verified"), ("T2", "original_ai")]
        assert [m["target_id"] for m in ok.artifacts[0].audit["target_mappings"]] == ["T2"]
        assert ok.artifacts[0].audit["target_mappings"][0]["trust"] == "unverified_abbreviation"
        assert ok.artifacts[0].audit["review_required"] == []

    def test_v_anchor_wrapped_continuation_line_is_valid(self):
        out = run_case(["Site Engineer"], "Minimum 5 years of experience as a Site Engineer on",
                       extra_lines=["  oil and gas projects"], setting={"line": 3, "text": "oil and gas projects"})
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and setting_texts(art) == ["oil and gas projects"]
        assert [s.line for s in art.requirement_spans] == [2, 3]

    def test_v_anchor_rejects_unanchored_lines(self):
        jd = "We are a leading oil and gas contractor.\n\nRequirements\n- Minimum 5 years as a Site Engineer"
        a = {"experience": {"minimum_years": 5, "relevant_roles": ["Site Engineer"]}}
        r = res(only_cid(a), "explicit_role",
                [(4, "Minimum 5 years as a Site Engineer"), (1, "We are a leading oil and gas contractor")],
                [{"hint": "T1", "type": "role", "jd_span": None}],
                setting={"line": 1, "text": "oil and gas contractor"}, duration="D1")
        _, out = classify(jd, a, ai(r), ai(r))
        assert rejected(out, "contains no anchor")

    def test_mapping_unused_when_target_is_verbatim(self):
        out = run_case(["Site Engineer"], "Minimum 5 years as a Site Engineer", maps=["Site Engineer"])
        art = out.artifacts[0]
        assert prov(out) == [("T1", "jd_verified")]
        assert [m["used"] for m in art.audit["target_mappings"]] == [False]
        assert art.audit["review_required"] == []

    def test_audit_fields_do_not_change_content_hash(self):
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع إنشائي",
                       maps=["كمدير مشروع إنشائي"])
        art = out.artifacts[0]
        assert "audit" not in art.semantic_dict()
        assert sc.S1Artifact.from_dict(art.to_dict()).audit["review_required"] == ["T1"]


def _stmt(hints, jd_lines, spans, *, years=0, types=None, setting=None, ambiguity=(), policy=None):
    """spans: [(line, text, marked)]; returns ClassifyResult after one main (+ identical repair) response."""
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    types = types or ["role"] * len(hints)
    tg = [{"hint": f"T{i}", "type": ty, "jd_span": None} for i, ty in enumerate(types, 1)]
    pol = policy or ("explicit_role" if set(types) == {"role"} else "functional")
    r = res(only_cid(a), pol, [], tg, setting=setting, ambiguity=ambiguity)
    r["requirement_spans"] = [{"line": ln, "text": t, **({"experience_requirement": True} if m else {})}
                              for ln, t, m in spans]
    _, out = classify("\n".join(jd_lines), a, ai(r), ai(r))
    return out


class TestStatementAnchor:
    """No-duration requirements anchored only by the model's experience_requirement marker."""
    NURSE_JD = ["We are a leading hospital group.", "Requirements", "Experience in nursing is preferred."]

    def test_no_years_target_not_forced_to_map(self):
        out = _stmt(["Registered Nurse"], self.NURSE_JD, [(3, "Experience in nursing is preferred", True)])
        art = out.artifacts[0]
        assert out.status == "ok" and art.requirement_spans[0].text == "Experience in nursing is preferred"
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        assert art.spec_status == "needs_confirmation"
        assert art.audit["target_mappings"] == [] and art.audit["requirement_anchor"] == "statement"
        assert "requirement_not_in_jd" not in codes(out)

    def test_unmarked_statement_still_needs_an_anchor(self):
        out = _stmt(["Registered Nurse"], self.NURSE_JD, [(3, "Experience in nursing is preferred", False)])
        assert rejected(out, "contains no anchor")

    def test_verbatim_functional_target_uses_normal_anchor(self):
        out = _stmt(["nursing"], self.NURSE_JD, [(3, "Experience in nursing is preferred", False)],
                    types=["function"])
        art = out.artifacts[0]
        assert prov(out) == [("T1", "jd_verified")] and art.spec_status == "resolved"
        assert art.audit["requirement_anchor"] == "evidence"
        # a marker on an evidence-anchored span changes nothing
        marked = _stmt(["nursing"], self.NURSE_JD, [(3, "Experience in nursing is preferred", True)],
                       types=["function"])
        assert marked.artifacts[0].spec_status == "resolved"
        assert marked.artifacts[0].audit["requirement_anchor"] == "evidence"

    def test_sector_without_duration(self):
        jd = ["About us: we are a leading bank.", "Requirements", "Experience in the banking sector is preferred."]
        out = _stmt([], jd, [(3, "Experience in the banking sector is preferred", True)], years=3,
                    policy="sector", setting={"line": 3, "text": "banking sector"})
        art = out.artifacts[0]
        assert out.status == "ok" and setting_texts(art) == ["banking sector"] and art.policy == "sector"
        assert codes(out) == ["n_not_in_jd"] and art.spec_status == "needs_confirmation"
        assert art.audit["requirement_anchor"] == "restriction"          # s1-5: the sector restriction anchors

    @pytest.mark.parametrize("context", ["We are a leading bank", "Our projects include healthcare and education"])
    def test_context_lines_never_qualify(self, context):
        jd = [context + ".", "Requirements", "- Minimum 5 years as a Site Engineer"]
        hints = ["Site Engineer"]
        # unmarked context line as the only span: no anchor
        assert rejected(_stmt(hints, jd, [(1, context, False)], years=5), "contains no anchor")
        # marked context line added next to an anchored requirement: still rejected
        out = _stmt(hints, jd, [(3, "Minimum 5 years as a Site Engineer", False), (1, context, True)], years=5)
        assert rejected(out, "contains no anchor")

    def test_marked_context_alone_can_never_resolve(self):
        # residual risk: the marker is the model's declaration; the outcome is still unconfirmed
        jd = ["We are a leading bank.", "Requirements", "- Teamwork"]
        out = _stmt(["Bank Teller"], jd, [(1, "We are a leading bank", True)])
        assert out.artifacts[0].spec_status == "needs_confirmation" and codes(out) == ["target_not_in_jd"]

    def test_target_absent_requirement_not_in_jd_still_possible(self):
        out = _stmt(["Registered Nurse"], ["We are a leading bank.", "Requirements", "- Fluent English"], [],
                    ambiguity=["requirement_not_in_jd"])
        art = out.artifacts[0]
        assert codes(out) == ["target_not_in_jd", "requirement_not_in_jd"] and art.spec_status == "needs_confirmation"
        assert art.audit["requirement_anchor"] is None

    def test_at_most_one_marked_span_and_flag_type(self):
        jd = ["Experience in nursing is preferred.", "Experience in midwifery is an advantage."]
        out = _stmt(["Registered Nurse"], jd, [(1, "Experience in nursing is preferred", True),
                                               (2, "Experience in midwifery is an advantage", True)])
        assert rejected(out, "at most one requirement span")
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        r = res(only_cid(a), "explicit_role", [], [{"hint": "T1", "type": "role", "jd_span": None}])
        r["requirement_spans"] = [{"line": 1, "text": "Experience in nursing is preferred",
                                   "experience_requirement": "yes"}]
        _, bad = classify("\n".join(jd), a, ai(r), ai(r))
        assert rejected(bad, "experience_requirement must be true or false")

    def test_wrapped_continuation_of_statement_span(self):
        jd = ["Requirements", "Experience in community nursing or", "home healthcare is preferred."]
        out = _stmt(["Registered Nurse"], jd, [(2, "Experience in community nursing or", True),
                                               (3, "home healthcare is preferred", False)])
        assert out.status == "ok" and [s.line for s in out.artifacts[0].requirement_spans] == [2, 3]

    def test_recruiter_authority_is_what_resolves(self):
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        r = res(only_cid(a), "explicit_role", [], [{"hint": "T1", "type": "role", "jd_span": None}])
        r["requirement_spans"] = [{"line": 3, "text": "Experience in nursing is preferred",
                                   "experience_requirement": True}]
        _, out = classify("\n".join(self.NURSE_JD), a, ai(r), recruiter_fields={
            "experience.relevant_roles": "recruiter_confirmed"})
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and prov(out) == [("T1", "recruiter_confirmed")]

    def test_statement_anchor_survives_cache(self):
        cache = clf.InMemoryS1Cache()
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        r = res(only_cid(a), "explicit_role", [], [{"hint": "T1", "type": "role", "jd_span": None}])
        r["requirement_spans"] = [{"line": 3, "text": "Experience in nursing is preferred",
                                   "experience_requirement": True}]
        jd = "\n".join(self.NURSE_JD)
        classify(jd, a, ai(r), cache=cache)
        client, again = classify(jd, a, cache=cache)
        assert client.requests == [] and again.artifacts[0].audit["requirement_anchor"] == "statement"

    def test_assembler_invariant(self):
        from services.s1_requirements.validator import ParsedCriterion, ParsedTarget
        (c,) = enumerate_experience_criteria("J", {"experience": {"minimum_years": 0,
                                                                  "relevant_roles": ["Nursing"]}})
        jd = JDText("Experience in nursing is preferred.")
        sp = jd.span_on_line(1, "Experience in nursing is preferred")
        pc = ParsedCriterion(c.criterion_id, "functional", (sp,), (ParsedTarget("Nursing", "function", "T1"),),
                             (), None, (), "", statement_anchored=True)
        with pytest.raises(ValueError, match="cannot resolve"):
            asm.assemble_artifact(c, pc, jd, {}, run={})


class TestS12Versioning:
    def test_versions_and_fingerprint(self):
        assert (sc.S1_PROMPT_VERSION, sc.S1_VERSION) == ("s1-6.0", "1.5.0")
        assert (sc.S1_SCHEMA, sc.S1_INPUT_VERSION) == ("s1_requirement_spec_v3", "s1-in-1")
        # s1-6.0 was corrected before any evaluation (compound requirements are not ambiguous_context_scope):
        # af9f496563a4 -> 5b4172f709b2
        assert clf.prompt_fingerprint() == "5b4172f709b2"
        assert clf.prompt_fingerprint() not in ("af51355222e5", "e04beaeee3a2", "e64eb1a979e9", "c9b570d82f4e",
                                                "faad01d30b5c", "a0ae492a27e4", "b2a063ab2947", "4f22dddb117e",
                                                "af9f496563a4")

    def test_cache_identity_differs_from_s1_5_2(self, monkeypatch):
        # s1-6: old s1-5.2 cache entries (single setting) are unreachable
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-5.2")
        monkeypatch.setattr(clf, "S1_VERSION", "1.4.5")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "4f22dddb117e")
        assert clf.s1_cache_key(req) != k

    def test_cache_identity_differs_from_s1_5_1(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-5.1")
        monkeypatch.setattr(clf, "S1_VERSION", "1.4.1")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "b2a063ab2947")
        assert clf.s1_cache_key(req) != k

    def test_cache_identity_differs_from_s1_5(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-5")
        monkeypatch.setattr(clf, "S1_VERSION", "1.4.0")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "a0ae492a27e4")
        assert clf.s1_cache_key(req) != k

    def test_cache_identity_differs_from_s1_4(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k5 = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-4")
        monkeypatch.setattr(clf, "S1_VERSION", "1.3.0")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "faad01d30b5c")
        assert clf.s1_cache_key(req) != k5

    def test_cache_identity_differs_from_s1_1(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k2 = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-1")
        monkeypatch.setattr(clf, "S1_VERSION", "1.0.0")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "af51355222e5")
        assert clf.s1_cache_key(req) != k2

    def test_prompt_contract(self):
        p = clf.S1_SYSTEM_PROMPT
        for frag in ("SUBSTANTIALLY THE SAME", "\"none\" is always acceptable", "attached to the start of a word",
                     "report \"ambiguous_relevance\"",
                     "\"match\": \"exact\" | \"equivalent\" | \"none\"",
                     "absent, broader, narrower, adjacent, related, compatible, qualifier-changing, or uncertain",
                     "Never return a policy", "she is a <target>", "Nothing in the input tells you the type",
                     "A hint that differs from the JD wording is NOT a conflict",
                     # s1-5
                     "account for EVERY word on both sides", "one pair per word of the hint",
                     "\"jd_extra\": every other word of jd_span", "ANY material word means the JD phrase is NOT",
                     "A hint word with no JD counterpart means the JD phrase drops it",
                     "4 RESTRICTIONS", "EVERY phrase in the requirement statement that limits",
                     "Return [] ONLY when the requirement asks for general", "silently broadens",
                     "compound requirement",
                     # s1-6 experience contexts
                     "WHERE or IN WHAT SETTING otherwise relevant past experience must have been gained",
                     "geographic scope", "organisation type", "sector or domain", "project type", "work setting",
                     "ONE contiguous restriction is ONE context", "ALL of them must hold for the same past job",
                     "\"or\" inside a context stays inside it", "the location of this vacancy",
                     "rather than where the candidate's past experience was gained", "\"multicultural team\"",
                     "report \"ambiguous_context_scope\"", "\"kind\": \"role\" | \"function\" | \"context\" | \"vague\"",
                     "A separate sentence that restricts where THIS experience must have been gained",
                     "\"settings\": [], \"duration\""):
            assert frag in p, frag
        for gone in ("SHORTEST verbatim phrase", "MAY set jd_span", "display_text", "relevant role",
                     "RELEVANCE_BASIS", "\"total_experience\"", "relevance_basis",
                     # s1-6: categorical location / "multinational" exclusions and the single setting are gone
                     "a location, seniority", "\"fast-paced\", \"multinational\")", "5 SETTING (", "\"setting\": null",
                     "or the setting's scope is unclear", "\"kind\": \"role\" | \"function\" | \"sector\""):
            assert gone not in p, gone


def _raw(hints, line, targets, *, years=4, policy="explicit_role", ambiguity=(), job_id="J1"):
    """One-criterion JD + an explicit raw response (no match auto-fill)."""
    a = {"experience": {"minimum_years": years, "relevant_roles": list(hints)}}
    jd = "Requirements\n- " + line
    j = JDText(jd)
    (c,) = enumerate_experience_criteria(job_id, a)
    r = res(c.criterion_id, policy, [(2, line)], targets, ambiguity=ambiguity,
            duration="D1" if parse_durations(line) else None)
    return jd, a, ai(r), j, [c], {did: (ln, m) for did, ln, m in j.durations()}


def _val(*args, **kw):
    jd, a, raw, j, crits, durs = _raw(*args, **kw)
    return validate_response(raw, j, crits, durs)


LAB_LINE = "Minimum 4 years of experience as a Laboratory Technician"
HR_LINE = "Minimum 4 years of experience as a Human Resources Manager"
HR_ALIGN = {"alignment": alignment("HR Manager", "Human Resources Manager"), "jd_extra": []}


class TestS13Match:
    def test_match_required_and_enumerated(self):
        v = _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "jd_span": None}])
        assert any("match must be one of" in e for e in v.errors)
        v = _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "match": "partial",
                                                        "jd_span": None}])
        assert any("match must be one of" in e for e in v.errors)

    def test_exact_rules(self):
        ok = _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "match": "exact",
                                                         "jd_span": None}])
        assert ok.ok
        v = _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "match": "exact",
                                                        "jd_span": {"line": 2, "text": "Laboratory Technician"}}])
        assert any("match exact takes jd_span null" in e for e in v.errors)
        v = _val(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}])
        assert any("is not word for word" in e for e in v.errors)

    def test_equivalent_requires_span(self):
        v = _val(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "equivalent", "jd_span": None}])
        assert any("match equivalent requires jd_span" in e for e in v.errors)
        ok = _val(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "equivalent",
                                             "jd_span": {"line": 2, "text": "Human Resources Manager"}, **HR_ALIGN}])
        assert ok.ok

    def test_none_rules(self):
        v = _val(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "none",
                                            "jd_span": {"line": 2, "text": "Human Resources Manager"}}])
        assert any("match none takes jd_span null" in e for e in v.errors)
        assert _val(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "none", "jd_span": None}]).ok
        # verbatim hint + none needs ambiguous_relevance (embedded case)
        v = _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "match": "none",
                                                        "jd_span": None}])
        assert any("use match exact, or" in e for e in v.errors)
        assert _val(["Laboratory Technician"], LAB_LINE, [{"hint": "T1", "type": "role", "match": "none",
                                                           "jd_span": None}], ambiguity=["ambiguous_relevance"]).ok

    @pytest.mark.parametrize("match, span, prov, codes_", [
        ("exact", None, "jd_verified", []),
        # s1-5.2.2: HR is not defined in the JD -> an unverified abbreviation candidate
        ("equivalent", {"line": 2, "text": "Human Resources Manager"}, "original_ai", ["equivalence_unverified"]),
        ("none", None, "original_ai", ["target_not_in_jd"]),
    ])
    def test_provenance_unchanged_by_match(self, match, span, prov, codes_):
        hint, line = ("Laboratory Technician", LAB_LINE) if match == "exact" else ("HR Manager", HR_LINE)
        extra = HR_ALIGN if match == "equivalent" else {}
        jd, a, raw, *_ = _raw([hint], line, [{"hint": "T1", "type": "role", "match": match, "jd_span": span, **extra}])
        client = FakeClient(raw)
        out = run(clf.classify_job("J1", jd, a, client=client))
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == [prov] and [r.code for r in art.reasons] == codes_
        assert art.audit["target_matches"] == {"T1": match}

    def test_model_policy_never_controls_the_result(self):
        line = "Minimum 4 years of payroll administration experience"
        tg = [{"hint": "T1", "type": "function", "match": "exact", "jd_span": None}]
        jd, a, raw, *_ = _raw(["payroll administration"], line, tg, policy="explicit_role")
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw)))
        art = out.artifacts[0]
        assert out.meta["calls"] == 1 and art.spec_status == "resolved"          # no repair needed
        assert art.policy == "functional" and art.field_provenance["policy"] == "s1_interpreted"
        assert art.audit["policy_derivation"] == "from_types" and art.audit["ai"]["model_policy"] == "explicit_role"

    def test_repair_note_is_scoped(self):
        note = clf.repair_note(["x"])
        assert "ONLY the fields, targets and restrictions named in these errors will be taken" in note
        assert "Never drop a restriction" in note
        assert "Never return a policy" in note and "mixed" not in note

    def test_match_survives_cache(self):
        jd, a, raw, *_ = _raw(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "equivalent",
                                                          "jd_span": {"line": 2, "text": "Human Resources Manager"},
                                                          **HR_ALIGN}])
        cache = clf.InMemoryS1Cache()
        run(clf.classify_job("J1", jd, a, client=FakeClient(raw), cache=cache))
        again = run(clf.classify_job("J1", jd, a, client=FakeClient(), cache=cache))
        assert again.artifacts[0].audit["target_matches"] == {"T1": "equivalent"}


# ── s1-4: derived policy, relevance_basis, scoped repair, Arabic boundary, neutral input ───────────────

def _job(roles, years, lines):
    a = {"experience": {"minimum_years": years, "relevant_roles": list(roles)}}
    crits = enumerate_experience_criteria("J1", a)
    return a, "\n".join(lines), crits


def _resp(*items):
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


def _run_raw(jd, a, *raws):
    client = FakeClient(*raws)
    return client, run(clf.classify_job("J1", jd, a, client=client))


MIXED_LINE = "At least 3 years as a Payroll Officer or in payroll administration"
MIXED_JD = ["Requirements", "- " + MIXED_LINE + "."]


def _mixed_item(cid, **over):
    it = {"criterion_id": cid, "requirement_spans": [{"line": 2, "text": MIXED_LINE}],
          "restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "role"},
                           {"line": 2, "text": "payroll administration", "kind": "function"}],
          "duration": "D1", "ambiguity": [], "note": "n"}
    it.update(over)
    return it


class TestS14PolicyDerivation:
    @pytest.mark.parametrize("types, policy", [
        (["role", "role"], "explicit_role"), (["function", "function"], "functional"),
        (["role", "function"], "mixed"), (["function", "role"], "mixed")])
    def test_hint_policy_from_types(self, types, policy):
        line = "Minimum 4 years as a Translator or Interpreter"
        a, jd, (c,) = _job(["Translator", "Interpreter"], 4, ["Requirements", "- " + line + "."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
                "targets": [{"hint": f"T{i}", "type": ty, "match": "exact", "jd_span": None}
                            for i, ty in enumerate(types, 1)],
                "setting": None, "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        art = out.artifacts[0]
        assert art.policy == policy and art.audit["policy_derivation"] == "from_types"
        assert art.audit["ai"]["relevance_basis"] is None

    def test_restrictions_role_and_function_become_targets(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        _, out = _run_raw(jd, a, _resp(_mixed_item(c.criterion_id)))
        art = out.artifacts[0]
        assert out.meta["calls"] == 1 and art.spec_status == "resolved" and art.policy == "mixed"
        assert (art.audit["policy_derivation"], art.audit["ai"]["relevance_basis"]) == ("from_types", "targets")
        assert [(t.target_id, t.text, t.type, t.provenance) for t in art.targets] == [
            ("J1", "Payroll Officer", "role", "jd_asserted"), ("J2", "payroll administration", "function",
                                                               "jd_asserted")]
        assert art.audit["review_required"] == ["J1", "J2"]
        assert art.audit["restrictions"] == [{"line": 2, "text": "Payroll Officer", "kind": "role"},
                                             {"line": 2, "text": "payroll administration", "kind": "function"}]

    @pytest.mark.parametrize("line, restr, policy, basis, status, codes_, setting", [
        ("3-5 years of experience in procurement", [("procurement", "function")], "functional", "targets",
         "resolved", [], None),
        ("Minimum 3 years of experience in the telecom sector", [("telecom sector", "context")], "sector", "sector",
         "resolved", [], "telecom sector"),
        ("Minimum 3 years of professional experience", [], "pure_duration", "total_experience", "resolved", [], None),
        ("Minimum 3 years of relevant experience", [("relevant", "vague")], "pure_duration", "unspecified",
         "needs_confirmation", ["ambiguous_relevance"], None),
        ("Minimum 3 years as a Data Analyst in the telecom sector",
         [("Data Analyst", "role"), ("telecom sector", "context")], "explicit_role", "targets", "resolved", [],
         "telecom sector"),
        ("Minimum 3 years of relevant experience as a Data Analyst", [("relevant", "vague"), ("Data Analyst", "role")],
         "explicit_role", "targets", "resolved", [], None),
    ])
    def test_restriction_derivation(self, line, restr, policy, basis, status, codes_, setting):
        a, jd, (c,) = _job([], 3, ["Requirements", "- " + line + "."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
                "restrictions": [{"line": 2, "text": t, "kind": k} for t, k in restr],
                "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        art = out.artifacts[0]
        assert (art.policy, art.audit["ai"]["relevance_basis"], art.spec_status, [r.code for r in art.reasons]) == (
            policy, basis, status, codes_)
        assert setting_texts(art) == ([setting] if setting else [])
        if basis == "unspecified":
            assert art.reasons[0].field == "relevance_basis"
            with pytest.raises(asm.S1ViewError):
                asm.s2_views(with_resolution(art), require_resolved=True)   # never a trusted pure-duration req.

    def test_total_experience_is_never_a_model_claim(self):
        # an s1-4 style relevance_basis claim is ignored; an omitted restriction list is an error, not "general"
        line = "Minimum 3 years of experience in procurement"
        a, jd, (c,) = _job([], 3, ["Requirements", "- " + line + "."])
        claim = {"criterion_id": c.criterion_id, "relevance_basis": "total_experience",
                 "requirement_spans": [{"line": 2, "text": line}], "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(claim), _resp(claim))
        assert out.artifacts[0].spec_status == "failed_validation"
        assert any("restrictions must be a list" in e for e in out.validation["errors"])

    @pytest.mark.parametrize("over, frag", [
        ({"restrictions": None}, "restrictions must be a list"),
        ({"restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "person"}]}, "kind"),
        ({"restrictions": [{"line": 2, "text": "Payroll Offic", "kind": "role"}]}, "not verbatim"),
        ({"restrictions": [{"line": 1, "text": "Requirements", "kind": "function"}]}, "not inside"),
        ({"restrictions": [{"line": 2, "text": "3 years as a Payroll Officer", "kind": "role"}]},
         "must not include the duration"),
        ({"targets": [{"line": 2, "text": "Payroll Officer", "type": "role"}]}, "return restrictions, not targets"),
        ({"setting": {"line": 2, "text": "payroll administration"}}, "never a single \"setting\""),
        ({"settings": [{"line": 2, "text": "payroll administration"}]}, "are \"context\" restrictions"),
        # s1-6: the wire kind "sector" is gone ("context"); several contexts are allowed but never overlapping
        ({"restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "sector"}]}, "kind"),
        ({"restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "role"},
                           {"line": 2, "text": "payroll administration", "kind": "context"},
                           {"line": 2, "text": "administration", "kind": "context"}]}, "context restrictions"),
        ({"restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "role"},
                           {"line": 2, "text": "Payroll", "kind": "context"}]}, "overlaps the role restriction"),
        ({"restrictions": [{"line": 2, "text": "Payroll Officer", "kind": "role"},
                           {"line": 2, "text": "payroll administration", "kind": "context"}],
          "ambiguity": ["ambiguous_context_scope"]}, "ambiguous_context_scope means"),
    ])
    def test_restriction_structure_is_strict(self, over, frag):
        a, jd, crits = _job([], 3, MIXED_JD)
        j = JDText(jd)
        v = validate_response(_resp(_mixed_item(crits[0].criterion_id, **over)), j, crits,
                              {d: (ln, m) for d, ln, m in j.durations()})
        assert not v.ok and any(frag in e for e in v.errors), v.errors

    def test_payload_is_neutral(self):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        (crit,) = req.payload["criteria"]
        assert set(crit) == {"criterion_id", "kind", "has_years", "target_hints"}
        assert crit["kind"] == "years_with_targets"
        assert "relevant role" not in req.user_message and "display_text" not in req.user_message
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Translator"]}}
        assert clf.build_request(JDText("x"), enumerate_experience_criteria("J", a)).payload[
            "criteria"][0]["kind"] == "single_target"
        assert clf.build_request(JDText("x"), enumerate_experience_criteria("J", YEARS_ONLY)).payload[
            "criteria"][0]["kind"] == "years_only"
        # the recruiter-facing artifact keeps display_text unchanged
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        assert out.artifacts[0].display_text == JOB31_DISPLAY


class TestS14ScopedRepair:
    """Repair can only change what failed; destructive rewrites are discarded and the merge is re-validated."""

    def test_l2_policy_only_inconsistency_needs_no_repair(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        _, out = _run_raw(jd, a, _resp(_mixed_item(c.criterion_id, policy="explicit_role")))
        art = out.artifacts[0]
        assert out.meta["calls"] == 1 and art.policy == "mixed" and art.audit["ai"]["model_policy"] == "explicit_role"

    def test_l2_destructive_repair_is_discarded(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        main = _mixed_item(c.criterion_id, duration="D9")                       # only the duration is wrong
        destructive = _mixed_item(c.criterion_id, restrictions=[], duration="D1")     # tries "total experience"
        client, out = _run_raw(jd, a, _resp(main), _resp(destructive))
        art = out.artifacts[0]
        assert out.meta["outcome"] == "repaired" and art.spec_status == "resolved"
        assert art.policy == "mixed" and len(art.targets) == 2                   # targets survived
        assert art.required_years.provenance == "jd_verified"                     # duration fixed by the repair
        merge = out.meta["repair_merge"]
        assert merge["taken"] == [{"criterion_id": c.criterion_id, "field": "duration"}]
        assert merge["discarded_changes"] == 1

    ARABIC = ["المتطلبات", "- خبرة 5 سنوات كمدير مشروع إنشائي."]

    def _b1_item(self, cid, **t):
        tgt = {"hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 2, "text": "كمدير مشروع إنشائي"},
               "alignment": alignment("Construction Project Manager", "كمدير مشروع إنشائي"), "jd_extra": []}
        tgt.update(t)
        return {"criterion_id": cid, "requirement_spans": [{"line": 2, "text": "خبرة 5 سنوات كمدير مشروع إنشائي"}],
                "targets": [tgt], "setting": None, "duration": "D1", "ambiguity": [], "note": "n"}

    def test_b1_span_error_cannot_flip_match_or_type(self):
        a, jd, (c,) = _job(["Construction Project Manager"], 5, self.ARABIC)
        bad_span = {"line": 2, "text": "مدير مشروع انشائي"}                    # copy error (missing hamza)
        main = self._b1_item(c.criterion_id, jd_span=bad_span)
        gave_up = self._b1_item(c.criterion_id, match="none", jd_span=None, type="function")
        _, out = _run_raw(jd, a, _resp(main), _resp(gave_up))
        art = out.artifacts[0]
        assert {x["field"] for x in out.meta["repair_merge"]["taken"]} == {
            "target:T1:jd_span", "target:T1:alignment", "target:T1:jd_extra"}                # never match/type
        # s1-5.1: the claim left without a span is withdrawn deterministically and audited (never silent); the
        # type stays the main answer's, the repair's "function" is still discarded
        assert art.spec_status == "needs_confirmation" and [(t.type, t.provenance) for t in art.targets] == [
            ("role", "original_ai")]
        (w,) = art.audit["withdrawals"]
        assert art.audit["alignment_withdrawn"] and w["hint"] == "T1" and w["withdrawn_claim"]["match"] == "equivalent"
        assert any("match equivalent requires jd_span" in e for e in w["errors"])

    def test_b1_span_error_fixed_keeps_semantics(self):
        a, jd, (c,) = _job(["Construction Project Manager"], 5, self.ARABIC)
        main = self._b1_item(c.criterion_id, jd_span={"line": 2, "text": "مدير مشروع انشائي"})
        fixed_but_retyped = self._b1_item(c.criterion_id, type="function",
                                          jd_span={"line": 2, "text": "مدير مشروع إنشائي"},
                                          alignment=alignment("Construction Project Manager", "مدير مشروع إنشائي"))
        _, out = _run_raw(jd, a, _resp(main), _resp(fixed_but_retyped))
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and [(t.type, t.provenance) for t in art.targets] == [
            ("role", "jd_asserted")]                                                # type kept from main
        assert art.targets[0].jd_span.text == "مدير مشروع إنشائي"

    def test_semantic_mapping_error_may_withdraw_match(self):
        # V-sub-a is evidence the mapping is not the same role/function: the repair may change match to none
        line = "Minimum 5 years as a Marketing Manager"
        a, jd, (c,) = _job(["Senior Marketing Manager"], 5, ["Requirements", "- " + line + "."])
        item = lambda **t: {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
                            "targets": [{"hint": "T1", "type": "role", **t}], "setting": None, "duration": "D1",
                            "ambiguity": [], "note": "n"}
        main = item(match="equivalent", jd_span={"line": 2, "text": "Marketing Manager"})
        _, out = _run_raw(jd, a, _resp(main), _resp(item(match="none", jd_span=None)))
        art = out.artifacts[0]
        assert out.meta["outcome"] == "repaired" and [t.provenance for t in art.targets] == ["original_ai"]
        assert [r.code for r in art.reasons] == ["target_not_in_jd"]

    def test_error_free_criterion_is_never_changed(self):
        lines = ["Requirements", "- Experience as a Payroll Officer is required.",
                 "- Experience as a Translator is required."]
        a, jd, crits = _job(["Payroll Officer", "Translator"], 0, lines)
        c1, c2 = crits

        def item(c, ln, text, ty):
            return {"criterion_id": c.criterion_id, "requirement_spans": [{"line": ln, "text": text}],
                    "targets": [{"hint": "T1", "type": ty, "match": "exact", "jd_span": None}],
                    "setting": None, "duration": None, "ambiguity": [], "note": "n"}
        main = _resp(item(c1, 2, "Experience as a Payroll Officer is required", "role"),
                     item(c2, 3, "Experience as a Translator is required", "person"))
        repair = _resp(item(c1, 2, "Experience as a Payroll Officer is required", "function"),
                       item(c2, 3, "Experience as a Translator is required", "role"))
        _, out = _run_raw(jd, a, main, repair)
        by = {x.criterion_id: x for x in out.artifacts}
        assert by[c1.criterion_id].targets[0].type == "role"                       # main kept, flip discarded
        assert by[c2.criterion_id].targets[0].type == "role"                       # failed field repaired
        assert out.meta["repair_merge"]["discarded_changes"] == 1

    def test_valid_restrictions_cannot_be_dropped(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        bad = [{"line": 2, "text": "Payroll Officer", "kind": "role"},
               {"line": 2, "text": "payroll admin", "kind": "function"}]               # R1 not whole words
        main = _mixed_item(c.criterion_id, restrictions=bad)
        _, out = _run_raw(jd, a, _resp(main), _resp(_mixed_item(c.criterion_id, restrictions=[])))
        assert out.artifacts[0].spec_status == "failed_validation"              # no fallback to total experience
        assert out.meta["repair_merge"]["kept_main_restrictions"] == [c.criterion_id]
        only_role = [{"line": 2, "text": "Payroll Officer", "kind": "role"}]        # drops the function dimension
        _, out = _run_raw(jd, a, _resp(main), _resp(_mixed_item(c.criterion_id, restrictions=only_role)))
        assert out.artifacts[0].spec_status == "failed_validation"
        _, ok = _run_raw(jd, a, _resp(main), _resp(_mixed_item(c.criterion_id)))
        assert ok.artifacts[0].spec_status == "resolved" and ok.artifacts[0].policy == "mixed"

    def test_malformed_restriction_kind_can_be_repaired_but_not_dropped(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        bad = [{"line": 2, "text": "Payroll Officer", "kind": "role"},
               {"line": 2, "text": "payroll administration", "kind": "activity"}]      # R1 kind not allowed
        main = _mixed_item(c.criterion_id, restrictions=bad)
        _, ok = _run_raw(jd, a, _resp(main), _resp(_mixed_item(c.criterion_id)))
        assert ok.artifacts[0].spec_status == "resolved" and ok.artifacts[0].policy == "mixed"
        only_bad = _mixed_item(c.criterion_id, restrictions=[{"line": 2, "text": "Payroll Officer", "kind": "x"}])
        _, out = _run_raw(jd, a, _resp(only_bad), _resp(_mixed_item(c.criterion_id, restrictions=[])))
        assert out.artifacts[0].spec_status == "failed_validation"              # never repaired into []
        assert out.meta["repair_merge"]["kept_main_restrictions"] == [c.criterion_id]

    def test_duration_overlap_scopes_the_raw_restriction(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        rs = [{"line": 2, "text": "Payroll Officer", "kind": "x"},                     # R0 malformed (skipped)
              {"line": 2, "text": MIXED_LINE, "kind": "role"}]                         # R1 includes the duration
        j = JDText(jd)
        res = validate_response(_resp(_mixed_item(c.criterion_id, restrictions=rs)), j, [c],
                                {d: (ln, m) for d, ln, m in j.durations()})
        scopes = {sc for e in res.scoped for sc in e.scopes if sc.startswith("restriction:")}
        assert scopes == {"restriction:R0", "restriction:R1"}

    def test_restrictions_untouched_when_other_field_fails(self):
        a, jd, (c,) = _job([], 3, MIXED_JD)
        main = _mixed_item(c.criterion_id, ambiguity=["unsure"])
        repair = _mixed_item(c.criterion_id, restrictions=[{"line": 2, "text": "Payroll Officer", "kind": "role"}])
        _, out = _run_raw(jd, a, _resp(main), _resp(repair))
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and art.policy == "mixed" and len(art.targets) == 2

    def test_extra_target_scope_keeps_hint_targets(self):
        cid = job31_cid()
        main = job31_ok(cid, targets=[{"hint": "T1", "type": "role", "match": "exact", "jd_span": None},
                                      {"hint": "T2", "type": "role", "match": "exact", "jd_span": None},
                                      {"line": 6, "text": "Construction Project Manager", "type": "role"}])
        repair = job31_ok(cid, targets=[{"hint": "T1", "type": "function", "match": "exact", "jd_span": None},
                                        {"hint": "T2", "type": "function", "match": "exact", "jd_span": None}])
        client = FakeClient(ai(main), ai(repair))
        out = run(clf.classify_job("JOB-2026-0031", JOB31_JD, JOB31_ANALYSIS, client=client))
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and [t.type for t in art.targets] == ["role", "role"]

    def test_unparseable_main_takes_repair_whole(self):
        cid = job31_cid()
        good = ai(job31_ok(cid, targets=[{"hint": "T1", "type": "role", "match": "exact", "jd_span": None},
                                         {"hint": "T2", "type": "role", "match": "exact", "jd_span": None}]))
        out = run(clf.classify_job("JOB-2026-0031", JOB31_JD, JOB31_ANALYSIS, client=FakeClient("not json", good)))
        assert out.artifacts[0].spec_status == "resolved" and out.meta["repair_merge"]["mode"] == "full_replace"


class TestS14ReviewFixes:
    def test_unknown_hint_cannot_rewrite_valid_hints(self):
        cid = job31_cid()
        main = job31_ok(cid, targets=[{"hint": "T1", "type": "role", "match": "exact", "jd_span": None},
                                      {"hint": "T9", "type": "role", "match": "none", "jd_span": None},
                                      {"hint": "T2", "type": "role", "match": "exact", "jd_span": None}])
        repair = job31_ok(cid, targets=[{"hint": "T1", "type": "function", "match": "exact", "jd_span": None},
                                        {"hint": "T2", "type": "function", "match": "exact", "jd_span": None}])
        out = run(clf.classify_job("JOB-2026-0031", JOB31_JD, JOB31_ANALYSIS, client=FakeClient(ai(main), ai(repair))))
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and [t.type for t in art.targets] == ["role", "role"]

    def test_merge_bug_is_internal_error_not_ai_unavailable(self, monkeypatch):
        def boom(*a, **k):
            raise KeyError("bug")
        monkeypatch.setattr(clf, "merge_repair", boom)
        bad = ai(job31_ok(job31_cid(), ambiguity=["unsure"]))
        out = run(clf.classify_job("JOB-2026-0031", JOB31_JD, JOB31_ANALYSIS, client=FakeClient(bad, bad)))
        assert (out.status_reason, out.artifacts[0].spec_status, out.artifacts[0].retryable) == (
            "internal_error", "failed_technical", True)

    def test_audit_ai_block_has_no_policy(self):
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        assert "policy" not in art.audit["ai"] and art.policy == "explicit_role"
        assert art.audit["policy_derivation"] == "from_types"


class TestS14ArabicBoundary:
    @pytest.mark.parametrize("word", ["كمدير", "بمدير", "لمدير", "ومدير", "فمدير", "فبمدير", "وكمدير", "ولمدير"])
    def test_proclitic_chains_accepted(self, word):
        j = JDText(f"خبرة {word} مشروع")
        assert [s.text for s in j.find("مدير مشروع")] == ["مدير مشروع"]

    @pytest.mark.parametrize("word", ["تمدير", "وبكمدير", "للمدير", "بكمدير", "ووكمدير", "مديره"])
    def test_non_proclitic_or_suffix_rejected(self, word):
        assert JDText(f"خبرة {word} مشروع").find("مدير") == []

    def test_latin_boundaries_unchanged(self):
        j = JDText("an xmanager and a manager, bmanager, kmanager")
        assert [s.start for s in j.find("manager")] == [j.lines[0].index("a manager") + 2]

    def test_arabic_hint_verbatim_after_proclitic_is_exact(self):
        a, jd, (c,) = _job(["مدير مشروع"], 5, ["المتطلبات", "- خبرة 5 سنوات كمدير مشروع."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": "خبرة 5 سنوات كمدير مشروع"}],
                "targets": [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}],
                "setting": None, "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and art.targets[0].provenance == "jd_verified"
        assert art.targets[0].jd_span.text == "مدير مشروع"


# ── s1-5: alignment coverage and compound requirements ───────────────────────────────────────────────

def _eqv(hint, line, jd_span, pairs, extra=(), years=5, ar=False):
    """One hint criterion with an "equivalent" mapping and an explicit alignment. Returns (validation, jd, a, raw)."""
    lines = ["المتطلبات" if ar else "Requirements", "- " + line + "."]
    a, jd, (c,) = _job([hint], years, lines)
    item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
            "targets": [{"hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 2, "text": jd_span},
                         "alignment": [{"hint": h, "jd": j, "relation": r} for h, j, r in pairs],
                         "jd_extra": [{"text": t, "kind": k} for t, k in extra]}],
            "setting": None, "duration": "D1", "ambiguity": [], "note": "n"}
    raw = _resp(item)
    j = JDText(jd)
    v = validate_response(raw, j, [c], {d: (ln, m) for d, ln, m in j.durations()})
    return v, jd, a, raw


def errs_with(v, frag):
    return not v.ok and any(frag in e for e in v.errors)


class TestS15AlignmentRejects:
    """The held-out and main false positives cannot pass the structural coverage contract."""

    def test_c2_dropped_construction(self):
        args = ("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع")
        v, *_ = _eqv(*args, [("Project", "مشروع", TR), ("Manager", "كمدير", TR)], ar=True)
        assert errs_with(v, "unpaired ['construction']")
        v, *_ = _eqv(*args, [("Construction Project", "مشروع", TR), ("Manager", "كمدير", TR)], ar=True)
        assert errs_with(v, "a pair maps ONE target word")                            # no lumping
        v, *_ = _eqv(*args, [("Construction", "مشروع", TR), ("Project", "مشروع", TR), ("Manager", "كمدير", TR)],
                     ar=True)
        assert errs_with(v, "used more than once")                                    # no JD word reuse

    def test_h1_added_enterprise_systems(self):
        args = ("software implementation", "Minimum 3 years of experience implementing enterprise software systems",
                "implementing enterprise software systems")
        honest = [("software", "software", SA), ("implementation", "implementing", FO)]
        v, *_ = _eqv(*args, honest, [("enterprise", "material"), ("systems", "material")], years=3)
        assert errs_with(v, "adds the material word 'enterprise'")
        v, *_ = _eqv(*args, honest, years=3)
        assert errs_with(v, "unaccounted ['enterprise', 'systems']")                 # cannot silently disappear
        v, *_ = _eqv(*args, [("software", "software systems", SA), ("implementation", "implementing enterprise", FO)],
                     years=3)
        assert errs_with(v, "are not the identical word") and errs_with(v, "relation form is one word")

    def test_ho03_dropped_equine(self):
        v, *_ = _eqv("Equine Veterinarian", "خبرة 3 سنوات كطبيب بيطري", "كطبيب بيطري",
                     [("Veterinarian", "كطبيب بيطري", TR)], years=3, ar=True)
        assert errs_with(v, "unpaired ['equine']")

    def test_ho05_added_ecommerce(self):
        line, span = "خبرة 4 سنوات في تطوير مواقع التجارة الإلكترونية", "تطوير مواقع التجارة الإلكترونية"
        v, *_ = _eqv("web development", line, span, [("web", "مواقع", TR), ("development", "تطوير", TR)],
                     [("التجارة", "material"), ("الإلكترونية", "material")], years=4, ar=True)
        assert errs_with(v, "adds the material word")
        v, *_ = _eqv("web development", line, span, [("web", "مواقع", TR), ("development", "تطوير", TR)],
                     years=4, ar=True)
        assert errs_with(v, "unaccounted")

    def test_ho07_added_corporate(self):
        line = "Minimum 3 years of experience planning corporate events"
        v, *_ = _eqv("event planning", line, "planning corporate events",
                     [("event", "events", FO), ("planning", "planning", SA)], [("corporate", "material")], years=3)
        assert errs_with(v, "adds the material word 'corporate'")
        v, *_ = _eqv("event planning", line, "planning corporate events",
                     [("event", "corporate events", FO), ("planning", "planning", SA)], years=3)
        assert errs_with(v, "relation form is one word to one word")

    def test_ho11_dropped_commercial(self):
        line = "Minimum 5 years of experience as a Pilot"
        v, *_ = _eqv("Commercial Pilot", line, "Pilot", [("Pilot", "Pilot", SA)])
        assert errs_with(v, "unpaired ['commercial']")
        v, *_ = _eqv("Commercial Pilot", line, "Pilot", [("Commercial Pilot", "Pilot", SA)])
        assert errs_with(v, "a pair maps ONE target word")

    def test_rejected_equivalent_can_only_end_unconfirmed(self):
        # material extra -> the repair may withdraw to none -> original_ai, never a resolved jd_asserted
        line = "Minimum 3 years of experience planning corporate events"
        v, jd, a, raw = _eqv("event planning", line, "planning corporate events",
                             [("event", "events", FO), ("planning", "planning", SA)], [("corporate", "material")],
                             years=3)
        withdrawn = json.loads(raw)
        withdrawn["criteria"][0]["targets"][0].update(match="none", jd_span=None, alignment=None, jd_extra=None)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, json.dumps(withdrawn))))
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == ["original_ai"] and art.spec_status == "needs_confirmation"
        same_again = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw)))       # s1-5.1 withdrawal
        art = same_again.artifacts[0]
        assert art.spec_status == "needs_confirmation" and [t.provenance for t in art.targets] == ["original_ai"]
        assert art.audit["alignment_withdrawn"] and same_again.meta["outcome"] == "repaired_withdrawn"

    def test_alignment_copy_error_cannot_flip_match(self):
        line = "Minimum 5 years of experience as a Human Resources Manager"
        v, jd, a, raw = _eqv("HR Manager", line, "Human Resources Manager",
                             [("HR", "Human Resorces", AB), ("Manager", "Manager", SA)])
        assert errs_with(v, "is not whole word(s) of the jd_span")
        gave_up = json.loads(raw)
        gave_up["criteria"][0]["targets"][0].update(match="none", jd_span=None)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, json.dumps(gave_up))))
        art = out.artifacts[0]                    # the repair's match change is discarded; s1-5.1 withdraws, audited
        assert art.spec_status == "needs_confirmation" and art.audit["alignment_withdrawn"]
        assert out.meta["repair_merge"]["discarded_changes"] == 1


class TestS15AlignmentAccepts:
    def _ok(self, *args, **kw):
        v, jd, a, raw = _eqv(*args, **kw)
        assert v.ok, v.errors
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw)))
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
        return art

    def test_translation_with_attached_particle(self):
        art = self._ok("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع إنشائي", "كمدير مشروع إنشائي",
                       [("Construction", "إنشائي", TR), ("Project", "مشروع", TR), ("Manager", "كمدير", TR)], ar=True)
        assert art.audit["target_mappings"][0]["alignment"][2] == {"hint": "Manager", "jd": "كمدير",
                                                                   "relation": "translation"}
        self._ok("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع إنشائي", "مدير مشروع إنشائي",
                 [("Construction", "إنشائي", TR), ("Project", "مشروع", TR), ("Manager", "مدير", TR)], ar=True)

    def test_translation_one_word_to_several(self):
        self._ok("Veterinarian", "خبرة 3 سنوات كطبيب بيطري", "كطبيب بيطري",
                 [("Veterinarian", "كطبيب بيطري", TR)], years=3, ar=True)

    def test_multiword_concept_translated_word_by_word(self):
        self._ok("Human Resources Manager", "خبرة 5 سنوات مدير الموارد البشرية", "مدير الموارد البشرية",
                 [("Human", "البشرية", TR), ("Resources", "الموارد", TR), ("Manager", "مدير", TR)], ar=True)

    def test_grammatical_form_and_function_word(self):
        # structurally valid (accepted by the validator) but, s1-5.2, only an unverified candidate
        for args, kw in [
            (("software implementation", "Minimum 3 years of experience implementing software",
              "implementing software", [("software", "software", SA), ("implementation", "implementing", FO)]),
             {"years": 3}),
            (("database administration", "Minimum 4 years of experience in administration of databases",
              "administration of databases", [("database", "databases", FO), ("administration", "administration", SA)],
              [("of", "grammatical")]), {"years": 4})]:
            v, jd, a, raw = _eqv(*args, **kw)
            assert v.ok, v.errors
            assert_form_candidate(run(clf.classify_job("J1", jd, a, client=FakeClient(raw))).artifacts[0])

    @pytest.mark.parametrize("hint, line, span, pairs", [
        ("PM", "Minimum 5 years as a Project Manager (P.M.)", "Project Manager", [("PM", "Project Manager", AB)]),
        ("PM", "Minimum 5 years as a P.M. (Project Manager)", "Project Manager", [("PM", "Project Manager", AB)]),
        ("PM", "Minimum 5 years as a P.M.", "P.M.", [("PM", "P.M.", AB)]),          # same acronym: unchanged
    ])
    def test_abbreviations(self, hint, line, span, pairs):
        self._ok(hint, line, span, pairs)

    @pytest.mark.parametrize("hint, line, span, pairs", [
        ("PM", "Minimum 5 years as a Project Manager", "Project Manager", [("PM", "Project Manager", AB)]),
        ("UX Designer", "Minimum 5 years as a User Experience Designer", "User Experience Designer",
         [("UX", "User Experience", AB), ("Designer", "Designer", SA)]),
        ("Human Resources Manager", "Minimum 5 years as an HR Manager", "HR Manager",
         [("Human Resources", "HR", AB), ("Manager", "Manager", SA)]),
    ])
    def test_undefined_abbreviations_are_candidates(self, hint, line, span, pairs):
        v, jd, a, raw = _eqv(hint, line, span, pairs)
        assert v.ok, v.errors                                                  # structurally valid
        assert_abbreviation_candidate(run(clf.classify_job("J1", jd, a, client=FakeClient(raw))).artifacts[0])

    def test_abbreviation_is_structural_not_free(self):
        v, *_ = _eqv("web developer", "Minimum 5 years as a website engineering builder developer",
                     "website engineering builder developer",
                     [("web", "website engineering builder", AB), ("developer", "developer", SA)])
        assert errs_with(v, "abbreviation needs an all-capitals acronym")
        v, *_ = _eqv("software implementation", "Minimum 5 years of experience implementing software",
                     "implementing software", [("software", "software", SA), ("implementation", "implementing", TR)])
        assert errs_with(v, "relation translation needs another language")


class TestS15Compound:
    R1_LINE = "7 years of overall experience, including at least 3 years as a Project Manager"

    def _r1(self, duration="D2", **over):
        a, jd, (c,) = _job(["Project Manager"], 3, ["Requirements", "- " + self.R1_LINE + "."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": self.R1_LINE}],
                "targets": [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}],
                "setting": None, "duration": duration, "ambiguity": [], "note": "n"}
        item.update(over)
        _, out = _run_raw(jd, a, _resp(item))
        return out.artifacts[0]

    @pytest.mark.parametrize("duration", ["D1", "D2", None])
    def test_two_thresholds_are_compound(self, duration):
        art = self._r1(duration)
        assert art.spec_status == "needs_confirmation" and "compound_requirement" in [r.code for r in art.reasons]
        assert art.required_years is None                                          # never collapsed to one N
        comp = art.audit["compound"]
        assert [(d["text"], d["years"]) for d in comp["durations"]] == [("7 years", 7.0), ("3 years", 3.0)]
        assert comp["targets"] == [{"target_id": "T1", "text": "Project Manager", "type": "role"}]
        assert comp["selected_duration"] == duration
        for require in (True, False):
            with pytest.raises(asm.S1ViewError, match="compound"):
                asm.s2_views(art, require_resolved=require)

    def test_compound_in_hintless_criterion_keeps_restrictions(self):
        line = "5 years of experience, including 2 years in procurement"
        a, jd, (c,) = _job([], 5, ["Requirements", "- " + line + "."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
                "restrictions": [{"line": 2, "text": "procurement", "kind": "function"}],
                "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        art = out.artifacts[0]
        assert "compound_requirement" in [r.code for r in art.reasons] and art.required_years is None
        assert art.audit["compound"]["restrictions"] == [{"line": 2, "text": "procurement", "kind": "function"}]

    def test_single_threshold_unaffected(self):
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and "compound" not in art.audit and art.required_years.value == 5
        line = "3-5 years of experience in procurement"                              # a range is one threshold
        a, jd, (c,) = _job([], 3, ["Requirements", "- " + line + "."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": line}],
                "restrictions": [{"line": 2, "text": "procurement", "kind": "function"}],
                "duration": "D1", "ambiguity": [], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        assert out.artifacts[0].spec_status == "resolved"

    def test_conflicting_versions_are_not_compound(self):
        lines = ["Summary", "We are hiring a Project Manager with at least 5 years of experience.", "",
                 "Requirements", "- Minimum 8 years of experience as a Project Manager."]
        a, jd, (c,) = _job(["Project Manager"], 5, lines)
        item = {"criterion_id": c.criterion_id,
                "requirement_spans": [{"line": 2, "text": lines[1][:-1]}, {"line": 5, "text": lines[4][2:-1]}],
                "targets": [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}],
                "setting": None, "duration": "D1", "ambiguity": ["conflicting_requirements"], "note": "n"}
        _, out = _run_raw(jd, a, _resp(item))
        codes_ = [r.code for r in out.artifacts[0].reasons]
        assert "conflicting_requirements" in codes_ and "compound_requirement" not in codes_


class TestStatusTaxonomy:
    def _c(self):
        return enumerate_experience_criteria("J", JOB31_ANALYSIS)[0]

    def test_reason_kinds(self):
        assert sc.Reason("target_not_in_jd").kind == "business"
        assert sc.Reason("ambiguous_relevance").kind == "ambiguity"
        assert sc.Reason("validation_failed").kind == "contract"
        assert {sc.Reason(c).kind for c in sc.TECHNICAL_CODES} == {"technical"}
        with pytest.raises(ValueError):
            sc.Reason("made_up")

    def test_pending_and_failed_artifacts(self):
        c = self._c()
        p = asm.pending_artifact(c)
        assert (p.spec_status, p.policy, p.reasons, p.requirement_text) == ("pending", None, (), JOB31_DISPLAY)
        t = asm.failed_artifact(c, "ai_unavailable", run={})
        assert (t.spec_status, t.retryable, t.reasons[0].kind) == ("failed_technical", True, "technical")
        v = asm.failed_artifact(c, "validation_failed", run={})
        assert (v.spec_status, v.retryable) == ("failed_validation", False)
        with pytest.raises(ValueError):
            asm.failed_artifact(c, "target_not_in_jd", run={})
        for a in (p, t, v):
            with pytest.raises(asm.S1ViewError):
                asm.s2_views(a, require_resolved=False)

    def test_artifact_invariants(self):
        base = dict(criterion_id="c", job_id="J", source_path="p", display_text="d", requirement_text="d",
                    required=True)
        with pytest.raises(ValueError):
            sc.S1Artifact(spec_status="resolved", policy="explicit_role", reasons=(sc.Reason("n_mismatch"),), **base)
        with pytest.raises(ValueError):
            sc.S1Artifact(spec_status="needs_confirmation", policy="explicit_role",
                          reasons=(sc.Reason("ai_unavailable"),), **base)
        with pytest.raises(ValueError):
            sc.S1Artifact(spec_status="failed_technical", reasons=(sc.Reason("n_mismatch"),), **base)
        with pytest.raises(ValueError):
            sc.S1Artifact(spec_status="resolved", **base)                          # no policy
        with pytest.raises(ValueError):
            sc.S1Artifact(spec_status="bogus", **base)
        with pytest.raises(ValueError):
            sc.Target("x", "sector", "jd_verified")
        with pytest.raises(ValueError):
            sc.Target("x", "role", "guessed")

    def test_ambiguity_reported_by_ai_needs_confirmation(self):
        cid = only_cid(YEARS_ONLY)
        _, out = classify("- 3 years of relevant experience", YEARS_ONLY, ai(res(
            cid, "pure_duration", [(1, "3 years of relevant experience")], duration="D1",
            ambiguity=["ambiguous_relevance"])))
        (art,) = out.artifacts
        assert art.spec_status == "needs_confirmation"
        assert [(r.code, r.kind) for r in art.reasons] == [("ambiguous_relevance", "ambiguity")]

    def test_requirement_not_in_jd(self):
        cid = job31_cid()
        r = job31_ok(cid, requirement_spans=[], duration=None, ambiguity=["requirement_not_in_jd"])
        _, out = classify("Unrelated JD text only", JOB31_ANALYSIS, ai(r), job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert art.spec_status == "needs_confirmation" and art.requirement_text == JOB31_DISPLAY
        assert {x.code for x in art.reasons} == {"requirement_not_in_jd", "target_not_in_jd", "n_not_in_jd"}


# ── 22 validation ───────────────────────────────────────────────────────────

def _validate(resp, jd=JOB31_JD, analysis=JOB31_ANALYSIS, job_id="JOB-2026-0031"):
    j = JDText(jd)
    raw = with_match(resp if isinstance(resp, str) else ai(*resp), jd, analysis, job_id)
    return validate_response(raw, j,
                             enumerate_experience_criteria(job_id, analysis),
                             {did: (ln, m) for did, ln, m in j.durations()})


class TestValidator:
    def test_valid(self):
        v = _validate([job31_ok(job31_cid())])
        assert v.ok and v.errors == []

    @pytest.mark.parametrize("over, frag", [
        ({"targets": [{"hint": "T1", "type": "role"}]}, "hint T2"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T1", "type": "role"},
                      {"hint": "T2", "type": "role"}]}, "exactly once"),
        ({"targets": [{"hint": "T1", "type": "role", "text": "Project Manager"}, {"hint": "T2", "type": "role"}]},
         "unchanged"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "role"},
                      {"line": 6, "text": "Project Manager", "type": "role"}]}, "add no other targets"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T9", "type": "role"}]}, "unknown hint"),
        ({"targets": [{"hint": "T1", "type": "position"}, {"hint": "T2", "type": "role"}]}, "type must be"),
        ({"requirement_spans": [{"line": 6, "text": "Minimum 6 years of experience"}]}, "not verbatim"),
        ({"requirement_spans": [{"line": 6, "text": "Mi"}]}, "at least 3"),
        ({"requirement_spans": []}, "requirement_spans is empty"),
        ({"requirement_spans": [{"line": 6, "text": "experience as a Construction Project Manager"}]},
         "duration D1"),
        ({"duration": "D7"}, "duration must be null or one of"),
        ({"duration": 5}, "duration must be null or one of"),
        ({"ambiguity": ["unsure"]}, "ambiguity must be"),
        ({"restrictions": [{"line": 6, "text": "Assistant Project Manager", "kind": "role"}]},
         "restrictions apply only to criteria without target_hints"),
        ({"setting": "construction"}, "settings[0]: must be an object"),    # s1-6 wire adapter: [setting]
    ])
    def test_rejections(self, over, frag):
        v = _validate([job31_ok(job31_cid(), **over)])
        assert not v.ok and v.results == {}
        assert any(frag in e for e in v.errors), v.errors

    def test_shape_errors(self):
        cid = job31_cid()
        assert not _validate("not json").ok
        assert any('"criteria" list' in e for e in _validate('{"results": []}').errors)
        assert any("missing result" in e for e in _validate([]).errors)
        assert any("more than once" in e for e in _validate([job31_ok(cid), job31_ok(cid)]).errors)
        assert any("unknown criterion_id" in e for e in _validate([job31_ok(cid), job31_ok("zzz")]).errors)

    def test_hintless_target_must_be_inside_requirement_span(self):
        jd = "Intro about procurement teams\n- 3 years of experience in contract management"
        cid = only_cid(YEARS_ONLY, "JOB-2026-0031")
        v = _validate([res(cid, "functional", [(2, "3 years of experience in contract management")],
                           [{"line": 1, "text": "procurement", "type": "function"}], duration="D1")],
                      jd=jd, analysis=YEARS_ONLY)
        assert any("not inside" in e for e in v.errors)
        v = _validate([res(cid, "functional", [(2, "3 years of experience in contract management")],
                           [{"line": 2, "text": "contract management", "type": "function"}], duration="D1")],
                      jd=jd, analysis=YEARS_ONLY)
        assert v.ok
        v = _validate([res(cid, "functional", [(2, "3 years of experience in contract management")],
                           [{"line": 2, "text": "ontract manag", "type": "function"}], duration="D1")],
                      jd=jd, analysis=YEARS_ONLY)
        assert any("whole words" in e for e in v.errors)                 # no partial-word spans

    def test_duration_only_for_years_criteria(self):
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        jd = "- 2 years as a Registered Nurse"
        (c,) = enumerate_experience_criteria("JOB-2026-0031", a)
        v = _validate([res(c.criterion_id, "explicit_role", [(1, jd[2:])], [{"hint": "T1", "type": "role"}],
                           duration="D1")], jd=jd, analysis=a)
        assert any("no years requirement" in e for e in v.errors)


# ── 23-24 repair / technical failures ───────────────────────────────────────

class TestCallFlow:
    def test_repair_success(self):
        cid = job31_cid()
        bad = job31_ok(cid, targets=[{"hint": "T1", "type": "role"}])
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(bad), ai(job31_ok(cid)), job_id="JOB-2026-0031")
        assert out.status == "ok" and out.meta["outcome"] == "repaired" and out.meta["calls"] == 2
        assert out.artifacts[0].spec_status == "resolved"
        assert out.validation["errors"] and out.validation["repair_errors"] == []
        rep = client.requests[1]["messages"]
        assert rep[2] == {"role": "assistant",
                          "content": with_match(ai(bad), JOB31_JD, JOB31_ANALYSIS, "JOB-2026-0031")}
        assert "hint T2" in rep[3]["content"] and "never add, drop or rewrite targets" in rep[3]["content"]
        assert [c["call"] for c in out.meta["call_log"]] == ["main", "repair"]

    def test_repair_still_invalid_is_failed_validation_and_cached(self):
        cid = job31_cid()
        bad = ai(job31_ok(cid, ambiguity=["unsure"]))
        cache = clf.InMemoryS1Cache()
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, bad, bad, job_id="JOB-2026-0031", cache=cache)
        (art,) = out.artifacts
        assert (out.status_reason, art.spec_status, art.retryable) == ("validation_failed", "failed_validation",
                                                                         False)
        assert [r.code for r in art.reasons] == ["validation_failed"]
        assert out.validation["repair_errors"]
        assert len(cache.store) == 1
        client2, again = classify(JOB31_JD, JOB31_ANALYSIS, job_id="JOB-2026-0031", cache=cache)
        assert client2.requests == [] and again.meta["cache_hit"] and again.artifacts[0].spec_status == \
            "failed_validation"

    @pytest.mark.parametrize("items, reason, retryable, calls", [
        ((RuntimeError("network down"),), "ai_unavailable", True, 0),
        (((ai(), "length"),), "output_truncated", False, 1),
        ((ai(), RuntimeError("timeout")), "ai_unavailable", True, 1),
        ((ai(), (ai(), "length")), "output_truncated", False, 2),
    ])
    def test_technical_failure_is_never_business_state(self, items, reason, retryable, calls):
        cache = clf.InMemoryS1Cache()
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, *items, job_id="JOB-2026-0031", cache=cache)
        (art,) = out.artifacts
        assert (art.spec_status, art.retryable, out.status_reason) == ("failed_technical", retryable, reason)
        assert [(r.code, r.kind) for r in art.reasons] == [(reason, "technical")]
        assert art.policy is None and art.targets == ()
        assert cache.store == {}                                  # technical outcomes are not cached
        assert len(client.requests) == len(items) and out.meta["calls"] == calls

    def test_exceeds_model_context_makes_no_call(self, monkeypatch):
        monkeypatch.setattr(clf, "S1_MAX_INPUT_TOKENS", 50)
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, job_id="JOB-2026-0031")
        assert client.requests == []
        assert (out.status_reason, out.artifacts[0].spec_status, out.artifacts[0].retryable) == (
            "exceeds_model_context", "failed_technical", False)

    def test_assembly_bug_is_internal_error(self, monkeypatch):
        def boom(*a, **k):
            raise KeyError("bug")
        monkeypatch.setattr(clf, "assemble_artifact", boom)
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        assert (out.status_reason, out.artifacts[0].spec_status, out.artifacts[0].retryable) == (
            "internal_error", "failed_technical", True)

    def test_request_contract(self):
        client, _ = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(job31_cid())), job_id="JOB-2026-0031")
        kw = client.requests[0]
        assert (kw["model"], kw["temperature"], kw["response_format"]) == ("gpt-4o-mini", 0.0,
                                                                           {"type": "json_object"})
        assert kw["messages"][0]["content"] == clf.S1_SYSTEM_PROMPT
        payload = json.loads(kw["messages"][1]["content"].split("\n", 1)[1])
        assert payload["duration_candidates"] == [{"id": "D1", "line": 6, "text": "5 years"}]
        (crit,) = payload["criteria"]
        assert crit["target_hints"] == [{"id": "T1", "text": "Construction Project Manager"},
                                        {"id": "T2", "text": "Assistant Project Manager"}]
        assert crit["has_years"] is True and "min_years" not in crit


# ── 25-26 determinism ───────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_artifact_and_hash(self):
        cid = job31_cid()
        outs = [classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")[1]
                for _ in range(2)]
        a, b = (o.artifacts[0] for o in outs)
        assert a.to_dict() == b.to_dict()
        assert a.input_hash == b.input_hash and a.content_hash == b.content_hash
        assert a.spec_version.startswith(f"s1-{sc.S1_VERSION}-") and a.prompt_fingerprint == clf.prompt_fingerprint()
        assert sc.S1Artifact.from_dict(json.loads(json.dumps(a.to_dict()))) == a

    def test_cache_hit_reproduces_artifact(self):
        cid = job31_cid()
        cache = clf.InMemoryS1Cache()
        _, first = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031", cache=cache)
        client, second = classify(JOB31_JD, JOB31_ANALYSIS, job_id="JOB-2026-0031", cache=cache)
        assert client.requests == [] and second.meta["cache_hit"]
        assert second.artifacts[0].content_hash == first.artifacts[0].content_hash
        assert second.artifacts[0].spec_version == first.artifacts[0].spec_version

    def test_jd_change_changes_input_hash(self):
        cid = job31_cid()
        _, a = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        _, b = classify(JOB31_JD + "\n- Fluent English", JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        assert a.artifacts[0].input_hash != b.artifacts[0].input_hash

    def test_views_are_deterministic(self):
        jd = "- At least 3 years as a Procurement Officer or in contract management"
        cid = only_cid(YEARS_ONLY)
        r = res(cid, "mixed", [(1, jd[2:])], [{"line": 1, "text": "Procurement Officer", "type": "role"},
                                               {"line": 1, "text": "contract management", "type": "function"}],
                duration="D1")
        art = classify(jd, YEARS_ONLY, ai(r))[1].artifacts[0]
        v1, v2 = views(art), views(art)
        assert v1 == v2 and [v.policy for v in v1] == ["explicit_role", "functional"]
        assert views(sc.S1Artifact.from_dict(art.to_dict())) == v1
        # the resolution survives the round trip (and keeps the view identical)
        r = with_resolution(art)
        assert asm.s2_views(sc.S1Artifact.from_dict(r.to_dict())) == v1


# ── 27 isolation ────────────────────────────────────────────────────────────

class TestIsolation:
    # The one allowed reader: the (unwired) qualifying-context service reuses the pure JD-text utilities for
    # verbatim grounding, and nothing else from S1.
    ALLOWED_READERS = {("qualifying_context", "validation.py"): "from services.s1_requirements.jd_text import "}

    def test_nothing_in_production_imports_s1(self):
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            root = BACKEND / sub
            for p in root.rglob("*.py") if root.exists() else []:
                if "s1_requirements" in p.parts:
                    continue
                text = p.read_text(encoding="utf-8")
                allowed = self.ALLOWED_READERS.get((p.parent.name, p.name))
                if allowed:
                    rest = [ln for ln in text.splitlines() if "s1_requirements" in ln and not ln.startswith(allowed)]
                    if rest:
                        hits.append(f"{p}: {rest}")
                elif "s1_requirements" in text:
                    hits.append(str(p))
        assert hits == []

    def test_s1_does_not_import_d01_or_s2(self):
        import ast
        for p in (BACKEND / "services" / "s1_requirements").glob("*.py"):
            mods = set()
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    mods |= {a.name for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    mods.add(node.module or "")
            for m in mods:
                for banned in ("llm_criteria_mapper", "s2_experience", "routers", "database", "sqlalchemy",
                               "models", "workers"):
                    assert banned not in m, (p.name, m)

    def test_requirement_spec_unchanged(self):
        import dataclasses
        assert [f.name for f in dataclasses.fields(RequirementSpec)] == [
            "policy", "required_years", "targets", "setting", "spec_version", "criterion_id",
            "criterion_text", "source_spans"]


# ── s1-5.1: canonical words, relation messages, abbreviations, duplicates, statement anchor, withdrawal ──

from services.s1_requirements import repair as rp                                  # noqa: E402
from services.s1_requirements.jd_text import locate_words, words                  # noqa: E402
from services.s1_requirements.validator import ParsedCriterion, ParsedTarget, ScopedError  # noqa: E402

PM_LINE = "Minimum 5 years as a Project Manager (P.M.)"


def _accepts(*args, **kw):
    v, jd, a, raw = _eqv(*args, **kw)
    assert v.ok, v.errors
    out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw)))
    art = out.artifacts[0]
    assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
    return art


class TestS151CanonicalWords:
    def test_words_and_dotted_acronyms(self):
        assert words(PM_LINE)[-3:] == ["project", "manager", "pm"]
        assert words("U.X") == ["ux"] and words("s.manager") == ["s", "manager"]   # not an acronym
        assert words("كمحاسب") == ["كمحاسب"]

    def test_locate_uses_the_span_boundary_rule(self):
        assert locate_words(["محاسب"], ["كمحاسب"]) == [(0, "ك")]
        assert locate_words(["مدير"], ["وبمدير"]) == [(0, "وب")]
        assert locate_words(["مدير"], ["ممدير"]) == []                                # not a proclitic chain
        assert locate_words(["محاسب"], ["المحاسب"]) == []                             # no other prefix stripping
        assert locate_words(["مدير"], ["مديرين"]) == []                                # right edge strict
        assert locate_words(["manager"], ["kmanager"]) == []                            # Latin unaffected
        # one implementation: JDText.find accepts exactly the same left residue
        assert JDText("خبرة كمحاسب").find("محاسب") and not JDText("خبرة ممحاسب").find("محاسب")

    @pytest.mark.parametrize("line, span, pairs", [
        ("خبرة سنتين كمحاسب", "كمحاسب", [("Accountant", "محاسب", TR)]),
        ("خبرة سنتين محاسب", "محاسب", [("Accountant", "محاسب", TR)]),
    ])
    def test_accountant_inside_attached_letter(self, line, span, pairs):
        _accepts("Accountant", line, span, pairs, years=2, ar=True)

    @pytest.mark.parametrize("line, span", [
        ("خبرة 5 سنوات كمدير مشروع", "كمدير مشروع"),
        ("خبرة 5 سنوات وبمدير مشروع", "وبمدير مشروع"),
    ])
    def test_manager_inside_proclitic_chain(self, line, span):
        _accepts("Project Manager", line, span, [("Project", "مشروع", TR), ("Manager", "مدير", TR)], ar=True)

    @pytest.mark.parametrize("line, span", [
        ("خبرة سنتين ممحاسب", "ممحاسب"),                  # arbitrary left residue
        ("خبرة سنتين المحاسب", "المحاسب"),                # the article is not an approved proclitic
        ("خبرة سنتين كمحاسبين", "كمحاسبين"),              # right-side residue
    ])
    def test_other_residues_rejected(self, line, span):
        v, *_ = _eqv("Accountant", line, span, [("Accountant", "محاسب", TR)], years=2, ar=True)
        assert errs_with(v, "is not whole word(s) of the jd_span")


class TestS151Relations:
    LINE = "خبرة 5 سنوات كمدير مشروع"

    def test_cross_language_same_or_form_names_translation(self):
        for rel in (SA, FO):
            v, *_ = _eqv("Project Manager", self.LINE, "كمدير مشروع",
                         [("Project", "مشروع", rel), ("Manager", "مدير", TR)], ar=True)
            assert errs_with(v, "'Project' / 'مشروع' are in different languages") and errs_with(
                v, "use relation translation")

    def test_cross_language_translation_accepted(self):
        _accepts("Project Manager", self.LINE, "كمدير مشروع", [("Project", "مشروع", TR), ("Manager", "مدير", TR)],
                 ar=True)

    def test_same_allows_only_the_approved_orthographic_residue(self):
        line = "خبرة 5 سنوات كمدير مشروع"
        v, *_ = _eqv("مدير مشروع", line, "كمدير مشروع", [("مدير", "كمدير", SA), ("مشروع", "مشروع", SA)], ar=True)
        assert v.ok, v.errors                                               # ك is orthographic, as for spans
        v, *_ = _eqv("مدير مشروع", "خبرة 5 سنوات ممدير مشروع", "ممدير مشروع",
                     [("مدير", "ممدير", SA), ("مشروع", "مشروع", SA)], ar=True)
        assert errs_with(v, "are not the identical word")

    def test_plural_needs_form(self):
        line, span = "Minimum 4 years of experience administering databases", "administering databases"
        v, *_ = _eqv("database administration", line, span,
                     [("database", "databases", SA), ("administration", "administering", FO)], years=4)
        assert errs_with(v, "'database' / 'databases' are not the identical word") and errs_with(
            v, "use relation form")
        v, jd, a, raw = _eqv("database administration", line, span,
                             [("database", "databases", FO), ("administration", "administering", FO)], years=4)
        assert v.ok, v.errors                                 # structurally valid; s1-5.2: candidate only
        assert_form_candidate(run(clf.classify_job("J1", jd, a, client=FakeClient(raw))).artifacts[0])


class TestS151Abbreviations:
    @pytest.mark.parametrize("span, pairs", [
        ("Project Manager", [("PM", "Project Manager", AB)]),        # preferred: the full form
        ("P.M.", [("PM", "P.M.", AB)]),                              # the acronym only
        ("P.M.", [("PM", "P.M.", SA)]),                              # dotted acronym = the same canonical word
    ])
    def test_one_occurrence_accepted(self, span, pairs):
        _accepts("PM", PM_LINE, span, pairs)

    @pytest.mark.parametrize("pairs", [[("PM", "Project Manager", AB)], [("PM", "P.M.", AB)]])
    def test_span_holding_both_forms_rejected(self, pairs):
        # s1-5.2.2: the span is the JD's own definition -> the specific duplicate-representation issue only
        v, *_ = _eqv("PM", PM_LINE, "Project Manager (P.M.)", pairs)
        (e,) = v.scoped
        assert e.issue.kind == "duplicate_representation" and "narrow jd_span to exactly ONE of them" in e.message
        assert not errs_with(v, "adds words to the target")

    def test_only_acronym_in_jd(self):
        _accepts("PM", "Minimum 5 years as a P.M. in a fast-paced environment", "P.M.", [("PM", "P.M.", AB)])

    def test_other_unaccounted_words_keep_the_generic_error(self):
        v, *_ = _eqv("PM", "Minimum 5 years as a Senior Project Manager", "Senior Project Manager",
                     [("PM", "Project Manager", AB)])
        assert errs_with(v, "unaccounted ['senior']") and not errs_with(v, "names the same role twice")

    def test_exact_match_semantics_unchanged(self):
        # canonical acronym keys are for alignment only: "PM" is still not word for word in "P.M."
        assert JDText("as a P.M. here").find("PM") == []


class TestS151DuplicatesAndStatementAnchor:
    def test_duplicate_hint_rejected_with_one_object_message(self):
        a, jd, (c,) = _job(["Accountant"], 2, ["المتطلبات", "- خبرة سنتين كمحاسب."])
        t = {"hint": "T1", "type": "role", "match": "none", "jd_span": None}
        item = {"criterion_id": c.criterion_id, "requirement_spans": [{"line": 2, "text": "خبرة سنتين كمحاسب"}],
                "targets": [t, dict(t, type="function")], "setting": None, "duration": "D1", "ambiguity": [],
                "note": "n"}
        j = JDText(jd)
        v = validate_response(_resp(item), j, [c], {d: (ln, m) for d, ln, m in j.durations()})
        assert errs_with(v, "must appear exactly once in targets, found 2: keep ONE object for T1 with one type, "
                            "one match and, if equivalent, one complete alignment")

    def _p1(self, marked):
        a, jd, (c,) = _job(["Registered Nurse"], 0, ["Requirements", "- Experience in nursing is preferred."])
        span = {"line": 2, "text": "Experience in nursing is preferred"}
        if marked:
            span["experience_requirement"] = True
        return a, jd, c, {"criterion_id": c.criterion_id, "requirement_spans": [span],
                          "targets": [{"hint": "T1", "type": "role", "match": "none", "jd_span": None}],
                          "setting": None, "duration": None, "ambiguity": [], "note": "n"}

    def test_target_none_is_not_requirement_absent(self):
        a, jd, c, plain = self._p1(False)
        j = JDText(jd)
        v = validate_response(_resp(plain), j, [c], {})
        assert errs_with(v, "keep it and mark it \"experience_requirement\": true") and errs_with(
            v, "only if the JD contains no such experience requirement at all, return [] with requirement_not_in_jd")
        _, _, _, marked = self._p1(True)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(_resp(plain), _resp(marked))))
        art = out.artifacts[0]
        assert art.spec_status == "needs_confirmation" and art.audit["requirement_anchor"] == "statement"
        assert [t.provenance for t in art.targets] == ["original_ai"]
        assert sc.AMB_REQUIREMENT_NOT_IN_JD not in [r.code for r in art.reasons]

    def test_about_us_statement_never_resolves(self):
        a, jd, (c,) = _job([], 3, ["We are a leading logistics group.", "Requirements", "- Teamwork."])
        item = {"criterion_id": c.criterion_id, "requirement_spans": [
            {"line": 1, "text": "We are a leading logistics group", "experience_requirement": True}],
            "restrictions": [], "duration": None, "ambiguity": [], "note": "n"}
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(_resp(item))))
        assert out.artifacts[0].spec_status != "resolved"


def _c2(**over):
    """C2-style dishonest alignment (the repair kept it): Construction has no counterpart."""
    args = ("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع",
            [("Construction", "مشروع", TR), ("Project", "مشروع", SA), ("Manager", "مدير", SA)])
    v, jd, a, raw = _eqv(*args, ar=True)
    d = json.loads(raw)
    d["criteria"][0].update(over)
    return v, jd, a, json.dumps(d, ensure_ascii=False)


class TestS151Withdrawal:
    def test_c2_withdrawn_to_none_after_repair(self):
        v, jd, a, raw = _c2()
        assert not v.ok
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw)))
        art = out.artifacts[0]
        assert out.meta["outcome"] == "repaired_withdrawn" and art.spec_status == "needs_confirmation"
        assert [(t.text, t.type, t.provenance, t.jd_span) for t in art.targets] == [
            ("Construction Project Manager", "role", "original_ai", None)]
        assert art.audit["alignment_withdrawn"] is True and art.audit["target_mappings"] == []
        (w,) = art.audit["withdrawals"]
        assert w["hint"] == "T1" and w["type"] == "role" and w["withdrawn_claim"]["match"] == "equivalent"
        assert w["withdrawn_claim"]["jd_span"] == {"line": 2, "text": "كمدير مشروع"} and w["errors"]
        # nothing else changed: spans, duration (jd_verified 5), reasons only target_not_in_jd
        assert art.requirement_text == "خبرة 5 سنوات كمدير مشروع"
        assert art.required_years.value == 5 and art.required_years.provenance == "jd_verified"
        assert [r.code for r in art.reasons] == [sc.BIZ_TARGET_NOT_IN_JD]
        with pytest.raises(asm.S1ViewError):                                          # no scoring view
            asm.s2_views(art)

    def test_h1_forced_alignment_withdrawn(self):
        args = ("software implementation", "Minimum 3 years of experience implementing enterprise software systems",
                "implementing enterprise software systems",
                [("software", "software systems", SA), ("implementation", "implementing enterprise", FO)])
        v, jd, a, raw = _eqv(*args, years=3)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw)))
        art = out.artifacts[0]
        assert art.spec_status == "needs_confirmation" and [t.provenance for t in art.targets] == ["original_ai"]
        assert art.audit["alignment_withdrawn"]

    @pytest.mark.parametrize("over", [
        {"ambiguity": ["unsure"]},                                         # ambiguity error
        {"duration": "D9"},                                                # duration error
        {"requirement_spans": [{"line": 2, "text": "not in the JD"}]},     # span error
        {"setting": {"line": 1, "text": "المتطلبات"}},                    # setting error
    ])
    def test_unrelated_error_blocks_withdrawal(self, over):
        _, jd, a, raw = _c2(**over)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw)))
        assert out.artifacts[0].spec_status == "failed_validation" and "alignment_withdrawn" not in out.meta

    def test_unrelated_error_in_another_criterion_blocks_withdrawal(self):
        crits = [SimpleNamespace(criterion_id=cid, target_hints=("A",)) for cid in ("C", "D")]
        raw = json.dumps({"criteria": [{"criterion_id": cid, "targets": [
            {"hint": "T1", "type": "role", "match": "equivalent"}]} for cid in ("C", "D")]})
        claim = ScopedError("C", ("target:T1:alignment",), "a")
        assert rp.plan_withdrawal([claim], raw, crits) == {"C": ("T1", ["a"])}
        assert rp.plan_withdrawal([claim, ScopedError("D", ("duration",), "b")], raw, crits) is None
        assert rp.plan_withdrawal([claim, ScopedError("D", ("restriction:R0",), "b")], raw, crits) is None

    @pytest.mark.parametrize("scoped", [
        [ScopedError("C", ("target:T1:alignment",), "a"), ScopedError("C", ("duration",), "b")],
        [ScopedError("C", ("target:T1",), "duplicate")],                              # whole target (duplicate)
        [ScopedError("C", ("target:T1:alignment", "target:T2:alignment"), "overlap")],  # two targets in one error
        [ScopedError("C", ("target:T1:alignment",), "a"), ScopedError("C", ("target:T2:match",), "b")],
        [ScopedError("C", ("target:T1:type",), "type")],                              # type is never withdrawable
        [ScopedError("C", ("target:T1:alignment", "ambiguity"), "adds words")],
        [ScopedError(None, ("response",), "bad json")],
        [],
    ])
    def test_plan_requires_one_claim_only(self, scoped):
        crit = SimpleNamespace(criterion_id="C", target_hints=("A", "B"))
        raw = json.dumps({"criteria": [{"criterion_id": "C", "targets": [
            {"hint": "T1", "type": "role", "match": "equivalent"}, {"hint": "T2", "type": "role", "match": "none"}]}]})
        assert rp.plan_withdrawal(scoped, raw, [crit]) is None

    @pytest.mark.parametrize("t1", [
        [{"hint": "T1", "type": "role", "match": "exact"}],                          # never touches exact/none
        [{"hint": "T1", "type": "role", "match": "none"}],
        [{"hint": "T1", "type": "role", "match": "equivalent"}] * 2,                 # duplicates never chosen
    ])
    def test_plan_only_for_a_single_equivalent_object(self, t1):
        crit = SimpleNamespace(criterion_id="C", target_hints=("A",))
        raw = json.dumps({"criteria": [{"criterion_id": "C", "targets": t1}]})
        assert rp.plan_withdrawal([ScopedError("C", ("target:T1:alignment",), "a")], raw, [crit]) is None

    def test_apply_touches_only_the_claim(self):
        crit = SimpleNamespace(criterion_id="C", target_hints=("A",))
        item = {"criterion_id": "C", "requirement_spans": [{"line": 2, "text": "x"}], "duration": "D1",
                "ambiguity": [], "setting": None,
                "targets": [{"hint": "T1", "type": "function", "match": "equivalent", "jd_span": {"line": 2,
                             "text": "y"}, "alignment": [{"hint": "a", "jd": "y", "relation": "same"}],
                             "jd_extra": []}]}
        raw = json.dumps({"criteria": [item]})
        plan = rp.plan_withdrawal([ScopedError("C", ("target:T1:alignment", "target:T1:match"), "why")], raw, [crit])
        new, audit = rp.apply_withdrawal(raw, plan)
        got = json.loads(new)["criteria"][0]
        assert got["targets"] == [{"hint": "T1", "type": "function", "match": "none", "jd_span": None,
                                   "alignment": [], "jd_extra": []}]
        assert {k: v for k, v in got.items() if k != "targets"} == {k: v for k, v in item.items() if k != "targets"}
        assert audit["C"]["errors"] == ["why"] and audit["C"]["withdrawn_claim"]["match"] == "equivalent"

    def test_assembler_refuses_a_withdrawn_target_that_establishes_evidence(self):
        a, jd, (c,) = _job(["Accountant"], 2, ["المتطلبات", "- خبرة سنتين كمحاسب."])
        j = JDText(jd)
        req = (j.span_on_line(2, "خبرة سنتين كمحاسب"),)
        durs = {d: (ln, m) for d, ln, m in j.durations()}
        rec = ({"hint": "T1", "type": "role", "withdrawn_claim": {}, "errors": []},)
        mapped = ParsedTarget("Accountant", "role", "T1", j.span_on_line(2, "كمحاسب"), "equivalent")
        pc = ParsedCriterion(c.criterion_id, "explicit_role", req, (mapped,), (), "D1", (), "", withdrawn=rec)
        with pytest.raises(ValueError, match="withdrawn equivalent claim"):
            asm.assemble_artifact(c, pc, j, durs, run={})
        plain = ParsedTarget("Accountant", "role", "T1", None, "none")
        ok = asm.assemble_artifact(c, ParsedCriterion(c.criterion_id, "explicit_role", req, (plain,), (), "D1",
                                                      (), "", withdrawn=rec), j, durs, run={})
        assert ok.spec_status == "needs_confirmation" and ok.audit["alignment_withdrawn"] is True

    def test_no_withdrawal_when_the_answer_is_valid(self):
        _, jd, a, ok = _eqv("Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع",
                            [("Project", "مشروع", TR), ("Manager", "مدير", TR)], ar=True)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(ok)))
        assert out.artifacts[0].audit["alignment_withdrawn"] is False and "alignment_withdrawn" not in out.meta


class TestS151QualifierRegression:
    """The qualifier false positives are still never accepted, even when the repair repeats them."""

    @pytest.mark.parametrize("args, kw", [
        (("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع",
          [("Project", "مشروع", TR), ("Manager", "مدير", TR)]), {"ar": True}),
        (("software implementation", "Minimum 3 years of experience implementing enterprise software systems",
          "implementing enterprise software systems",
          [("software", "software", SA), ("implementation", "implementing", FO)],
          [("enterprise", "grammatical"), ("systems", "material")]), {"years": 3}),
        (("Equine Veterinarian", "خبرة 3 سنوات كطبيب بيطري", "كطبيب بيطري",
          [("Veterinarian", "كطبيب بيطري", TR)]), {"years": 3, "ar": True}),
        (("web development", "خبرة 4 سنوات في تطوير مواقع التجارة الإلكترونية", "تطوير مواقع التجارة الإلكترونية",
          [("web", "مواقع", TR), ("development", "تطوير", TR)]), {"years": 4, "ar": True}),
        (("event planning", "Minimum 3 years of experience planning corporate events", "planning corporate events",
          [("event", "events", FO), ("planning", "planning", SA)], [("corporate", "material")]), {"years": 3}),
        (("Commercial Pilot", "Minimum 5 years of experience as a Pilot", "Pilot", [("Pilot", "Pilot", SA)]), {}),
    ])
    def test_never_jd_asserted(self, args, kw):
        v, jd, a, raw = _eqv(*args, **kw)
        assert not v.ok
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw)))
        art = out.artifacts[0]
        assert art.spec_status in ("needs_confirmation", "failed_validation")
        assert all(t.provenance != "jd_asserted" for t in art.targets)


# ── s1-5.2: FORM is not trust-bearing ───────────────────────────────────────────────────────────────

def _classify_eqv(*args, **kw):
    v, jd, a, raw = _eqv(*args, **kw)
    assert v.ok, v.errors                                   # structurally valid: the validator is unchanged
    return run(clf.classify_job("J1", jd, a, client=FakeClient(raw))).artifacts[0]


class TestS152FormNotTrustBearing:
    def test_a_administration_support_form_is_only_a_candidate(self):
        art = _classify_eqv("database administration", "Minimum 4 years of experience in database support",
                            "database support",
                            [("database", "database", SA), ("administration", "support", FO)], years=4)
        m = assert_form_candidate(art, "database support")
        (r,) = art.reasons
        assert r.detail == ("'database administration' ~ 'database support' (unverified grammatical-form "
                            "equivalence: administration→support)")
        assert m["relations"] == ["form", "same"] and m["alignment"][1] == {
            "hint": "administration", "jd": "support", "relation": "form"}

    def test_b_valid_form_proposal_same_safe_outcome(self):
        art = _classify_eqv("database administration", "Minimum 4 years of experience administering databases",
                            "administering databases",
                            [("database", "databases", FO), ("administration", "administering", FO)], years=4)
        assert_form_candidate(art, "administering databases")
        assert art.reasons[0].detail.endswith("database→databases; administration→administering)")
        assert art.required_years.provenance == "jd_verified"                 # the rest of the criterion intact

    @pytest.mark.parametrize("args, kw, rels", [
        (("Accountant", "خبرة سنتين كمحاسب", "كمحاسب", [("Accountant", "محاسب", TR)]), {"years": 2, "ar": True},
         ["translation"]),
        (("PM", "Minimum 5 years of experience as a Project Manager (P.M.)", "Project Manager",
          [("PM", "Project Manager", AB)]), {}, ["abbreviation"]),           # s1-5.2.2: a JD-defined abbreviation
    ])
    def test_c_trust_bearing_equivalent_stays_jd_asserted(self, args, kw, rels):
        art = _classify_eqv(*args, **kw)
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
        (m,) = art.audit["target_mappings"]
        assert m["used"] is True and m["trust"] == "trust_bearing" and m["relations"] == rels
        assert len(views(art)) == 1                                      # unchanged: a scoring view

    def test_d_genuine_exact_phrase_stays_jd_verified(self):
        out = run_case(["database administration"], "Minimum 4 years of experience in database administration",
                       years=4, types=["function"])
        assert prov(out) == [("T1", "jd_verified")] and out.artifacts[0].spec_status == "resolved"
        # a form mapping elsewhere in the span never decides it: the complete phrase is the evidence
        line = "Minimum 4 years of experience in database administration, administering databases"
        v, jd, a, raw = _eqv("database administration", line, "administering databases",
                             [("database", "databases", FO), ("administration", "administering", FO)], years=4)
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw))).artifacts[0]
        assert [t.provenance for t in art.targets] == ["jd_verified"]
        assert "equivalence_unverified" not in [r.code for r in art.reasons]
        assert art.audit["target_mappings"][0]["trust"] == "unverified_form"
        assert art.audit["target_mappings"][0]["used"] is False

    def test_e_no_s2_view_even_for_a_permissive_caller(self):
        art = _classify_eqv("database administration", "Minimum 4 years of experience administering databases",
                            "administering databases",
                            [("database", "databases", FO), ("administration", "administering", FO)], years=4)
        with pytest.raises(asm.S1ViewError, match="unverified equivalence"):
            asm.s2_views(art, require_resolved=False)
        # contrast: an ordinary needs_confirmation artifact has a permissive preview view only once its qualifying
        # context is resolved (s1-6: never on its own)
        plain = run_case(["database administration"], "Minimum 4 years of experience in database support",
                         years=4, types=["function"]).artifacts[0]
        assert plain.spec_status == "needs_confirmation" and views(plain, require_resolved=False)

    def test_f_genuine_none_keeps_target_not_in_jd(self):
        out = run_case(["database administration"], "Minimum 4 years of experience in database support",
                       years=4, types=["function"])
        assert prov(out) == [("T1", "original_ai")] and codes(out) == ["target_not_in_jd"]
        assert out.artifacts[0].audit["target_mappings"] == []

    def test_g_withdrawal_unchanged_and_never_a_form_candidate(self):
        _, jd, a, raw = _c2()
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw))).artifacts[0]
        assert art.audit["alignment_withdrawn"] and [r.code for r in art.reasons] == ["target_not_in_jd"]
        assert art.audit["target_mappings"] == []

    def test_reason_taxonomy(self):
        assert sc.REASON_KINDS["equivalence_unverified"] == "business"
        assert sc.Reason("equivalence_unverified", "targets.T1", "x").to_dict() == {
            "code": "equivalence_unverified", "kind": "business", "field": "targets.T1", "detail": "x"}
        assert sc.S1_SCHEMA == "s1_requirement_spec_v3"          # s1-5.2 kept v2; s1-6 (settings) bumped to v3

    def test_artifact_roundtrip_keeps_candidate(self):
        art = _classify_eqv("database administration", "Minimum 4 years of experience administering databases",
                            "administering databases",
                            [("database", "databases", FO), ("administration", "administering", FO)], years=4)
        back = sc.S1Artifact.from_dict(art.to_dict())
        assert back.to_dict() == art.to_dict()
        with pytest.raises(asm.S1ViewError):
            asm.s2_views(back, require_resolved=False)

    def test_j_form_cannot_launder_a_mislabelled_qualifier(self):
        # "corporate" dishonestly labelled grammatical passes the structure; the form pair keeps it a candidate
        art = _classify_eqv("event planning", "Minimum 3 years of experience planning corporate events",
                            "planning corporate events",
                            [("event", "events", FO), ("planning", "planning", SA)], [("corporate", "grammatical")],
                            years=3)
        assert_form_candidate(art)


# ── s1-5.2.1: structured issues, pair-level repair merge, material lock ────────────────────────────

from services.s1_requirements.validator import Issue  # noqa: E402

B1_LINE, B1_SPAN = "خبرة لا تقل عن 5 سنوات كمدير مشروع إنشائي", "كمدير مشروع إنشائي"
B1_OK = [("Construction", "إنشائي", TR), ("Project", "مشروع", TR), ("Manager", "مدير", TR)]


def _pairs(pairs):
    return [{"hint": h, "jd": j, "relation": r} for h, j, r in pairs]


def _repair_of(raw, **target_over):
    d = json.loads(raw)
    d["criteria"][0]["targets"][0].update(target_over)
    return json.dumps(d, ensure_ascii=False)


def _b1(main_pairs, repair_pairs=None, **repair_over):
    v, jd, a, raw = _eqv("Construction Project Manager", B1_LINE, B1_SPAN, main_pairs, ar=True)
    rep = _repair_of(raw, **({"alignment": _pairs(repair_pairs)} if repair_pairs is not None else {}), **repair_over)
    client = FakeClient(raw, rep)
    out = run(clf.classify_job("J1", jd, a, client=client))
    return v, out, client


def _merged_alignment(out):
    return [(p["hint"], p["jd"], p["relation"]) for p in out.artifacts[0].audit["target_mappings"][0]["alignment"]]


class TestS1521StructuredIssues:
    def test_pair_issue_is_machine_addressable(self):
        v, *_ = _eqv("Construction Project Manager", B1_LINE, B1_SPAN,
                     B1_OK[:2] + [("Manager", "مدير", SA)], ar=True)
        (e,) = v.scoped
        assert e.issue == Issue("T1", "pair", "relation", 2, ("manager",))
        assert e.scopes == ("target:T1:alignment", "target:T1:jd_extra", "target:T1:match", "target:T1:jd_span")

    def test_non_local_units(self):
        v, *_ = _eqv("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع",
                     [("Project", "مشروع", TR), ("Manager", "مدير", TR)], ar=True)
        assert [e.issue.unit for e in v.scoped] == ["coverage"]
        v, *_ = _eqv("event planning", "Minimum 3 years of experience planning corporate events",
                     "planning corporate events", [("event", "events", FO), ("planning", "planning", SA)],
                     [("corporate", "material")], years=3)
        assert [(e.issue.unit, e.issue.kind, e.issue.index) for e in v.scoped] == [("extra", "material", 0)]
        v, *_ = _eqv("Accountant", "خبرة سنتين كمحاسب", "كمحاسب", [("Auditor", "محاسب", TR)], years=2, ar=True)
        assert v.scoped[0].issue == Issue("T1", "pair", "copy", 0)            # not target words: never pair-merged


class TestS1521PairMerge:
    def test_a_b1_valid_pairs_preserved_invalid_pair_taken(self):
        v, out, client = _b1(B1_OK[:2] + [("Manager", "مدير", SA)],
                             [("Construction", "إنشائي", TR), ("Project", "مشروع", SA), ("Manager", "مدير", TR)])
        assert not v.ok
        art = out.artifacts[0]
        assert _merged_alignment(out) == B1_OK                          # Project kept from main, Manager from repair
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
        rm = out.meta["repair_merge"]
        assert rm["pair_merged"] == [{"criterion_id": art.criterion_id, "hint": "T1", "invalid": [2], "replaced": [2]}]
        assert rm["discarded_changes"] == 1                                  # the Project damage was discarded
        note = client.requests[1]["messages"][-1]["content"]
        assert "ONLY alignment[2] (target word(s) 'manager') will be taken" in note

    def test_b_g2_form_pair_repaired_but_still_unverified(self):
        line, span = "Minimum 4 years of experience administering databases", "administering databases"
        v, jd, a, raw = _eqv("database administration", line, span,
                             [("database", "databases", SA), ("administration", "administering", FO)], years=4)
        rep = _repair_of(raw, alignment=_pairs([("database", "databases", FO), ("administration", "support", FO)]))
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep)))
        art = out.artifacts[0]
        assert _merged_alignment(out) == [("database", "databases", FO), ("administration", "administering", FO)]
        assert_form_candidate(art, "administering databases")               # FORM stays non-trust-bearing
        with pytest.raises(asm.S1ViewError):                                 # O: hard S2 block
            asm.s2_views(art, require_resolved=False)

    def test_c_g2_repair_gives_up_change_discarded_then_withdrawn(self):
        line, span = "Minimum 4 years of experience administering databases", "administering databases"
        v, jd, a, raw = _eqv("database administration", line, span,
                             [("database", "databases", SA), ("administration", "administering", FO)], years=4)
        rep = _repair_of(raw, match="none", jd_span=None, alignment=None, jd_extra=None)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep)))
        art = out.artifacts[0]
        assert out.meta["repair_merge"]["pair_merged"][0]["replaced"] == []   # no pair invented
        assert out.meta["outcome"] == "repaired_withdrawn" and art.audit["alignment_withdrawn"]
        assert [t.provenance for t in art.targets] == ["original_ai"] and art.spec_status == "needs_confirmation"
        assert [r.code for r in art.reasons] == ["target_not_in_jd"]

    def test_d_duplicate_replacement_is_ambiguous(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)],
                        B1_OK + [("Manager", "كمدير", TR)])
        assert out.meta["repair_merge"]["pair_merged"][0]["replaced"] == []
        assert out.artifacts[0].audit["alignment_withdrawn"] and out.artifacts[0].spec_status == "needs_confirmation"

    def test_e_reordered_repair_pairs(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)], list(reversed(B1_OK)))
        assert _merged_alignment(out) == B1_OK and out.artifacts[0].spec_status == "resolved"

    def test_f_g_h_repair_cannot_touch_valid_pairs_span_or_match(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)],
                        [("Construction", "إنشائي", FO), ("Project", "مشروع", SA), ("Manager", "مدير", TR)],
                        match="none", jd_span={"line": 2, "text": "مشروع إنشائي"},
                        jd_extra=[{"text": "مدير", "kind": "grammatical"}])
        art = out.artifacts[0]
        m = art.audit["target_mappings"][0]
        assert _merged_alignment(out) == B1_OK and m["match"] == "equivalent" and m["mapped_text"] == B1_SPAN
        assert m["jd_extra"] == [] and art.spec_status == "resolved"

    def test_i_replacement_outside_the_main_span_is_rejected(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)], B1_OK[:2] + [("Manager", "خبرة", TR)])
        assert _alignment_was_withdrawn(out)

    def test_j_multiple_invalid_pairs(self):
        _, out, _ = _b1([("Construction", "إنشائي", TR), ("Project", "مشروع", SA), ("Manager", "مدير", SA)], B1_OK)
        assert out.meta["repair_merge"]["pair_merged"][0]["invalid"] == [1, 2]
        assert _merged_alignment(out) == B1_OK and out.artifacts[0].spec_status == "resolved"

    def test_j_partial_replacement_keeps_the_other_invalid_pair(self):
        _, out, _ = _b1([("Construction", "إنشائي", TR), ("Project", "مشروع", SA), ("Manager", "مدير", SA)],
                        [("Manager", "مدير", TR)])                               # Project never repaired
        assert out.meta["repair_merge"]["pair_merged"][0]["replaced"] == [2] and _alignment_was_withdrawn(out)

    def test_k_coverage_interaction_rejected_by_full_validation(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)], B1_OK[:2] + [("Manager", "مشروع", TR)])
        assert out.meta["repair_merge"]["pair_merged"][0]["replaced"] == [2] and _alignment_was_withdrawn(out)

    def test_k_coverage_error_stays_whole_target(self):
        # an unpaired target word is not pair-local: today's whole-target scope applies (no pair merge)
        v, jd, a, raw = _eqv("Construction Project Manager", B1_LINE, B1_SPAN, B1_OK[1:], ar=True)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, _repair_of(raw, alignment=_pairs(B1_OK)))))
        assert out.meta["repair_merge"]["pair_merged"] == [] and out.artifacts[0].spec_status == "resolved"

    def test_l_abbreviation_pair_untouched_and_split_allowed(self):
        line, span = "Minimum 5 years of experience as a Human Resources Manager", "Human Resources Manager"
        v, jd, a, raw = _eqv("HR Manager", line, span, [("HR", "Human Resources", AB), ("Manager", "Manager", TR)])
        rep = _repair_of(raw, alignment=_pairs([("HR", "Human", AB), ("Manager", "Manager", SA)]))
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep)))
        assert _merged_alignment(out) == [("HR", "Human Resources", AB), ("Manager", "Manager", SA)]
        assert_abbreviation_candidate(out.artifacts[0])            # s1-5.2.2: HR is not defined in this JD
        # a lumped pair may be replaced by pairs that exactly split its target words
        _, out, _ = _b1([("Construction", "إنشائي", TR), ("Project Manager", "مدير", TR)],
                        [("Project", "مشروع", TR), ("Manager", "مدير", TR)])
        assert out.meta["repair_merge"]["pair_merged"][0]["replaced"] == [1]

    def test_m_translation_trust_unchanged(self):
        _, out, _ = _b1(B1_OK[:2] + [("Manager", "مدير", SA)], B1_OK)
        (m,) = out.artifacts[0].audit["target_mappings"]
        assert m["trust"] == "trust_bearing" and m["used"] is True and len(views(out.artifacts[0])) == 1


def _alignment_was_withdrawn(out):
    art = out.artifacts[0]
    return (art.audit.get("alignment_withdrawn") is True and art.spec_status == "needs_confirmation"
            and all(t.provenance != "jd_asserted" for t in art.targets))


class TestS1521MaterialLock:
    LINE, SPAN = "خبرة سنتين كمحاسب قانوني", "كمحاسب قانوني"
    MAIN = [("Accountant", "محاسب", TR)]

    def _run(self, **repair_over):
        v, jd, a, raw = _eqv("Accountant", self.LINE, self.SPAN, self.MAIN, [("قانوني", "material")], years=2, ar=True)
        assert not v.ok
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, _repair_of(raw, **repair_over))))
        return out, jd, a

    def _would_pass_unlocked(self, jd, a, **over):
        crits = enumerate_experience_criteria("J1", a)
        v, _, _, raw = _eqv("Accountant", self.LINE, self.SPAN, self.MAIN, [("قانوني", "material")], years=2, ar=True)
        j = JDText(jd)
        return validate_response(_repair_of(raw, **over), j, crits, {d: (ln, m) for d, ln, m in j.durations()}).ok

    def test_p_material_cannot_be_relabelled_grammatical(self):
        over = {"jd_extra": [{"text": "قانوني", "kind": "grammatical"}]}
        out, jd, a = self._run(**over)
        assert self._would_pass_unlocked(jd, a, **over)                       # the hole the lock closes
        assert _alignment_was_withdrawn(out) and out.meta["repair_merge"]["material_locked"]

    def test_q_material_cannot_be_removed_by_narrowing_the_span(self):
        over = {"jd_span": {"line": 2, "text": "كمحاسب"}, "jd_extra": []}
        out, jd, a = self._run(**over)
        assert self._would_pass_unlocked(jd, a, **over)
        assert _alignment_was_withdrawn(out) and out.meta["repair_merge"]["material_locked"]

    def test_r_material_cannot_be_absorbed_into_a_pair(self):
        over = {"alignment": _pairs([("Accountant", "محاسب قانوني", TR)]), "jd_extra": []}
        out, jd, a = self._run(**over)
        assert self._would_pass_unlocked(jd, a, **over)
        assert _alignment_was_withdrawn(out) and out.meta["repair_merge"]["material_locked"]

    def test_lock_also_holds_for_a_duplicated_hint(self):
        # main lists T1 twice (whole-target repair scope); one copy marks the qualifier material
        v, jd, a, raw = _eqv("Accountant", self.LINE, self.SPAN, self.MAIN, [("قانوني", "material")], years=2, ar=True)
        d = json.loads(raw)
        t = d["criteria"][0]["targets"][0]
        d["criteria"][0]["targets"] = [t, dict(t, type="function")]
        main = json.dumps(d, ensure_ascii=False)
        rep = _repair_of(raw, jd_extra=[{"text": "قانوني", "kind": "grammatical"}])
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(main, rep)))
        assert out.meta["repair_merge"]["material_locked"] and out.artifacts[0].spec_status == "failed_validation"

    def test_giving_up_is_always_allowed(self):
        out, _, _ = self._run(match="none", jd_span=None, alignment=None, jd_extra=None)
        art = out.artifacts[0]
        assert out.meta["repair_merge"]["material_locked"] == [] and [r.code for r in art.reasons] == [
            "target_not_in_jd"] and not art.audit["alignment_withdrawn"]


class TestS1521SafetyUnchanged:
    def test_s_withdrawal_unchanged(self):
        _, jd, a, raw = _c2()
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, raw))).artifacts[0]
        assert art.audit["alignment_withdrawn"] and [t.provenance for t in art.targets] == ["original_ai"]
        assert [r.code for r in art.reasons] == ["target_not_in_jd"]

    @pytest.mark.parametrize("hint, line, span, main_pairs, fixed, kw", [
        # C2: Construction has no counterpart; a pair-level "fix" cannot invent one
        ("Construction Project Manager", "خبرة 5 سنوات كمدير مشروع", "كمدير مشروع",
         [("Construction", "مشروع", SA), ("Project", "مشروع", TR), ("Manager", "مدير", TR)],
         [("Construction", "مشروع", TR)], {"ar": True}),
        # HO03: Equine has no counterpart
        ("Equine Veterinarian", "خبرة 3 سنوات كطبيب بيطري", "كطبيب بيطري",
         [("Equine", "بيطري", SA), ("Veterinarian", "كطبيب", TR)], [("Equine", "بيطري", FO)], {"years": 3, "ar": True}),
        # HO11: Commercial has no counterpart
        ("Commercial Pilot", "Minimum 5 years of experience as a Pilot", "Pilot",
         [("Commercial", "Pilot", TR), ("Pilot", "Pilot", SA)], [("Commercial", "Pilot", SA)], {}),
    ])
    def test_t_qualifier_regressions_stay_safe_through_pair_repair(self, hint, line, span, main_pairs, fixed, kw):
        v, jd, a, raw = _eqv(hint, line, span, main_pairs, **kw)
        assert not v.ok
        rep = _repair_of(raw, alignment=_pairs(fixed + [p for p in main_pairs if p[0] != fixed[0][0]]))
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep))).artifacts[0]
        assert all(t.provenance != "jd_asserted" for t in art.targets) and art.spec_status != "resolved"

    @pytest.mark.parametrize("hint, line, span, pairs, extra, kw", [
        ("software implementation", "Minimum 3 years of experience implementing enterprise software systems",
         "implementing enterprise software systems", [("software", "software", SA),
                                                      ("implementation", "implementing", FO)],
         [("enterprise", "material"), ("systems", "material")], {"years": 3}),            # H1
        ("web development", "خبرة 4 سنوات في تطوير مواقع التجارة الإلكترونية", "تطوير مواقع التجارة الإلكترونية",
         [("web", "مواقع", TR), ("development", "تطوير", TR)],
         [("التجارة", "material"), ("الإلكترونية", "material")], {"years": 4, "ar": True}),  # HO05
        ("event planning", "Minimum 3 years of experience planning corporate events", "planning corporate events",
         [("event", "events", FO), ("planning", "planning", SA)], [("corporate", "material")], {"years": 3}),  # HO07
    ])
    def test_t_material_qualifiers_cannot_be_repaired_away(self, hint, line, span, pairs, extra, kw):
        v, jd, a, raw = _eqv(hint, line, span, pairs, extra, **kw)
        rep = _repair_of(raw, jd_extra=[{"text": t, "kind": "grammatical"} for t, _ in extra])
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep))).artifacts[0]
        assert all(t.provenance != "jd_asserted" for t in art.targets) and art.spec_status != "resolved"
        assert "equivalence_unverified" not in [r.code for r in art.reasons]          # never even a candidate


# ── s1-5.2.2: JD-defined abbreviation trust + duplicate-representation normalization ───────────────

from services.s1_requirements.jd_text import abbreviation_definitions, exact_definition  # noqa: E402

K1_LINE = "Minimum 5 years as a Project Manager (P.M.)"


def _classify_raw(*args, **kw):
    v, jd, a, raw = _eqv(*args, **kw)
    client = FakeClient(raw, raw)
    out = run(clf.classify_job("J1", jd, a, client=client))
    return v, out, client


class TestS1522AbbreviationTrust:
    @pytest.mark.parametrize("hint, line, span, pairs", [
        ("PM", "Minimum 5 years as a Product Manager", "Product Manager", [("PM", "Product Manager", AB)]),   # A
        ("PM", "Minimum 5 years as a Plant Manager", "Plant Manager", [("PM", "Plant Manager", AB)]),         # B
        ("HR Manager", "Minimum 5 years as a Hotel Reservations Manager", "Hotel Reservations Manager",       # C
         [("HR", "Hotel Reservations", AB), ("Manager", "Manager", SA)]),
        ("UX Designer", "Minimum 5 years as a User Experience Designer", "User Experience Designer",          # D
         [("UX", "User Experience", AB), ("Designer", "Designer", SA)]),
        ("Human Resources Manager", "Minimum 5 years as an HR Manager", "HR Manager",                        # E
         [("Human Resources", "HR", AB), ("Manager", "Manager", SA)]),
    ])
    def test_a_to_e_undefined_expansions_are_never_evidence(self, hint, line, span, pairs):
        v, out, _ = _classify_raw(hint, line, span, pairs)
        assert v.ok
        m = assert_abbreviation_candidate(out.artifacts[0])                 # T: no S2, even require_resolved=False
        assert m["relations"] == sorted({r for _, _, r in pairs})

    def test_a_a_definition_of_another_role_does_not_help(self):
        line = "Minimum 5 years as a Project Manager (P.M.) or a Product Manager"
        v, out, _ = _classify_raw("PM", line, "Product Manager", [("PM", "Product Manager", AB)])
        assert_abbreviation_candidate(out.artifacts[0])

    @pytest.mark.parametrize("line, rel", [
        ("Minimum 5 years as a P.M. in a fast-paced environment", AB),
        ("Minimum 5 years as a P.M. in a fast-paced environment", SA),
    ])
    def test_f_same_acronym_orthography_unchanged(self, line, rel):
        v, out, _ = _classify_raw("PM", line, "P.M.", [("PM", "P.M.", rel)])
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
        assert art.audit["target_mappings"][0]["trust"] == "trust_bearing"

    @pytest.mark.parametrize("text, defined", [
        ("Minimum 5 years as a Project Manager (P.M.).", "Project Manager (P.M.)"),             # G
        ("Minimum 5 years as a P.M. (Project Manager).", "P.M. (Project Manager)"),             # H
        ("P.M. (project manager) experience", "P.M. (project manager)"),
    ])
    def test_g_h_jd_definitions(self, text, defined):
        (d,) = abbreviation_definitions(text)
        assert d.text == defined and d.acronym == "pm" and d.full_words == ("project", "manager")

    @pytest.mark.parametrize("text", [
        "Project Manager (P.M.",                          # M malformed
        "Project Manager P.M.)",
        "Project Manager (P M)",
        "Project Manager (London)",                       # N unrelated parenthetical
        "Product Marketing team; P.M. role",
        "Project Manager (PM/PMP)",                       # O several acronyms
        "Project Manager ((PM))",                         # P nested parentheses
        "(Project Manager (PM))",
        "Senior Project Manager (PM)",                    # R/S a qualifier before the defined phrase
        "a Project Manager (PM) Lead",                    # ... or after it
        "enterprise project management (PM)",             # an unbounded lower-case full form
        "Project Manager (PMO)",                          # initials do not match
    ])
    def test_m_to_p_not_definitions(self, text):
        assert all(d.acronym != "pm" for d in abbreviation_definitions(text))

    def test_o_two_definitions_are_not_one_exact_span(self):
        assert exact_definition("Project Manager (PM) and Human Resources (HR)") is None
        assert exact_definition("Project Manager (P.M.)") is not None

    def test_r_senior_project_manager_spm_never_establishes_pm(self):
        line = "Minimum 5 years as a Senior Project Manager (SPM)"
        (d,) = abbreviation_definitions(line)
        assert d.acronym == "spm"
        v, *_ = _eqv("PM", line, "SPM", [("PM", "SPM", AB)])
        assert errs_with(v, "abbreviation needs an all-capitals acronym")
        v, out, _ = _classify_raw("PM", line, "Project Manager", [("PM", "Project Manager", AB)])
        assert_abbreviation_candidate(out.artifacts[0])                     # "Senior" is not dropped silently

    def test_s_project_manager_vs_senior_project_manager_pm(self):
        line = "Minimum 5 years as a Senior Project Manager (PM)"
        assert abbreviation_definitions(line) == []                        # "Senior" blocks the definition
        v, out, _ = _classify_raw("Project Manager", line, "PM", [("Project Manager", "PM", AB)])
        art = out.artifacts[0]                                              # (a 2-letter span is also too short)
        assert art.spec_status != "resolved" and all(t.provenance != "jd_asserted" for t in art.targets)
        v, *_ = _eqv("Project Manager", line, "Senior Project Manager (PM)",
                     [("Project", "Project", SA), ("Manager", "Manager", SA)])
        assert errs_with(v, "adds words to the target")                    # generic rule stays authoritative

    def test_q_material_qualifier_cannot_be_dropped(self):
        line = "Minimum 5 years as a Senior Project Manager (P.M.)"
        assert abbreviation_definitions(line) == []                        # "Senior" blocks the definition
        v, jd, a, raw = _eqv("PM", line, "Senior Project Manager", [("PM", "Project Manager", AB)],
                             [("Senior", "material")])
        assert not v.ok and all(e.issue is None or e.issue.kind != "duplicate_representation" for e in v.scoped)
        rep = _repair_of(raw, jd_extra=[{"text": "Senior", "kind": "grammatical"}])
        art = run(clf.classify_job("J1", jd, a, client=FakeClient(raw, rep))).artifacts[0]
        assert all(t.provenance != "jd_asserted" for t in art.targets) and art.spec_status != "resolved"


class TestS1522Normalization:
    @pytest.mark.parametrize("pairs, narrowed", [
        ([("PM", "P.M.", AB)], "P.M."),
        ([("PM", "Project Manager", AB)], "Project Manager"),
    ])
    def test_i_to_l_k1_normalized_without_a_repair_call(self, pairs, narrowed):
        v, jd, a, raw = _eqv("PM", K1_LINE, "Project Manager (P.M.)", pairs)
        assert [e.issue.kind for e in v.scoped] == ["duplicate_representation"]
        client = FakeClient(raw)                                             # L: one response only
        out = run(clf.classify_job("J1", jd, a, client=client))
        art = out.artifacts[0]
        assert len(client.requests) == 1 and out.meta["outcome"] == "normalized" and not out.meta["repair_used"]
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"   # J
        assert art.targets[0].jd_span.text == narrowed and len(views(art)) == 1
        (rec,) = art.audit["span_normalizations"]                                                         # K
        assert art.audit["span_normalized"] is True and rec["reason"] == "duplicate_representation"
        assert (rec["original_span"], rec["normalized_span"]) == ("Project Manager (P.M.)", narrowed)
        assert rec["definition"] == {"text": "Project Manager (P.M.)", "acronym": "P.M.", "full_form": "Project Manager"}
        (m,) = art.audit["target_mappings"]
        assert m["trust"] == "trust_bearing" and m["jd_definitions"][0]["text"] == "Project Manager (P.M.)"
        assert m["alignment"] == [{"hint": h, "jd": j, "relation": r} for h, j, r in pairs]   # alignment unchanged

    def test_reverse_definition_normalized(self):
        line = "Minimum 5 years as a P.M. (Project Manager)"
        v, jd, a, raw = _eqv("PM", line, "P.M. (Project Manager)", [("PM", "P.M.", AB)])
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw)))
        assert out.meta["outcome"] == "normalized" and out.artifacts[0].targets[0].jd_span.text == "P.M."

    @pytest.mark.parametrize("extra, pairs", [
        ([("in", "grammatical")], [("PM", "P.M.", AB)]),              # jd_extra present: no narrowing
        ([], [("PM", "P.M.", AB), ("PM", "Project Manager", AB)]),    # more than one pair: no narrowing
    ])
    def test_normalization_refused_unless_every_condition_holds(self, extra, pairs):
        v, jd, a, raw = _eqv("PM", K1_LINE, "Project Manager (P.M.)", pairs, extra)
        rp_raw, recs = rp.normalize_duplicate_representations(v.scoped, raw, enumerate_experience_criteria("J1", a))
        assert recs == [] and rp_raw == raw

    def test_normalization_is_revalidated_and_falls_back_to_repair(self):
        # a criterion-level error elsewhere stays: the narrowed answer is still invalid -> the repair call runs
        v, jd, a, raw = _eqv("PM", K1_LINE, "Project Manager (P.M.)", [("PM", "P.M.", AB)])
        bad = _repair_of(raw)
        d = json.loads(bad)
        d["criteria"][0]["ambiguity"] = ["unsure"]
        bad = json.dumps(d, ensure_ascii=False)
        fixed = json.loads(bad)
        fixed["criteria"][0]["ambiguity"] = []
        client = FakeClient(bad, json.dumps(fixed, ensure_ascii=False))
        out = run(clf.classify_job("J1", jd, a, client=client))
        art = out.artifacts[0]
        assert len(client.requests) == 2 and out.meta["span_normalized"] and art.spec_status == "resolved"
        assert art.targets[0].jd_span.text == "P.M." and art.audit["span_normalized"] is True
        assert "duplicate_representation" not in client.requests[1]["messages"][-1]["content"]

    def test_k2_and_k1_oracles_unchanged_in_meaning(self):
        v, out, _ = _classify_raw("PM", K1_LINE, "Project Manager", [("PM", "Project Manager", AB)])
        assert [t.provenance for t in out.artifacts[0].targets] == ["jd_asserted"]
        assert out.artifacts[0].audit["target_mappings"][0]["jd_definitions"][0]["text"] == "Project Manager (P.M.)"


# ── s1-5.2.2.1: post-repair duplicate-representation normalization ──────────────────────────────────

def _k1_real():
    """The exact real-model K1 sequence: main exact (invalid), repair equivalent with the definition span."""
    _, jd, a, rep = _eqv("PM", K1_LINE, "Project Manager (P.M.)", [("PM", "P.M.", AB)])
    main = _repair_of(rep, match="exact", jd_span=None, alignment=None, jd_extra=None)
    d = json.loads(main)
    for k in ("alignment", "jd_extra"):
        d["criteria"][0]["targets"][0].pop(k)
    return jd, a, json.dumps(d, ensure_ascii=False), rep


class TestS15221PostRepairNormalization:
    def test_k1_real_answers_recover_after_repair(self):
        jd, a, main, rep = _k1_real()
        assert json.loads(main)["criteria"][0]["targets"] == [{"hint": "T1", "type": "role", "match": "exact",
                                                                "jd_span": None}]
        assert json.loads(rep)["criteria"][0]["targets"][0] == {
            "hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 2, "text": "Project Manager (P.M.)"},
            "alignment": [{"hint": "PM", "jd": "P.M.", "relation": "abbreviation"}], "jd_extra": []}
        client = FakeClient(main, rep)
        out = run(clf.classify_job("J1", jd, a, client=client))
        art = out.artifacts[0]
        assert len(client.requests) == 2 and out.meta["calls"] == 2                 # main + ONE repair
        assert any("is not word for word" in e for e in out.validation["errors"])     # main was invalid
        assert out.meta["outcome"] == "repaired_normalized" and "alignment_withdrawn" not in out.meta
        assert [t.provenance for t in art.targets] == ["jd_asserted"] and art.spec_status == "resolved"
        assert art.targets[0].jd_span.text == "P.M." and len(views(art)) == 1
        assert art.audit["alignment_withdrawn"] is False
        (rec,) = art.audit["span_normalizations"]
        assert rec == {"criterion_id": art.criterion_id, "hint": "T1", "original_span": "Project Manager (P.M.)",
                       "normalized_span": "P.M.", "line": 2, "reason": "duplicate_representation",
                       "definition": {"text": "Project Manager (P.M.)", "acronym": "P.M.",
                                      "full_form": "Project Manager"},
                       "stage": "post_repair", "claim_source": "repair"}
        (m,) = art.audit["target_mappings"]
        assert m["alignment"] == [{"hint": "PM", "jd": "P.M.", "relation": "abbreviation"}]    # unchanged
        assert m["trust"] == "trust_bearing" and m["jd_definitions"][0]["text"] == "Project Manager (P.M.)"
        taken = {x["field"] for x in out.meta["repair_merge"]["taken"]}
        assert {"target:T1:match", "target:T1:jd_span"} <= taken                  # the repair made the claim

    def test_pre_repair_stage_is_labelled(self):
        _, jd, a, raw = _eqv("PM", K1_LINE, "Project Manager (P.M.)", [("PM", "P.M.", AB)])
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(raw)))
        (rec,) = out.artifacts[0].audit["span_normalizations"]
        assert (rec["stage"], rec["claim_source"]) == ("pre_repair", "main") and out.meta["calls"] == 1

    def test_idempotent(self):
        _, jd, a, raw = _eqv("PM", K1_LINE, "Project Manager (P.M.)", [("PM", "P.M.", AB)])
        crits = enumerate_experience_criteria("J1", a)
        j = JDText(jd)
        durs = {d: (ln, m) for d, ln, m in j.durations()}
        once, recs = rp.normalize_duplicate_representations(validate_response(raw, j, crits, durs).scoped, raw, crits)
        assert recs and validate_response(once, j, crits, durs).ok
        twice, again = rp.normalize_duplicate_representations(validate_response(once, j, crits, durs).scoped, once,
                                                               crits)
        assert again == [] and twice == once

    @pytest.mark.parametrize("info, source", [
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "target:T1:jd_span"}]}, "repair"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "target:T1:match"}]}, "repair"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "target:T1"}]}, "repair"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "criterion"}]}, "repair"),
        ({"mode": "full_replace", "taken": []}, "repair"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "target:T1:alignment[0]"}]}, "main"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "ambiguity"}]}, "main"),
        ({"mode": "scoped", "taken": [{"criterion_id": "D", "field": "target:T1:jd_span"}]}, "main"),
        ({"mode": "scoped", "taken": [{"criterion_id": "C", "field": "target:T2:jd_span"}]}, "main"),
    ])
    def test_claim_source_from_the_merge_record(self, info, source):
        assert clf._claim_source(info, {"criterion_id": "C", "hint": "T1"}) == source

    @pytest.mark.parametrize("over", [
        {"jd_extra": [{"text": "in", "kind": "grammatical"}]},                        # jd_extra present
        {"alignment": [{"hint": "PM", "jd": "P.M.", "relation": "abbreviation"},
                       {"hint": "PM", "jd": "Project Manager", "relation": "abbreviation"}]},   # two pairs
    ])
    def test_post_repair_refusal_falls_through_to_withdrawal(self, over):
        jd, a, main, rep = _k1_real()
        bad = _repair_of(rep, **over)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(main, bad)))
        art = out.artifacts[0]
        assert "span_normalized" not in out.meta and art.audit["span_normalized"] is False
        assert all(t.provenance != "jd_asserted" for t in art.targets) and art.spec_status != "resolved"

    def test_post_repair_does_not_rescue_an_undefined_or_qualified_claim(self):
        # a repair that proposes an undefined expansion / a qualified title gets no normalization and no trust
        line = "Minimum 5 years as a Senior Project Manager (P.M.)"
        _, jd, a, rep = _eqv("PM", line, "Senior Project Manager (P.M.)", [("PM", "P.M.", AB)])
        main = _repair_of(rep, match="exact", jd_span=None, alignment=None, jd_extra=None)
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(main, rep)))
        art = out.artifacts[0]
        assert "span_normalized" not in out.meta
        assert all(t.provenance != "jd_asserted" for t in art.targets) and art.spec_status != "resolved"
