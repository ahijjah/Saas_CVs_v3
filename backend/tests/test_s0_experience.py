"""
S0 v2 (anchor-and-attach) — deterministic foundation + AI structurer contract.

The structurer is exercised with a fake client returning RECORDED responses;
no OpenAI call is made. Layout fixtures mirror the real failure patterns found
in the 50-CV production audit (acronym employers, one-line title/employer/date,
date-first, several roles under one employer, dates inside bullets, unrecognised
headings, education dates, missing/unsupported dates, OCR noise).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.experience_accounting import (
    ABSENT, CANNOT_DETERMINE, MATCHED, NOT_RELEVANT, PARTIAL, QUALIFYING, ExperienceAccountingError,
    RequirementSpec, UnownedEvidence, account_relevant_years, build_experience_context,
    build_experience_entries, decide_experience_status,
)
from services.s0_experience import structurer as st
from services.s0_experience.dates import extract_anchors, month_interval, parse_point
from services.s0_experience.schema import (
    DATES_DATED, DATES_PARTIAL, DATES_UNDATED, S0Document, STRUCTURE_FAILED, STRUCTURE_REPAIRED,
    STRUCTURE_UNVERIFIED, STRUCTURE_VALIDATED,
)
from services.s0_experience.text import (
    numbered_lines, ocr_noise_indicator, split_lines, text_sha256,
)
from services.s0_experience.validator import validate_structure

TODAY = date(2026, 10, 1)
AS_OF = (2026, 10)
BACKEND = Path(__file__).resolve().parent.parent


# ── helpers ──────────────────────────────────────────────────────────────────

def ln(lines: list[str], needle: str, start: int = 1) -> int:
    for i in range(start - 1, len(lines)):
        if needle in lines[i]:
            return i + 1
    raise AssertionError(f"{needle!r} not found")


def anchors_of(text: str):
    return extract_anchors(split_lines(text), AS_OF).anchors


def aid(text: str, needle: str) -> str:
    lines = split_lines(text)
    target = ln(lines, needle)
    return next(a.anchor_id for a in anchors_of(text) if a.line == target)


def entry(anchor_id, kind="employment", title=None, employer=None, header=(), body=(),
          ownership="certain", undated_reason=None, **extra) -> dict:
    d = {"anchor_id": anchor_id, "kind": kind, "header_lines": list(header),
         "body_lines": [list(r) for r in body], "ownership": ownership,
         "undated_reason": undated_reason}
    if title:
        d["title_line"], d["title_text"] = title
    if employer:
        d["employer_line"], d["employer_text"] = employer
    d.update(extra)
    return d


def response(entries, ignored=()) -> str:
    return json.dumps({"entries": list(entries), "ignored_anchors": list(ignored)})


class _Msg:
    def __init__(self, content, finish_reason="stop"):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = finish_reason


DEFAULT_USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}


class FakeClient:
    """Records every request; returns queued responses or raises (Exception).
    A response is a str (finish_reason "stop", DEFAULT_USAGE) or a dict
    {"content", "finish_reason", "usage"}."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests: list[dict] = []
        outer = self

        class _Completions:
            async def create(self, **kw):
                outer.requests.append(kw)
                item = outer.queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, str):
                    item = {"content": item}
                usage = type("U", (), dict(item.get("usage", DEFAULT_USAGE)))()
                return type("R", (), {"choices": [_Msg(item["content"], item.get("finish_reason", "stop"))],
                                      "usage": usage})()

        self.chat = type("C", (), {"completions": _Completions()})()


def build(text, *responses, cache=None, client=None):
    client = client or FakeClient(*responses)
    # A private loop: asyncio.run() would clear the main-thread loop that other
    # test modules (test_security_detection) obtain via get_event_loop().
    loop = asyncio.new_event_loop()
    try:
        doc = loop.run_until_complete(st.build_s0(text, client=client, today=TODAY, cache=cache))
    finally:
        loop.close()
    return doc, client


def validate(text, raw):
    lines = split_lines(text)
    return validate_structure(raw, lines, anchors_of(text))


# ── realistic layouts (patterns from the production audit) ───────────────────

ACRONYM_CV = """Jane Doe
PROFESSIONAL EXPERIENCE
Programme Manager
IISD
March 2019 - Present
- Managed 4 donor-funded programmes
- Supervised a team of 6
Project Officer
UNDP
Jan 2015 - Feb 2019
- Planned community projects with UNICEF
EDUCATION
Bachelor of Arts, Kabul University, 2010 - 2014"""


def acronym_response(text=ACRONYM_CV):
    L = split_lines(text)
    return response(
        [entry(aid(text, "March 2019"), title=(ln(L, "Programme Manager"), "Programme Manager"),
               employer=(ln(L, "IISD"), "IISD"), header=(3, 4, 5), body=[(6, 7)]),
         entry(aid(text, "Jan 2015"), title=(ln(L, "Project Officer"), "Project Officer"),
               employer=(ln(L, "UNDP"), "UNDP"), header=(8, 9, 10), body=[(11, 11)])],
        [{"anchor_id": aid(text, "2010 - 2014"), "disposition": "education"}])


ONE_LINE_CV = """WORK HISTORY
Programme Manager | IISD | 2019 - 2024
- Managed 4 programmes
Project Officer, UNDP, 01/2015 - 02/2019
- Planned community projects"""


DATE_FIRST_CV = """EXPERIENCE
2019 - 2024
Programme Manager
Intl Institute for Sustainable Development
- Managed 4 programmes
2015 - 2019
Project Officer
United Nations Development Programme
- Planned community projects"""


MULTI_ROLE_CV = """EXPERIENCE
UNICEF
Senior Education Officer
2021 - 2024
- Led the education portfolio
Education Officer
2018 - 2021
- Supported school programmes"""


BULLET_DATE_CV = """EXPERIENCE
Programme Manager
IISD
2019 - 2024
- Led the 2020-2021 national census programme
- Supervised a team of 6
Project Officer
UNDP
2015 - 2019
- Planned community projects"""


UNDATED_CV = """EXPERIENCE
Programme Manager – Save the Children
- Managed child-protection programmes
Volunteer – Red Crescent
- First-aid trainer"""


CONCURRENT_CV = """EXPERIENCE
Programme Manager
IISD
Jan 2018 - Dec 2022
- Managed programmes
Part-time Lecturer
Kabul University
Sept 2020 - Jun 2021
- Taught project management"""


OCR_CV = """EXPERIENCE
Pr0gramme M@nager ~ I I S D
2019 - 2024
- M a n a g e d  4  p r 0 g r a m m e s ;; ## ** ^^
~~ ¦¦ §§ ¤¤ ¦ ¦ ¤ § ¦ ¤"""


# ═════════════════════════════════════════════════════════════════════════════
# S0a text
# ═════════════════════════════════════════════════════════════════════════════

class TestText:

    def test_line_numbers_preserved_blank_lines_omitted_in_prompt(self):
        lines = split_lines("A\n\n\tB  \nC")
        assert lines == ["A", "", " B", "C"]
        out = numbered_lines(lines)
        assert out.splitlines() == ["L0001| A", "L0003|  B", "L0004| C"]

    def test_hash_is_sha256_of_extracted_text(self):
        import hashlib
        assert text_sha256("x") == hashlib.sha256(b"x").hexdigest()

    def test_ocr_indicator(self):
        assert ocr_noise_indicator(split_lines(ACRONYM_CV))["ocr_suspected"] is False
        assert ocr_noise_indicator(split_lines(OCR_CV))["ocr_suspected"] is True


# ═════════════════════════════════════════════════════════════════════════════
# S0b anchors / grammar
# ═════════════════════════════════════════════════════════════════════════════

def one(text):
    (a,) = extract_anchors([text], AS_OF).anchors
    return a


class TestDateGrammar:

    @pytest.mark.parametrize("text, start, end, current, grammar", [
        ("March 2019 - Present", (2019, 3), (None, None), True, "month_name→present"),
        ("Sept 2019 – Present", (2019, 9), (None, None), True, "month_name→present"),
        ("Sep. 2019 - Jun. 2021", (2019, 9), (2021, 6), False, "month_name→month_name"),
        ("15/03/2019 - 20/06/2024", (2019, 3), (2024, 6), False, "dd/mm/yyyy→dd/mm/yyyy"),
        ("15.03.2019 – 20.06.2024", (2019, 3), (2024, 6), False, "dd/mm/yyyy→dd/mm/yyyy"),
        ("2019/03 - 2024/06", (2019, 3), (2024, 6), False, "yyyy/mm→yyyy/mm"),
        ("2019-03 – 2024-06", (2019, 3), (2024, 6), False, "yyyy/mm→yyyy/mm"),
        ("03/2019 - 06/2021", (2019, 3), (2021, 6), False, "mm/yyyy→mm/yyyy"),
        ("2019 - 2021", (2019, None), (2021, None), False, "yyyy→yyyy"),
        ("2019/2021", (2019, None), (2021, None), False, "yyyy→yyyy"),
        ("2019-21", (2019, None), (2021, None), False, "yyyy→yy"),
        ("Mar 15, 2019 - Jun 30, 2021", (2019, 3), (2021, 6), False, "month_name_day→month_name_day"),
        ("15 March 2019 to 30 June 2021", (2019, 3), (2021, 6), False, "day_month_name→day_month_name"),
        ("2015 to date", (2015, None), (None, None), True, "yyyy→present"),
        ("Jan 2018 till date", (2018, 1), (None, None), True, "month_name→present"),
        ("Since 2015", (2015, None), (None, None), True, "open:yyyy"),
        ("From Jan 2018", (2018, 1), (None, None), True, "open:month_name"),
        ("مارس 2019 - حاليا", (2019, 3), (None, None), True, "month_name→present"),
    ])
    def test_supported_forms(self, text, start, end, current, grammar):
        a = one(text)
        assert (a.start_year, a.start_month) == start
        assert (a.end_year, a.end_month) == end
        assert a.is_current is current and a.grammar == grammar and a.parse_status == "ok"
        assert a.duration_known is True
        if current:                                   # no stored interval; measured at scoring time
            assert a.interval is None and a.duration_months is None
            assert a.duration_at(AS_OF) > 0

    def test_open_range_flag(self):
        assert one("Since 2015").open_end is True
        assert one("2015 - Present").open_end is False

    @pytest.mark.parametrize("text", ["١٥/٠٣/٢٠١٩ - ٢٠/٠٦/٢٠٢١", "۲۰۱۹ - ۲۰۲۱"])
    def test_arabic_indic_digits(self, text):
        a = one(text)
        assert a.digits_normalized and a.start_year == 2019 and a.end_year == 2021
        assert a.text == text                     # verbatim, original digits

    def test_day_month_order(self):
        assert one("05/03/2019 - 06/04/2020").day_month_ambiguous is True      # read as DD/MM
        a = one("12/25/2019 - 01/15/2021")                                     # second > 12 -> MM/DD
        assert (a.start_month, a.end_month, a.day_month_ambiguous) == (12, 1, False)

    def test_precision_and_duration_convention(self):
        assert one("Jan 2019 - Jul 2020").duration_months == 18
        a = one("Jan 2019 - 2020")                 # one month missing -> whole years
        assert a.precision == "year" and a.duration_months == 12
        assert month_interval(2020, None, 2020, None) is None

    def test_cross_line_range(self):
        scan = extract_anchors(["Programme Manager", "Jan 2019 –", "Present"], AS_OF)
        (a,) = scan.anchors
        assert (a.line, a.end_line, a.is_current) == (2, 3, True)

    @pytest.mark.parametrize("text, reason", [
        ("2019 - 2018", "end_before_start"), ("1960 - 2020", "span_over_50_years"),
        ("2030 - 2031", "start_in_future"), ("2019 - 2028", "end_in_future"),
    ])
    def test_invalid_ranges_kept_without_interval(self, text, reason):
        a = one(text)
        assert a.parse_status == "invalid" and a.invalid_reason == reason
        assert a.interval is None and a.duration_known is False
        assert a.interval_at(AS_OF) is None

    @pytest.mark.parametrize("text", ["Tel: +93 700 2019 2021", "Graduated in 2014",
                                      "from Kabul University 2015", "Budget 2019"])
    def test_non_ranges_are_not_anchors(self, text):
        assert extract_anchors([text], AS_OF).anchors == []

    @pytest.mark.parametrize("text, kind", [("Jan '19 – Mar '21", "month_apostrophe_year"),
                                            ("Q3/19 – Q1/21", "quarter")])
    def test_unsupported_recorded_as_unparsed_never_guessed(self, text, kind):
        scan = extract_anchors([text], AS_OF)
        assert scan.anchors == [] and {u["kind"] for u in scan.unparsed} == {kind}

    def test_anchor_ids_in_document_order_and_bullet_dates_detected(self):
        anchors = anchors_of(BULLET_DATE_CV)
        assert [a.anchor_id for a in anchors] == ["A1", "A2", "A3"]
        assert [a.text for a in anchors] == ["2019 - 2024", "2020-2021", "2015 - 2019"]

    def test_parse_point_rejects_garbage(self):
        assert parse_point("Smarch 2019") is None
        assert parse_point("31/13/2019") is None


# ═════════════════════════════════════════════════════════════════════════════
# S0d validator rules
# ═════════════════════════════════════════════════════════════════════════════

class TestValidatorAccepts:

    def test_acronym_layout_valid(self):
        assert validate(ACRONYM_CV, acronym_response()).ok

    def test_one_line_layouts_valid(self):
        L = split_lines(ONE_LINE_CV)
        r = response([
            entry(aid(ONE_LINE_CV, "IISD"), title=(2, "Programme Manager"), employer=(2, "IISD"),
                  header=(2,), body=[(3, 3)]),
            entry(aid(ONE_LINE_CV, "UNDP"), title=(4, "Project Officer"), employer=(4, "UNDP"),
                  header=(4,), body=[(5, 5)])])
        assert L[1].startswith("Programme Manager |")
        assert validate(ONE_LINE_CV, r).ok

    def test_date_first_layout_valid(self):
        r = response([
            entry("A1", title=(3, "Programme Manager"),
                  employer=(4, "Intl Institute for Sustainable Development"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(7, "Project Officer"),
                  employer=(8, "United Nations Development Programme"), header=(6, 7, 8), body=[(9, 9)])])
        assert validate(DATE_FIRST_CV, r).ok

    def test_shared_employer_header_valid(self):
        r = response([
            entry("A1", title=(3, "Senior Education Officer"), employer=(2, "UNICEF"),
                  header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Education Officer"), employer=(2, "UNICEF"),
                  header=(2, 6, 7), body=[(8, 8)])])
        assert validate(MULTI_ROLE_CV, r).ok

    def test_bullet_date_ignored_inside_responsibility(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"), header=(2, 3, 4), body=[(5, 6)]),
            entry("A3", title=(7, "Project Officer"), employer=(8, "UNDP"), header=(7, 8, 9), body=[(10, 10)])],
            [{"anchor_id": "A2", "disposition": "inside_responsibility", "owner_entry": 0}])
        assert validate(BULLET_DATE_CV, r).ok

    def test_undated_entries_valid(self):
        r = response([
            entry(None, title=(2, "Programme Manager"), employer=(2, "Save the Children"), header=(2,),
                  body=[(3, 3)], undated_reason="no dates given"),
            entry(None, kind="volunteer", title=(4, "Volunteer"), employer=(4, "Red Crescent"),
                  header=(4,), body=[(5, 5)], undated_reason="no dates given")])
        assert validate(UNDATED_CV, r).ok

    def test_extra_fields_are_ignored_not_trusted(self):
        r = json.loads(acronym_response())
        r["entries"][0]["years"] = 99
        r["entries"][0]["start_date"] = "1990-01"
        assert validate(ACRONYM_CV, r).ok


class TestValidatorRejects:

    def _errs(self, text, raw):
        res = validate(text, raw)
        assert not res.ok
        return " | ".join(res.errors)

    @pytest.mark.parametrize("raw, frag", [
        ("not json", "V0 response is not valid JSON"),
        ("[]", "V0 response must be a JSON object"),
        (json.dumps({"entries": {}}), "V0 'entries' must be a list"),
        (json.dumps({"entries": []}), "V0 'ignored_anchors' must be a list"),
    ])
    def test_v0_shape(self, raw, frag):
        assert frag in self._errs(ACRONYM_CV, raw)

    def test_v1_missing_anchor(self):
        r = json.loads(acronym_response())
        r["ignored_anchors"] = []
        assert "V1 anchor A3" in self._errs(ACRONYM_CV, r)

    def test_v1_duplicate_anchor(self):
        r = json.loads(acronym_response())
        r["ignored_anchors"].append({"anchor_id": "A1", "disposition": "other"})
        assert "V1 anchor A1 accounted for more than once" in self._errs(ACRONYM_CV, r)

    def test_v1_unknown_anchor(self):
        r = json.loads(acronym_response())
        r["ignored_anchors"].append({"anchor_id": "A99", "disposition": "other"})
        assert "V1 unknown anchor_id 'A99'" in self._errs(ACRONYM_CV, r)

    def test_v2_undated_needs_reason(self):
        r = json.loads(acronym_response())
        r["entries"].append(entry(None, header=(1,)))
        assert "V2 entries[2]" in self._errs(ACRONYM_CV, r)

    @pytest.mark.parametrize("field, value, frag", [
        ("kind", "job", "V3 entries[0]: kind 'job'"),
        ("ownership", "maybe", "V3 entries[0]: ownership 'maybe'"),
    ])
    def test_v3_vocab(self, field, value, frag):
        r = json.loads(acronym_response())
        r["entries"][0][field] = value
        assert frag in self._errs(ACRONYM_CV, r)

    def test_v3_disposition(self):
        r = json.loads(acronym_response())
        r["ignored_anchors"][0]["disposition"] = "school"
        assert "V3 ignored_anchors[0]: disposition 'school'" in self._errs(ACRONYM_CV, r)

    def test_v4_title_not_verbatim(self):
        r = json.loads(acronym_response())
        r["entries"][0]["title_text"] = "Program Director"
        assert "V4 entries[0]: title_text 'Program Director' is not verbatim" in self._errs(ACRONYM_CV, r)

    def test_v4_title_line_must_be_header(self):
        r = json.loads(acronym_response())
        r["entries"][0]["header_lines"] = [4, 5]
        assert "V4 entries[0]: title_line 3 must be one of the entry's header_lines" in self._errs(ACRONYM_CV, r)

    def test_v4_line_out_of_range(self):
        r = json.loads(acronym_response())
        r["entries"][0]["header_lines"] = [3, 4, 500]
        assert "V4 entries[0].header_lines: line 500" in self._errs(ACRONYM_CV, r)

    def test_v5_bad_range(self):
        r = json.loads(acronym_response())
        r["entries"][0]["body_lines"] = [[7, 6]]
        assert "V5 entries[0]: body range [7, 6]" in self._errs(ACRONYM_CV, r)

    def test_v5_entry_without_lines(self):
        r = json.loads(acronym_response())
        r["entries"].append(entry(None, header=(), undated_reason="x"))
        assert "V5 entries[2]: entry claims no lines" in self._errs(ACRONYM_CV, r)

    def test_v6_overlapping_ownership(self):
        r = json.loads(acronym_response())
        r["entries"][1]["body_lines"] = [[7, 11]]            # steals line 7 from entry 0
        assert "V6 line 7 is claimed by several entries" in self._errs(ACRONYM_CV, r)

    def test_v6_shared_line_not_employer_line(self):
        r = response([
            entry("A1", title=(3, "Senior Education Officer"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Education Officer"), header=(2, 6, 7), body=[(8, 8)])])
        assert "V6 line 2" in self._errs(MULTI_ROLE_CV, r)

    def test_v6_shared_header_must_be_directly_above(self):
        text = MULTI_ROLE_CV.replace("Education Officer\n2018", "Accountant\nACME\n2016 - 2018\n"
                                     "Education Officer\n2018")
        L = split_lines(text)
        r = response([
            entry("A1", title=(3, "Senior Education Officer"), employer=(2, "UNICEF"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Accountant"), employer=(7, "ACME"), header=(6, 7, 8)),
            entry("A3", title=(ln(L, "Education Officer", 9), "Education Officer"), employer=(2, "UNICEF"),
                  header=(2, ln(L, "Education Officer", 9), ln(L, "2018 - 2021")),
                  body=[(len(L), len(L))])])
        assert "V6 line 2" in self._errs(text, r)

    def test_v7_anchor_far_from_entry(self):
        r = json.loads(acronym_response())
        r["entries"][0]["anchor_id"], r["entries"][1]["anchor_id"] = "A2", "A1"
        assert "V7" in self._errs(ACRONYM_CV, r)

    def test_v8_two_jobs_merged(self):
        # entry 0 swallows the UNDP job (its anchor A2 sits inside entry 0's body)
        r = response([
            entry("A1", title=(3, "Programme Manager"), employer=(4, "IISD"), header=(3, 4, 5), body=[(6, 11)]),
            entry("A2", kind="other", header=(10,), undated_reason=None)],
            [{"anchor_id": "A3", "disposition": "education"}])
        errs = self._errs(ACRONYM_CV, r)
        assert "V8 entries[0] body contains anchor A2" in errs

    def test_v8_inside_responsibility_must_be_in_owner(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"), header=(2, 3, 4), body=[(5, 6)]),
            entry("A3", title=(7, "Project Officer"), employer=(8, "UNDP"), header=(7, 8, 9), body=[(10, 10)])],
            [{"anchor_id": "A2", "disposition": "inside_responsibility", "owner_entry": 1}])
        assert "V8 anchor A2 (line 5) ignored as inside_responsibility" in self._errs(BULLET_DATE_CV, r)

    def test_v9_education_anchor_inside_employment(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"), header=(2, 3, 4), body=[(5, 6)]),
            entry("A3", title=(7, "Project Officer"), employer=(8, "UNDP"), header=(7, 8, 9), body=[(10, 10)])],
            [{"anchor_id": "A2", "disposition": "education"}])
        assert "V9 anchor A2 ignored as education lies inside employment" in self._errs(BULLET_DATE_CV, r)


# ═════════════════════════════════════════════════════════════════════════════
# S0c / end-to-end document assembly
# ═════════════════════════════════════════════════════════════════════════════

class TestStructuredDocuments:

    def test_acronym_employers_validated(self):
        doc, client = build(ACRONYM_CV, acronym_response())
        assert doc.structure_status == STRUCTURE_VALIDATED and doc.date_status == DATES_DATED
        e1, e2 = doc.entries
        assert (e1.entry_id, e1.title.text, e1.employer.text) == ("E1", "Programme Manager", "IISD")
        assert e1.source_text.splitlines()[-1] == "- Supervised a team of 6"
        assert "Project Officer" not in e1.source_text            # no neighbour bleed
        assert (e2.title.text, e2.employer.text, e2.duration_months) == ("Project Officer", "UNDP", 49)
        assert doc.ignored_anchors == [{"anchor_id": "A3", "disposition": "education", "owner_entry_id": None}]
        assert len(client.requests) == 1

    def test_dates_and_durations_come_only_from_anchors(self):
        r = json.loads(acronym_response())
        r["entries"][0]["years"] = 99
        doc, _ = build(ACRONYM_CV, json.dumps(r))
        e1, e2 = doc.entries
        assert e1.is_current and e1.interval is None and e1.duration_months is None
        assert e2.interval == doc.anchors[1].interval and e2.duration_months == 49
        es = build_experience_entries(doc, as_of=AS_OF)
        assert [x.duration_months for x in es] == [91, 49]

    def test_shared_employer_header(self):
        r = response([
            entry("A1", title=(3, "Senior Education Officer"), employer=(2, "UNICEF"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Education Officer"), employer=(2, "UNICEF"), header=(2, 6, 7), body=[(8, 8)])])
        doc, _ = build(MULTI_ROLE_CV, r)
        assert [e.shared_header_lines for e in doc.entries] == [(2,), (2,)]
        assert doc.owned_line_map()[2] == ("E1", "E2")
        assert doc.owned_line_map()[5] == ("E1",)

    def test_bullet_date_owned_by_its_entry(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"), header=(2, 3, 4), body=[(5, 6)]),
            entry("A3", title=(7, "Project Officer"), employer=(8, "UNDP"), header=(7, 8, 9), body=[(10, 10)])],
            [{"anchor_id": "A2", "disposition": "inside_responsibility", "owner_entry": 0}])
        doc, _ = build(BULLET_DATE_CV, r)
        assert len(doc.entries) == 2
        assert doc.ignored_anchors[0] == {"anchor_id": "A2", "disposition": "inside_responsibility",
                                          "owner_entry_id": "E1"}

    def test_unrecognised_heading_and_education_kind_excluded(self):
        text = ("CAREER JOURNEY\nData Analyst\nACME Ltd\n2020 - 2023\n- Built dashboards\n"
                "STUDIES\nBSc Computer Science\nKabul University\n2016 - 2020")
        r = response([
            entry("A1", title=(2, "Data Analyst"), employer=(3, "ACME Ltd"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", kind="education", title=(7, "BSc Computer Science"), header=(7, 8, 9))])
        doc, _ = build(text, r)
        assert [e.kind for e in doc.entries] == ["employment", "education"]
        assert [e.entry_id for e in doc.experience_entries()] == ["E1"]
        assert [e.entry_id for e in build_experience_entries(doc, as_of=AS_OF)] == ["E1"]

    def test_one_line_layout(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(2, "IISD"), header=(2,), body=[(3, 3)]),
            entry("A2", title=(4, "Project Officer"), employer=(4, "UNDP"), header=(4,), body=[(5, 5)])])
        doc, _ = build(ONE_LINE_CV, r)
        assert [(e.title.text, e.employer.text) for e in doc.entries] == [
            ("Programme Manager", "IISD"), ("Project Officer", "UNDP")]
        assert doc.anchors[1].grammar == "mm/yyyy→mm/yyyy"

    def test_undated_cv(self):
        r = response([
            entry(None, title=(2, "Programme Manager"), employer=(2, "Save the Children"), header=(2,),
                  body=[(3, 3)], undated_reason="no dates given"),
            entry(None, kind="volunteer", title=(4, "Volunteer"), employer=(4, "Red Crescent"),
                  header=(4,), body=[(5, 5)], undated_reason="no dates given")])
        doc, _ = build(UNDATED_CV, r)
        assert doc.anchors == [] and doc.date_status == DATES_UNDATED
        assert all(not e.duration_known and e.undated_reason == "no dates given" for e in doc.entries)
        es = build_experience_entries(doc, as_of=AS_OF)
        acc = account_relevant_years(es, {e.entry_id: QUALIFYING for e in es})
        assert acc.q_months == 0 and acc.u_q is True

    def test_partially_dated(self):
        text = UNDATED_CV.replace("Volunteer – Red Crescent", "Volunteer – Red Crescent 2016 - 2018")
        r = response([
            entry(None, title=(2, "Programme Manager"), employer=(2, "Save the Children"), header=(2,),
                  body=[(3, 3)], undated_reason="no dates given"),
            entry("A1", kind="volunteer", title=(4, "Volunteer"), header=(4,), body=[(5, 5)])])
        doc, _ = build(text, r)
        assert doc.date_status == DATES_PARTIAL

    def test_concurrent_jobs_not_double_counted(self):
        r = response([
            entry("A1", title=(2, "Programme Manager"), employer=(3, "IISD"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Part-time Lecturer"), employer=(7, "Kabul University"),
                  header=(6, 7, 8), body=[(9, 9)])])
        doc, _ = build(CONCURRENT_CV, r)
        es = build_experience_entries(doc, as_of=AS_OF)
        acc = account_relevant_years(es, {"E1": QUALIFYING, "E2": QUALIFYING})
        assert acc.q_months == 59                 # Jan 2018 .. Dec 2022, lecturer inside it

    def test_ocr_noisy_cv_flagged(self):
        r = response([entry("A1", title=(2, "Pr0gramme M@nager"), header=(2, 3), body=[(4, 4)])],
                     [])
        doc, _ = build(OCR_CV, r)
        assert doc.ocr["ocr_suspected"] is True and doc.structure_status == STRUCTURE_VALIDATED

    def test_uncertain_ownership_leaves_lines_unowned(self):
        r = json.loads(acronym_response())
        r["entries"][1]["ownership"] = "uncertain"
        doc, _ = build(ACRONYM_CV, json.dumps(r))
        assert [e.entry_id for e in doc.entries] == ["E1"]
        assert doc.uncertain_entries == [{"anchor_id": "A2", "kind": "employment",
                                          "claimed_lines": [8, 9, 10, 11], "display_only": True}]
        assert all(ln not in doc.owned_line_map() for ln in (8, 9, 10, 11))
        assert [e.entry_id for e in build_experience_entries(doc, as_of=AS_OF)] == ["E1"]

    def test_unowned_lines_are_positive_only(self):
        r = json.loads(acronym_response())
        r["entries"][0]["body_lines"] = [[6, 6]]           # structurer unsure about line 7
        doc, _ = build(ACRONYM_CV, json.dumps(r))
        assert 6 in doc.owned_line_map() and 7 not in doc.owned_line_map()


# ═════════════════════════════════════════════════════════════════════════════
# Present / open-ended durations are computed at the scoring as_of
# ═════════════════════════════════════════════════════════════════════════════

class TestPresentAtScoringTime:

    def test_current_anchor_stores_no_end_and_no_interval(self):
        a = one("March 2019 - Present")
        assert (a.end_year, a.end_month, a.interval, a.duration_months) == (None, None, None, None)
        assert a.to_dict()["end"] == {"year": None, "month": None}

    def test_interval_at_scoring_month(self):
        a = one("March 2019 - Present")
        assert a.duration_at((2026, 10)) == 91
        assert a.duration_at((2027, 3)) == 96
        assert a.interval_at((2026, 10)) == (2019 * 12 + 2, 2026 * 12 + 9)
        y = one("Since 2015")                                   # start month unknown -> whole years
        assert y.duration_at((2026, 10)) == 132 and y.duration_at((2027, 1)) == 144

    def test_as_of_on_or_before_start_has_no_duration(self):
        a = one("March 2019 - Present")
        assert a.interval_at((2019, 3)) is None and a.interval_at((2018, 1)) is None

    def test_closed_ranges_are_immutable(self):
        a = one("Jan 2015 - Feb 2019")
        assert a.interval_at((2019, 3)) == a.interval_at((2040, 1)) == a.interval
        assert a.duration_at((2040, 1)) == 49

    @pytest.mark.parametrize("bad", [None, (2026,), (2026, 13), (2026, 0), ("2026", 10), [2026, 10]])
    def test_as_of_must_be_year_month_tuple(self, bad):
        with pytest.raises(ValueError):
            one("March 2019 - Present").interval_at(bad)

    def test_same_cached_document_two_scoring_months(self):
        cache = st.InMemoryS0Cache()
        doc, _ = build(ACRONYM_CV, acronym_response(), cache=cache)
        later, client = build(ACRONYM_CV, cache=cache)          # cache hit
        assert client.requests == [] and later.to_dict() == doc.to_dict()
        oct26 = build_experience_entries(later, as_of=(2026, 10))
        mar27 = build_experience_entries(later, as_of=(2027, 3))
        assert [e.duration_months for e in oct26] == [91, 49]
        assert [e.duration_months for e in mar27] == [96, 49]   # current grows, closed fixed
        labels = {"E1": QUALIFYING, "E2": QUALIFYING}
        assert account_relevant_years(mar27, labels).q_months > account_relevant_years(oct26, labels).q_months
        assert {e.as_of for e in mar27} == {(2027, 3)}

    def test_cache_key_and_hit_independent_of_build_date(self):
        cache = st.InMemoryS0Cache()
        client = FakeClient(acronym_response())
        loop = asyncio.new_event_loop()
        try:
            d1 = loop.run_until_complete(st.build_s0(ACRONYM_CV, client=client, today=date(2026, 10, 1), cache=cache))
            d2 = loop.run_until_complete(st.build_s0(ACRONYM_CV, client=client, today=date(2029, 5, 1), cache=cache))
        finally:
            loop.close()
        assert len(client.requests) == 1 and d1.cache_key == d2.cache_key
        assert d2.built_as_of == "2026-10"                      # original build month kept
        assert "as_of" not in inspect.signature(st.s0_cache_key).parameters

    def test_built_as_of_used_only_for_validity(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        assert doc.built_as_of == "2026-10" and "as_of" not in doc.to_dict()
        assert doc.to_dict()["built_as_of"] == "2026-10"

    def test_build_entries_requires_explicit_as_of(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        with pytest.raises(TypeError):
            build_experience_entries(doc)                        # noqa — as_of is required
        with pytest.raises(ValueError):
            build_experience_entries(doc, as_of=(2026, 13))

    def test_duration_known_and_date_status_independent_of_as_of(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        assert [e.duration_known for e in doc.entries] == [True, True]
        assert doc.date_status == DATES_DATED

    def test_no_system_clock_in_duration_code(self):
        for rel in ("services/s0_experience/dates.py", "services/s0_experience/schema.py",
                    "services/experience_accounting.py"):
            src = (BACKEND / rel).read_text(encoding="utf-8")
            for banned in ("date.today", "datetime.now", "datetime.utcnow", "time.time"):
                assert banned not in src, (rel, banned)


# ═════════════════════════════════════════════════════════════════════════════
# Repair / failure modes
# ═════════════════════════════════════════════════════════════════════════════

class TestRepairAndFailure:

    def test_repair_success(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        doc, client = build(ACRONYM_CV, json.dumps(bad), acronym_response())
        assert doc.structure_status == STRUCTURE_REPAIRED and doc.structurer["repair_used"] is True
        assert doc.structurer["calls"] == 2 and len(doc.entries) == 2
        assert any(e.startswith("V1 anchor A3") for e in doc.validation["errors"])
        msgs = client.requests[1]["messages"]
        assert msgs[-2] == {"role": "assistant", "content": json.dumps(bad)}
        assert "V1 anchor A3" in msgs[-1]["content"] and "never writing dates" in msgs[-1]["content"]

    def test_failed_repair_is_unverified_without_ownership(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        doc, client = build(ACRONYM_CV, json.dumps(bad), json.dumps(bad))
        assert doc.structure_status == STRUCTURE_UNVERIFIED
        assert doc.status_reason == "validation_failed" and doc.retryable is False
        assert doc.entries == [] and doc.owned_line_map() == {} and doc.experience_entries() == []
        assert build_experience_entries(doc, as_of=AS_OF) == []
        assert [a.anchor_id for a in doc.anchors] == ["A1", "A2", "A3"]        # dates kept
        assert doc.candidate_blocks and all(b["display_only"] for b in doc.candidate_blocks)
        assert doc.validation["repair_errors"]
        assert len(client.requests) == 2                                       # exactly one repair

    def test_ai_unavailable_is_retryable_and_not_cached(self):
        cache = st.InMemoryS0Cache()
        doc, client = build(ACRONYM_CV, TimeoutError("boom"), cache=cache)
        assert doc.structure_status == STRUCTURE_UNVERIFIED
        assert doc.status_reason == "ai_unavailable" and doc.retryable is True
        assert doc.entries == [] and doc.anchors and cache.store == {}
        doc2, client2 = build(ACRONYM_CV, acronym_response(), cache=cache)
        assert doc2.structure_status == STRUCTURE_VALIDATED and len(client2.requests) == 1

    def test_ai_failure_during_repair_is_ai_unavailable(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        doc, _ = build(ACRONYM_CV, json.dumps(bad), ConnectionError("down"))
        assert doc.status_reason == "ai_unavailable" and doc.validation["errors"]

    def test_empty_text_failed(self):
        doc, client = build("   \n ")
        assert doc.structure_status == STRUCTURE_FAILED and doc.status_reason == "no_text"
        assert client.requests == []


    def test_internal_error_failed_and_retryable(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("bug")
        monkeypatch.setattr(st, "extract_anchors", boom)
        doc, _ = build(ACRONYM_CV)
        assert doc.structure_status == STRUCTURE_FAILED and doc.retryable is True
        assert st.is_cacheable(doc) is False

    def test_garbage_json_goes_to_repair(self):
        doc, _ = build(ACRONYM_CV, "Sure! here is the JSON", acronym_response())
        assert doc.structure_status == STRUCTURE_REPAIRED


# ═════════════════════════════════════════════════════════════════════════════
# Input/output safety: model-context budget, finish_reason, usage, full text
# ═════════════════════════════════════════════════════════════════════════════

def synthetic_cv(target_chars: int, arabic: bool = False):
    """A CV of about ``target_chars`` with N jobs, plus a matching VALID response."""
    if arabic:
        title, employer, bullet = ("مدير برامج أول", "برنامج الأمم المتحدة الإنمائي، كابول",
                                   "- تنسيق التخطيط والميزانية والرصد وإعداد التقارير للمكون رقم")
    else:
        title, employer, bullet = ("Senior Programme Manager", "United Nations Development Programme, Kabul",
                                   "- Coordinated planning, budgeting, monitoring and reporting for component")
    lines = ["EXPERIENCE"]
    jobs = []
    k = 0
    while len("\n".join(lines)) < target_chars:
        y = 1975 + (k % 50)
        t = len(lines) + 1
        lines += [title, employer, f"Jan {y} - Dec {y}"] + [f"{bullet} {i}" for i in range(6)]
        jobs.append((t, t + 3, t + 8))
        k += 1
    text = "\n".join(lines)
    ents = [entry(f"A{i}", title=(t, title), employer=(t + 1, employer), header=(t, t + 1, t + 2),
                  body=[(b0, b1)]) for i, (t, b0, b1) in enumerate(jobs, 1)]
    return text, response(ents)


def user_message_of(client, i=0):
    return client.requests[i]["messages"][1]["content"]


class TestInputOutputSafety:

    def test_limits(self):
        assert st.S0C_MODEL_CONTEXT_TOKENS == 128_000 and st.S0C_MAX_TOKENS == 16_384
        assert st.S0C_MAX_INPUT_TOKENS == int((128_000 - 16_384) * 0.9) == 100_454
        assert not hasattr(st, "S0C_MAX_INPUT_CHARS")

    def test_request_uses_full_output_allowance(self):
        _, client = build(ACRONYM_CV, acronym_response())
        assert client.requests[0]["max_tokens"] == 16_384

    def test_token_bound_is_utf8_bytes_plus_overhead(self):
        msgs = [{"role": "system", "content": "abc"}, {"role": "user", "content": "مرحبا"}]
        assert st.request_token_upper_bound(msgs) == 3 + 10 + 2 * 16

    @pytest.mark.parametrize("chars", [2_570, 5_980, 9_598, 19_012])
    def test_production_sizes_single_call_validated(self, chars):
        text, resp = synthetic_cv(chars)
        doc, client = build(text, resp)
        assert len(client.requests) == 1 and doc.structure_status == STRUCTURE_VALIDATED
        assert doc.structurer["request_token_upper_bound"] < st.S0C_MAX_INPUT_TOKENS

    def test_20k_character_cv(self):
        text, resp = synthetic_cv(20_000)
        assert len(text) >= 20_000
        doc, client = build(text, resp)
        assert doc.structure_status == STRUCTURE_VALIDATED and len(doc.entries) == len(doc.anchors)

    def test_no_silent_truncation_every_non_blank_line_sent(self):
        text, resp = synthetic_cv(20_000)
        text = text.replace("EXPERIENCE", "EXPERIENCE\n\n   \n")
        _, client = build(text, resp)
        user = user_message_of(client)
        lines = split_lines(text)
        for n, line in enumerate(lines, 1):
            if line.strip():
                assert f"L{n:04d}| {line}" in user, n

    def test_arabic_heavy_input(self):
        text, resp = synthetic_cv(19_012, arabic=True)
        doc, client = build(text, resp)
        bound = doc.structurer["request_token_upper_bound"]
        assert bound >= len(text.encode("utf-8")) > len(text)      # never under-counts
        assert bound < st.S0C_MAX_INPUT_TOKENS and len(client.requests) == 1
        assert doc.structure_status == STRUCTURE_VALIDATED

    def _bound_of(self, text):
        lines = split_lines(text)
        msgs = [{"role": "system", "content": st.S0C_SYSTEM_PROMPT},
                {"role": "user", "content": st.build_user_message(lines, anchors_of(text))}]
        return st.request_token_upper_bound(msgs)

    def test_request_just_below_budget_is_sent(self, monkeypatch):
        monkeypatch.setattr(st, "S0C_MAX_INPUT_TOKENS", self._bound_of(ACRONYM_CV))
        doc, client = build(ACRONYM_CV, acronym_response())
        assert len(client.requests) == 1 and doc.structure_status == STRUCTURE_VALIDATED

    def test_request_above_budget_exceeds_model_context_without_call(self, monkeypatch):
        monkeypatch.setattr(st, "S0C_MAX_INPUT_TOKENS", self._bound_of(ACRONYM_CV) - 1)
        cache = st.InMemoryS0Cache()
        doc, client = build(ACRONYM_CV, cache=cache)
        assert client.requests == []
        assert (doc.structure_status, doc.status_reason, doc.retryable) == (
            STRUCTURE_UNVERIFIED, "exceeds_model_context", False)
        assert doc.entries == [] and doc.anchors and doc.owned_line_map() == {}
        assert doc.cache_key in cache.store                        # deterministic -> cached
        doc2, client2 = build(ACRONYM_CV, cache=cache)
        assert client2.requests == [] and doc2.status_reason == "exceeds_model_context"

    def test_repair_request_over_budget(self, monkeypatch):
        monkeypatch.setattr(st, "S0C_MAX_INPUT_TOKENS", self._bound_of(ACRONYM_CV) + 50)
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        doc, client = build(ACRONYM_CV, json.dumps(bad))
        assert len(client.requests) == 1 and doc.status_reason == "exceeds_model_context"
        assert doc.validation["errors"] and doc.structurer["repair_used"] is False

    def test_truncated_initial_call_no_repair(self):
        cache = st.InMemoryS0Cache()
        trunc = {"content": acronym_response()[:120], "finish_reason": "length",
                 "usage": {"prompt_tokens": 900, "completion_tokens": 16384, "total_tokens": 17284}}
        doc, client = build(ACRONYM_CV, trunc, cache=cache)
        assert len(client.requests) == 1                           # no repair call
        assert (doc.structure_status, doc.status_reason, doc.retryable) == (
            STRUCTURE_UNVERIFIED, "output_truncated", False)
        assert doc.validation == {"errors": [], "repair_errors": []}   # not treated as invalid JSON
        assert doc.structurer["call_log"] == [{"call": "main", "finish_reason": "length",
                                               "prompt_tokens": 900, "completion_tokens": 16384,
                                               "total_tokens": 17284}]
        assert doc.entries == [] and doc.cache_key in cache.store
        doc2, client2 = build(ACRONYM_CV, cache=cache)
        assert client2.requests == [] and doc2.status_reason == "output_truncated"

    def test_truncated_repair_call(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        trunc = {"content": acronym_response()[:200], "finish_reason": "length"}
        doc, client = build(ACRONYM_CV, json.dumps(bad), trunc)
        assert len(client.requests) == 2 and doc.status_reason == "output_truncated"
        assert doc.retryable is False and doc.validation["errors"]
        assert [c["call"] for c in doc.structurer["call_log"]] == ["main", "repair"]
        assert doc.structurer["call_log"][1]["finish_reason"] == "length"

    def test_usage_metadata_recorded(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        doc, _ = build(ACRONYM_CV, json.dumps(bad), acronym_response())
        log = doc.structurer["call_log"]
        assert [c["call"] for c in log] == ["main", "repair"]
        assert all(c["finish_reason"] == "stop" and c["prompt_tokens"] == 1000
                   and c["completion_tokens"] == 200 and c["total_tokens"] == 1200 for c in log)
        assert doc.structurer["token_count_method"] == "utf8_bytes_upper_bound"
        assert doc.structurer["request_token_upper_bound"] > 0
        assert doc.structurer["repair_request_token_upper_bound"] > doc.structurer["request_token_upper_bound"]

    def test_missing_usage_tolerated(self):
        class Bare:
            def __init__(self):
                self.requests = []
                outer = self

                class _C:
                    async def create(self, **kw):
                        outer.requests.append(kw)
                        return type("R", (), {"choices": [_Msg(acronym_response(), None)]})()
                self.chat = type("C", (), {"completions": _C()})()
        doc, _ = build(ACRONYM_CV, client=Bare())
        assert doc.structure_status == STRUCTURE_VALIDATED
        assert doc.structurer["call_log"][0] == {"call": "main", "finish_reason": None, "prompt_tokens": None,
                                                 "completion_tokens": None, "total_tokens": None}


# ═════════════════════════════════════════════════════════════════════════════
# S0 document -> S5 (structure context end to end)
# ═════════════════════════════════════════════════════════════════════════════

def failed_repair_doc(text=ACRONYM_CV):
    bad = json.loads(acronym_response(text))
    bad["ignored_anchors"] = []
    doc, _ = build(text, json.dumps(bad), json.dumps(bad))
    assert doc.structure_status == STRUCTURE_UNVERIFIED
    return doc


class TestS0ToS5:

    def test_context_from_validated_document(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        ctx = build_experience_context(doc, as_of=AS_OF)
        assert ctx.structure_status == "validated" and ctx.s0_cache_key == doc.cache_key
        assert [e.entry_id for e in ctx.entries] == ["E1", "E2"]
        assert {3, 4, 5, 6, 7, 8, 9, 10, 11} <= ctx.owned_lines
        assert [aid for aid, _ in ctx.anchor_intervals] == ["A1", "A2", "A3"]

    def test_mixed_current_and_closed_with_explicit_as_of(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        spec = RequirementSpec(policy="explicit_role", required_years=8)
        labels = {"E1": QUALIFYING, "E2": NOT_RELEVANT}
        oct26 = decide_experience_status(spec, build_experience_context(doc, as_of=(2026, 10)), labels)
        mar27 = decide_experience_status(spec, build_experience_context(doc, as_of=(2027, 3)), labels)
        assert (oct26.status, oct26.audit["qualifying_years"]) == (PARTIAL, 7.58)
        assert (mar27.status, mar27.audit["qualifying_years"]) == (MATCHED, 8.0)
        assert (oct26.audit["duration_as_of"], mar27.audit["duration_as_of"]) == ("2026-10", "2027-03")
        assert oct26.audit["s0_cache_key"] == mar27.audit["s0_cache_key"] == doc.cache_key

    def test_unverified_document_threshold_never_matched(self):
        doc = failed_repair_doc()
        ctx = build_experience_context(doc, as_of=AS_OF)
        assert ctx.entries == () and ctx.owned_lines == frozenset()
        spec = RequirementSpec(policy="functional", required_years=1)
        ev = UnownedEvidence("U1", "Managed 4 donor-funded programmes", QUALIFYING,
                             "programme management", 6, 6)
        d = decide_experience_status(spec, ctx, {}, [ev])
        assert (d.status, d.cd_reason) == (CANNOT_DETERMINE, "structure_unverified")
        assert d.audit["structure_status"] == "unverified" and d.audit["responsible_evidence_ids"] == ["U1"]
        with pytest.raises(ExperienceAccountingError):
            decide_experience_status(spec, ctx, {"E1": QUALIFYING})

    def test_unverified_pure_duration_upper_bound(self):
        ctx = build_experience_context(failed_repair_doc(), as_of=AS_OF)
        # all anchors incl. the education one: 48 (2010-2014) + 49 (Jan 2015-Feb 2019)
        # + 91 (Mar 2019-present at 2026-10) = 188 months (end-exclusive convention)
        for n in (10, 20, 26):                        # U = 15.67 >= 0.6 N -> inconclusive
            d = decide_experience_status(RequirementSpec(policy="pure_duration", required_years=n), ctx, {})
            assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified",
                                                       "DU2_upper_bound_inconclusive")
            assert d.audit["duration"]["upper_bound_years"] == 15.67
        low = decide_experience_status(RequirementSpec(policy="pure_duration", required_years=30), ctx, {})
        assert (low.status, low.rule) == (ABSENT, "DU3_upper_bound_below_partial")   # 15.67 < 18

    def test_unverified_unparsed_dates_block_upper_bound(self):
        text = ACRONYM_CV + "\nConsultant, Jan '08 – Mar '09"
        ctx = build_experience_context(failed_repair_doc(text), as_of=AS_OF)
        assert ctx.has_unparsed_dates
        d = decide_experience_status(RequirementSpec(policy="pure_duration", required_years=20), ctx, {})
        assert (d.status, d.cd_reason) == (CANNOT_DETERMINE, "structure_unverified")

    def test_trusted_pure_duration_excludes_education(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        ctx = build_experience_context(doc, as_of=AS_OF)        # education anchor A3 is not an entry
        # E1 91 + E2 49 = 140 months = 11.67 years (education 2010-2014 excluded)
        d = decide_experience_status(RequirementSpec(policy="pure_duration", required_years=12), ctx, {})
        assert (d.status, d.audit["duration"]["total_years"]) == (PARTIAL, 11.67)
        assert d.audit["duration"]["entry_ids"] == ["E1", "E2"]
        ok = decide_experience_status(RequirementSpec(policy="pure_duration", required_years=11), ctx, {})
        assert ok.status == MATCHED

    def test_failed_document(self):
        doc, _ = build("   ")
        ctx = build_experience_context(doc, as_of=AS_OF)
        d = decide_experience_status(RequirementSpec(policy="functional", required_years=3), ctx, {})
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified", "F_structure_failed")
        assert d.status != ABSENT

    def test_unowned_evidence_cannot_cite_owned_lines(self):
        doc, _ = build(ACRONYM_CV, acronym_response())
        ctx = build_experience_context(doc, as_of=AS_OF)
        ev = UnownedEvidence("U1", "Managed 4 donor-funded programmes", QUALIFYING, "x", 6, 6)
        with pytest.raises(ExperienceAccountingError, match="cites owned lines"):
            decide_experience_status(RequirementSpec(policy="functional", required_years=3), ctx,
                                     {"E1": QUALIFYING, "E2": QUALIFYING}, [ev])


# ═════════════════════════════════════════════════════════════════════════════
# Request contract, cache, schema
# ═════════════════════════════════════════════════════════════════════════════

class TestRequestContract:

    def test_one_call_temperature_zero_json(self):
        _, client = build(ACRONYM_CV, acronym_response())
        (req,) = client.requests
        assert req["temperature"] == 0.0 and req["response_format"] == {"type": "json_object"}
        assert req["model"] == st.S0C_MODEL

    def test_prompt_carries_anchor_ids_and_numbered_lines(self):
        _, client = build(ACRONYM_CV, acronym_response())
        user = client.requests[0]["messages"][1]["content"]
        assert "A1 | line 5 | 'March 2019 - Present'" in user
        assert "L0004| IISD" in user and "CV LINES:" in user

    def test_system_prompt_rules(self):
        p = st.S0C_SYSTEM_PROMPT
        for frag in ("Never write, compute or correct a date", "EXACTLY ONCE", "LEAVE IT OUT",
                     '"ownership": "uncertain"', "VERBATIM", "inside_responsibility"):
            assert frag in p


class TestCache:

    def test_key_identity(self):
        k = st.s0_cache_key("cv text")
        assert k == st.s0_cache_key("cv text")
        assert k != st.s0_cache_key("cv text ")
        assert k != st.s0_cache_key("cv text", model="other-model")
        assert k != st.s0_cache_key("cv text", prompt_version="s0c-2")

    def test_s0_is_job_independent(self):
        params = set(inspect.signature(st.build_s0).parameters)
        assert params == {"extracted_text", "client", "model", "prompt_version", "today", "cache"}
        assert set(inspect.signature(st.s0_cache_key).parameters) == {"extracted_text", "prompt_version", "model"}

    def test_cache_hit_skips_call_and_round_trips(self):
        cache = st.InMemoryS0Cache()
        doc, _ = build(ACRONYM_CV, acronym_response(), cache=cache)
        assert list(cache.store) == [doc.cache_key]
        doc2, client2 = build(ACRONYM_CV, cache=cache)
        assert client2.requests == [] and doc2.to_dict() == doc.to_dict()

    def test_validation_failed_unverified_is_cached(self):
        bad = json.loads(acronym_response())
        bad["ignored_anchors"] = []
        cache = st.InMemoryS0Cache()
        doc, _ = build(ACRONYM_CV, json.dumps(bad), json.dumps(bad), cache=cache)
        assert doc.status_reason == "validation_failed" and doc.cache_key in cache.store


class TestSchema:

    def test_json_round_trip(self):
        doc, _ = build(MULTI_ROLE_CV, response([
            entry("A1", title=(3, "Senior Education Officer"), employer=(2, "UNICEF"), header=(2, 3, 4), body=[(5, 5)]),
            entry("A2", title=(6, "Education Officer"), employer=(2, "UNICEF"), header=(2, 6, 7), body=[(8, 8)])]))
        d = json.loads(json.dumps(doc.to_dict()))
        assert d["_schema"] == "s0_experience_v1" and d["s0_version"] == "1.0.0"
        assert S0Document.from_dict(d).to_dict() == doc.to_dict()
        assert build_experience_entries(d, as_of=AS_OF) == build_experience_entries(doc, as_of=AS_OF)

    def test_rejects_other_schema(self):
        with pytest.raises(ValueError):
            S0Document.from_dict({"_schema": "cvfacts"})


class TestIsolation:

    def test_s0_does_not_use_legacy_extractor_or_scoring(self):
        for f in (BACKEND / "services" / "s0_experience").glob("*.py"):
            src = f.read_text(encoding="utf-8")
            for banned in ("cv_evidence", "llm_criteria_mapper", "deterministic_scoring",
                           "criteria_matcher"):
                assert banned not in src, (f.name, banned)

    def test_nothing_in_production_imports_s0(self):
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            for p in (BACKEND / sub).rglob("*.py") if (BACKEND / sub).exists() else []:
                # shadow-only packages (S0 itself, S4/S5, S2) may use S0
                if ("s0_experience" in p.parts or "s2_experience" in p.parts
                        or p.name == "experience_accounting.py"):
                    continue
                if "s0_experience" in p.read_text(encoding="utf-8"):
                    hits.append(str(p))
        assert hits == []
