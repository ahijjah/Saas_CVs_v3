"""
S2 Phase 1 — semantic classification of validated S0 entries (shadow only).

All model responses are RECORDED fixtures returned by a fake client; no OpenAI
call is made. These tests pin the contract (input, masking, validation, quote
reconstruction, call flow, cache, guards); they do not measure model quality.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.experience_accounting import (
    CANNOT_DETERMINE, MATCHED, PARTIAL, RequirementSpec, build_experience_context,
    decide_experience_status,
)
from services.s0_experience import structurer as s0
from services.s0_experience.text import split_lines
from services.s2_experience import classifier as s2
from services.s2_experience import masking as mk
from services.s2_experience.masking import (
    MASK_TOKEN, locate_quote, mask_free_text, mask_text, mask_threshold, merge_spans,
)
from services.s2_experience.validator import validate_response

TODAY = date(2026, 10, 1)
BACKEND = Path(__file__).resolve().parent.parent


# ── fake client / helpers ────────────────────────────────────────────────────

class _Choice:
    def __init__(self, content, finish_reason):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = finish_reason


class FakeClient:
    """Queued responses: str (finish 'stop'), dict {content, finish_reason, usage} or Exception."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests: list[dict] = []
        outer = self

        class _C:
            async def create(self, **kw):
                outer.requests.append(kw)
                item = outer.queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, str):
                    item = {"content": item}
                usage = type("U", (), dict(item.get("usage", {"prompt_tokens": 900, "completion_tokens": 150,
                                                              "total_tokens": 1050})))()
                return type("R", (), {"choices": [_Choice(item["content"], item.get("finish_reason", "stop"))],
                                      "usage": usage})()

        self.chat = type("Ch", (), {"completions": _C()})()


def run(coro):
    loop = asyncio.new_event_loop()          # never clear the main-thread loop
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def s0_doc(text, s0_response):
    return run(s0.build_s0(text, client=FakeClient(s0_response), today=TODAY))


def s0_entry(anchor_id, title=None, employer=None, header=(), body=(), kind="employment",
             ownership="certain", undated_reason=None):
    d = {"anchor_id": anchor_id, "kind": kind, "header_lines": list(header),
         "body_lines": [list(r) for r in body], "ownership": ownership, "undated_reason": undated_reason}
    if title:
        d["title_line"], d["title_text"] = title
    if employer:
        d["employer_line"], d["employer_text"] = employer
    return d


def s0_resp(entries, ignored=()):
    return json.dumps({"entries": list(entries), "ignored_anchors": list(ignored)})


def res(entry_id, label, quotes=(), basis="responsibilities", reason="r", missing=(), **extra):
    d = {"entry_id": entry_id, "label": label, "basis": basis,
         "quotes": [{"line": ln, "text": t} for ln, t in quotes], "reason": reason, "missing": list(missing)}
    d.update(extra)
    return d


def s2_resp(*results):
    return json.dumps({"results": list(results)})


def spec(policy="explicit_role", n=5, targets=("programme manager", "project manager"), setting=None,
         text="Minimum 5 years of relevant experience as programme manager or project manager",
         cid="C1", version="fx-1"):
    return RequirementSpec(policy=policy, required_years=n, targets=tuple(targets), setting=setting,
                           spec_version=version, criterion_id=cid, criterion_text=text,
                           source_spans=tuple(targets))


def classify(doc, text, sp, *responses, cache=None, client=None):
    client = client or FakeClient(*responses)
    return run(s2.classify_criterion(doc, sp, extracted_text=text, client=client, cache=cache)), client


# ── fixture CVs ──────────────────────────────────────────────────────────────

CV = """Jane Doe
PROFESSIONAL EXPERIENCE
Programme Manager
IISD
March 2019 - Present
- Managed 4 donor-funded programmes and a team of 6
- Led the 2020-2021 national census programme
Project Assistant
UNDP
Jan 2015 - Feb 2019
- Organised logistics and meeting minutes for project teams
Consultant
ACME Ltd
- Advised clients
EDUCATION
Bachelor of Arts, Kabul University, 2010 - 2014"""
# anchors: A1 l5 (current), A2 l7 (inside bullet), A3 l10, A4 l16 (education)
CV_S0 = s0_resp([
    s0_entry("A1", title=(3, "Programme Manager"), employer=(4, "IISD"), header=(3, 4, 5), body=[(6, 7)]),
    s0_entry("A3", title=(8, "Project Assistant"), employer=(9, "UNDP"), header=(8, 9, 10), body=[(11, 11)]),
    s0_entry(None, title=(12, "Consultant"), employer=(13, "ACME Ltd"), header=(12, 13), body=[(14, 14)],
             undated_reason="no dates given"),
], [{"anchor_id": "A2", "disposition": "inside_responsibility", "owner_entry": 0},
    {"anchor_id": "A4", "disposition": "education"}])

GOOD = s2_resp(
    res("E1", "qualifying", [(3, "Programme Manager"), (6, "Managed 4 donor-funded programmes")],
        basis="title_and_responsibilities"),
    res("E2", "related", [(11, "Organised logistics and meeting minutes")]),
    res("E3", "insufficient", [(12, "Consultant")], basis="title", missing=["responsibilities", "function"]),
)

MULTI = """EXPERIENCE
UNICEF
Senior Education Officer
2021 - 2024
- Led the education portfolio
Education Officer
2018 - 2021
- Supported school programmes"""
MULTI_S0 = s0_resp([
    s0_entry("A1", title=(3, "Senior Education Officer"), employer=(2, "UNICEF"), header=(2, 3, 4), body=[(5, 5)]),
    s0_entry("A2", title=(6, "Education Officer"), employer=(2, "UNICEF"), header=(2, 6, 7), body=[(8, 8)])])

ARABIC = """الخبرة العملية
مدير برامج
برنامج الأمم المتحدة الإنمائي
مارس 2019 - حاليا
- إدارة أربعة برامج ممولة من المانحين وفريق من ستة موظفين
مساعد مشروع
منظمة الصحة العالمية
يناير 2015 - فبراير 2019
- تنظيم الخدمات اللوجستية للاجتماعات"""
ARABIC_S0 = s0_resp([
    s0_entry("A1", title=(2, "مدير برامج"), employer=(3, "برنامج الأمم المتحدة الإنمائي"), header=(2, 3, 4), body=[(5, 5)]),
    s0_entry("A2", title=(6, "مساعد مشروع"), employer=(7, "منظمة الصحة العالمية"), header=(6, 7, 8), body=[(9, 9)])])


@pytest.fixture(scope="module")
def doc():
    d = s0_doc(CV, CV_S0)
    assert d.structure_status == "validated" and [e.entry_id for e in d.entries] == ["E1", "E2", "E3"]
    return d


# ═════════════════════════════════════════════════════════════════════════════
# Masking
# ═════════════════════════════════════════════════════════════════════════════

class TestMasking:

    def test_entry_lines_masked(self, doc):
        req = s2.build_request(doc, spec(), CV)
        lines = {ln["line"]: ln["text"] for e in req.payload["entries"] for ln in e["lines"]}
        assert lines[5] == MASK_TOKEN                                  # single-line, current
        assert lines[7] == "- Led the [dates] national census programme"   # ignored anchor in a bullet
        assert lines[10] == MASK_TOKEN
        assert lines[6] == "- Managed 4 donor-funded programmes and a team of 6"   # free text untouched

    def test_two_line_range_masked(self):
        text = "EXPERIENCE\nProgramme Manager\nIISD\nJan 2019 –\nPresent\n- Managed programmes"
        d = s0_doc(text, s0_resp([s0_entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"),
                                           header=(2, 3, 4, 5), body=[(6, 6)])]))
        lines = {ln["line"]: ln["text"] for ln in s2.build_request(d, spec(), text).payload["entries"][0]["lines"]}
        assert lines[4] == MASK_TOKEN and lines[5] == MASK_TOKEN

    def test_open_ended_and_invalid_ranges_masked(self):
        text = "EXPERIENCE\nAdviser\nSince 2015\n- Advised ministry\nTrainer\n2019 - 2018\n- Ran workshops"
        d = s0_doc(text, s0_resp([
            s0_entry("A1", title=(2, "Adviser"), header=(2, 3), body=[(4, 4)]),
            s0_entry("A2", title=(5, "Trainer"), header=(5, 6), body=[(7, 7)])]))
        assert d.anchors[1].parse_status == "invalid"
        lines = {ln["line"]: ln["text"] for e in s2.build_request(d, spec(), text).payload["entries"]
                 for ln in e["lines"]}
        assert lines[3] == MASK_TOKEN and lines[6] == MASK_TOKEN

    def test_unparsed_date_text_masked(self):
        text = "EXPERIENCE\nAdviser, Jan '19 – Mar '21\n- Advised ministry"
        d = s0_doc(text, s0_resp([s0_entry(None, title=(2, "Adviser"), header=(2,), body=[(3, 3)],
                                           undated_reason="unparsed dates")]))
        assert d.unparsed_date_texts
        line2 = s2.build_request(d, spec(), text).payload["entries"][0]["lines"][0]["text"]
        assert line2 == f"Adviser, {MASK_TOKEN} – {MASK_TOKEN}" and "19" not in line2

    def test_merge_overlapping_and_adjacent(self):
        assert merge_spans([(5, 9), (0, 3), (3, 4), (8, 12)]) == [(0, 4), (5, 12)]
        ml = mask_text("abcdefghij", [(2, 4), (4, 6)])
        assert ml.masked == "ab[dates]ghij"

    def test_title_and_employer_masked(self):
        assert mask_free_text("Officer 2019-2021") == "Officer [dates]"
        assert mask_free_text("Programme Manager") == "Programme Manager"

    @pytest.mark.parametrize("text, expected", [
        ("Minimum 5 years of relevant experience as programme manager",
         "Minimum [N] years of relevant experience as programme manager"),
        ("At least 3+ years in ICT support", "At least [N] years in ICT support"),
        ("5-year experience in education", "[N] year experience in education"),
        ("minimum of 3-5 years", "minimum of [N] years"),
        ("five (5) years of experience", "[N] years of experience"),
        ("Minimum 18 months", "Minimum [N] months"),
        ("3 to 5 yrs", "[N] yrs"),
        ("خبرة لا تقل عن خمس سنوات في إدارة المشاريع", "خبرة لا تقل عن [N] سنوات في إدارة المشاريع"),
        ("خبرة ٥ سنوات", "خبرة [N] سنوات"),
        ("خبرة سنتين على الأقل", "خبرة [N] سنوات على الأقل"),
        ("Experience as Programme Manager", "Experience as Programme Manager"),
    ])
    def test_criterion_threshold_masked(self, text, expected):
        assert mask_threshold(text) == expected


class TestQuoteReconstruction:

    def test_normalised_match_maps_to_original(self):
        ml = mask_text("Programme Manager | IISD | 2019 - 2024 | Kabul", [(27, 38)], 3)
        span, why = locate_quote("programme   MANAGER", ml)
        assert why is None and (span.char_start, span.char_end, span.original_text) == (0, 17, "Programme Manager")
        span, _ = locate_quote("Kabul", ml)
        assert span.original_text == "Kabul" and ml.original[span.char_start:span.char_end] == "Kabul"

    @pytest.mark.parametrize("quote", ["IISD | [dates]", "dates] | Kabul", "IISD | [dat"])
    def test_overlapping_mask_rejected(self, quote):
        ml = mask_text("Programme Manager | IISD | 2019 - 2024 | Kabul", [(27, 38)], 3)
        span, why = locate_quote(quote, ml)
        assert span is None and why in ("overlaps_mask", "not_found")

    def test_first_occurrence(self):
        ml = mask_text("led team; led team", [], 1)
        span, _ = locate_quote("led team", ml)
        assert (span.char_start, span.occurrence) == (0, "first_of_2")

    def test_arabic(self):
        line = "- إدارة أربعة برامج ممولة من المانحين"
        span, _ = locate_quote("إدارة أربعة برامج", mask_text(line, [], 1))
        assert span.original_text == "إدارة أربعة برامج"

    def test_persisted_evidence_is_original_cv_text(self, doc):
        r = s2_resp(res("E1", "qualifying", [(6, "MANAGED 4 donor-funded   programmes")]),
                    res("E2", "related", [(11, "organised logistics")]),
                    res("E3", "not_relevant", [(14, "Advised clients")]))
        out, _ = classify(doc, CV, spec(), r)
        q = out.results[0]["quotes"][0]
        assert q["original_text"] == "Managed 4 donor-funded programmes"
        assert q["model_text"] == "MANAGED 4 donor-funded   programmes"
        assert split_lines(CV)[5][q["char_start"]:q["char_end"]] == q["original_text"]
        assert out.results[1]["quotes"][0]["original_text"] == "Organised logistics"


# ═════════════════════════════════════════════════════════════════════════════
# Input contract
# ═════════════════════════════════════════════════════════════════════════════

class TestInputContract:

    def test_payload_shape(self, doc):
        p = s2.build_request(doc, spec(), CV).payload
        assert set(p) == {"s2_input_version", "criterion", "entries"}
        assert set(p["criterion"]) == {"criterion_id", "policy", "targets", "setting", "criterion_text",
                                       "source_spans"}
        assert all(set(e) == {"entry_id", "title", "employer", "lines"} for e in p["entries"])
        assert p["criterion"]["criterion_text"] == \
            "Minimum [N] years of relevant experience as programme manager or project manager"

    def test_no_prohibited_fields_or_values_reach_the_model(self, doc):
        _, client = classify(doc, CV, spec(n=7), GOOD)
        msg = client.requests[0]["messages"][1]["content"]
        for banned in ("required_years", "duration", "interval", "is_current", "as_of", "kind",
                       "employment", "education", "status", "cd_reason", "Present", "total",
                       "anchor", "Bachelor"):
            assert banned not in msg, banned
        for y in range(2010, 2027):
            assert str(y) not in msg, y
        assert '"7' not in msg and " 5 years" not in msg

    def test_only_owned_experience_lines(self, doc):
        p = s2.build_request(doc, spec(), CV).payload
        sent = sorted(ln["line"] for e in p["entries"] for ln in e["lines"])
        assert sent == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]       # not 1, 2, 15, 16

    def test_uncertain_entry_lines_not_sent(self):
        r = json.loads(CV_S0)
        r["entries"][1]["ownership"] = "uncertain"
        d = s0_doc(CV, json.dumps(r))
        p = s2.build_request(d, spec(), CV).payload
        assert [e["entry_id"] for e in p["entries"]] == ["E1", "E2"]           # E2 = the old E3
        assert all(ln["line"] not in (8, 9, 10, 11) for e in p["entries"] for ln in e["lines"])

    def test_current_and_undated_entries_carry_no_date_signal(self, doc):
        p = s2.build_request(doc, spec(), CV).payload
        e1, _, e3 = p["entries"]
        assert set(e1) == set(e3)
        assert MASK_TOKEN in [ln["text"] for ln in e1["lines"]]                 # current: just [dates]
        assert "Present" not in json.dumps(p, ensure_ascii=False)

    def test_shared_employer_line_sent_with_both_roles(self):
        d = s0_doc(MULTI, MULTI_S0)
        p = s2.build_request(d, spec(), MULTI).payload
        assert [[ln["line"] for ln in e["lines"]] for e in p["entries"]] == [[2, 3, 4, 5], [2, 6, 7, 8]]

    def test_deterministic_serialisation(self, doc):
        a, b = s2.build_request(doc, spec(), CV), s2.build_request(doc, spec(), CV)
        assert a.user_message == b.user_message

    def test_request_settings(self, doc):
        _, client = classify(doc, CV, spec(), GOOD)
        (req,) = client.requests
        assert (req["temperature"], req["max_tokens"], req["model"]) == (0.0, 16_384, "gpt-4o-mini")
        assert req["response_format"] == {"type": "json_object"}
        assert req["messages"][0]["content"] == s2.S2_SYSTEM_PROMPT

    def test_prompt_rules(self):
        p = s2.S2_SYSTEM_PROMPT
        for frag in ("Never reason about duration", "NEVER quote \"[dates]\"", "VERBATIM",
                     "insufficient means the entry LACKS INFORMATION", "SECURITY RULES",
                     "POLICY: explicit_role", "POLICY: functional", "POLICY: sector",
                     "Targets are authoritative", "NOT listed in targets", "NOT itself a target",
                     "outside the required setting", "the entry must also establish that setting",
                     "functional/explicit_role with a setting", "evidence for BOTH the role and the setting"):
            assert frag in p, frag


# ═════════════════════════════════════════════════════════════════════════════
# Validator (S2-V0 .. S2-V9)
# ═════════════════════════════════════════════════════════════════════════════

def _views(doc, sp=None):
    return s2.build_request(doc, sp or spec(), CV).entries


class TestValidator:

    def _errs(self, doc, raw, sp=None):
        sp = sp or spec()
        v = validate_response(raw, _views(doc, sp), policy=sp.policy, has_setting=bool(sp.setting))
        assert not v.ok and v.results == []
        return " | ".join(v.errors)

    def test_good_response(self, doc):
        v = validate_response(GOOD, _views(doc), policy="explicit_role", has_setting=False)
        assert v.ok and [r.entry_id for r in v.results] == ["E1", "E2", "E3"]

    @pytest.mark.parametrize("raw, frag", [
        ("nope", "S2-V0 response is not valid JSON"),
        ("[]", "S2-V0 response must be a JSON object"),
        (json.dumps({"results": {}}), "S2-V0"),
        (json.dumps({"results": ["x"]}), "S2-V0 results[0] must be an object"),
    ])
    def test_v0(self, doc, raw, frag):
        assert frag in self._errs(doc, raw)

    def test_v1_missing_entry(self, doc):
        r = json.loads(GOOD)
        r["results"].pop()
        assert "S2-V1 entry E3 has no result" in self._errs(doc, r)

    def test_v1_duplicate(self, doc):
        r = json.loads(GOOD)
        r["results"].append(r["results"][0])
        assert "S2-V1 entry E1 has more than one result" in self._errs(doc, r)

    def test_v1_hallucinated_id(self, doc):
        r = json.loads(GOOD)
        r["results"].append(res("E9", "related", [(6, "Managed")]))
        assert "S2-V1 results[3]: unknown entry_id 'E9'" in self._errs(doc, r)

    @pytest.mark.parametrize("field, value, frag", [
        ("label", "Qualifying", "S2-V2"), ("label", "relevant", "S2-V2"),
        ("basis", "experience", "S2-V2"), ("reason", " ", "S2-V3"), ("reason", "x" * 501, "S2-V3"),
    ])
    def test_v2_v3(self, doc, field, value, frag):
        r = json.loads(GOOD)
        r["results"][1][field] = value
        assert frag in self._errs(doc, r)

    def test_v2_missing_enum(self, doc):
        r = json.loads(GOOD)
        r["results"][2]["missing"] = ["dates"]
        assert "S2-V2" in self._errs(doc, r)

    @pytest.mark.parametrize("label", ["qualifying", "related", "not_relevant"])
    def test_v4_quote_required(self, doc, label):
        r = json.loads(GOOD)
        r["results"][1].update(label=label, quotes=[])
        assert f"S2-V4 results[1] (E2): label {label} requires" in self._errs(doc, r)

    def test_v4_insufficient_needs_missing(self, doc):
        r = json.loads(GOOD)
        r["results"][2]["missing"] = []
        assert "S2-V4 results[2] (E3): insufficient requires" in self._errs(doc, r)

    def test_v4_missing_only_with_insufficient(self, doc):
        r = json.loads(GOOD)
        r["results"][0]["missing"] = ["function"]
        assert "'missing' is only allowed with insufficient" in self._errs(doc, r)

    def test_v5_quote_from_another_entry(self, doc):
        r = json.loads(GOOD)
        r["results"][1]["quotes"] = [{"line": 6, "text": "Managed 4 donor-funded programmes"}]
        assert "S2-V5 results[1] (E2).quotes[0]: line 6 is not one of entry E2's lines" in self._errs(doc, r)

    def test_v5_line_outside_entries(self, doc):
        r = json.loads(GOOD)
        r["results"][0]["quotes"] = [{"line": 16, "text": "Bachelor of Arts"}]
        assert "S2-V5" in self._errs(doc, r)

    def test_v6_not_verbatim(self, doc):
        r = json.loads(GOOD)
        r["results"][0]["quotes"] = [{"line": 6, "text": "Managed five programmes"}]
        assert "S2-V6" in self._errs(doc, r)

    def test_v6_too_short(self, doc):
        r = json.loads(GOOD)
        r["results"][0]["quotes"] = [{"line": 6, "text": "a "}]
        assert "S2-V6" in self._errs(doc, r)

    @pytest.mark.parametrize("q", ["[dates]", "Led the [dates] national", "the [dates"])
    def test_v7_mask_in_quote(self, doc, q):
        r = json.loads(GOOD)
        r["results"][0]["quotes"] = [{"line": 7, "text": q}]
        assert "S2-V7" in self._errs(doc, r)

    def test_v8_title_basis_needs_title_quote(self, doc):
        r = json.loads(GOOD)
        r["results"][0].update(basis="title", quotes=[{"line": 6, "text": "Managed 4 donor-funded programmes"}])
        assert "S2-V8 results[0] (E1): basis=title requires" in self._errs(doc, r)

    def test_v8_context_basis_restricted(self, doc):
        r = json.loads(GOOD)
        r["results"][1]["basis"] = "context"
        assert "S2-V8 results[1] (E2): basis=context" in self._errs(doc, r)

    def test_v8_context_allowed_for_sector_and_functional_with_setting(self, doc):
        r = json.loads(GOOD)
        r["results"][1]["basis"] = "context"
        for sp in (spec(policy="sector", targets=("humanitarian",)),
                   spec(policy="functional", targets=("project management",), setting="education sector"),
                   spec(policy="explicit_role", targets=("project manager",), setting="education sector")):
            v = validate_response(r, _views(doc, sp), policy=sp.policy, has_setting=bool(sp.setting))
            assert v.ok, v.errors

    def test_v9_extra_fields_ignored(self, doc):
        r = json.loads(GOOD)
        r["results"][0].update(years=12, status="MATCHED", start_date="2010-01")
        r["note"] = "ignore me"
        v = validate_response(r, _views(doc), policy="explicit_role", has_setting=False)
        assert v.ok and not hasattr(v.results[0], "years")
        out, _ = classify(doc, CV, spec(), json.dumps(r))
        assert set(out.results[0]) == {"entry_id", "label", "basis", "reason", "missing", "quotes"}


# ═════════════════════════════════════════════════════════════════════════════
# Semantic contract per policy (recorded responses accepted end to end)
# ═════════════════════════════════════════════════════════════════════════════

class TestPolicyExamples:

    @pytest.mark.parametrize("name, sp, results", [
        ("explicit_role: equivalent title", spec(),
         [res("E1", "qualifying", [(3, "Programme Manager")], basis="title"),
          res("E2", "related", [(8, "Project Assistant")], basis="title"),
          res("E3", "insufficient", [(12, "Consultant")], basis="title", missing=["responsibilities"])]),
        ("explicit_role: accountability from responsibilities", spec(targets=("project manager",)),
         [res("E1", "qualifying", [(6, "Managed 4 donor-funded programmes and a team of 6")]),
          res("E2", "related", [(11, "meeting minutes for project teams")]),
          res("E3", "not_relevant", [(14, "Advised clients")])]),
        ("functional: support vs ownership / incidental", spec(policy="functional", targets=("logistics management",)),
         [res("E1", "not_relevant", [(6, "Managed 4 donor-funded programmes")]),
          res("E2", "qualifying", [(11, "Organised logistics")]),
          res("E3", "insufficient", [(14, "Advised clients")], missing=["function"])]),
        ("functional with setting", spec(policy="functional", targets=("programme management",),
                                         setting="development sector"),
         [res("E1", "qualifying", [(4, "IISD"), (6, "Managed 4 donor-funded programmes")], basis="context"),
          res("E2", "related", [(11, "Organised logistics")]),
          res("E3", "insufficient", [(13, "ACME Ltd")], basis="context", missing=["setting", "function"])]),
        ("sector", spec(policy="sector", targets=("international development",)),
         [res("E1", "qualifying", [(4, "IISD")], basis="context"),
          res("E2", "qualifying", [(9, "UNDP")], basis="context"),
          res("E3", "insufficient", [(13, "ACME Ltd")], basis="context", missing=["employer_context"])]),
    ])
    def test_examples_validate_and_pass_through(self, doc, name, sp, results):
        out, _ = classify(doc, CV, sp, s2_resp(*results))
        assert out.ok, (name, out.validation)
        assert [r["label"] for r in out.results] == [r["label"] for r in results]

    def test_generic_title_strong_responsibilities(self):
        text = "EXPERIENCE\nOfficer\nMinistry of Planning\n2016 - 2022\n- Led delivery and budget of 3 donor projects"
        d = s0_doc(text, s0_resp([s0_entry("A1", title=(2, "Officer"), employer=(3, "Ministry of Planning"),
                                           header=(2, 3, 4), body=[(5, 5)])]))
        out, _ = classify(d, text, spec(targets=("project manager",)),
                          s2_resp(res("E1", "qualifying", [(5, "Led delivery and budget of 3 donor projects")])))
        assert out.ok and out.labels() == {"E1": "qualifying"}

    def test_impressive_but_irrelevant(self):
        text = "EXPERIENCE\nChief Financial Officer\nGlobal Bank\n2010 - 2024\n- Oversaw treasury and audit"
        d = s0_doc(text, s0_resp([s0_entry("A1", title=(2, "Chief Financial Officer"), employer=(3, "Global Bank"),
                                           header=(2, 3, 4), body=[(5, 5)])]))
        out, _ = classify(d, text, spec(policy="functional", targets=("ICT systems support",)),
                          s2_resp(res("E1", "not_relevant", [(2, "Chief Financial Officer")], basis="title")))
        assert out.labels() == {"E1": "not_relevant"}

    def test_multiple_roles_same_employer(self):
        d = s0_doc(MULTI, MULTI_S0)
        out, _ = classify(d, MULTI, spec(targets=("education officer",)), s2_resp(
            res("E1", "qualifying", [(3, "Senior Education Officer")], basis="title"),
            res("E2", "qualifying", [(2, "UNICEF"), (6, "Education Officer")], basis="title")))
        assert out.ok and out.results[1]["quotes"][0]["original_text"] == "UNICEF"

    def test_arabic(self):
        d = s0_doc(ARABIC, ARABIC_S0)
        req = s2.build_request(d, spec(), ARABIC)
        assert "2019" not in req.user_message and "حاليا" not in req.user_message
        out, _ = classify(d, ARABIC, spec(), s2_resp(
            res("E1", "qualifying", [(2, "مدير برامج"), (5, "إدارة أربعة برامج ممولة")], basis="title_and_responsibilities"),
            res("E2", "related", [(6, "مساعد مشروع")], basis="title")))
        assert out.ok and out.results[0]["quotes"][1]["original_text"] == "إدارة أربعة برامج ممولة"


# ═════════════════════════════════════════════════════════════════════════════
# s2-2 contract: authoritative targets + setting for explicit_role
# ═════════════════════════════════════════════════════════════════════════════

CONSTRUCTION = """EXPERIENCE
Assistant Project Manager
BuildCo Construction
Jan 2019 - Dec 2021
- Coordinated subcontractors and site schedules for commercial buildings
Assistant Project Manager
CloudSoft Technologies
Jan 2017 - Dec 2018
- Coordinated software release plans for the mobile app team
Project Manager
Delta Contracting LLC
Jan 2014 - Dec 2016
- Managed construction projects from initiation to closeout
Site Engineer
Delta Contracting LLC
Jan 2012 - Dec 2013
- Supervised concrete works on residential towers
Assistant Project Manager
XYZ Ltd
Jan 2010 - Dec 2011
- Coordinated project schedules and budgets
Project Assistant
BuildCo Construction
Jan 2008 - Dec 2009
- Prepared meeting minutes for the project team"""
CONSTRUCTION_S0 = s0_resp([
    s0_entry(f"A{k + 1}", title=(2 + 4 * k, t), employer=(3 + 4 * k, emp),
             header=(2 + 4 * k, 3 + 4 * k, 4 + 4 * k), body=[(5 + 4 * k, 5 + 4 * k)])
    for k, (t, emp) in enumerate([
        ("Assistant Project Manager", "BuildCo Construction"),
        ("Assistant Project Manager", "CloudSoft Technologies"),
        ("Project Manager", "Delta Contracting LLC"),
        ("Site Engineer", "Delta Contracting LLC"),
        ("Assistant Project Manager", "XYZ Ltd"),
        ("Project Assistant", "BuildCo Construction")])])

# The real JOB-2026-0031 shape: explicit targets incl. an assistant role, setting construction.
CONSTRUCTION_TEXT = ("Minimum 5 years of experience in a relevant role "
                     "(Construction Project Manager or Assistant Project Manager)")


def construction_spec(setting="construction"):
    return RequirementSpec(policy="explicit_role", required_years=5,
                           targets=("Construction Project Manager", "Assistant Project Manager"),
                           setting=setting, spec_version="fx-2", criterion_id="C-CPM",
                           criterion_text=CONSTRUCTION_TEXT,
                           source_spans=("Construction Project Manager", "Assistant Project Manager"))


CONSTRUCTION_RESULTS = [
    # explicit target "Assistant Project Manager" in construction -> qualifying (title + employer setting)
    res("E1", "qualifying", [(2, "Assistant Project Manager"), (3, "BuildCo Construction")], basis="title"),
    # same target title outside construction -> related
    res("E2", "related", [(6, "Assistant Project Manager"), (9, "Coordinated software release plans")],
        basis="title"),
    # equivalent PM accountability; setting from employer context -> qualifying, basis context
    res("E3", "qualifying", [(11, "Delta Contracting LLC"),
                             (13, "Managed construction projects from initiation to closeout")], basis="context"),
    # supporting construction role -> related
    res("E4", "related", [(17, "Supervised concrete works on residential towers")]),
    # target role, setting not establishable -> insufficient / setting
    res("E5", "insufficient", [(18, "Assistant Project Manager")], basis="title", missing=["setting"]),
    # non-target assistant/support role -> related
    res("E6", "related", [(22, "Project Assistant")], basis="title"),
]


@pytest.fixture(scope="module")
def construction_doc():
    d = s0_doc(CONSTRUCTION, CONSTRUCTION_S0)
    assert d.structure_status == "validated" and len(d.experience_entries()) == 6
    return d


class TestExplicitRoleSetting:

    def test_contract_labels_pass_through(self, construction_doc):
        out, _ = classify(construction_doc, CONSTRUCTION, construction_spec(), s2_resp(*CONSTRUCTION_RESULTS))
        assert out.ok, out.validation
        assert out.labels() == {"E1": "qualifying", "E2": "related", "E3": "qualifying",
                                "E4": "related", "E5": "insufficient", "E6": "related"}
        e3 = out.results[2]
        assert e3["basis"] == "context" and e3["quotes"][0]["original_text"] == "Delta Contracting LLC"
        assert out.results[4]["missing"] == ["setting"]

    def test_request_carries_setting_and_masks_threshold_and_dates(self, construction_doc):
        req = s2.build_request(construction_doc, construction_spec(), CONSTRUCTION)
        c = req.payload["criterion"]
        assert c["setting"] == "construction" and c["policy"] == "explicit_role"
        assert c["targets"] == ["Construction Project Manager", "Assistant Project Manager"]
        assert c["criterion_text"].startswith("Minimum [N] years")
        assert req.payload["s2_input_version"] == "s2-in-1"
        for leaked in ("2019", "2021", "Jan", "Dec", "required_years", '"5"'):
            assert leaked not in req.user_message, leaked

    def test_explicit_role_with_setting_allows_context_basis(self, construction_doc):
        sp = construction_spec()
        v = validate_response(s2_resp(*CONSTRUCTION_RESULTS),
                              s2.build_request(construction_doc, sp, CONSTRUCTION).entries,
                              policy=sp.policy, has_setting=True)
        assert v.ok, v.errors

    def test_explicit_role_without_setting_rejects_context_basis(self, construction_doc):
        sp = construction_spec(setting=None)
        v = validate_response(s2_resp(*CONSTRUCTION_RESULTS),
                              s2.build_request(construction_doc, sp, CONSTRUCTION).entries,
                              policy=sp.policy, has_setting=False)
        assert v.errors == ["S2-V8 results[2] (E3): basis=context is only allowed for sector criteria or "
                            "functional/explicit_role criteria with a setting"]

    def test_context_basis_without_setting_is_repaired_not_trusted(self, construction_doc):
        sp = construction_spec(setting=None)
        fixed = [dict(r) for r in CONSTRUCTION_RESULTS]
        fixed[2] = res("E3", "qualifying", [(13, "Managed construction projects from initiation to closeout")])
        out, client = classify(construction_doc, CONSTRUCTION, sp,
                               s2_resp(*CONSTRUCTION_RESULTS), s2_resp(*fixed))
        assert out.ok and out.structurer["outcome"] == "repaired" and len(client.requests) == 2
        assert any("S2-V8" in e for e in out.validation["errors"])


class TestS2Versioning:

    def test_versions(self):
        assert (s2.S2_PROMPT_VERSION, s2.S2_VERSION, s2.S2_INPUT_VERSION) == ("s2-2", "1.1.0", "s2-in-1")

    def test_cache_key_depends_on_prompt_version_and_s2_version(self, monkeypatch):
        k = s2.s2_cache_key("s0", "spec")
        assert k != s2.s2_cache_key("s0", "spec", prompt_version="s2-1")
        monkeypatch.setattr(s2, "S2_VERSION", "1.0.0")
        assert s2.s2_cache_key("s0", "spec") != k

    def test_cache_key_depends_on_prompt_text(self, monkeypatch):
        k = s2.s2_cache_key("s0", "spec")
        monkeypatch.setattr(s2, "S2_SYSTEM_PROMPT", s2.S2_SYSTEM_PROMPT + " ")
        assert s2.s2_cache_key("s0", "spec") != k

    def test_setting_changes_spec_hash(self, construction_doc):
        a = s2.build_request(construction_doc, construction_spec(), CONSTRUCTION)
        b = s2.build_request(construction_doc, construction_spec(setting=None), CONSTRUCTION)
        assert (s2.spec_semantic_hash(construction_spec(), a.criterion_text_masked)
                != s2.spec_semantic_hash(construction_spec(setting=None), b.criterion_text_masked))

    def test_result_records_versions(self, construction_doc):
        out, _ = classify(construction_doc, CONSTRUCTION, construction_spec(), s2_resp(*CONSTRUCTION_RESULTS))
        d = out.to_dict()
        assert d["s2_version"] == "1.1.0" and d["structurer"]["prompt_version"] == "s2-2"
        assert d["structurer"]["prompt_fingerprint"] == s2.prompt_fingerprint()

    def test_old_version_cache_entry_is_not_reused(self, construction_doc):
        cache = s2.InMemoryS2Cache()
        sp = construction_spec()
        out, _ = classify(construction_doc, CONSTRUCTION, sp, s2_resp(*CONSTRUCTION_RESULTS), cache=cache)
        stale = dict(out.to_dict(), s2_version="1.0.0")
        cache.store = {s2.s2_cache_key(out.s0_cache_key, out.spec_hash, prompt_version="s2-1"): stale}
        _, client = classify(construction_doc, CONSTRUCTION, sp, s2_resp(*CONSTRUCTION_RESULTS), cache=cache)
        assert len(client.requests) == 1          # stale s2-1 entry ignored; fresh call made


# ═════════════════════════════════════════════════════════════════════════════
# Call flow / failure semantics
# ═════════════════════════════════════════════════════════════════════════════

BAD = s2_resp(res("E1", "qualifying", [(3, "Programme Manager")], basis="title"))   # E2/E3 missing


class TestCallFlow:

    def test_valid_main(self, doc):
        out, client = classify(doc, CV, spec(), GOOD)
        assert (out.status, out.status_reason, out.retryable) == ("ok", None, False)
        assert out.structurer["outcome"] == "validated" and out.structurer["calls"] == 1
        assert out.labels() == {"E1": "qualifying", "E2": "related", "E3": "insufficient"}
        assert len(client.requests) == 1

    def test_repair_success(self, doc):
        out, client = classify(doc, CV, spec(), BAD, GOOD)
        assert out.ok and out.structurer["outcome"] == "repaired" and out.structurer["repair_used"]
        assert any("S2-V1 entry E2 has no result" in e for e in out.validation["errors"])
        msgs = client.requests[1]["messages"]
        assert msgs[-2] == {"role": "assistant", "content": BAD}
        assert "S2-V1 entry E2 has no result" in msgs[-1]["content"]

    def test_repair_failure(self, doc):
        out, client = classify(doc, CV, spec(), BAD, BAD)
        assert (out.status, out.status_reason, out.retryable) == ("failed", "validation_failed", False)
        assert out.results == [] and out.validation["repair_errors"] and len(client.requests) == 2
        with pytest.raises(RuntimeError):
            out.labels()

    @pytest.mark.parametrize("responses", [(TimeoutError("t"),), (BAD, ConnectionError("down"))])
    def test_ai_unavailable(self, doc, responses):
        out, _ = classify(doc, CV, spec(), *responses)
        assert (out.status, out.status_reason, out.retryable) == ("failed", "ai_unavailable", True)
        assert out.results == []

    def test_truncated_main_no_repair(self, doc):
        out, client = classify(doc, CV, spec(), {"content": GOOD[:60], "finish_reason": "length"})
        assert (out.status, out.status_reason, out.retryable) == ("failed", "output_truncated", False)
        assert len(client.requests) == 1 and out.validation == {"errors": [], "repair_errors": []}

    def test_truncated_repair(self, doc):
        out, client = classify(doc, CV, spec(), BAD, {"content": GOOD[:60], "finish_reason": "length"})
        assert out.status_reason == "output_truncated" and len(client.requests) == 2

    def test_over_budget_no_call(self, doc, monkeypatch):
        monkeypatch.setattr(s2, "S2_MAX_INPUT_TOKENS", 100)
        out, client = classify(doc, CV, spec())
        assert (out.status, out.status_reason, out.retryable) == ("failed", "exceeds_model_context", False)
        assert client.requests == [] and out.structurer["request_token_upper_bound"] > 100

    def test_repair_over_budget(self, doc, monkeypatch):
        req = s2.build_request(doc, spec(), CV)
        from services.s0_experience.llm_call import request_token_upper_bound
        main = request_token_upper_bound([{"role": "system", "content": s2.S2_SYSTEM_PROMPT},
                                          {"role": "user", "content": req.user_message}])
        monkeypatch.setattr(s2, "S2_MAX_INPUT_TOKENS", main + 10)
        out, client = classify(doc, CV, spec(), BAD)
        assert out.status_reason == "exceeds_model_context" and len(client.requests) == 1

    def test_internal_error(self, doc, monkeypatch):
        def boom(_):
            raise mk.MaskingError("bug")
        monkeypatch.setattr(s2, "mask_threshold", boom)
        out, client = classify(doc, CV, spec())
        assert (out.status, out.status_reason, out.retryable) == ("failed", "internal_error", True)
        assert client.requests == []

    def test_usage_recorded(self, doc):
        out, _ = classify(doc, CV, spec(), BAD, GOOD)
        log = out.structurer["call_log"]
        assert [c["call"] for c in log] == ["main", "repair"]
        assert all(c["finish_reason"] == "stop" and c["total_tokens"] == 1050 for c in log)
        assert out.structurer["input_sha256"] and out.structurer["max_input_tokens"] == 100_454

    def test_no_partial_results(self, doc):
        out, _ = classify(doc, CV, spec(), BAD, BAD)
        assert out.results == []


# ═════════════════════════════════════════════════════════════════════════════
# Guards / bypass
# ═════════════════════════════════════════════════════════════════════════════

class TestGuards:

    def test_pure_duration_skipped(self, doc):
        out, client = classify(doc, CV, RequirementSpec(policy="pure_duration", required_years=3, criterion_id="C9"))
        assert (out.status, out.status_reason) == ("skipped", "pure_duration") and client.requests == []

    def test_unverified_not_run(self):
        bad = json.loads(CV_S0)
        bad["ignored_anchors"] = []
        d = run(s0.build_s0(CV, client=FakeClient(json.dumps(bad), json.dumps(bad)), today=TODAY))
        assert d.structure_status == "unverified"
        out, client = classify(d, CV, spec())
        assert (out.status, out.status_reason) == ("not_run", "structure_unverified") and client.requests == []

    def test_failed_not_run(self):
        d = run(s0.build_s0("  ", client=FakeClient(), today=TODAY))
        out, client = classify(d, "  ", spec())
        assert (out.status, out.status_reason) == ("not_run", "structure_failed") and client.requests == []

    def test_zero_entries_ok_no_call(self):
        text = "EXPERIENCE\nBachelor of Arts, Kabul University, 2010 - 2014"
        d = s0_doc(text, s0_resp([], [{"anchor_id": "A1", "disposition": "education"}]))
        out, client = classify(d, text, spec())
        assert (out.status, out.results, client.requests) == ("ok", [], [])
        assert out.labels() == {}

    def test_text_mismatch_rejected(self, doc):
        with pytest.raises(ValueError, match="text_sha256"):
            classify(doc, CV + " ", spec())

    def test_spec_requirements(self, doc):
        with pytest.raises(ValueError):
            classify(doc, CV, spec(cid=""))


# ═════════════════════════════════════════════════════════════════════════════
# Cache
# ═════════════════════════════════════════════════════════════════════════════

class TestCache:

    def test_key_identity(self, doc):
        h = s2.spec_semantic_hash(spec(), "x")
        assert s2.spec_semantic_hash(spec(n=9), "x") == h                      # required_years excluded
        assert s2.spec_semantic_hash(spec(targets=("team leader",)), "x") != h
        assert s2.spec_semantic_hash(spec(), "y") != h
        k = s2.s2_cache_key("s0", h)
        assert k != s2.s2_cache_key("s0b", h) and k != s2.s2_cache_key("s0", h, model="m2")
        assert k != s2.s2_cache_key("s0", h, prompt_version="s2-99")

    def test_threshold_change_reuses_cache(self, doc):
        cache = s2.InMemoryS2Cache()
        a, _ = classify(doc, CV, spec(n=5), GOOD, cache=cache)
        b, client = classify(doc, CV, spec(n=10, text="Minimum 10 years of relevant experience as "
                                                    "programme manager or project manager"), cache=cache)
        assert client.requests == [] and b.to_dict() == a.to_dict()

    @pytest.mark.parametrize("responses, reason, cached", [
        ((GOOD,), None, True),
        ((BAD, BAD), "validation_failed", True),
        ((TimeoutError("t"),), "ai_unavailable", False),
        (({"content": "{", "finish_reason": "length"},), "output_truncated", False),
    ])
    def test_cache_policy(self, doc, responses, reason, cached):
        cache = s2.InMemoryS2Cache()
        out, _ = classify(doc, CV, spec(), *responses, cache=cache)
        assert out.status_reason == reason and (out.cache_key in cache.store) is cached
        again, client = classify(doc, CV, spec(), GOOD, cache=cache)
        assert (client.requests == []) is cached

    def test_exceeds_model_context_cached(self, doc, monkeypatch):
        monkeypatch.setattr(s2, "S2_MAX_INPUT_TOKENS", 100)
        cache = s2.InMemoryS2Cache()
        out, _ = classify(doc, CV, spec(), cache=cache)
        assert out.status_reason == "exceeds_model_context" and out.cache_key in cache.store

    def test_internal_error_not_cached(self, doc, monkeypatch):
        cache = s2.InMemoryS2Cache()
        monkeypatch.setattr(s2, "_run", _raise_internal)
        out, _ = classify(doc, CV, spec(), cache=cache)
        assert out.status_reason == "internal_error" and cache.store == {}


async def _raise_internal(req, sp, client, model, meta, base, spec_hash, key):
    return s2.S2Result("failed", "internal_error", True, spec_hash=spec_hash, cache_key=key, **base)


class TestResultObject:

    def test_s2_result_v1(self, doc):
        out, _ = classify(doc, CV, spec(), GOOD)
        d = json.loads(json.dumps(out.to_dict(), ensure_ascii=False))
        for k in ("_schema", "s2_version", "cache_key", "criterion_id", "spec_version", "spec_hash",
                  "s0_cache_key", "s0_version", "s0_structure_status", "status", "status_reason",
                  "retryable", "structurer", "validation", "results"):
            assert k in d, k
        assert d["_schema"] == "s2_result_v1" and d["s0_cache_key"] == doc.cache_key
        st = d["structurer"]
        for k in ("prompt_code", "prompt_version", "prompt_fingerprint", "model", "temperature",
                  "max_output_tokens", "max_input_tokens", "token_count_method", "input_sha256",
                  "request_token_upper_bound", "calls", "repair_used", "call_log"):
            assert k in st, k
        q = d["results"][0]["quotes"][0]
        assert set(q) == {"line", "char_start", "char_end", "original_text", "model_text", "occurrence"}
        assert s2.S2Result.from_dict(d).to_dict() == d


# ═════════════════════════════════════════════════════════════════════════════
# End to end: S0 -> recorded S2 -> S4/S5 (fixture RequirementSpecs)
# ═════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:

    def test_s0_s2_s5(self, doc):
        sp = spec(n=8)
        out, _ = classify(doc, CV, sp, s2_resp(
            res("E1", "qualifying", [(6, "Managed 4 donor-funded programmes")]),
            res("E2", "not_relevant", [(11, "Organised logistics")]),
            res("E3", "not_relevant", [(14, "Advised clients")])))
        labels = out.labels()
        oct26 = decide_experience_status(sp, build_experience_context(doc, as_of=(2026, 10)), labels)
        mar27 = decide_experience_status(sp, build_experience_context(doc, as_of=(2027, 3)), labels)
        assert (oct26.status, oct26.audit["qualifying_years"]) == (PARTIAL, 7.58)
        assert (mar27.status, mar27.audit["qualifying_years"]) == (MATCHED, 8.0)

    def test_undated_qualifying_gives_detail_missing(self, doc):
        sp = spec(n=10)
        out, _ = classify(doc, CV, sp, s2_resp(
            res("E1", "qualifying", [(6, "Managed 4 donor-funded programmes")]),
            res("E2", "related", [(11, "Organised logistics")]),
            res("E3", "qualifying", [(14, "Advised clients")])))
        d = decide_experience_status(sp, build_experience_context(doc, as_of=(2026, 10)), out.labels())
        assert (d.status, d.cd_reason, d.uncertainty_entry_ids) == (CANNOT_DETERMINE, "detail_missing", ("E3",))


# ═════════════════════════════════════════════════════════════════════════════
# Isolation
# ═════════════════════════════════════════════════════════════════════════════

class TestIsolation:

    def test_s2_does_not_use_d01_or_scoring_or_local_matcher(self):
        for f in (BACKEND / "services" / "s2_experience").glob("*.py"):
            src = f.read_text(encoding="utf-8")
            for banned in ("llm_criteria_mapper", "deterministic_scoring", "criteria_matcher",
                           "cv_evidence", "rapidfuzz"):
                assert banned not in src, (f.name, banned)

    def test_nothing_in_production_imports_s2(self):
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            root = BACKEND / sub
            for p in root.rglob("*.py") if root.exists() else []:
                if "s2_experience" in p.parts:
                    continue
                if "s2_experience" in p.read_text(encoding="utf-8"):
                    hits.append(str(p))
        assert hits == []
