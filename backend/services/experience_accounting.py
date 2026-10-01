"""
P0-02 experience-criteria accounting — deterministic foundation (S0 / S4 / S5).

SHADOW-ONLY: nothing in the production scoring path imports this module yet.
It never calls an LLM, never infers relevance and uses no fuzzy matching.

  S0  build_experience_entries(s0_doc, as_of=(year, month))
      Entry view over an S0 v2 document (services.s0_experience): only
      experience-kind entries of a TRUSTED structure (validated / repaired).
      An unverified or failed structure yields no entries — its lines are
      unowned and never contribute years. ``as_of`` (required, no default, no
      system clock) is the scoring month: present/open-ended ranges are
      measured up to it; closed ranges are fixed. Every entry carries it, and
      the S5 audit records it as ``duration_as_of`` so a historical result is
      reproducible from the S0 cache identity + duration_as_of.

  S4  account_relevant_years(entries, labels)
      Given recorded relevance labels (qualifying / related / not_relevant /
      insufficient — produced later by S2, supplied as fixtures for now),
      merges overlapping date ranges per label and returns Q, R, I and the
      undated flags U_Q / U_I.

  S5  decide_experience_status(spec, ctx, labels, unowned=())
      ``ctx = build_experience_context(s0_doc, as_of=...)`` carries the S0
      structure_status / date_status / cache identity, the owned entries
      (trusted structures only) and the deterministic anchors. ``unowned`` is
      S2 evidence no validated entry owns (UO_Q / UO_I / UO_R): it can show
      what the candidate did but never contributes years. Applies the frozen
      decision table (T1-T5 with T2s, N1-N4, pure-duration D/DU rules,
      structure_failed) and returns the status, cd_reason and an audit record
      naming the rule, the entries and the unowned evidence responsible.

Duration convention: services.s0_experience.dates.month_interval — both
months known -> month arithmetic, otherwise whole years; [start, end) month
index; zero-length ranges have unknown duration. An entry whose duration is
unknown is *undated*: it contributes to U_Q / U_I, never to Q / R / I.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from services.s0_experience.dates import check_as_of
from services.s0_experience.schema import (
    STRUCTURE_FAILED, TRUSTED_STRUCTURE, S0Document,
)

# ── vocabulary ───────────────────────────────────────────────────────────────

QUALIFYING = "qualifying"
RELATED = "related"
NOT_RELEVANT = "not_relevant"
INSUFFICIENT = "insufficient"
LABELS = (QUALIFYING, RELATED, NOT_RELEVANT, INSUFFICIENT)

POLICY_EXPLICIT_ROLE = "explicit_role"
POLICY_FUNCTIONAL = "functional"
POLICY_SECTOR = "sector"
POLICY_PURE_DURATION = "pure_duration"
POLICIES = (POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_SECTOR, POLICY_PURE_DURATION)

MATCHED = "MATCHED"
PARTIAL = "PARTIAL"
ABSENT = "ABSENT"
CANNOT_DETERMINE = "CANNOT_DETERMINE"

CD_DETAIL_MISSING = "detail_missing"
CD_RELEVANCE_UNVERIFIED = "relevance_unverified"
CD_STRUCTURE_UNVERIFIED = "structure_unverified"

# Existing pure-duration rule (criteria_matcher numeric fallback):
#   total >= N -> MATCHED; total >= 0.6 * N -> PARTIAL; otherwise ABSENT.
PURE_DURATION_PARTIAL_RATIO = 0.6

# Rule identifiers recorded in the audit (threshold table, then N = null table).
RULE_QUALIFYING_MEETS_THRESHOLD = "T1_qualifying_meets_threshold"
RULE_UNDATED_QUALIFYING = "T2_undated_qualifying"
RULE_UNOWNED_QUALIFYING = "T2s_unowned_qualifying_evidence"
RULE_INSUFFICIENT_COULD_CLOSE_GAP = "T3_insufficient_could_close_gap"
RULE_SHORTFALL_OR_RELATED = "T4_shortfall_or_related_only"
RULE_NO_RELEVANT_EXPERIENCE = "T5_no_relevant_experience"
RULE_NO_YEARS_QUALIFYING = "N1_qualifying"
RULE_NO_YEARS_INSUFFICIENT = "N2_insufficient"
RULE_NO_YEARS_RELATED = "N3_related"
RULE_NO_YEARS_NONE = "N4_no_relevant_experience"
RULE_STRUCTURE_FAILED = "F_structure_failed"
# Pure-duration criteria — trusted structure (owned experience-kind entries):
RULE_DURATION_MET = "D1_total_meets_threshold"
RULE_DURATION_UNDATED = "D2_undated_experience_entry"
RULE_DURATION_PARTIAL = "D3_total_partial"
RULE_DURATION_SHORT = "D4_total_insufficient"
# Pure-duration criteria — unverified structure (upper bound over ALL anchors):
RULE_DURATION_UB_NOT_ESTABLISHED = "DU1_upper_bound_not_established"
RULE_DURATION_UB_INCONCLUSIVE = "DU2_upper_bound_inconclusive"
RULE_DURATION_UB_SHORT = "DU3_upper_bound_below_partial"


class ExperienceAccountingError(ValueError):
    """Inputs that the deterministic stages refuse to interpret."""


# ── S0: experience entries from the S0 v2 artifact ───────────────────────────

@dataclass(frozen=True)
class ExperienceEntry:
    entry_id: str                      # S0 v2 entry ID (E1..En, document order)
    anchor_id: str | None
    kind: str
    title: str
    employer: str
    start_year: int | None
    start_month: int | None
    end_year: int | None
    end_month: int | None
    is_current: bool
    interval: tuple[int, int] | None   # [start, end) month index; None = undated
    duration_months: int | None        # None = duration unknown
    source_text: str                   # verbatim owned lines
    flags: tuple[str, ...] = ()
    as_of: tuple[int, int] | None = None   # scoring month the interval was computed at

    @property
    def dated(self) -> bool:
        return self.duration_months is not None

    @property
    def years(self) -> float | None:
        return None if self.duration_months is None else round(self.duration_months / 12.0, 2)


def build_experience_entries(s0_doc: S0Document | Mapping, *,
                             as_of: tuple[int, int]) -> list[ExperienceEntry]:
    """S0 — experience-kind entries of a trusted S0 v2 structure, else [].
    Intervals are computed at the scoring month ``as_of`` (required)."""
    as_of = check_as_of(as_of)
    doc = s0_doc if isinstance(s0_doc, S0Document) else S0Document.from_dict(dict(s0_doc))
    anchors = {a.anchor_id: a for a in doc.anchors}
    out: list[ExperienceEntry] = []
    for e in doc.experience_entries():
        a = anchors.get(e.anchor_id) if e.anchor_id else None
        iv = a.interval_at(as_of) if a is not None else None
        out.append(ExperienceEntry(
            entry_id=e.entry_id, anchor_id=e.anchor_id, kind=e.kind,
            title=e.title.text if e.title else "",
            employer=e.employer.text if e.employer else "",
            start_year=a.start_year if a else None, start_month=a.start_month if a else None,
            end_year=a.end_year if a else None, end_month=a.end_month if a else None,
            is_current=e.is_current,
            interval=iv, duration_months=None if iv is None else iv[1] - iv[0],
            source_text=e.source_text, flags=e.flags, as_of=as_of,
        ))
    return out


# ── S4: relevant-years accounting ────────────────────────────────────────────

def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _months(intervals: Iterable[tuple[int, int]]) -> int:
    return sum(e - s for s, e in _merge(intervals))


@dataclass(frozen=True)
class RelevantYears:
    q_months: int
    r_months: int
    i_months: int
    u_q: bool
    u_i: bool
    q_undated_ids: tuple[str, ...]
    i_undated_ids: tuple[str, ...]
    ids_by_label: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def q(self) -> float:
        return round(self.q_months / 12.0, 2)

    @property
    def r(self) -> float:
        return round(self.r_months / 12.0, 2)

    @property
    def i(self) -> float:
        return round(self.i_months / 12.0, 2)


def _validate_labels(entries: list[ExperienceEntry], labels: Mapping[str, str]) -> None:
    ids = {e.entry_id for e in entries}
    unknown = sorted(set(labels) - ids)
    if unknown:
        raise ExperienceAccountingError(f"labels for unknown entries: {unknown}")
    missing = sorted(ids - set(labels), key=lambda x: int(x[1:]))
    if missing:
        raise ExperienceAccountingError(f"entries without a label: {missing}")
    bad = {k: v for k, v in labels.items() if v not in LABELS}
    if bad:
        raise ExperienceAccountingError(f"invalid labels: {bad}")


def account_relevant_years(entries: list[ExperienceEntry], labels: Mapping[str, str]) -> RelevantYears:
    """S4 — every entry must carry exactly one recorded label.

    Overlapping date ranges are merged within each label, so concurrent roles
    are not double-counted. Undated entries never add years; they set U_Q /
    U_I (related / not_relevant undated entries have no effect on status).
    """
    _validate_labels(entries, labels)
    by = {lab: [e for e in entries if labels[e.entry_id] == lab] for lab in LABELS}
    return RelevantYears(
        q_months=_months(e.interval for e in by[QUALIFYING] if e.dated),
        r_months=_months(e.interval for e in by[RELATED] if e.dated),
        i_months=_months(e.interval for e in by[INSUFFICIENT] if e.dated),
        u_q=any(not e.dated for e in by[QUALIFYING]),
        u_i=any(not e.dated for e in by[INSUFFICIENT]),
        q_undated_ids=tuple(e.entry_id for e in by[QUALIFYING] if not e.dated),
        i_undated_ids=tuple(e.entry_id for e in by[INSUFFICIENT] if not e.dated),
        ids_by_label={lab: tuple(e.entry_id for e in by[lab]) for lab in LABELS},
    )


# ── S4 inputs beyond owned entries: structure context + unowned evidence ──────

@dataclass(frozen=True)
class UnownedEvidence:
    """S2 evidence that no validated entry owns (summary text, lines the S0
    structurer left unowned, lines of uncertain entries, or ANY line when the
    structure is unverified). It may show WHAT the candidate did but never
    contributes years. Supplied as a recorded fixture until S2 exists; S2's
    validator will additionally check ``quote`` verbatim against lines
    ``line_start``..``line_end`` of the CV text."""
    evidence_id: str
    quote: str
    label: str                       # qualifying | related | insufficient | not_relevant
    reason: str
    line_start: int                  # 1-based, inclusive
    line_end: int

    def to_audit(self) -> dict:
        return {"evidence_id": self.evidence_id, "label": self.label, "quote": self.quote,
                "reason": self.reason, "lines": [self.line_start, self.line_end]}


@dataclass(frozen=True)
class ExperienceContext:
    """Everything S5 needs from S0 for ONE scoring month (``duration_as_of``).
    Build it with ``build_experience_context``; ``entries`` is empty unless the
    structure is trusted (validated / repaired)."""
    structure_status: str
    date_status: str
    duration_as_of: tuple[int, int]
    entries: tuple[ExperienceEntry, ...] = ()
    owned_lines: frozenset[int] = frozenset()
    line_count: int = 0
    # Pure-duration upper bound inputs (deterministic anchors, ALL of them):
    anchor_intervals: tuple[tuple[str, tuple[int, int]], ...] = ()
    unbounded_anchor_ids: tuple[str, ...] = ()     # invalid or unknown-length anchors
    has_unparsed_dates: bool = False
    s0_cache_key: str | None = None
    s0_version: str | None = None

    @property
    def trusted(self) -> bool:
        return self.structure_status in TRUSTED_STRUCTURE


def build_experience_context(s0_doc: S0Document | Mapping, *, as_of: tuple[int, int]) -> ExperienceContext:
    """S0 document + the scoring month -> S5 input. No system clock."""
    as_of = check_as_of(as_of)
    doc = s0_doc if isinstance(s0_doc, S0Document) else S0Document.from_dict(dict(s0_doc))
    bounded, unbounded = [], []
    for a in doc.anchors:
        iv = a.interval_at(as_of)
        if iv is None:
            unbounded.append(a.anchor_id)
        else:
            bounded.append((a.anchor_id, iv))
    return ExperienceContext(
        structure_status=doc.structure_status, date_status=doc.date_status, duration_as_of=as_of,
        entries=tuple(build_experience_entries(doc, as_of=as_of)),
        owned_lines=frozenset(doc.owned_line_map()), line_count=doc.line_count,
        anchor_intervals=tuple(bounded), unbounded_anchor_ids=tuple(unbounded),
        has_unparsed_dates=bool(doc.unparsed_date_texts),
        s0_cache_key=doc.cache_key or None, s0_version=doc.s0_version)


def _validate_unowned(ctx: ExperienceContext, unowned: Sequence[UnownedEvidence]) -> None:
    seen: set[str] = set()
    for ev in unowned:
        if ev.evidence_id in seen:
            raise ExperienceAccountingError(f"duplicate unowned evidence id {ev.evidence_id!r}")
        seen.add(ev.evidence_id)
        if ev.label not in LABELS:
            raise ExperienceAccountingError(f"unowned evidence {ev.evidence_id}: invalid label {ev.label!r}")
        if not (ev.quote or "").strip() or not (ev.reason or "").strip():
            raise ExperienceAccountingError(f"unowned evidence {ev.evidence_id}: quote and reason are required")
        if not (isinstance(ev.line_start, int) and isinstance(ev.line_end, int)
                and 1 <= ev.line_start <= ev.line_end
                and (ctx.line_count == 0 or ev.line_end <= ctx.line_count)):
            raise ExperienceAccountingError(
                f"unowned evidence {ev.evidence_id}: invalid line range {ev.line_start}-{ev.line_end}")
        clash = sorted(set(range(ev.line_start, ev.line_end + 1)) & ctx.owned_lines)
        if clash:
            raise ExperienceAccountingError(
                f"unowned evidence {ev.evidence_id} cites owned lines {clash}: "
                f"evidence inside an entry must be given as that entry's label")


# ── S5: decision ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RequirementSpec:
    """S1 output, created once per job criterion (supplied as a fixture here)."""
    policy: str
    required_years: float | None          # N; None = no years threshold
    targets: tuple[str, ...] = ()
    setting: str | None = None
    spec_version: str = ""

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise ExperienceAccountingError(f"unknown policy {self.policy!r}")
        if self.required_years is not None and not self.required_years > 0:
            raise ExperienceAccountingError(f"required_years must be > 0 or None, got {self.required_years!r}")
        if self.policy == POLICY_PURE_DURATION and self.required_years is None:
            raise ExperienceAccountingError("a pure_duration criterion needs required_years")


@dataclass(frozen=True)
class ExperienceDecision:
    status: str
    cd_reason: str | None
    rule: str
    uncertainty_entry_ids: tuple[str, ...]
    audit: dict[str, Any]
    uncertainty_evidence_ids: tuple[str, ...] = ()


def decide_experience_status(
    spec: RequirementSpec,
    ctx: ExperienceContext,
    labels: Mapping[str, str],
    unowned: Sequence[UnownedEvidence] = (),
) -> ExperienceDecision:
    """S5 — the frozen decision table, first matching rule wins.

    structure_status "failed"  -> CANNOT_DETERMINE / structure_unverified (any criterion).
    structure_status "unverified" -> no owned entries (Q = R = I = 0); semantic
    evidence only as UO_*; a years-threshold criterion can never be MATCHED.

    Years threshold (N given):
      T1  Q >= N                                         -> MATCHED
      T2  Q < N and an owned qualifying entry is undated -> CD / detail_missing
      T2s Q < N and UO_Q                                 -> CD / structure_unverified
      T3  Q < N and (owned insufficient could close the gap, or an owned
          insufficient entry is undated, or UO_I)        -> CD / relevance_unverified
      T4  Q + R > 0, or an owned qualifying/related entry (dated or not),
          or UO_R                                        -> PARTIAL
      T5  otherwise                                      -> ABSENT
    No threshold (N = None):
      N1 owned qualifying or UO_Q -> MATCHED;  N2 owned insufficient or UO_I ->
      CD / relevance_unverified;  N3 owned related or UO_R -> PARTIAL;  N4 ABSENT.
    Pure duration: see _decide_pure_duration.
    """
    entries = list(ctx.entries)
    if not ctx.trusted and entries:
        raise ExperienceAccountingError(
            f"structure {ctx.structure_status!r} must not supply owned entries")
    if any(e.as_of != ctx.duration_as_of for e in entries):
        raise ExperienceAccountingError("entries were built with a different as_of than the context")
    if spec.policy == POLICY_PURE_DURATION:
        # Total experience only: no relevance labels, no semantic evidence.
        if labels or unowned:
            raise ExperienceAccountingError("pure_duration criteria take no relevance labels or evidence")
        acc = account_relevant_years([], {})
    else:
        acc = account_relevant_years(entries, labels)
    _validate_unowned(ctx, unowned)
    by_id = {e.entry_id: e for e in entries}
    uo = {lab: [ev for ev in unowned if ev.label == lab] for lab in LABELS}
    uncertainty: tuple[str, ...] = ()
    responsible: list[UnownedEvidence] = []
    cd_reason: str | None = None
    gap_months: int | None = None
    i_gap_months: int | None = None
    duration_detail: dict[str, Any] = {}

    if ctx.structure_status == STRUCTURE_FAILED:
        status, rule, cd_reason = CANNOT_DETERMINE, RULE_STRUCTURE_FAILED, CD_STRUCTURE_UNVERIFIED
    elif spec.policy == POLICY_PURE_DURATION:
        status, rule, cd_reason, uncertainty, duration_detail = _decide_pure_duration(spec, ctx)
    elif spec.required_years is None:
        if acc.ids_by_label[QUALIFYING] or uo[QUALIFYING]:
            status, rule = MATCHED, RULE_NO_YEARS_QUALIFYING
            responsible = [] if acc.ids_by_label[QUALIFYING] else uo[QUALIFYING]
        elif acc.ids_by_label[INSUFFICIENT] or uo[INSUFFICIENT]:
            status, rule, cd_reason = CANNOT_DETERMINE, RULE_NO_YEARS_INSUFFICIENT, CD_RELEVANCE_UNVERIFIED
            uncertainty, responsible = acc.ids_by_label[INSUFFICIENT], uo[INSUFFICIENT]
        elif acc.ids_by_label[RELATED] or uo[RELATED]:
            status, rule = PARTIAL, RULE_NO_YEARS_RELATED
            responsible = [] if acc.ids_by_label[RELATED] else uo[RELATED]
        else:
            status, rule = ABSENT, RULE_NO_YEARS_NONE
    else:
        n_months = round(spec.required_years * 12)
        gap_months = max(0, n_months - acc.q_months)
        q_intervals = [by_id[i].interval for i in acc.ids_by_label[QUALIFYING] if by_id[i].dated]
        # Time insufficient entries add beyond what qualifying entries already
        # cover (concurrent roles are not counted twice).
        dated_insufficient = [by_id[i] for i in acc.ids_by_label[INSUFFICIENT] if by_id[i].dated]
        i_gap_months = _months(q_intervals + [e.interval for e in dated_insufficient]) - acc.q_months
        could_close = acc.q_months + i_gap_months >= n_months and bool(dated_insufficient)
        contributing = tuple(e.entry_id for e in dated_insufficient
                             if _months(q_intervals + [e.interval]) > acc.q_months)
        if acc.q_months >= n_months:
            status, rule = MATCHED, RULE_QUALIFYING_MEETS_THRESHOLD
        elif acc.u_q:
            status, rule, cd_reason = CANNOT_DETERMINE, RULE_UNDATED_QUALIFYING, CD_DETAIL_MISSING
            uncertainty = acc.q_undated_ids
        elif uo[QUALIFYING]:
            status, rule, cd_reason = CANNOT_DETERMINE, RULE_UNOWNED_QUALIFYING, CD_STRUCTURE_UNVERIFIED
            responsible = uo[QUALIFYING]
        elif acc.u_i or could_close or uo[INSUFFICIENT]:
            status, rule, cd_reason = CANNOT_DETERMINE, RULE_INSUFFICIENT_COULD_CLOSE_GAP, CD_RELEVANCE_UNVERIFIED
            uncertainty = tuple(sorted(set(acc.i_undated_ids) | set(contributing if could_close else ()),
                                       key=lambda x: int(x[1:])))
            responsible = uo[INSUFFICIENT]
        elif acc.ids_by_label[QUALIFYING] or acc.ids_by_label[RELATED] or uo[RELATED]:
            # Positive evidence of some relevant experience (an undated related
            # entry or unowned related evidence counts; zero evidence does not).
            status, rule = PARTIAL, RULE_SHORTFALL_OR_RELATED
            responsible = [] if (acc.ids_by_label[QUALIFYING] or acc.ids_by_label[RELATED]) else uo[RELATED]
        else:
            status, rule = ABSENT, RULE_NO_RELEVANT_EXPERIENCE
        if not ctx.trusted and status == MATCHED:          # structural invariant (Q = 0)
            raise AssertionError("unverified structure produced MATCHED")

    audit = {
        "rule": rule,
        "status": status,
        "cd_reason": cd_reason,
        "structure_status": ctx.structure_status,
        "date_status": ctx.date_status,
        "s0_cache_key": ctx.s0_cache_key,
        "s0_version": ctx.s0_version,
        "duration_as_of": f"{ctx.duration_as_of[0]:04d}-{ctx.duration_as_of[1]:02d}",
        "policy": spec.policy,
        "targets": list(spec.targets),
        "setting": spec.setting,
        "spec_version": spec.spec_version,
        "required_years": spec.required_years,
        "qualifying_years": acc.q,
        "related_years": acc.r,
        "insufficient_years": acc.i,
        "gap_years": None if gap_months is None else round(gap_months / 12.0, 2),
        "insufficient_years_beyond_qualifying": (
            None if i_gap_months is None else round(i_gap_months / 12.0, 2)),
        "undated_qualifying": acc.u_q,
        "undated_insufficient": acc.u_i,
        "unowned_counts": {lab: len(uo[lab]) for lab in (QUALIFYING, INSUFFICIENT, RELATED, NOT_RELEVANT)},
        "uncertainty_entry_ids": list(uncertainty),
        "responsible_evidence_ids": [ev.evidence_id for ev in responsible],
        "owned_entry_ids": [e.entry_id for e in entries],
        "entries": [{
            "entry_id": e.entry_id,
            "label": labels.get(e.entry_id),
            "title": e.title,
            "employer": e.employer,
            "start": _ym(e.start_year, e.start_month),
            "end": "present" if e.is_current else _ym(e.end_year, e.end_month),
            "years": e.years,
            "dated": e.dated,
        } for e in entries],
        "unowned_evidence": [ev.to_audit() for ev in unowned],
        "duration": duration_detail or None,
    }
    return ExperienceDecision(status=status, cd_reason=cd_reason, rule=rule,
                              uncertainty_entry_ids=uncertainty, audit=audit,
                              uncertainty_evidence_ids=tuple(ev.evidence_id for ev in responsible))


def _decide_pure_duration(spec: RequirementSpec, ctx: ExperienceContext):
    """Pure-duration criteria: no relevance labels, total experience only.

    Trusted structure: T = merged duration of dated experience-kind entries.
      T >= N -> MATCHED; T < N with an undated experience entry -> CD /
      detail_missing; else the existing rule (>= 0.6 N PARTIAL, else ABSENT).
    Unverified structure: U = merged duration of ALL valid anchors — an UPPER
      BOUND only (anchors may include education/training). An upper bound can
      prove a candidate cannot reach a level, never that they reached it:
        DU1 unparsed date text or an anchor of unknown length -> CD / structure_unverified
        DU2 U >= 0.6 N                                       -> CD / structure_unverified
        DU3 U <  0.6 N (every anchor of known length)        -> ABSENT
      MATCHED and PARTIAL are both impossible.
    """
    n_months = round(spec.required_years * 12)
    partial_months = PURE_DURATION_PARTIAL_RATIO * n_months
    if ctx.trusted:
        dated = [e for e in ctx.entries if e.dated]
        undated = tuple(e.entry_id for e in ctx.entries if not e.dated)
        total = _months(e.interval for e in dated)
        detail = {"basis": "owned_experience_entries", "total_years": round(total / 12.0, 2),
                  "entry_ids": [e.entry_id for e in dated], "undated_entry_ids": list(undated)}
        if total >= n_months:
            return MATCHED, RULE_DURATION_MET, None, (), detail
        if undated:
            return CANNOT_DETERMINE, RULE_DURATION_UNDATED, CD_DETAIL_MISSING, undated, detail
        if total >= partial_months:
            return PARTIAL, RULE_DURATION_PARTIAL, None, (), detail
        return ABSENT, RULE_DURATION_SHORT, None, (), detail
    upper = _months(iv for _, iv in ctx.anchor_intervals)
    detail = {"basis": "all_anchors_upper_bound", "upper_bound_years": round(upper / 12.0, 2),
              "anchor_ids": [aid for aid, _ in ctx.anchor_intervals],
              "unbounded_anchor_ids": list(ctx.unbounded_anchor_ids),
              "has_unparsed_dates": ctx.has_unparsed_dates}
    if ctx.has_unparsed_dates or ctx.unbounded_anchor_ids:
        return CANNOT_DETERMINE, RULE_DURATION_UB_NOT_ESTABLISHED, CD_STRUCTURE_UNVERIFIED, (), detail
    if upper >= partial_months:
        return CANNOT_DETERMINE, RULE_DURATION_UB_INCONCLUSIVE, CD_STRUCTURE_UNVERIFIED, (), detail
    return ABSENT, RULE_DURATION_UB_SHORT, None, (), detail


def _ym(y: int | None, m: int | None) -> str | None:
    if y is None:
        return None
    return f"{y:04d}-{m:02d}" if m is not None else f"{y:04d}"
