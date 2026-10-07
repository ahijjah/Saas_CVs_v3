"""
PASS A — experience TARGET: wire schema, request builder and strict validator (the prompt is prompt_a.py, the
model call pass_a_runner.py).

Wire (per criterion):
  {"criterion_id", "requirement_spans": [{line, text, experience_requirement?}],
   "targets": [{hint, type, match, jd_span, alignment?, jd_extra?}]           # criteria WITH target hints
   | "restrictions": [{line, text, kind: "role" | "function" | "vague"}],    # criteria WITHOUT target hints
   "target_basis": "targets" | "total_experience" | "setting_only" | "unspecified",
   "duration": "Dn" | null, "ambiguity": [...], "note"}

Validation = the frozen s1-5.x target / restriction / span / duration / mapping / alignment / anchor rules
(services.s1_requirements.validator, reused unchanged) PLUS:
  - no "settings" / "setting" / "contexts" / "context_spans" / "target_gap", restriction kinds role / function /
    vague only, and no ambiguous_context_scope: Pass A never reads contexts;
  - "target_basis" is required and must match the typed restrictions:
      hinted criterion                     -> "targets"
      role/function restriction(s)         -> "targets"
      only vague restriction(s)            -> "unspecified"
      no restriction                       -> "total_experience" | "setting_only"
    so a no-target reading is always DECLARED, never inferred from an empty list (F2).
Errors carry repair scopes; v3 messages that talk about contexts are never shown to the model (_repair_safe).
The request carries no qualifying-context data, no recruiter decision and no analysis field other than the
target hints and the years (S1 independence).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from services.s1_requirements.criteria import KIND_ROLE_ONLY, KIND_YEARS_AND_ROLES, KIND_YEARS_ONLY, CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_RELEVANCE, AMB_CONFLICTING_REQUIREMENTS, AMB_MULTIPLE_DURATIONS, AMB_REQUIREMENT_NOT_IN_JD,
    POLICY_SECTOR, canonical, sha256,
)
from services.s1_requirements.validator import (
    SCOPE_AMBIGUITY, SCOPE_CRITERION, SCOPE_RESPONSE, SCOPE_RESTRICTIONS, SCOPE_SETTING_PREFIX, SCOPE_SETTINGS, ParsedCriterion,
    ScopedError, hint_ids, validate_response,
)
from services.s1_two_pass.schema import (
    BASIS_SETTING_ONLY, BASIS_TARGETS, BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED, PASS_A_RESTRICTION_KINDS,
    S1A_INPUT_VERSION, S1A_MODEL, S1A_PROMPT_VERSION, S1V4_VERSION, TARGET_BASES,
)
from services.s1_two_pass.prompt_a import pass_a_prompt_fingerprint

# the neutral criterion kinds of the v3 input (same values; the v3 classifier module, which holds the model call,
# is never imported here)
INPUT_KINDS = {KIND_YEARS_AND_ROLES: "years_with_targets", KIND_YEARS_ONLY: "years_only",
               KIND_ROLE_ONLY: "single_target"}

# s1-5.2 ambiguity codes only: ambiguous_context_scope belongs to the context pass
PASS_A_AMBIGUITY_CODES = (AMB_AMBIGUOUS_RELEVANCE, AMB_MULTIPLE_DURATIONS, AMB_CONFLICTING_REQUIREMENTS,
                          AMB_REQUIREMENT_NOT_IN_JD)

PASS_A_FORBIDDEN_KEYS = ("settings", "setting", "contexts", "context_spans", "target_gap")


@dataclass
class PassARequest:
    payload: dict
    jd: JDText
    criteria: list[CriterionInput]
    durations: dict[str, tuple[int, DurationMatch]]

    @property
    def user_message(self) -> str:
        return "INPUT:\n" + canonical(self.payload)

    @property
    def input_hash(self) -> str:
        return sha256(self.user_message)


def build_pass_a_request(jd: JDText, criteria: list[CriterionInput]) -> PassARequest:
    durs = {did: (ln, m) for did, ln, m in jd.durations()}
    payload = {
        "s1a_input_version": S1A_INPUT_VERSION,
        "jd_lines": jd.numbered(),
        "duration_candidates": [{"id": did, "line": ln, "text": m.text} for did, (ln, m) in durs.items()],
        "criteria": [{"criterion_id": c.criterion_id, "kind": INPUT_KINDS[c.kind], "has_years": c.has_years,
                      "target_hints": [{"id": hid, "text": t} for hid, t in hint_ids(c).items()]}
                     for c in criteria],
    }
    return PassARequest(payload, jd, criteria, durs)


def pass_a_cache_key(req: PassARequest, *, prompt_fingerprint: str | None = None, model: str = S1A_MODEL) -> str:
    fp = pass_a_prompt_fingerprint() if prompt_fingerprint is None else prompt_fingerprint
    return sha256(f"s1a|{S1V4_VERSION}|{req.input_hash}|{S1A_PROMPT_VERSION}:{fp}|{model}")


@dataclass(frozen=True)
class PassACriterion:
    parsed: ParsedCriterion          # s1-5.x parse (settings always ())
    target_basis: str


@dataclass
class PassAValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)            # every error (audit)
    results: dict[str, PassACriterion] = field(default_factory=dict)
    scoped: list[ScopedError] = field(default_factory=list)    # repair-facing, context-free (see _repair_safe)


SCOPE_TARGET_BASIS = "target_basis"
NEUTRAL_FIX = "return only role, function or vague restrictions, quoting only the words that name the role or the work"
_CONTEXT_WORDS = ("context", "setting", "sector")


def _repair_safe(v3_scoped: list[ScopedError], flagged: set[str]) -> list[ScopedError]:
    """The v3 validator speaks about settings/contexts; Pass A never shows those words to the model. A v3 error
    on a criterion that already has a Pass A shape error is dropped (that criterion is repaired as a whole); any
    other v3 error that mentions a context is replaced by a neutral instruction with the same scopes. The error
    itself is never dropped from the validity decision."""
    out: list[ScopedError] = []
    for e in v3_scoped:
        if e.criterion_id in flagged:
            continue
        if any(x == SCOPE_SETTINGS or x.startswith(SCOPE_SETTING_PREFIX) for x in e.scopes) or any(
                w in e.message.lower() for w in _CONTEXT_WORDS):
            out.append(ScopedError(e.criterion_id, (SCOPE_CRITERION,), f"criterion {e.criterion_id}: {NEUTRAL_FIX}"))
            continue
        out.append(e)
    return out


def validate_pass_a(raw: str, jd: JDText, criteria: list[CriterionInput],
                    durations: dict[str, tuple[int, DurationMatch]]) -> PassAValidation:
    own: list[ScopedError] = []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        e = ScopedError(None, (SCOPE_RESPONSE,), f"response is not valid JSON: {exc}")
        return PassAValidation(False, [e.message], scoped=[e])
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        e = ScopedError(None, (SCOPE_RESPONSE,), 'response must be an object with a "criteria" list')
        return PassAValidation(False, [e.message], scoped=[e])
    by_id = {c.criterion_id: c for c in criteria}
    basis: dict[str, str] = {}
    flagged: set[str] = set()                   # criteria with a Pass A shape error: repaired as a whole
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("criterion_id")
        w = f"criterion {cid}"
        for k in PASS_A_FORBIDDEN_KEYS:
            if k in it and it[k] not in (None, []):
                own.append(ScopedError(cid, (SCOPE_CRITERION,), f"{w}: remove the key {k!r}; the answer has no "
                                                                f"such field"))
                flagged.add(cid)
        for j, r in enumerate(it.get("restrictions") or []):
            if isinstance(r, dict) and r.get("kind") not in PASS_A_RESTRICTION_KINDS:
                own.append(ScopedError(cid, (SCOPE_CRITERION,), f"{w} restrictions[{j}]: kind {r.get('kind')!r} "
                                       f"is not allowed; a restriction is a role, a function or vague: re-type it "
                                       f"if it names a role or work, otherwise remove it"))
                flagged.add(cid)
        amb = it.get("ambiguity")
        if isinstance(amb, list) and any(a not in PASS_A_AMBIGUITY_CODES for a in amb if isinstance(a, str)):
            own.append(ScopedError(cid, (SCOPE_AMBIGUITY,), f"{w}: ambiguity codes are only "
                                                            f"{list(PASS_A_AMBIGUITY_CODES)}"))
        b = it.get("target_basis")
        if b not in TARGET_BASES:
            own.append(ScopedError(cid, (SCOPE_TARGET_BASIS,), f"{w}: target_basis is required: one of "
                                                               f"{list(TARGET_BASES)}"))
        elif cid in by_id:
            basis[cid] = b
    v = validate_response(raw, jd, criteria, durations)
    results: dict[str, PassACriterion] = {}
    for cid, pc in v.results.items():
        b, c = basis.get(cid), by_id[cid]
        if b is None:
            continue
        hinted = bool(hint_ids(c))
        rf = [r for r in pc.restrictions if r.kind in ("role", "function")]
        vague = [r for r in pc.restrictions if r.kind == "vague"]
        if hinted or rf:
            want = (BASIS_TARGETS,)
        elif vague:
            want = (BASIS_UNSPECIFIED,)
        else:
            want = (BASIS_TOTAL_EXPERIENCE, BASIS_SETTING_ONLY)
        if b not in want:
            sc = (SCOPE_TARGET_BASIS,) if hinted else (SCOPE_TARGET_BASIS, SCOPE_RESTRICTIONS)
            own.append(ScopedError(cid, sc, f"criterion {cid}: target_basis {b!r} does not match the returned "
                                            f"targets/restrictions (expected one of {list(want)}); a target basis "
                                            f"is never inferred from an empty list"))
            continue
        if b == BASIS_SETTING_ONLY:
            pc = replace(pc, policy=POLICY_SECTOR, relevance_basis="sector")
        results[cid] = PassACriterion(pc, b)
    scoped = own + _repair_safe(list(v.scoped), flagged)
    errors = [e.message for e in own] + list(v.errors)
    ok = not errors and len(results) == len(criteria)
    return PassAValidation(ok, errors, results if ok else {}, [] if ok else scoped)
