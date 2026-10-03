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


def classify(jd, analysis, *responses, job_id="J1", **kw):
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
        cid = job31_cid()
        ok = job31_ok(cid, requirement_spans=[{"line": 6, "text": JOB31_REQ}, {"line": 3, "text":
                      "We deliver commercial construction projects across the region"}],
                      setting={"line": 3, "text": "commercial construction"})
        _, out = classify(JOB31_JD, JOB31_ANALYSIS, ai(ok), job_id="JOB-2026-0031")
        assert out.artifacts[0].setting.text == "commercial construction"
        assert out.artifacts[0].field_provenance["setting"] == "jd_asserted"
        bad = job31_ok(cid, setting={"line": 3, "text": "commercial construction"})   # outside its spans
        val = validate_response(ai(bad), JDText(JOB31_JD), enumerate_experience_criteria("JOB-2026-0031",
                                JOB31_ANALYSIS), {did: (ln, m) for did, ln, m in JDText(JOB31_JD).durations()})
        assert not val.ok and any("setting" in e and "not inside" in e for e in val.errors)
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
        m = {"line": 5, "text": "project management roles"}
        _, out = classify(ELSEWHERE_JD, JOB31_ANALYSIS, ai(elsewhere(job31_cid(), t1=m, t2=m)),
                          job_id="JOB-2026-0031")
        (art,) = out.artifacts
        assert art.spec_status == "resolved" and art.reasons == ()
        assert [(t.text, t.provenance, t.jd_span.text, t.jd_span.line) for t in art.targets] == [
            ("Construction Project Manager", "jd_asserted", "project management roles", 5),
            ("Assistant Project Manager", "jd_asserted", "project management roles", 5)]   # text unchanged
        (v,) = asm.s2_views(art)
        assert v.targets == ("Construction Project Manager", "Assistant Project Manager")

    def test_partial_mapping(self):
        m = {"line": 5, "text": "project management roles"}
        _, out = classify(ELSEWHERE_JD, JOB31_ANALYSIS, ai(elsewhere(job31_cid(), t1=m)), job_id="JOB-2026-0031")
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
        m = {"line": 5, "text": "project management roles"}
        cache = clf.InMemoryS1Cache()
        _, first = classify(ELSEWHERE_JD, JOB31_ANALYSIS, ai(elsewhere(job31_cid(), t1=m, t2=m)),
                            job_id="JOB-2026-0031", cache=cache)
        client, second = classify(ELSEWHERE_JD, JOB31_ANALYSIS, job_id="JOB-2026-0031", cache=cache)
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
    return validate_response(resp if isinstance(resp, str) else ai(*resp), j,
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
        assert rep[2] == {"role": "assistant", "content": ai(bad)}
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
        assert a.spec_version.startswith("s1-1.0.0-") and a.prompt_fingerprint == clf.prompt_fingerprint()
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
