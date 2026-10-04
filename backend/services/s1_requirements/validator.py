"""
S1 deterministic validator for the AI classifier output (prompt s1-4, S1 1.3.0).

The AI may only: type the analysis_json target hints (role | function), judge
their match (exact | equivalent | none), select verbatim JD spans (requirement
spans, targets for hint-less criteria, a criterion-specific setting, a hint's
equivalent wording), state the relevance basis of a hint-less criterion,
choose a duration candidate id and report ambiguity codes. Everything it
returns is verified here; nothing is coerced. The POLICY is never taken from
the model: it is derived here (see "Policy derivation").

Every error is SCOPED (criterion + field or target) so that the single repair
call can be merged field-by-field into the main answer (services.s1_requirements
.repair): valid primitives are never rewritten by a repair.

Rules
  R1  JSON object {"criteria": [...]}, exactly one result per criterion_id.
  R2  ambiguity codes in AMBIGUITY_CODES (no duplicates). A "policy" key from the
      model is ignored (recorded as model_policy for diagnostics only).
  R3  requirement_spans: [{line, text}], text >= 3 chars, verbatim (comparison
      form, whole words; Arabic proclitics allowed, see jd_text) on that JD line;
      the same holds for every span below.
      Empty  <=>  ambiguity contains requirement_not_in_jd.
  R4  hint criteria: targets are exactly the hints, each once, as {"hint": "Tn",
      "type", "match", "jd_span"}; a returned "text" must equal the hint text
      exactly (no rewrite); no JD-span targets may be added (that would broaden
      the OR set). A "jd_span" maps the hint to the verbatim JD text stating it;
      it must lie inside one of the criterion's requirement spans. The hint text
      itself is never replaced by the mapped span.
  R5  hint-less criteria: targets are {"line", "text", "type"} verbatim JD spans
      (>= 3 chars) lying INSIDE one of the criterion's requirement spans; no
      duplicates (comparison form).
  R6  setting: null or {"line", "text"} verbatim JD span inside one of the
      criterion's requirement spans. No free text, no analysis_json source.
  R7  duration: null or a duration candidate id that lies inside one of the
      criterion's requirement spans; only for criteria with years.

  Policy derivation (deterministic, s1-4):
    hint criteria:   all role -> explicit_role; all function -> functional;
                     role + function -> mixed. relevance_basis must be absent
                     or "targets".
    hint-less criteria require relevance_basis:
      targets           >= 1 JD-selected target; policy from the target types
      sector            no targets, setting required          -> sector
      total_experience  no targets, no setting                -> pure_duration
      unspecified       no targets, no setting                -> pure_duration,
                        always with ambiguous_relevance (never a trusted
                        pure-duration requirement; added by the assembler)

  s1-2 target-mapping guards (span/string checks only; semantic equivalence is
  the AI's judgment, audited, never decided here):
  V-M8   a mapped jd_span has at most 12 words and does not overlap the selected
         duration span or the setting span.
  V-M9   mapped jd_spans of different hints do not overlap.
  V-M10  a mapped jd_span does not overlap another hint's verbatim match inside
         the criterion's requirement spans.
  V-sub-a  a mapping is not a proper whole-word sub-phrase of its target
           ("Construction Project Manager" -> "Project Manager" fails).
  V-sub-b  a mapping does not contain its target as a proper whole-word
           sub-phrase ("software implementation" -> "enterprise software
           implementation" fails).
  s1-3 target match (hint targets only; consistency checks, no semantics):
  V-match  "match" is required and one of exact | equivalent | none;
           exact      -> jd_span null AND the hint is word for word inside one of
                         the criterion's requirement spans;
           equivalent -> jd_span required (all mapping guards above apply);
           none       -> jd_span null; if the hint is nevertheless word for word
                         inside a requirement span (embedded in a longer
                         qualified phrase) the criterion must report
                         ambiguous_relevance.
  V-anchor every requirement span contains an anchor (the selected duration, a
         verbatim hint match, a mapped jd_span or a JD-selected target) or is the
         line immediately after an anchored requirement span (wrapped bullet).
         Statement anchor: ONLY when none of the criterion's spans contains any
         of those anchors, ONE span the model marks "experience_requirement":
         true anchors itself (e.g. "Experience in nursing is preferred.").
         Such a criterion can never resolve on its own: no anchor means no
         selected duration (n_not_in_jd when it has years) and no verbatim or
         mapped hint (target_not_in_jd for every hint); hint-less criteria
         always have years. Only explicit recruiter authority can resolve it.
         The marker is never an anchor next to other anchors, so it cannot be
         used to add About-us/context lines to an anchored requirement.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from services.s1_requirements.criteria import CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText, normalize
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_RELEVANCE, AMB_REQUIREMENT_NOT_IN_JD, AMBIGUITY_CODES, BASIS_SECTOR, BASIS_TARGETS,
    BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED, MATCH_EQUIVALENT, MATCH_EXACT, MATCH_NONE, MATCHES,
    POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_PURE_DURATION, POLICY_SECTOR,
    RELEVANCE_BASES, TARGET_FUNCTION, TARGET_ROLE, TARGET_TYPES, Span,
)

MIN_SPAN_CHARS = 3
MAX_MAPPING_WORDS = 12
_TOKEN = re.compile(r"\w+", re.UNICODE)

# error scopes (see services.s1_requirements.repair for how each is merged)
SCOPE_RESPONSE = "response"
SCOPE_CRITERION = "criterion"
SCOPE_SPANS = "requirement_spans"
SCOPE_SETTING = "setting"
SCOPE_DURATION = "duration"
SCOPE_AMBIGUITY = "ambiguity"
SCOPE_BASIS = "relevance_basis"
SCOPE_TARGETS_IF_EMPTY = "targets_if_empty"      # targets may be supplied only where none exist
SCOPE_TARGETS_EXTRA = "targets_extra"            # non-hint targets added to a hint criterion
SCOPE_TARGET_PREFIX = "target:"                  # target:T1 (whole hint target) | target:T1:<field> (one field of it)
                                                 # | target:J0 (hint-less target, by index)

POLICY_FROM_TYPES = "from_types"
POLICY_FROM_BASIS = "relevance_basis"


@dataclass(frozen=True)
class ParsedTarget:
    text: str
    type: str
    hint_id: str | None = None        # "T1".. when it is an analysis_json hint
    span: Span | None = None          # AI-selected JD span (hint-less target, or a hint's explicit mapping)
    match: str | None = None          # hint targets: exact | equivalent | none


@dataclass(frozen=True)
class ParsedCriterion:
    criterion_id: str
    policy: str                        # DERIVED (never the model's)
    requirement_spans: tuple[Span, ...]
    targets: tuple[ParsedTarget, ...]
    setting: Span | None
    duration_id: str | None
    ambiguity: tuple[str, ...]
    note: str = ""
    statement_anchored: bool = False   # anchored only by the model's experience_requirement marker
    relevance_basis: str | None = None
    policy_derivation: str = POLICY_FROM_TYPES
    model_policy: str | None = None    # diagnostics only; never controls the result


@dataclass(frozen=True)
class ScopedError:
    criterion_id: str | None
    scopes: tuple[str, ...]
    message: str


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    results: dict[str, ParsedCriterion] = field(default_factory=dict)
    scoped: list[ScopedError] = field(default_factory=list)


def hint_ids(c: CriterionInput) -> dict[str, str]:
    return {f"T{i}": t for i, t in enumerate(c.target_hints, 1)}


class _Errors:
    def __init__(self):
        self.messages: list[str] = []
        self.scoped: list[ScopedError] = []

    def add(self, cid: str | None, scopes, message: str) -> None:
        self.messages.append(message)
        self.scoped.append(ScopedError(cid, tuple(scopes), message))

    def __len__(self):
        return len(self.messages)


def _span(jd: JDText, obj, where: str, errs: list[str]) -> Span | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("line"), int) or not isinstance(obj.get("text"), str):
        errs.append(f"{where}: must be an object {{\"line\": int, \"text\": str}}")
        return None
    text = obj["text"]
    if len(normalize(text)) < MIN_SPAN_CHARS:
        errs.append(f"{where}: text must be at least {MIN_SPAN_CHARS} characters")
        return None
    sp = jd.span_on_line(obj["line"], text, boundaries=True)
    if sp is None:
        errs.append(f"{where}: text {text!r} is not verbatim (whole words) on JD line {obj['line']}")
    return sp


def _inside(sp: Span, req: tuple[Span, ...]) -> bool:
    return any(sp.within(r) for r in req)


def _overlap(a: Span, b: Span) -> bool:
    return a.line == b.line and a.start < b.end and b.start < a.end


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(normalize(s))


def _proper_subphrase(small: list[str], big: list[str]) -> bool:
    """small is a contiguous, strictly shorter token run of big."""
    n = len(small)
    return 0 < n < len(big) and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _tscope(key: str, *fields: str) -> tuple[str, ...] | str:
    """target:<key> (whole target) or, with fields, (target:<key>:<field>, ...)."""
    if not fields:
        return SCOPE_TARGET_PREFIX + key
    return tuple(f"{SCOPE_TARGET_PREFIX}{key}:{f}" for f in fields)


def _mapping_errors(c: CriterionInput, targets: list[ParsedTarget], req: tuple[Span, ...], jd: JDText,
                    dur_span: Span | None, setting: Span | None, w: str) -> list[tuple[tuple[str, ...], str]]:
    errs: list[tuple[tuple[str, ...], str]] = []
    hints = hint_ids(c)
    verbatim = {hid: [s for s in jd.find(text) if any(s.within(r) for r in req)] for hid, text in hints.items()}
    mapped = [(t.hint_id, t.span) for t in targets if t.hint_id and t.span is not None]
    for hid, sp in mapped:
        tw = f"{w} target {hid} jd_span {sp.text!r}"
        sc = _tscope(hid, "jd_span")                     # copy errors: only the span may change
        sem = _tscope(hid, "jd_span", "match")           # evidence the mapping is not the same: span or match
        mt, ht = tokens(sp.text), tokens(hints[hid])
        if len(mt) > MAX_MAPPING_WORDS:
            errs.append((sc, f"{tw}: a mapping has at most {MAX_MAPPING_WORDS} words; map only the phrase naming "
                             f"the role/function"))
        if dur_span is not None and _overlap(sp, dur_span):
            errs.append((sc, f"{tw}: a mapping must not include the duration {dur_span.text!r}"))
        if setting is not None and _overlap(sp, setting):
            errs.append((sc, f"{tw}: a mapping must not include the setting {setting.text!r}"))
        if _proper_subphrase(mt, ht):
            errs.append((sem, f"{tw}: drops words of the target {hints[hid]!r}; that is not the same role/function: "
                              f"use null"))
        if _proper_subphrase(ht, mt):
            errs.append((sem + (SCOPE_AMBIGUITY,),
                         f"{tw}: adds words to the target {hints[hid]!r}; use null and, if the target appears "
                         f"only inside this longer phrase, report ambiguous_relevance"))
        for other, osp in mapped:
            if other != hid and hid < other and _overlap(sp, osp):
                errs.append((_tscope(hid, "jd_span", "match") + _tscope(other, "jd_span", "match"),
                             f"{w}: hints {hid} and {other} are mapped to overlapping phrases; one phrase may be "
                             f"mapped by at most one hint: use null for both"))
        for other, spans in verbatim.items():
            if other != hid and any(_overlap(sp, v) for v in spans):
                errs.append((sem, f"{tw}: overlaps the verbatim text of hint {other} ({hints[other]!r})"))
    return errs


def implied_policy(types: list[str]) -> str | None:
    kinds = set(types)
    if not kinds:
        return None
    if kinds == {TARGET_ROLE}:
        return POLICY_EXPLICIT_ROLE
    if kinds == {TARGET_FUNCTION}:
        return POLICY_FUNCTIONAL
    return POLICY_MIXED


def _match_errors(c: CriterionInput, targets: list[ParsedTarget], req: tuple[Span, ...], jd: JDText,
                  amb: list[str], w: str, bad_span: set[str] = frozenset()) -> list[tuple[tuple[str, ...], str]]:
    """``bad_span``: hints whose supplied jd_span already failed span validation; that span error alone
    scopes the target (only the span may be repaired), so no match error is added for it."""
    errs: list[tuple[tuple[str, ...], str]] = []
    hints = hint_ids(c)
    for t in targets:
        if not t.hint_id or t.hint_id in bad_span:
            continue
        tw = f"{w} target {t.hint_id}"
        sc = _tscope(t.hint_id, "match", "jd_span")
        verbatim = any(_inside(s, req) for s in jd.find(hints[t.hint_id]))
        if t.match not in MATCHES:
            errs.append((sc, f"{tw}: match must be one of {list(MATCHES)}, got {t.match!r}"))
        elif t.match == MATCH_EXACT:
            if t.span is not None:
                errs.append((sc, f"{tw}: match exact takes jd_span null"))
            if not verbatim:
                errs.append((sc, f"{tw}: match exact but {hints[t.hint_id]!r} is not word for word inside this "
                                 f"criterion's requirement_spans; use equivalent (with jd_span) or none"))
        elif t.match == MATCH_EQUIVALENT:
            if t.span is None:
                errs.append((sc, f"{tw}: match equivalent requires jd_span (the verbatim JD phrase); if you are not "
                                 f"certain the meaning is the same, use none"))
        elif t.match == MATCH_NONE:
            if t.span is not None:
                errs.append((sc, f"{tw}: match none takes jd_span null"))
            if verbatim and AMB_AMBIGUOUS_RELEVANCE not in amb:
                errs.append((_tscope(t.hint_id, "match") + (SCOPE_AMBIGUITY,),
                             f"{tw}: {hints[t.hint_id]!r} is word for word inside the requirement_spans: use match "
                             f"exact, or, if it is only part of a longer qualified phrase, keep none and report "
                             f"ambiguous_relevance"))
    return errs


def _anchor_errors(req: tuple[Span, ...], anchors: list[Span], marked: list[Span],
                   w: str) -> tuple[list[str], bool]:
    anchored_lines = {r.line for r in req if any(a.within(r) for a in anchors)}
    errs: list[str] = []
    statement = False
    if req and not anchored_lines and marked:
        if len(marked) > 1:
            return [f"{w}: at most one requirement span may be marked experience_requirement"], False
        anchored_lines, statement = {marked[0].line}, True
    for r in req:
        if r.line in anchored_lines or (r.line - 1) in anchored_lines:
            continue
        errs.append(f"{w} requirement span {r.text!r} (line {r.line}) contains no anchor (the selected duration, "
                    f"a target or its mapped phrase) and does not continue an anchored line: quote only this "
                    f"criterion's requirement statement, or return [] with requirement_not_in_jd")
    return errs, statement


def validate_response(raw: str, jd: JDText, criteria: list[CriterionInput],
                      durations: dict[str, tuple[int, DurationMatch]]) -> ValidationResult:
    E = _Errors()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        E.add(None, (SCOPE_RESPONSE,), f"response is not valid JSON: {exc}")
        return ValidationResult(False, E.messages, scoped=E.scoped)
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        E.add(None, (SCOPE_RESPONSE,), 'response must be an object with a "criteria" list')
        return ValidationResult(False, E.messages, scoped=E.scoped)

    by_id = {c.criterion_id: c for c in criteria}
    seen: dict[str, dict] = {}
    for i, it in enumerate(items):
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        if cid not in by_id:
            E.add(None, (SCOPE_RESPONSE,), f"criteria[{i}]: unknown criterion_id {cid!r}")
        elif cid in seen:
            E.add(cid, (SCOPE_CRITERION,), f"criterion {cid}: returned more than once")
        else:
            seen[cid] = it
    for cid in by_id:
        if cid not in seen:
            E.add(cid, (SCOPE_CRITERION,), f"criterion {cid}: missing result")

    results: dict[str, ParsedCriterion] = {}
    for cid, it in seen.items():
        c = by_id[cid]
        w = f"criterion {cid}"
        n_err = len(E)

        def add(scopes, msg, _cid=cid):
            E.add(_cid, scopes, msg)

        def span(obj, where, scopes):
            msgs: list[str] = []
            sp = _span(jd, obj, where, msgs)
            for m in msgs:
                add(scopes, m)
            return sp

        model_policy = it.get("policy") if isinstance(it.get("policy"), str) else None
        amb = it.get("ambiguity") or []
        if not isinstance(amb, list) or any(a not in AMBIGUITY_CODES for a in amb) or len(set(amb)) != len(amb):
            add((SCOPE_AMBIGUITY,), f"{w}: ambiguity must be a list of distinct codes from {list(AMBIGUITY_CODES)}")
            amb = []

        # R3 requirement spans
        req: list[Span] = []
        rs = it.get("requirement_spans")
        if not isinstance(rs, list):
            add((SCOPE_SPANS,), f"{w}: requirement_spans must be a list")
            rs = []
        marked: list[Span] = []
        for j, obj in enumerate(rs):
            sp = span(obj, f"{w} requirement_spans[{j}]", (SCOPE_SPANS,))
            flag = obj.get("experience_requirement", False) if isinstance(obj, dict) else False
            if not isinstance(flag, bool):
                add((SCOPE_SPANS,), f"{w} requirement_spans[{j}]: experience_requirement must be true or false")
                flag = False
            if sp is not None:
                req.append(sp)
                if flag:
                    marked.append(sp)
        if not rs and AMB_REQUIREMENT_NOT_IN_JD not in amb:
            add((SCOPE_SPANS, SCOPE_AMBIGUITY), f"{w}: requirement_spans is empty; quote the JD requirement or "
                                                f"report {AMB_REQUIREMENT_NOT_IN_JD}")
        if rs and AMB_REQUIREMENT_NOT_IN_JD in amb:
            add((SCOPE_SPANS, SCOPE_AMBIGUITY), f"{w}: {AMB_REQUIREMENT_NOT_IN_JD} reported but requirement_spans "
                                                f"given")
        req_t = tuple(req)

        # R4 / R5 targets
        hints = hint_ids(c)
        targets: list[ParsedTarget] = []
        tl = it.get("targets")
        if not isinstance(tl, list):
            add((SCOPE_TARGETS_IF_EMPTY,) + tuple(_tscope(h) for h in hints), f"{w}: targets must be a list")
            tl = []
        used_hints: list[str] = []
        bad_span: set[str] = set()
        seen_norm: set[str] = set()
        for j, t in enumerate(tl):
            tw = f"{w} targets[{j}]"
            hid = t.get("hint") if isinstance(t, dict) else None
            tsc = (_tscope(hid),) if hid in hints else (_tscope(f"J{j}"),)
            if not isinstance(t, dict):
                add(tsc, f"{tw}: must be an object")
                continue
            ttype = t.get("type")
            if ttype not in TARGET_TYPES:
                add(_tscope(hid, "type") if hid in hints else tsc,
                    f"{tw}: type must be one of {list(TARGET_TYPES)}, got {ttype!r}")
            if "hint" in t:
                if hid not in hints:                      # treated like an added target: hint targets are kept
                    add((SCOPE_TARGETS_EXTRA,) if hints else (_tscope(f"J{j}"),),
                        f"{tw}: unknown hint {hid!r} (valid: {sorted(hints)})")
                    continue
                if "text" in t and t["text"] != hints[hid]:
                    add(_tscope(hid, "text"), f"{tw}: hint {hid} text must be returned unchanged as {hints[hid]!r}")
                used_hints.append(hid)
                mapped = None
                if t.get("jd_span") is not None:          # explicit mapping to the JD requirement
                    mapped = span(t["jd_span"], f"{tw} jd_span", _tscope(hid, "jd_span"))
                    if mapped is not None and not _inside(mapped, req_t):
                        add(_tscope(hid, "jd_span"), f"{tw} jd_span: {mapped.text!r} is not inside one of this "
                                                     f"criterion's requirement_spans")
                        mapped = None
                    if mapped is None:
                        bad_span.add(hid)
                if ttype in TARGET_TYPES:
                    targets.append(ParsedTarget(hints[hid], ttype, hint_id=hid, span=mapped, match=t.get("match")))
            else:
                if hints:
                    add((SCOPE_TARGETS_EXTRA,),
                        f"{tw}: this criterion has target hints {sorted(hints)}; return exactly those and add no "
                        f"other targets")
                    continue
                sp = span(t, tw, tsc)
                if sp is None:
                    continue
                if not _inside(sp, req_t):
                    add(tsc, f"{tw}: {sp.text!r} is not inside one of this criterion's requirement_spans")
                    continue
                key = normalize(sp.text)
                if key in seen_norm:
                    add(tsc, f"{tw}: duplicate target {sp.text!r}")
                    continue
                seen_norm.add(key)
                if ttype in TARGET_TYPES:
                    targets.append(ParsedTarget(sp.text, ttype, span=sp))
        for hid in sorted(hints):
            k = used_hints.count(hid)
            if k != 1:
                add((_tscope(hid),), f"{w}: hint {hid} ({hints[hid]!r}) must appear exactly once in targets, "
                                     f"found {k}")

        # R6 setting
        setting = None
        so = it.get("setting")
        if so is not None:
            setting = span(so, f"{w} setting", (SCOPE_SETTING,))
            if setting is not None and not _inside(setting, req_t):
                add((SCOPE_SETTING, SCOPE_SPANS), f"{w} setting: {setting.text!r} is not inside one of this "
                                                  f"criterion's requirement_spans")
                setting = None

        # R7 duration
        dur = it.get("duration")
        if dur is not None:
            if dur not in durations:
                add((SCOPE_DURATION,), f"{w}: duration must be null or one of {sorted(durations)}, got {dur!r}")
                dur = None
            elif not c.has_years:
                add((SCOPE_DURATION,), f"{w}: this criterion has no years requirement; duration must be null")
                dur = None
            else:
                line, m = durations[dur]
                if not _inside(Span(line, m.start, m.end, m.text), req_t):
                    add((SCOPE_DURATION, SCOPE_SPANS),
                        f"{w}: duration {dur} ({m.text!r}, line {line}) is not inside one of this criterion's "
                        f"requirement_spans")
                    dur = None

        # policy derivation (never the model's policy)
        types = [t.type for t in targets]
        basis = it.get("relevance_basis")
        policy, derivation = implied_policy(types), POLICY_FROM_TYPES
        if hints:
            if basis not in (None, BASIS_TARGETS):
                add((SCOPE_BASIS,), f"{w}: relevance_basis applies only to criteria without target_hints; omit it "
                                    f"(got {basis!r})")
            basis = None
        elif basis not in RELEVANCE_BASES:
            add((SCOPE_BASIS,), f"{w}: relevance_basis must be one of {list(RELEVANCE_BASES)}, got {basis!r}")
        elif basis == BASIS_TARGETS:
            if not tl:
                add((SCOPE_BASIS, SCOPE_TARGETS_IF_EMPTY),
                    f"{w}: relevance_basis targets needs at least one target selected from the requirement_spans")
        else:
            if tl:
                add((SCOPE_BASIS,), f"{w}: relevance_basis {basis} takes no targets; the requirement names "
                                    f"targets, so use relevance_basis targets")
            if basis == BASIS_SECTOR:
                if so is None:
                    add((SCOPE_BASIS, SCOPE_SETTING), f"{w}: relevance_basis sector needs a setting span")
                policy, derivation = POLICY_SECTOR, POLICY_FROM_BASIS
            else:                                            # total_experience | unspecified
                if so is not None:
                    add((SCOPE_BASIS, SCOPE_SETTING), f"{w}: relevance_basis {basis} takes no setting; a stated "
                                                      f"setting means relevance_basis sector")
                if not c.has_years:
                    add((SCOPE_BASIS,), f"{w}: relevance_basis {basis} needs a criterion with a years requirement")
                policy, derivation = POLICY_PURE_DURATION, POLICY_FROM_BASIS

        # s1-2 mapping guards, s1-3 match rules and requirement-span anchoring
        dur_span = None
        if dur is not None:
            dl, dm = durations[dur]
            dur_span = Span(dl, dm.start, dm.end, dm.text)
        for scopes, msg in _mapping_errors(c, targets, req_t, jd, dur_span, setting, w):
            add(scopes, msg)
        for scopes, msg in _match_errors(c, targets, req_t, jd, list(amb), w, bad_span):
            add(scopes, msg)
        anchors = [s for s in (dur_span,) if s is not None]
        anchors += [t.span for t in targets if t.span is not None]
        anchors += [s for text in hints.values() for s in jd.find(text) if _inside(s, req_t)]
        anchor_errs, statement_anchored = _anchor_errors(req_t, anchors, marked, w)
        for msg in anchor_errs:
            add((SCOPE_SPANS,), msg)

        note = it.get("note") if isinstance(it.get("note"), str) else ""
        if len(E) == n_err and policy is not None:
            results[cid] = ParsedCriterion(cid, policy, req_t, tuple(targets), setting, dur, tuple(amb), note,
                                           statement_anchored, basis, derivation, model_policy)
        elif len(E) == n_err:                                # defensive: no derivable policy
            add((SCOPE_CRITERION,), f"{w}: no policy can be derived (no valid targets and no relevance_basis)")

    ok = not E.messages
    return ValidationResult(ok, E.messages, results if ok else {}, E.scoped)
