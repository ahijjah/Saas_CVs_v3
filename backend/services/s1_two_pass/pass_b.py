"""
PASS B — qualifying CONTEXT: wire schema, request builder and strict validator (no prompt, no model call here).

Input: the JD lines and, per criterion, its FROZEN Pass A frame (targets with ids, target_basis, requirement
spans, selected duration). Read-only: Pass B has no field that could add, delete or retype a target, and its
request never carries the qualifying-context analysis, its audit or any recruiter decision on it.

Wire (per criterion):
  {"criterion_id",
   "contexts": [{"line", "text", "scope": "all" | "one_alternative" | "part_duration" | "softened",
                 "applies_to": ["T1", ...]}],
   "context_spans": [{"line", "text"}],          # extra JD sentences that restrict THIS experience
   "target_gap": [{"line", "text"}],             # REQUIRED (may be []): phrases restricting WHAT experience
                                                 # counts that are not among the given targets
   "excluded": [{"text", "why"}],                # optional, audit only, never used for resolution
   "note"}

Rules (all deterministic; nothing is coerced):
  B1 exactly one result per criterion; keys that would change targets (targets, restrictions, target_basis,
     policy, settings, setting) are errors.
  B2 "contexts", "context_spans" and "target_gap" are required lists; every scope / applies_to is required.
  B3 a context_span is verbatim (whole words) and lies 1..CONTEXT_WINDOW lines AFTER a Pass A requirement span;
     it must hold at least one context of this criterion.
  B4 a context is verbatim and inside a Pass A requirement span or a context_span; it never overlaps a target
     span, the selected duration or another context; distinct; at most MAX_SETTINGS.
  B5 applies_to references existing target ids only: scope "all" -> every target id; "one_alternative" -> a
     non-empty PROPER subset (needs >= 2 targets); other scopes -> a non-empty subset; [] when there are no
     targets (total_experience / setting_only).
  B6 a target_gap phrase is verbatim, inside the spans, and overlaps no target, context or duration.
  B7 excluded: shape only ({"text": str, "why": one of EXCLUDED_WHY}).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from services.s1_requirements.jd_text import JDText, normalize
from services.s1_requirements.schema import MAX_SETTINGS, Span, Target, canonical, sha256
from services.s1_two_pass.schema import (
    CONTEXT_SCOPES, CONTEXT_WINDOW, EXCLUDED_WHY, PROMPT_PENDING, S1B_INPUT_VERSION, S1B_PROMPT_VERSION,
    S1V4_VERSION, SCOPE_ALL, SCOPE_ONE_ALTERNATIVE,
)

PASS_B_FORBIDDEN_KEYS = ("targets", "restrictions", "target_basis", "policy", "settings", "setting")


@dataclass(frozen=True)
class TargetFrame:
    """The frozen Pass A reading of one criterion, as Pass B sees it (read-only)."""
    criterion_id: str
    target_basis: str
    targets: tuple[Target, ...]
    requirement_spans: tuple[Span, ...]
    duration_span: Span | None = None

    def target_ids(self) -> tuple[str, ...]:
        return tuple(t.target_id for t in self.targets)

    def to_payload(self) -> dict:
        return {"criterion_id": self.criterion_id, "target_basis": self.target_basis,
                "targets": [{"id": t.target_id, "text": t.text, "type": t.type,
                             "jd_span": t.jd_span.to_dict() if t.jd_span else None} for t in self.targets],
                "requirement_spans": [{"line": s.line, "text": s.text} for s in self.requirement_spans],
                "duration": self.duration_span.text if self.duration_span else None}


@dataclass
class PassBRequest:
    payload: dict
    jd: JDText
    frames: list[TargetFrame]

    @property
    def user_message(self) -> str:
        return "INPUT:\n" + canonical(self.payload)

    @property
    def input_hash(self) -> str:
        return sha256(self.user_message)


def build_pass_b_request(jd: JDText, frames: list[TargetFrame]) -> PassBRequest:
    payload = {"s1b_input_version": S1B_INPUT_VERSION, "jd_lines": jd.numbered(),
               "criteria": [f.to_payload() for f in frames]}
    return PassBRequest(payload, jd, list(frames))


def pass_b_cache_key(req: PassBRequest, *, prompt_fingerprint: str = PROMPT_PENDING, model: str = "") -> str:
    # the input already holds the frozen Pass A frames, so the key changes whenever the targets change
    return sha256(f"s1b|{S1V4_VERSION}|{req.input_hash}|{S1B_PROMPT_VERSION}:{prompt_fingerprint}|{model}")


@dataclass(frozen=True)
class ParsedContext:
    span: Span
    scope: str
    applies_to: tuple[str, ...]


@dataclass(frozen=True)
class PassBCriterion:
    criterion_id: str
    contexts: tuple[ParsedContext, ...]
    context_spans: tuple[Span, ...]
    target_gap: tuple[Span, ...]
    excluded: tuple[dict, ...] = ()
    note: str = ""


@dataclass
class PassBValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    results: dict[str, PassBCriterion] = field(default_factory=dict)


def _overlap(a: Span, b: Span) -> bool:
    return a.line == b.line and a.start < b.end and b.start < a.end


def _span(jd: JDText, obj, where: str, errs: list[str]) -> Span | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("line"), int) or not isinstance(obj.get("text"), str):
        errs.append(f"{where}: must be an object {{\"line\": int, \"text\": str}}")
        return None
    if len(normalize(obj["text"])) < 2:
        errs.append(f"{where}: text is too short")
        return None
    sp = jd.span_on_line(obj["line"], obj["text"], boundaries=True)
    if sp is None:
        errs.append(f"{where}: {obj['text']!r} is not verbatim (whole words) on JD line {obj['line']}")
    return sp


def validate_pass_b(raw: str, jd: JDText, frames: list[TargetFrame]) -> PassBValidation:
    errs: list[str] = []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return PassBValidation(False, [f"response is not valid JSON: {exc}"])
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return PassBValidation(False, ['response must be an object with a "criteria" list'])
    by_id = {f.criterion_id: f for f in frames}
    seen: dict[str, dict] = {}
    for i, it in enumerate(items):
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        if cid not in by_id:
            errs.append(f"criteria[{i}]: unknown criterion_id {cid!r}")
        elif cid in seen:
            errs.append(f"criterion {cid}: returned more than once")
        else:
            seen[cid] = it
    for cid in by_id:
        if cid not in seen:
            errs.append(f"criterion {cid}: missing result")
    results: dict[str, PassBCriterion] = {}
    for cid, it in seen.items():
        f, w, n0 = by_id[cid], f"criterion {cid}", len(errs)
        for k in PASS_B_FORBIDDEN_KEYS:
            if k in it:
                errs.append(f"{w}: the context pass never returns {k!r}: targets are fixed input")
        for k in ("contexts", "context_spans", "target_gap"):
            if not isinstance(it.get(k), list):
                errs.append(f"{w}: {k} is required and must be a list ([] if none)")
        if len(errs) > n0:
            continue
        # B3 context sentences
        cspans: list[Span] = []
        for j, o in enumerate(it["context_spans"]):
            sp = _span(jd, o, f"{w} context_spans[{j}]", errs)
            if sp is None:
                continue
            if any(sp.within(r) for r in f.requirement_spans):
                continue                                          # already a requirement span: nothing to add
            if not any(0 < sp.line - r.line <= CONTEXT_WINDOW for r in f.requirement_spans):
                errs.append(f"{w} context_spans[{j}]: line {sp.line} is not within {CONTEXT_WINDOW} lines after "
                            f"this criterion's requirement statement")
                continue
            cspans.append(sp)
        inside_spans = tuple(f.requirement_spans) + tuple(cspans)
        target_spans = [t.jd_span for t in f.targets if t.jd_span is not None]
        ids = f.target_ids()
        # B4/B5 contexts
        ctxs: list[ParsedContext] = []
        if len(it["contexts"]) > MAX_SETTINGS:
            errs.append(f"{w}: at most {MAX_SETTINGS} contexts")
        for j, o in enumerate(it["contexts"][:MAX_SETTINGS]):
            cw = f"{w} contexts[{j}]"
            sp = _span(jd, o, cw, errs)
            scope = o.get("scope") if isinstance(o, dict) else None
            ap = o.get("applies_to") if isinstance(o, dict) else None
            if scope not in CONTEXT_SCOPES:
                errs.append(f"{cw}: scope is required: one of {list(CONTEXT_SCOPES)}")
                continue
            if not isinstance(ap, list) or not all(isinstance(x, str) for x in ap):
                errs.append(f"{cw}: applies_to is required: a list of target ids ([] when there are no targets)")
                continue
            if sp is None:
                continue
            if not any(sp.within(r) for r in inside_spans):
                errs.append(f"{cw}: {sp.text!r} is not inside this criterion's requirement statement")
                continue
            if any(_overlap(sp, t) for t in target_spans):
                errs.append(f"{cw}: {sp.text!r} overlaps a target: a context never contains or changes a target")
                continue
            if f.duration_span is not None and _overlap(sp, f.duration_span):
                errs.append(f"{cw}: {sp.text!r} must not include the duration")
                continue
            if any(_overlap(sp, c.span) for c in ctxs) or any(normalize(sp.text) == normalize(c.span.text)
                                                             for c in ctxs):
                errs.append(f"{cw}: {sp.text!r} overlaps or repeats another context")
                continue
            unknown = [x for x in ap if x not in ids]
            if unknown:
                errs.append(f"{cw}: applies_to names unknown target ids {unknown} (valid: {list(ids)})")
                continue
            if len(set(ap)) != len(ap):
                errs.append(f"{cw}: applies_to repeats a target id")
                continue
            if not ids and ap:
                errs.append(f"{cw}: there are no targets; applies_to must be []")
                continue
            if ids and scope == SCOPE_ALL and set(ap) != set(ids):
                errs.append(f"{cw}: scope \"all\" applies to every target {list(ids)}")
                continue
            if scope == SCOPE_ONE_ALTERNATIVE and not (0 < len(ap) < len(ids)):
                errs.append(f"{cw}: scope \"one_alternative\" needs a non-empty proper subset of {list(ids)}")
                continue
            if ids and not ap:
                errs.append(f"{cw}: applies_to must name the targets this context restricts")
                continue
            ctxs.append(ParsedContext(sp, scope, tuple(ap)))
        used_cspans = [s for s in cspans if any(c.span.within(s) for c in ctxs)]
        if len(used_cspans) != len(cspans):
            errs.append(f"{w}: every context_span must hold a context of this criterion")
        # B6 target gap
        gap: list[Span] = []
        for j, o in enumerate(it["target_gap"]):
            gw = f"{w} target_gap[{j}]"
            sp = _span(jd, o, gw, errs)
            if sp is None:
                continue
            if not any(sp.within(r) for r in inside_spans):
                errs.append(f"{gw}: {sp.text!r} is not inside this criterion's requirement statement")
            elif any(_overlap(sp, t) for t in target_spans):
                errs.append(f"{gw}: {sp.text!r} is already a target")
            elif any(_overlap(sp, c.span) for c in ctxs):
                errs.append(f"{gw}: {sp.text!r} is a context, not a missing target")
            elif f.duration_span is not None and _overlap(sp, f.duration_span):
                errs.append(f"{gw}: {sp.text!r} must not include the duration")
            else:
                gap.append(sp)
        # B7 excluded (audit only)
        exc = it.get("excluded", [])
        if exc is None:
            exc = []
        if not isinstance(exc, list) or not all(isinstance(x, dict) and isinstance(x.get("text"), str)
                                                and x.get("why") in EXCLUDED_WHY for x in exc):
            errs.append(f"{w}: excluded must be a list of {{\"text\", \"why\": one of {list(EXCLUDED_WHY)}}}")
            exc = []
        if len(errs) == n0:
            key = lambda s: (s.line, s.start, s.end)                      # noqa: E731
            results[cid] = PassBCriterion(
                cid, tuple(sorted(ctxs, key=lambda c: key(c.span))), tuple(sorted(cspans, key=key)),
                tuple(sorted(gap, key=key)), tuple(dict(x) for x in exc),
                it.get("note") if isinstance(it.get("note"), str) else "")
    ok = not errs
    return PassBValidation(ok, errs, results if ok else {})
