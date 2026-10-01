"""
P0-02 experience accounting (S4 / S5) — deterministic, shadow only.

Labels and requirement specs are recorded fixtures (S1/S2 are not implemented);
no LLM, no fuzzy matching. Entries are built directly (S4/S5 unit tests); the
S0 v2 -> ExperienceEntry adapter is covered in test_s0_experience.py.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.experience_accounting import (
    ABSENT, CANNOT_DETERMINE, INSUFFICIENT, MATCHED, NOT_RELEVANT, PARTIAL, QUALIFYING, RELATED,
    ExperienceAccountingError, ExperienceContext, ExperienceEntry, RequirementSpec,
    UnownedEvidence, account_relevant_years, decide_experience_status,
)
from services.s0_experience.dates import month_interval

Q, R, NR, I = QUALIFYING, RELATED, NOT_RELEVANT, INSUFFICIENT
AS_OF = (2026, 10)


# ── helpers ──────────────────────────────────────────────────────────────────

def _exp(title="Role", sy=None, sm=None, ey=None, em=None, current=False, years=None, employer="Org"):
    """A job spec; ``years`` is accepted for readability only (duration is derived)."""
    return dict(title=title, sy=sy, sm=sm, ey=ey, em=em, current=current, employer=employer)


def _entries(*exps) -> list[ExperienceEntry]:
    out = []
    for i, x in enumerate(exps, 1):
        iv = month_interval(x["sy"], x["sm"], x["ey"], x["em"])
        out.append(ExperienceEntry(
            entry_id=f"E{i}", anchor_id=f"A{i}" if x["sy"] else None, kind="employment",
            title=x["title"], employer=x["employer"], start_year=x["sy"], start_month=x["sm"],
            end_year=x["ey"], end_month=x["em"], is_current=x["current"], interval=iv,
            duration_months=None if iv is None else iv[1] - iv[0], source_text=x["title"],
            as_of=AS_OF))
    return out


def _labels(*labs) -> dict[str, str]:
    return {f"E{i}": lab for i, lab in enumerate(labs, 1)}


def _spec(n, policy="functional"):
    return RequirementSpec(policy=policy, required_years=n, targets=("project management",),
                           spec_version="fixture-1")


def _ctx(entries=(), structure="validated", date_status="dated", **kw) -> ExperienceContext:
    return ExperienceContext(structure_status=structure, date_status=date_status,
                             duration_as_of=AS_OF, entries=tuple(entries),
                             line_count=kw.pop("line_count", 200), **kw)


def _decide(n, exps, labs, policy="functional", unowned=(), **ctx_kw):
    return decide_experience_status(_spec(n, policy), _ctx(_entries(*exps), **ctx_kw),
                                     _labels(*labs), unowned)


def _uo(label, i=1, line=150):
    return UnownedEvidence(evidence_id=f"U{i}", quote=f"quote {i}", label=label,
                           reason=f"reason {i}", line_start=line, line_end=line)


# ═════════════════════════════════════════════════════════════════════════════
# S4 — relevant-years accounting
# ═════════════════════════════════════════════════════════════════════════════

class TestS4Accounting:

    def test_per_label_totals(self):
        es = _entries(_exp("A", 2010, None, 2013), _exp("B", 2013, None, 2015),
                      _exp("C", 2015, None, 2016), _exp("D", 2016, None, 2020))
        acc = account_relevant_years(es, _labels(Q, R, I, NR))
        assert (acc.q, acc.r, acc.i) == (3.0, 2.0, 1.0)
        assert (acc.u_q, acc.u_i) == (False, False)

    def test_overlapping_qualifying_roles_not_double_counted(self):
        es = _entries(_exp("A", 2015, 1, 2020, 1), _exp("B", 2018, 1, 2022, 1), _exp("C", 2019, 1, 2019, 7))
        acc = account_relevant_years(es, _labels(Q, Q, Q))
        assert acc.q == 7.0                                  # 2015-01 .. 2022-01

    def test_adjacent_ranges_merge_without_gap_or_overlap(self):
        es = _entries(_exp("A", 2015, None, 2017), _exp("B", 2017, None, 2019))
        assert account_relevant_years(es, _labels(Q, Q)).q == 4.0

    def test_overlap_merged_within_each_label_only(self):
        es = _entries(_exp("A", 2015, None, 2020), _exp("B", 2016, None, 2018))
        acc = account_relevant_years(es, _labels(Q, R))
        assert (acc.q, acc.r) == (5.0, 2.0)

    def test_undated_entries_set_flags_not_years(self):
        es = _entries(_exp("A"), _exp("B"), _exp("C"), _exp("D", 2015, None, 2017))
        acc = account_relevant_years(es, _labels(Q, I, R, Q))
        assert (acc.q, acc.i, acc.r) == (2.0, 0.0, 0.0)
        assert acc.u_q and acc.u_i
        assert acc.q_undated_ids == ("E1",) and acc.i_undated_ids == ("E2",)

    @pytest.mark.parametrize("labels, fragment", [
        ({"E1": Q}, "without a label"),
        ({"E1": Q, "E2": Q, "E3": Q}, "unknown entries"),
        ({"E1": Q, "E2": "relevant"}, "invalid labels"),
    ])
    def test_label_set_must_match_entries_exactly(self, labels, fragment):
        es = _entries(_exp("A", 2015, None, 2017), _exp("B", 2017, None, 2019))
        with pytest.raises(ExperienceAccountingError, match=fragment):
            account_relevant_years(es, labels)


# ═════════════════════════════════════════════════════════════════════════════
# S5 — decision table (years threshold)
# ═════════════════════════════════════════════════════════════════════════════

class TestS5Threshold:

    def test_t1_qualifying_only_meets_threshold(self):
        d = _decide(5, [_exp("PM", 2015, None, 2021)], [Q])
        assert (d.status, d.cd_reason, d.rule) == (MATCHED, None, "T1_qualifying_meets_threshold")
        assert d.uncertainty_entry_ids == ()

    def test_t1_overlap_cannot_inflate_to_threshold(self):
        # two concurrent 3-year roles = 3 years, not 6
        d = _decide(5, [_exp("A", 2018, None, 2021), _exp("B", 2018, None, 2021)], [Q, Q])
        assert d.status == PARTIAL and d.audit["qualifying_years"] == 3.0

    def test_t1_wins_over_undated_and_insufficient(self):
        d = _decide(5, [_exp("PM", 2010, None, 2016), _exp("X"), _exp("Y")], [Q, Q, I])
        assert d.status == MATCHED

    def test_t2_undated_qualifying(self):
        d = _decide(5, [_exp("PM", 2019, None, 2021), _exp("Consultant PM")], [Q, Q])
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "detail_missing", "T2_undated_qualifying")
        assert d.uncertainty_entry_ids == ("E2",)

    def test_t2_precedes_t3(self):
        d = _decide(5, [_exp("PM"), _exp("Consultant")], [Q, I])
        assert d.rule == "T2_undated_qualifying" and d.uncertainty_entry_ids == ("E1",)

    def test_t3_dated_insufficient_decisive(self):
        d = _decide(5, [_exp("PM", 2019, None, 2022), _exp("Consultant", 2015, None, 2019)], [Q, I])
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "relevance_unverified",
                                                   "T3_insufficient_could_close_gap")
        assert d.uncertainty_entry_ids == ("E2",)
        assert d.audit["gap_years"] == 2.0 and d.audit["insufficient_years_beyond_qualifying"] == 4.0

    def test_t3_dated_insufficient_not_decisive_falls_through(self):
        d = _decide(5, [_exp("PM", 2019, None, 2022), _exp("Consultant", 2017, None, 2018)], [Q, I])
        assert (d.status, d.rule) == (PARTIAL, "T4_shortfall_or_related_only")
        assert d.cd_reason is None and d.uncertainty_entry_ids == ()

    def test_t3_insufficient_overlapping_qualifying_does_not_count_twice(self):
        # insufficient 2017-2022 overlaps qualifying 2018-2022: adds only 1 year -> 5 total
        d = _decide(5, [_exp("PM", 2018, None, 2022), _exp("Consultant", 2017, None, 2022)], [Q, I])
        assert d.rule == "T3_insufficient_could_close_gap"
        assert d.audit["insufficient_years_beyond_qualifying"] == 1.0
        d = _decide(6, [_exp("PM", 2018, None, 2022), _exp("Consultant", 2017, None, 2022)], [Q, I])
        assert d.rule == "T4_shortfall_or_related_only"

    def test_t3_several_insufficient_only_together_close_gap(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("C1", 2016, None, 2018),
                        _exp("C2", 2018, None, 2019), _exp("C3", 2012, None, 2012, years=0.0)],
                    [Q, I, I, NR])
        assert d.rule == "T3_insufficient_could_close_gap"
        assert d.uncertainty_entry_ids == ("E2", "E3")

    def test_t3_undated_insufficient(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("Consultant")], [Q, I])
        assert (d.status, d.cd_reason) == (CANNOT_DETERMINE, "relevance_unverified")
        assert d.uncertainty_entry_ids == ("E2",)

    def test_t3_records_undated_and_decisive_dated(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("C1"), _exp("C2", 2014, None, 2019)], [Q, I, I])
        assert d.uncertainty_entry_ids == ("E2", "E3")

    def test_t3_undated_with_non_decisive_dated_names_only_undated(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("C1"), _exp("C2", 2018, None, 2019)], [Q, I, I])
        assert d.uncertainty_entry_ids == ("E2",)

    def test_t4_related_only(self):
        d = _decide(5, [_exp("Teaching Assistant", 2010, None, 2020)], [R])
        assert (d.status, d.rule) == (PARTIAL, "T4_shortfall_or_related_only")
        assert d.audit["related_years"] == 10.0 and d.audit["qualifying_years"] == 0.0

    def test_t4_qualifying_shortfall(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("Sales", 2010, None, 2020)], [Q, NR])
        assert d.status == PARTIAL

    def test_t5_no_relevant_experience(self):
        d = _decide(5, [_exp("Sales", 2010, None, 2020), _exp("Driver")], [NR, NR])
        assert (d.status, d.cd_reason, d.rule) == (ABSENT, None, "T5_no_relevant_experience")

    def test_t5_no_entries(self):
        d = _decide(5, [], [])
        assert d.status == ABSENT

    def test_undated_related_has_no_effect(self):
        d = _decide(5, [_exp("Assistant")], [R])
        assert d.status == PARTIAL and d.audit["related_years"] == 0.0

    def test_fractional_threshold(self):
        d = _decide(1.5, [_exp("PM", 2020, 1, 2021, 7)], [Q])
        assert d.status == MATCHED
        d = _decide(1.5, [_exp("PM", 2020, 1, 2021, 6)], [Q])
        assert d.status == PARTIAL


class TestS5NoThreshold:

    @pytest.mark.parametrize("labs, status, rule, cd", [
        ([Q, R, I], MATCHED, "N1_qualifying", None),
        ([R, I, NR], CANNOT_DETERMINE, "N2_insufficient", "relevance_unverified"),
        ([R, NR], PARTIAL, "N3_related", None),
        ([NR, NR], ABSENT, "N4_no_relevant_experience", None),
        ([], ABSENT, "N4_no_relevant_experience", None),
    ])
    def test_rules(self, labs, status, rule, cd):
        exps = [_exp(f"R{i}", 2015, None, 2016) for i in range(len(labs))]
        d = _decide(None, exps, labs)
        assert (d.status, d.rule, d.cd_reason) == (status, rule, cd)

    def test_undated_qualifying_still_matches(self):
        d = _decide(None, [_exp("CRM admin")], [Q])
        assert d.status == MATCHED

    def test_insufficient_entries_recorded(self):
        d = _decide(None, [_exp("A", 2015, None, 2016), _exp("B")], [I, I])
        assert d.uncertainty_entry_ids == ("E1", "E2")


class TestS5SpecAndAudit:

    def test_audit_record(self):
        d = _decide(5, [_exp("PM", 2019, 3, 2021, 3, employer="IISD"), _exp("Consultant", 2014, None, 2018)],
                    [Q, I], policy="explicit_role")
        a = d.audit
        assert a["rule"] == "T3_insufficient_could_close_gap"
        assert a["status"] == CANNOT_DETERMINE and a["cd_reason"] == "relevance_unverified"
        assert a["uncertainty_entry_ids"] == ["E2"]
        assert (a["policy"], a["spec_version"], a["required_years"]) == ("explicit_role", "fixture-1", 5)
        assert (a["qualifying_years"], a["insufficient_years"], a["gap_years"]) == (2.0, 4.0, 3.0)
        assert a["entries"][0] == {"entry_id": "E1", "label": Q, "title": "PM", "employer": "IISD",
                                   "start": "2019-03", "end": "2021-03", "years": 2.0, "dated": True}
        assert a["entries"][1]["start"] == "2014" and a["entries"][1]["label"] == I
        json.dumps(a)                                        # JSON-safe

    def test_audit_current_role(self):
        d = _decide(1, [_exp("PM", 2019, 3, 2026, 10, current=True)], [Q])
        assert d.audit["entries"][0]["end"] == "present"

    def test_audit_records_duration_as_of(self):
        d = _decide(5, [_exp("PM", 2015, None, 2021)], [Q])
        assert d.audit["duration_as_of"] == "2026-10"
        assert _decide(None, [], []).audit["duration_as_of"] == "2026-10"

    def test_mixed_as_of_rejected(self):
        a, b = _entries(_exp("A", 2015, None, 2017), _exp("B", 2018, None, 2020))
        b = dataclasses.replace(b, as_of=(2027, 1))
        with pytest.raises(ExperienceAccountingError, match="different as_of"):
            decide_experience_status(_spec(5), _ctx([a, b]), _labels(Q, Q))

    def test_current_entry_without_as_of_rejected(self):
        (a,) = _entries(_exp("PM", 2019, 3, 2026, 10, current=True))
        a = dataclasses.replace(a, as_of=None)
        with pytest.raises(ExperienceAccountingError, match="different as_of"):
            decide_experience_status(_spec(1), _ctx([a]), _labels(Q))

    def test_pure_duration_needs_required_years(self):
        with pytest.raises(ExperienceAccountingError, match="pure_duration"):
            RequirementSpec(policy="pure_duration", required_years=None)

    @pytest.mark.parametrize("kw", [dict(policy="relevant", required_years=5),
                                    dict(policy="functional", required_years=0),
                                    dict(policy="functional", required_years=-1)])
    def test_invalid_spec(self, kw):
        with pytest.raises(ExperienceAccountingError):
            RequirementSpec(**kw)

    def test_same_inputs_same_decision(self):
        exps = [_exp("PM", 2019, None, 2022), _exp("C", 2015, None, 2019), _exp("TA", 2010, None, 2015)]
        assert _decide(5, exps, [Q, I, R]) == _decide(5, exps, [Q, I, R])


# ═════════════════════════════════════════════════════════════════════════════
# Unowned evidence (UO_Q / UO_I / UO_R) and structure-status safeguards
# ═════════════════════════════════════════════════════════════════════════════

class TestUnownedThreshold:

    def test_t2s_unowned_qualifying(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022)], [Q], unowned=[_uo(Q)])
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified",
                                                   "T2s_unowned_qualifying_evidence")
        assert d.uncertainty_evidence_ids == ("U1",)
        assert d.audit["responsible_evidence_ids"] == ["U1"]

    def test_t1_overrides_unowned_uncertainty(self):
        d = _decide(5, [_exp("PM", 2015, None, 2021)], [Q],
                    unowned=[_uo(Q, 1), _uo(I, 2, 151), _uo(R, 3, 152)])
        assert (d.status, d.rule, d.cd_reason) == (MATCHED, "T1_qualifying_meets_threshold", None)
        assert d.uncertainty_evidence_ids == ()

    def test_t2_precedes_t2s(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("Consultant PM")], [Q, Q], unowned=[_uo(Q)])
        assert (d.rule, d.cd_reason) == ("T2_undated_qualifying", "detail_missing")
        assert d.uncertainty_entry_ids == ("E2",) and d.uncertainty_evidence_ids == ()

    def test_t2s_precedes_t3(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("Consultant")], [Q, I],
                    unowned=[_uo(Q, 1), _uo(I, 2, 151)])
        assert d.rule == "T2s_unowned_qualifying_evidence" and d.uncertainty_evidence_ids == ("U1",)

    def test_t3_unowned_insufficient(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022)], [Q], unowned=[_uo(I)])
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "relevance_unverified",
                                                   "T3_insufficient_could_close_gap")
        assert d.uncertainty_evidence_ids == ("U1",) and d.uncertainty_entry_ids == ()

    def test_t3_owned_and_unowned_insufficient_both_recorded(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("C", 2014, None, 2019)], [Q, I],
                    unowned=[_uo(I)])
        assert d.uncertainty_entry_ids == ("E2",) and d.uncertainty_evidence_ids == ("U1",)

    def test_t4_unowned_related(self):
        d = _decide(5, [_exp("Sales", 2010, None, 2020)], [NR], unowned=[_uo(R)])
        assert (d.status, d.rule) == (PARTIAL, "T4_shortfall_or_related_only")
        assert d.uncertainty_evidence_ids == ("U1",)

    def test_t4_owned_related_takes_precedence_over_unowned(self):
        d = _decide(5, [_exp("TA", 2010, None, 2020)], [R], unowned=[_uo(R)])
        assert d.status == PARTIAL and d.uncertainty_evidence_ids == ()

    def test_unowned_not_relevant_is_absent(self):
        d = _decide(5, [_exp("Sales", 2010, None, 2020)], [NR], unowned=[_uo(NR)])
        assert (d.status, d.rule) == (ABSENT, "T5_no_relevant_experience")

    def test_zero_evidence_is_absent(self):
        d = _decide(5, [], [])
        assert d.status == ABSENT and d.audit["unowned_counts"] == {Q: 0, I: 0, R: 0, NR: 0}


class TestUnownedNoThreshold:

    @pytest.mark.parametrize("ev, status, rule, cd", [
        ([Q], MATCHED, "N1_qualifying", None),
        ([I], CANNOT_DETERMINE, "N2_insufficient", "relevance_unverified"),
        ([R], PARTIAL, "N3_related", None),
        ([NR], ABSENT, "N4_no_relevant_experience", None),
        ([I, Q], MATCHED, "N1_qualifying", None),
        ([R, I], CANNOT_DETERMINE, "N2_insufficient", "relevance_unverified"),
    ])
    def test_unowned_only(self, ev, status, rule, cd):
        d = _decide(None, [], [], unowned=[_uo(lab, i, 150 + i) for i, lab in enumerate(ev, 1)])
        assert (d.status, d.rule, d.cd_reason) == (status, rule, cd)

    def test_owned_qualifying_reported_without_unowned_responsibility(self):
        d = _decide(None, [_exp("PM", 2019, None, 2020)], [Q], unowned=[_uo(Q)])
        assert d.status == MATCHED and d.uncertainty_evidence_ids == ()


class TestStructureStatus:

    @pytest.mark.parametrize("ev", [[Q], [I], [R], [Q, I, R], []])
    def test_unverified_threshold_never_matched(self, ev):
        d = _decide(1, [], [], structure="unverified",
                    unowned=[_uo(lab, i, 150 + i) for i, lab in enumerate(ev, 1)])
        assert d.status != MATCHED
        assert d.audit["structure_status"] == "unverified" and d.audit["qualifying_years"] == 0

    def test_unverified_uo_q_structure_unverified(self):
        d = _decide(1, [], [], structure="unverified", unowned=[_uo(Q)])
        assert (d.status, d.cd_reason) == (CANNOT_DETERMINE, "structure_unverified")

    def test_unverified_no_years_criterion_may_match(self):
        d = _decide(None, [], [], structure="unverified", unowned=[_uo(Q)])
        assert d.status == MATCHED

    def test_unverified_must_not_supply_entries(self):
        with pytest.raises(ExperienceAccountingError, match="must not supply owned entries"):
            _decide(5, [_exp("PM", 2015, None, 2021)], [Q], structure="unverified")

    @pytest.mark.parametrize("n, policy", [(5, "functional"), (None, "explicit_role"), (3, "pure_duration")])
    def test_failed_never_silently_absent(self, n, policy):
        d = decide_experience_status(_spec(n, policy), _ctx(structure="failed"), {})
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified", "F_structure_failed")

    def test_failed_must_not_supply_entries(self):
        with pytest.raises(ExperienceAccountingError):
            _decide(5, [_exp("PM", 2015, None, 2021)], [Q], structure="failed")

    @pytest.mark.parametrize("labs, status, cd", [
        ([Q], CANNOT_DETERMINE, "detail_missing"),
        ([I], CANNOT_DETERMINE, "relevance_unverified"),
        ([R], PARTIAL, None),
        ([NR], ABSENT, None),
    ])
    def test_undated_trusted_structure(self, labs, status, cd):
        d = _decide(3, [_exp("Role")], labs, date_status="undated")
        assert (d.status, d.cd_reason) == (status, cd)

    def test_undated_no_years_qualifying_matches(self):
        assert _decide(None, [_exp("Role")], [Q], date_status="undated").status == MATCHED

    def test_repaired_behaves_like_validated(self):
        a = _decide(5, [_exp("PM", 2015, None, 2021)], [Q], structure="repaired")
        assert a.status == MATCHED and a.audit["structure_status"] == "repaired"


class TestUnownedValidation:

    @pytest.mark.parametrize("ev, frag", [
        (UnownedEvidence("U1", "q", "relevant", "r", 1, 1), "invalid label"),
        (UnownedEvidence("U1", " ", Q, "r", 1, 1), "quote and reason are required"),
        (UnownedEvidence("U1", "q", Q, "", 1, 1), "quote and reason are required"),
        (UnownedEvidence("U1", "q", Q, "r", 0, 1), "invalid line range"),
        (UnownedEvidence("U1", "q", Q, "r", 5, 4), "invalid line range"),
        (UnownedEvidence("U1", "q", Q, "r", 199, 201), "invalid line range"),
    ])
    def test_rejected(self, ev, frag):
        with pytest.raises(ExperienceAccountingError, match=frag):
            _decide(5, [], [], unowned=[ev])

    def test_duplicate_ids(self):
        with pytest.raises(ExperienceAccountingError, match="duplicate"):
            _decide(5, [], [], unowned=[_uo(Q, 1), _uo(R, 1, 151)])

    def test_evidence_inside_owned_lines_rejected(self):
        with pytest.raises(ExperienceAccountingError, match="cites owned lines"):
            _decide(5, [], [], unowned=[_uo(Q, 1, 12)], owned_lines=frozenset({11, 12}))


class TestPureDuration:

    def _pd(self, n, exps, structure="validated", **kw):
        return decide_experience_status(RequirementSpec(policy="pure_duration", required_years=n),
                                        _ctx(_entries(*exps), structure=structure, **kw), {})

    def test_trusted_total_meets_threshold(self):
        d = self._pd(5, [_exp("A", 2010, None, 2013), _exp("B", 2013, None, 2016)])
        assert (d.status, d.rule) == (MATCHED, "D1_total_meets_threshold")
        assert d.audit["duration"]["total_years"] == 6.0

    def test_trusted_overlap_merged(self):
        d = self._pd(5, [_exp("A", 2016, None, 2020), _exp("B", 2017, None, 2019)])
        assert d.status == PARTIAL and d.audit["duration"]["total_years"] == 4.0

    def test_trusted_undated_experience_short(self):
        d = self._pd(5, [_exp("A", 2018, None, 2020), _exp("B")])
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "detail_missing",
                                                   "D2_undated_experience_entry")
        assert d.uncertainty_entry_ids == ("E2",)

    def test_trusted_undated_but_dated_total_meets(self):
        assert self._pd(2, [_exp("A", 2018, None, 2020), _exp("B")]).status == MATCHED

    @pytest.mark.parametrize("years, status, rule", [
        (3, PARTIAL, "D3_total_partial"), (2, ABSENT, "D4_total_insufficient"), (0, ABSENT, "D4_total_insufficient"),
    ])
    def test_trusted_existing_threshold_rule(self, years, status, rule):
        exps = [_exp("A", 2010, None, 2010 + years)] if years else []
        d = self._pd(5, exps)
        assert (d.status, d.rule) == (status, rule)

    def test_labels_or_evidence_rejected(self):
        with pytest.raises(ExperienceAccountingError, match="no relevance labels"):
            decide_experience_status(RequirementSpec(policy="pure_duration", required_years=3),
                                     _ctx(), {}, [_uo(Q)])

    def _ub(self, n, intervals, **kw):
        return decide_experience_status(
            RequirementSpec(policy="pure_duration", required_years=n),
            _ctx(structure="unverified",
                 anchor_intervals=tuple((f"A{i}", iv) for i, iv in enumerate(intervals, 1)), **kw), {})

    # N = 5 years -> PARTIAL band starts at 3 years (0.6 N)
    @pytest.mark.parametrize("years, status, rule, cd", [
        (6, CANNOT_DETERMINE, "DU2_upper_bound_inconclusive", "structure_unverified"),    # >= N: not MATCHED
        (5, CANNOT_DETERMINE, "DU2_upper_bound_inconclusive", "structure_unverified"),
        (4, CANNOT_DETERMINE, "DU2_upper_bound_inconclusive", "structure_unverified"),    # not PARTIAL
        (3, CANNOT_DETERMINE, "DU2_upper_bound_inconclusive", "structure_unverified"),    # exactly 0.6 N
        (2, ABSENT, "DU3_upper_bound_below_partial", None),                               # bound < 3 years
        (0, ABSENT, "DU3_upper_bound_below_partial", None),
    ])
    def test_unverified_upper_bound_rules(self, years, status, rule, cd):
        d = self._ub(5, [(2010 * 12, (2010 + years) * 12)] if years else [])
        assert (d.status, d.rule, d.cd_reason) == (status, rule, cd)
        assert d.audit["duration"]["upper_bound_years"] == float(years)

    @pytest.mark.parametrize("years", [0, 1, 2, 3, 4, 5, 6, 10, 40])
    def test_unverified_never_matched_or_partial(self, years):
        d = self._ub(5, [(1980 * 12, (1980 + years) * 12)] if years else [])
        assert d.status not in (MATCHED, PARTIAL)

    def test_unverified_upper_bound_merges_overlaps(self):
        d = self._ub(5, [(2016 * 12, 2018 * 12), (2017 * 12, 2018 * 12)])
        assert d.audit["duration"]["upper_bound_years"] == 2.0 and d.status == ABSENT

    def test_unverified_unparsed_dates(self):
        d = self._ub(5, [(2018 * 12, 2020 * 12)], has_unparsed_dates=True)      # U = 2 years
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified",
                                                   "DU1_upper_bound_not_established")

    def test_unverified_unbounded_anchor(self):
        d = self._ub(5, [(2018 * 12, 2020 * 12)], unbounded_anchor_ids=("A9",))  # U = 2 years
        assert (d.status, d.cd_reason, d.rule) == (CANNOT_DETERMINE, "structure_unverified",
                                                   "DU1_upper_bound_not_established")
        assert d.audit["duration"]["unbounded_anchor_ids"] == ["A9"]


class TestAuditCompleteness:

    def test_audit_fields(self):
        d = _decide(5, [_exp("PM", 2020, None, 2022), _exp("C")], [Q, I], unowned=[_uo(Q), _uo(R, 2, 151)],
                    s0_cache_key="abc123", s0_version="1.0.0")
        a = d.audit
        for k in ("rule", "status", "cd_reason", "structure_status", "date_status", "s0_cache_key",
                  "s0_version", "duration_as_of", "policy", "required_years", "qualifying_years",
                  "related_years", "insufficient_years", "undated_qualifying", "undated_insufficient",
                  "unowned_counts", "uncertainty_entry_ids", "responsible_evidence_ids",
                  "owned_entry_ids", "entries", "unowned_evidence", "duration"):
            assert k in a, k
        assert (a["structure_status"], a["date_status"], a["s0_cache_key"]) == ("validated", "dated", "abc123")
        assert a["unowned_counts"] == {Q: 1, I: 0, R: 1, NR: 0}
        assert a["owned_entry_ids"] == ["E1", "E2"]
        assert a["unowned_evidence"][0] == {"evidence_id": "U1", "label": Q, "quote": "quote 1",
                                            "reason": "reason 1", "lines": [150, 150]}
        assert a["responsible_evidence_ids"] == ["U1"] and a["rule"] == "T2s_unowned_qualifying_evidence"
        json.dumps(a)


class TestNotWiredIntoScoring:

    def test_no_production_module_imports_experience_accounting(self):
        backend = os.path.join(os.path.dirname(__file__), "..")
        hits = []
        for sub in ("services", "workers", "routers", "api"):
            root = os.path.join(backend, sub)
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if f.endswith(".py") and f != "experience_accounting.py":
                        with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                            if "experience_accounting" in fh.read():
                                hits.append(os.path.join(dirpath, f))
        assert hits == []
