"""
S1 deterministic validator for the AI classifier output (prompt s1-2, S1 1.1.0).

The AI may only: type the analysis_json target hints (role | function), pick a
policy, select verbatim JD spans (requirement spans, targets for hint-less
criteria, a criterion-specific setting), choose a duration candidate id and
report ambiguity codes. Everything it returns is verified here; nothing is
coerced. Errors are exact strings fed back in the single repair call.

Rules
  R1  JSON object {"criteria": [...]}, exactly one result per criterion_id.
  R2  policy in S1_POLICIES; ambiguity codes in AMBIGUITY_CODES (no duplicates).
  R3  requirement_spans: [{line, text}], text >= 3 chars, verbatim (comparison
      form, whole words) on that JD line; the same holds for every span below.
      Empty  <=>  ambiguity contains requirement_not_in_jd.
  R4  hint criteria: targets are exactly the hints, each once, as {"hint": "Tn",
      "type"}; a returned "text" must equal the hint text exactly (no rewrite);
      no JD-span targets may be added (that would broaden the OR set). An
      optional "jd_span": {line, text} maps the hint to the verbatim JD text
      stating it; it must lie inside one of the criterion's requirement spans.
      The hint text itself is never replaced by the mapped span.
  R5  hint-less criteria: targets are {"line", "text", "type"} verbatim JD spans
      (>= 3 chars) lying INSIDE one of the criterion's requirement spans; no
      duplicates (comparison form).
  R6  setting: null or {"line", "text"} verbatim JD span inside one of the
      criterion's requirement spans. No free text, no analysis_json source.
  R7  duration: null or a duration candidate id that lies inside one of the
      criterion's requirement spans; only for criteria with years.
  R8  policy/target consistency:
        explicit_role  >= 1 target, all role
        functional     >= 1 target, all function
        mixed          >= 1 role and >= 1 function target
        sector         no targets, no hints, setting required
        pure_duration  no targets, no hints, no setting, criterion has years

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
    AMB_REQUIREMENT_NOT_IN_JD, AMBIGUITY_CODES, POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED,
    POLICY_PURE_DURATION, POLICY_SECTOR, S1_POLICIES, TARGET_FUNCTION, TARGET_ROLE, TARGET_TYPES, Span,
)

MIN_SPAN_CHARS = 3
MAX_MAPPING_WORDS = 12
_TOKEN = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class ParsedTarget:
    text: str
    type: str
    hint_id: str | None = None        # "T1".. when it is an analysis_json hint
    span: Span | None = None          # AI-selected JD span (hint-less target, or a hint's explicit mapping)


@dataclass(frozen=True)
class ParsedCriterion:
    criterion_id: str
    policy: str
    requirement_spans: tuple[Span, ...]
    targets: tuple[ParsedTarget, ...]
    setting: Span | None
    duration_id: str | None
    ambiguity: tuple[str, ...]
    note: str = ""
    statement_anchored: bool = False   # anchored only by the model's experience_requirement marker


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    results: dict[str, ParsedCriterion] = field(default_factory=dict)


def hint_ids(c: CriterionInput) -> dict[str, str]:
    return {f"T{i}": t for i, t in enumerate(c.target_hints, 1)}


def _span(jd: JDText, obj, where: str, errors: list[str]) -> Span | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("line"), int) or not isinstance(obj.get("text"), str):
        errors.append(f"{where}: must be an object {{\"line\": int, \"text\": str}}")
        return None
    text = obj["text"]
    if len(normalize(text)) < MIN_SPAN_CHARS:
        errors.append(f"{where}: text must be at least {MIN_SPAN_CHARS} characters")
        return None
    sp = jd.span_on_line(obj["line"], text, boundaries=True)
    if sp is None:
        errors.append(f"{where}: text {text!r} is not verbatim (whole words) on JD line {obj['line']}")
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


def _mapping_errors(c: CriterionInput, targets: list[ParsedTarget], req: tuple[Span, ...], jd: JDText,
                    dur_span: Span | None, setting: Span | None, w: str) -> list[str]:
    errs: list[str] = []
    hints = hint_ids(c)
    verbatim = {hid: [s for s in jd.find(text) if any(s.within(r) for r in req)] for hid, text in hints.items()}
    mapped = [(t.hint_id, t.span) for t in targets if t.hint_id and t.span is not None]
    for hid, sp in mapped:
        tw = f"{w} target {hid} jd_span {sp.text!r}"
        mt, ht = tokens(sp.text), tokens(hints[hid])
        if len(mt) > MAX_MAPPING_WORDS:
            errs.append(f"{tw}: a mapping has at most {MAX_MAPPING_WORDS} words; map only the phrase naming the "
                        f"role/function")
        if dur_span is not None and _overlap(sp, dur_span):
            errs.append(f"{tw}: a mapping must not include the duration {dur_span.text!r}")
        if setting is not None and _overlap(sp, setting):
            errs.append(f"{tw}: a mapping must not include the setting {setting.text!r}")
        if _proper_subphrase(mt, ht):
            errs.append(f"{tw}: drops words of the target {hints[hid]!r}; that is not the same role/function: "
                        f"use null")
        if _proper_subphrase(ht, mt):
            errs.append(f"{tw}: adds words to the target {hints[hid]!r}; use null and, if the target appears only "
                        f"inside this longer phrase, report ambiguous_relevance")
        for other, osp in mapped:
            if other != hid and hid < other and _overlap(sp, osp):
                errs.append(f"{w}: hints {hid} and {other} are mapped to overlapping phrases; one phrase may be "
                            f"mapped by at most one hint: use null for both")
        for other, spans in verbatim.items():
            if other != hid and any(_overlap(sp, v) for v in spans):
                errs.append(f"{tw}: overlaps the verbatim text of hint {other} ({hints[other]!r})")
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
    errors: list[str] = []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return ValidationResult(False, [f"response is not valid JSON: {exc}"])
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ValidationResult(False, ['response must be an object with a "criteria" list'])

    by_id = {c.criterion_id: c for c in criteria}
    seen: dict[str, dict] = {}
    for i, it in enumerate(items):
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        if cid not in by_id:
            errors.append(f"criteria[{i}]: unknown criterion_id {cid!r}")
        elif cid in seen:
            errors.append(f"criterion {cid}: returned more than once")
        else:
            seen[cid] = it
    for cid in by_id:
        if cid not in seen:
            errors.append(f"criterion {cid}: missing result")

    results: dict[str, ParsedCriterion] = {}
    for cid, it in seen.items():
        c = by_id[cid]
        w = f"criterion {cid}"
        n_err = len(errors)
        policy = it.get("policy")
        if policy not in S1_POLICIES:
            errors.append(f"{w}: policy must be one of {list(S1_POLICIES)}, got {policy!r}")
        amb = it.get("ambiguity") or []
        if not isinstance(amb, list) or any(a not in AMBIGUITY_CODES for a in amb) or len(set(amb)) != len(amb):
            errors.append(f"{w}: ambiguity must be a list of distinct codes from {list(AMBIGUITY_CODES)}")
            amb = []

        # R3 requirement spans
        req: list[Span] = []
        rs = it.get("requirement_spans")
        if not isinstance(rs, list):
            errors.append(f"{w}: requirement_spans must be a list")
            rs = []
        marked: list[Span] = []
        for j, obj in enumerate(rs):
            sp = _span(jd, obj, f"{w} requirement_spans[{j}]", errors)
            flag = obj.get("experience_requirement", False) if isinstance(obj, dict) else False
            if not isinstance(flag, bool):
                errors.append(f"{w} requirement_spans[{j}]: experience_requirement must be true or false")
                flag = False
            if sp is not None:
                req.append(sp)
                if flag:
                    marked.append(sp)
        if not rs and AMB_REQUIREMENT_NOT_IN_JD not in amb:
            errors.append(f"{w}: requirement_spans is empty; quote the JD requirement or report "
                          f"{AMB_REQUIREMENT_NOT_IN_JD}")
        if rs and AMB_REQUIREMENT_NOT_IN_JD in amb:
            errors.append(f"{w}: {AMB_REQUIREMENT_NOT_IN_JD} reported but requirement_spans given")
        req_t = tuple(req)

        # R4 / R5 targets
        hints = hint_ids(c)
        targets: list[ParsedTarget] = []
        tl = it.get("targets")
        if not isinstance(tl, list):
            errors.append(f"{w}: targets must be a list")
            tl = []
        used_hints: list[str] = []
        seen_norm: set[str] = set()
        for j, t in enumerate(tl):
            tw = f"{w} targets[{j}]"
            if not isinstance(t, dict):
                errors.append(f"{tw}: must be an object")
                continue
            ttype = t.get("type")
            if ttype not in TARGET_TYPES:
                errors.append(f"{tw}: type must be one of {list(TARGET_TYPES)}, got {ttype!r}")
            if "hint" in t:
                hid = t.get("hint")
                if hid not in hints:
                    errors.append(f"{tw}: unknown hint {hid!r} (valid: {sorted(hints)})")
                    continue
                if "text" in t and t["text"] != hints[hid]:
                    errors.append(f"{tw}: hint {hid} text must be returned unchanged as {hints[hid]!r}")
                used_hints.append(hid)
                mapped = None
                if t.get("jd_span") is not None:          # optional explicit mapping to the JD requirement
                    mapped = _span(jd, t["jd_span"], f"{tw} jd_span", errors)
                    if mapped is not None and not _inside(mapped, req_t):
                        errors.append(f"{tw} jd_span: {mapped.text!r} is not inside one of this criterion's "
                                      f"requirement_spans")
                        mapped = None
                if ttype in TARGET_TYPES:
                    targets.append(ParsedTarget(hints[hid], ttype, hint_id=hid, span=mapped))
            else:
                if hints:
                    errors.append(f"{tw}: this criterion has target hints {sorted(hints)}; return exactly those "
                                  f"and add no other targets")
                    continue
                sp = _span(jd, t, tw, errors)
                if sp is None:
                    continue
                if not _inside(sp, req_t):
                    errors.append(f"{tw}: {sp.text!r} is not inside one of this criterion's requirement_spans")
                    continue
                key = normalize(sp.text)
                if key in seen_norm:
                    errors.append(f"{tw}: duplicate target {sp.text!r}")
                    continue
                seen_norm.add(key)
                if ttype in TARGET_TYPES:
                    targets.append(ParsedTarget(sp.text, ttype, span=sp))
        if policy not in (POLICY_SECTOR, POLICY_PURE_DURATION):
            for hid in sorted(hints):
                k = used_hints.count(hid)
                if k != 1:
                    errors.append(f"{w}: hint {hid} ({hints[hid]!r}) must appear exactly once in targets, "
                                  f"found {k}")

        # R6 setting
        setting = None
        so = it.get("setting")
        if so is not None:
            setting = _span(jd, so, f"{w} setting", errors)
            if setting is not None and not _inside(setting, req_t):
                errors.append(f"{w} setting: {setting.text!r} is not inside one of this criterion's "
                              f"requirement_spans")
                setting = None

        # R7 duration
        dur = it.get("duration")
        if dur is not None:
            if dur not in durations:
                errors.append(f"{w}: duration must be null or one of {sorted(durations)}, got {dur!r}")
                dur = None
            elif not c.has_years:
                errors.append(f"{w}: this criterion has no years requirement; duration must be null")
                dur = None
            else:
                line, m = durations[dur]
                if not _inside(Span(line, m.start, m.end, m.text), req_t):
                    errors.append(f"{w}: duration {dur} ({m.text!r}, line {line}) is not inside one of this "
                                  f"criterion's requirement_spans")
                    dur = None

        # R8 policy consistency
        roles = sum(1 for t in targets if t.type == TARGET_ROLE)
        funcs = sum(1 for t in targets if t.type == TARGET_FUNCTION)
        if policy == POLICY_EXPLICIT_ROLE and not (roles >= 1 and funcs == 0):
            errors.append(f"{w}: explicit_role needs >= 1 target and every target of type role")
        elif policy == POLICY_FUNCTIONAL and not (funcs >= 1 and roles == 0):
            errors.append(f"{w}: functional needs >= 1 target and every target of type function")
        elif policy == POLICY_MIXED and not (roles >= 1 and funcs >= 1):
            errors.append(f"{w}: mixed needs at least one role and one function target")
        elif policy == POLICY_SECTOR:
            if tl or hints:
                errors.append(f"{w}: sector takes no targets and is not allowed for a criterion with target hints")
            if so is None:
                errors.append(f"{w}: sector needs a setting span")
        elif policy == POLICY_PURE_DURATION:
            if tl or hints or so is not None:
                errors.append(f"{w}: pure_duration takes no targets, no setting and no target hints")
            if not c.has_years:
                errors.append(f"{w}: pure_duration needs a criterion with a years requirement")

        # s1-2 mapping guards and requirement-span anchoring
        dur_span = None
        if dur is not None:
            dl, dm = durations[dur]
            dur_span = Span(dl, dm.start, dm.end, dm.text)
        errors.extend(_mapping_errors(c, targets, req_t, jd, dur_span, setting, w))
        anchors = [s for s in (dur_span,) if s is not None]
        anchors += [t.span for t in targets if t.span is not None]
        anchors += [s for text in hints.values() for s in jd.find(text) if _inside(s, req_t)]
        anchor_errs, statement_anchored = _anchor_errors(req_t, anchors, marked, w)
        errors.extend(anchor_errs)

        note = it.get("note") if isinstance(it.get("note"), str) else ""
        if len(errors) == n_err:
            results[cid] = ParsedCriterion(cid, policy, req_t, tuple(targets), setting, dur, tuple(amb), note,
                                           statement_anchored)

    ok = not errors
    return ValidationResult(ok, errors, results if ok else {})
