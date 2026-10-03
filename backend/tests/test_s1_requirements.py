"""
S1 RequirementSpec foundation — offline tests (fake AI clients only; no OpenAI,
no database). All JD texts here are SYNTHETIC; the JOB-2026-0031 case uses the
real D-01 criterion text / analysis_json shape with a synthetic JD.
"""
import asyncio
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


def with_match(raw, jd, analysis, job_id):
    """Fill ``match`` on hint targets that do not set it (pre-s1-3 test outputs), using the value the s1-2
    semantics imply: jd_span -> equivalent; hint verbatim in a requirement span -> exact (none when the
    criterion reports ambiguous_relevance, i.e. the embedded case); otherwise none. Explicit values are kept."""
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
        req = [j.span_on_line(sp.get("line"), sp.get("text", "")) for sp in it.get("requirement_spans") or []
               if isinstance(sp, dict)]
        req = [r for r in req if r is not None]
        for t in it["targets"]:
            if not isinstance(t, dict) or "hint" not in t or "match" in t or t.get("hint") not in hints:
                continue
            if t.get("jd_span") is not None:
                t["match"] = "equivalent"
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
        assert art.setting is None                                # domain_knowledge is never a setting
        assert art.requirement_text == JOB31_REQ
        (view,) = asm.s2_views(art)                               # ONE homogeneous S2 view
        assert view == RequirementSpec(
            policy="explicit_role", required_years=5.0,
            targets=("Construction Project Manager", "Assistant Project Manager"), setting=None,
            spec_version=art.spec_version, criterion_id=cid, criterion_text=JOB31_REQ,
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
        (view,) = asm.s2_views(out.artifacts[0])
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
        (v,) = asm.s2_views(art)
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
        role, func = asm.s2_views(art)
        assert (role.policy, role.targets, func.policy, func.targets) == (
            "explicit_role", ("Procurement Officer",), "functional", ("contract management",))
        for v in (role, func):                                   # shared identity / N / setting / text
            assert (v.criterion_id, v.spec_version, v.required_years, v.setting, v.criterion_text) == (
                cid, art.spec_version, 3, "energy sector", line)

    def test_pure_duration(self):
        jd = "- 3+ years of professional experience"
        cid = only_cid(YEARS_ONLY)
        _, out = classify(jd, YEARS_ONLY, ai(res(cid, "pure_duration", [(1, "3+ years of professional experience")],
                                                 duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.required_years.parsed["bound"] == "at_least"
        (v,) = asm.s2_views(art)
        assert (v.policy, v.targets, v.setting, v.required_years) == ("pure_duration", (), None, 3)

    def test_sector(self):
        a = {"experience": {"minimum_years": 2, "relevant_roles": []}}
        jd = "- 2 years of experience in the banking sector"
        cid = only_cid(a)
        _, out = classify(jd, a, ai(res(cid, "sector", [(1, jd[2:])], setting={"line": 1, "text": "banking sector"},
                                        duration="D1")))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.setting.text == "banking sector"
        (v,) = asm.s2_views(art)
        assert (v.policy, v.targets, v.setting, v.source_spans) == ("sector", ("banking sector",), None,
                                                                    ("banking sector",))

    def test_role_only_criteria_have_no_years(self):
        a = {"experience": {"minimum_years": 0, "relevant_roles": ["Registered Nurse"]}}
        jd = "- Experience as a Registered Nurse"
        cid = only_cid(a)
        _, out = classify(jd, a, ai(res(cid, "explicit_role", [(1, jd[2:])], [{"hint": "T1", "type": "role"}])))
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.required_years is None and art.required is False
        (v,) = asm.s2_views(art)
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
            asm.s2_views(art)
        (v,) = asm.s2_views(art, require_resolved=False)
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
        assert out.artifacts[0].setting.text == "oil and gas projects"
        assert out.artifacts[0].field_provenance["setting"] == "jd_asserted"
        # an About-us line cannot be added as a requirement span to obtain a setting (V-anchor)
        about = res(cid, "explicit_role", [(2, req), (1, "We build oil and gas plants")],
                    [{"hint": "T1", "type": "role"}], setting={"line": 1, "text": "oil and gas plants"},
                    duration="D1")
        _, out = classify(jd, a, ai(about), ai(about))
        assert out.artifacts[0].spec_status == "failed_validation"
        assert any("contains no anchor" in e for e in out.validation["errors"])
        bad = job31_ok(job31_cid(), setting={"line": 3, "text": "commercial construction"})   # outside its spans
        val = validate_response(ai(bad), JDText(JOB31_JD), enumerate_experience_criteria("JOB-2026-0031",
                                JOB31_ANALYSIS), {did: (ln, m) for did, ln, m in JDText(JOB31_JD).durations()})
        assert not val.ok and any("setting" in e and "not inside" in e for e in val.errors)
        cid = job31_cid()
        free = job31_ok(cid, setting="construction")                                 # free text
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(free), ai(free), job_id="JOB-2026-0031")
        assert out.artifacts[0].spec_status == "failed_validation"

    def test_domain_knowledge_never_becomes_setting(self):
        cid = job31_cid()
        client, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(job31_ok(cid)), job_id="JOB-2026-0031")
        art = out.artifacts[0]
        assert art.setting is None and art.field_provenance["setting"] is None
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
        (v,) = asm.s2_views(art)
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


def rejected(out, frag):
    return out.artifacts[0].spec_status == "failed_validation" and any(
        frag in e for e in out.validation["errors"])


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
            "match": "equivalent"}]
        assert art.audit["review_required"] == ["T1"]

    def test_arabic_attached_letter_must_be_copied(self):
        out = run_case(["Construction Project Manager"], "خبرة 5 سنوات كمدير مشروع إنشائي",
                       maps=["مدير مشروع إنشائي"])
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
        assert prov(out) == [("T1", "jd_asserted")] and out.artifacts[0].spec_status == "resolved"

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
        assert prov(out) == [("T1", "jd_asserted")]
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
        assert rejected(out, "must not include the setting")

    def test_v_m10_mapping_cannot_take_another_hints_text(self):
        line = "Minimum 5 years as a Construction Project Manager or Assistant PM"
        hints = ["Construction Project Manager", "Assistant Project Manager"]
        out = run_case(hints, line, maps=[None, "Construction Project Manager"])
        assert rejected(out, "overlaps the verbatim text of hint T1")
        ok = run_case(hints, line, maps=[None, "Assistant PM"])
        assert prov(ok) == [("T1", "jd_verified"), ("T2", "jd_asserted")]
        assert [m["target_id"] for m in ok.artifacts[0].audit["target_mappings"]] == ["T2"]
        assert ok.artifacts[0].audit["review_required"] == ["T2"]

    def test_v_anchor_wrapped_continuation_line_is_valid(self):
        out = run_case(["Site Engineer"], "Minimum 5 years of experience as a Site Engineer on",
                       extra_lines=["  oil and gas projects"], setting={"line": 3, "text": "oil and gas projects"})
        art = out.artifacts[0]
        assert art.spec_status == "resolved" and art.setting.text == "oil and gas projects"
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
        assert out.status == "ok" and art.setting.text == "banking sector" and art.policy == "sector"
        assert codes(out) == ["n_not_in_jd"] and art.spec_status == "needs_confirmation"
        assert art.audit["requirement_anchor"] == "statement"

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
                             None, None, (), "", statement_anchored=True)
        with pytest.raises(ValueError, match="cannot resolve"):
            asm.assemble_artifact(c, pc, jd, {}, run={})


class TestS12Versioning:
    def test_versions_and_fingerprint(self):
        assert (sc.S1_PROMPT_VERSION, sc.S1_VERSION) == ("s1-3", "1.2.0")
        assert clf.prompt_fingerprint() == "c9b570d82f4e"
        assert clf.prompt_fingerprint() not in ("af51355222e5", "e04beaeee3a2", "e64eb1a979e9")

    def test_cache_identity_differs_from_s1_2(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k3 = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-2")
        monkeypatch.setattr(clf, "S1_VERSION", "1.1.0")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "e64eb1a979e9")
        assert clf.s1_cache_key(req) != k3

    def test_cache_identity_differs_from_s1_1(self, monkeypatch):
        req = clf.build_request(JDText(JOB31_JD), enumerate_experience_criteria("J", JOB31_ANALYSIS))
        k2 = clf.s1_cache_key(req)
        monkeypatch.setattr(clf, "S1_PROMPT_VERSION", "s1-1")
        monkeypatch.setattr(clf, "S1_VERSION", "1.0.0")
        monkeypatch.setattr(clf, "prompt_fingerprint", lambda: "af51355222e5")
        assert clf.s1_cache_key(req) != k2

    def test_prompt_contract(self):
        p = clf.S1_SYSTEM_PROMPT
        for frag in ("SUBSTANTIALLY THE SAME", "material qualifier", "\"none\" is always acceptable",
                     "complete phrase with all its qualifiers", "attached to a word", "report \"ambiguous_relevance\"",
                     "NOT evidence that a target is a role", "shortest complete verbatim phrase",
                     "\"match\": \"exact\" | \"equivalent\" | \"none\"", "it is REQUIRED",
                     "absent, broader, narrower, adjacent, related, compatible, qualifier-changing, or uncertain",
                     "decide every target's type first, then derive the policy",
                     "\"mixed\" is wrong for a single target",
                     "A hint that differs from the JD wording is NOT a conflict"):
            assert frag in p, frag
        assert "SHORTEST verbatim phrase" not in p and "MAY set jd_span" not in p


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
                                             "jd_span": {"line": 2, "text": "Human Resources Manager"}}])
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
        ("equivalent", {"line": 2, "text": "Human Resources Manager"}, "jd_asserted", []),
        ("none", None, "original_ai", ["target_not_in_jd"]),
    ])
    def test_provenance_unchanged_by_match(self, match, span, prov, codes_):
        hint, line = ("Laboratory Technician", LAB_LINE) if match == "exact" else ("HR Manager", HR_LINE)
        jd, a, raw, *_ = _raw([hint], line, [{"hint": "T1", "type": "role", "match": match, "jd_span": span}])
        client = FakeClient(raw)
        out = run(clf.classify_job("J1", jd, a, client=client))
        art = out.artifacts[0]
        assert [t.provenance for t in art.targets] == [prov] and [r.code for r in art.reasons] == codes_
        assert art.audit["target_matches"] == {"T1": match}

    def test_policy_errors_name_the_implied_policy(self):
        line = "Minimum 4 years of payroll administration experience"
        v = _val(["payroll administration"], line,
                 [{"hint": "T1", "type": "function", "match": "exact", "jd_span": None}], policy="explicit_role")
        assert any("explicit_role needs" in e and "imply policy functional" in e for e in v.errors)
        v = _val(["payroll administration"], line,
                 [{"hint": "T1", "type": "function", "match": "exact", "jd_span": None}], policy="mixed")
        assert any("mixed needs at least one role AND one function" in e and "imply policy functional" in e
                   for e in v.errors)
        v = _val(["Laboratory Technician"], LAB_LINE,
                 [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None}], policy="functional")
        assert any("imply policy explicit_role" in e for e in v.errors)
        two = "Minimum 4 years as a Translator or in translation"
        v = _val(["Translator", "translation"], two,
                 [{"hint": "T1", "type": "role", "match": "exact", "jd_span": None},
                  {"hint": "T2", "type": "function", "match": "exact", "jd_span": None}], policy="functional")
        assert any("imply policy mixed" in e for e in v.errors)

    def test_repair_note_states_policy_derivation(self):
        note = clf.repair_note(["x"])
        assert "all function -> functional" in note and "all role -> explicit_role" in note
        assert "Never switch to mixed unless both types are present" in note

    def test_repair_to_mixed_fails_repair_to_functional_succeeds(self):
        line = "Minimum 4 years of payroll administration experience"
        tg = [{"hint": "T1", "type": "function", "match": "exact", "jd_span": None}]
        jd, a, bad, *_ = _raw(["payroll administration"], line, tg, policy="explicit_role")
        _, _, mixed, *_ = _raw(["payroll administration"], line, tg, policy="mixed")
        _, _, good, *_ = _raw(["payroll administration"], line, tg, policy="functional")
        out = run(clf.classify_job("J1", jd, a, client=FakeClient(bad, mixed)))
        assert out.artifacts[0].spec_status == "failed_validation"
        client = FakeClient(bad, good)
        out = run(clf.classify_job("J1", jd, a, client=client))
        assert out.artifacts[0].spec_status == "resolved" and out.meta["outcome"] == "repaired"
        assert "imply policy functional" in client.requests[1]["messages"][3]["content"]

    def test_match_survives_cache(self):
        jd, a, raw, *_ = _raw(["HR Manager"], HR_LINE, [{"hint": "T1", "type": "role", "match": "equivalent",
                                                          "jd_span": {"line": 2, "text": "Human Resources Manager"}}])
        cache = clf.InMemoryS1Cache()
        run(clf.classify_job("J1", jd, a, client=FakeClient(raw), cache=cache))
        again = run(clf.classify_job("J1", jd, a, client=FakeClient(), cache=cache))
        assert again.artifacts[0].audit["target_matches"] == {"T1": "equivalent"}


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
        ({"policy": "sectorish"}, "policy must be one of"),
        ({"targets": [{"hint": "T1", "type": "role"}]}, "hint T2"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T1", "type": "role"},
                      {"hint": "T2", "type": "role"}]}, "exactly once"),
        ({"targets": [{"hint": "T1", "type": "role", "text": "Project Manager"}, {"hint": "T2", "type": "role"}]},
         "unchanged"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "role"},
                      {"line": 6, "text": "Project Manager", "type": "role"}]}, "add no other targets"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T9", "type": "role"}]}, "unknown hint"),
        ({"targets": [{"hint": "T1", "type": "position"}, {"hint": "T2", "type": "role"}]}, "type must be"),
        ({"targets": [{"hint": "T1", "type": "role"}, {"hint": "T2", "type": "function"}]}, "explicit_role needs"),
        ({"requirement_spans": [{"line": 6, "text": "Minimum 6 years of experience"}]}, "not verbatim"),
        ({"requirement_spans": [{"line": 6, "text": "Mi"}]}, "at least 3"),
        ({"requirement_spans": []}, "requirement_spans is empty"),
        ({"requirement_spans": [{"line": 6, "text": "experience as a Construction Project Manager"}]},
         "duration D1"),
        ({"duration": "D7"}, "duration must be null or one of"),
        ({"duration": 5}, "duration must be null or one of"),
        ({"ambiguity": ["unsure"]}, "ambiguity must be"),
        ({"policy": "sector", "targets": [], "setting": None}, "sector takes no targets"),
        ({"policy": "pure_duration", "targets": []}, "pure_duration takes no targets"),
        ({"setting": "construction"}, "setting: must be an object"),
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
        bad = ai(job31_ok(cid, policy="nope"))
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
        v1, v2 = asm.s2_views(art), asm.s2_views(art)
        assert v1 == v2 and [v.policy for v in v1] == ["explicit_role", "functional"]
        assert asm.s2_views(sc.S1Artifact.from_dict(art.to_dict())) == v1


# ── 27 isolation ────────────────────────────────────────────────────────────

class TestIsolation:
    def test_nothing_in_production_imports_s1(self):
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            root = BACKEND / sub
            for p in root.rglob("*.py") if root.exists() else []:
                if "s1_requirements" in p.parts:
                    continue
                if "s1_requirements" in p.read_text(encoding="utf-8"):
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
