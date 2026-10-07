"""
PASS A — experience TARGET: wire schema, request builder and strict validator (no prompt, no model call here).

Wire (per criterion):
  {"criterion_id", "requirement_spans": [{line, text, experience_requirement?}],
   "targets": [{hint, type, match, jd_span, alignment?, jd_extra?}]           # criteria WITH target hints
   | "restrictions": [{line, text, kind: "role" | "function" | "vague"}],    # criteria WITHOUT target hints
   "target_basis": "targets" | "total_experience" | "setting_only" | "unspecified",
   "duration": "Dn" | null, "ambiguity": [...], "note"}

Validation = the frozen s1-5.x target / restriction / span / duration / mapping / alignment / anchor rules
(services.s1_requirements.validator, reused unchanged) PLUS:
  - no "settings" / "setting" and no restriction kind "context" / "sector": Pass A never reads contexts;
  - "target_basis" is required and must match the typed restrictions:
      hinted criterion                     -> "targets"
      role/function restriction(s)         -> "targets"
      only vague restriction(s)            -> "unspecified"
      no restriction                       -> "total_experience" | "setting_only"
    so a no-target reading is always DECLARED, never inferred from an empty list (F2).
The request carries no qualifying-context data, no recruiter decision and no analysis field other than the
target hints and the years (S1 independence).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from services.s1_requirements.criteria import KIND_ROLE_ONLY, KIND_YEARS_AND_ROLES, KIND_YEARS_ONLY, CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import POLICY_SECTOR, canonical, sha256
from services.s1_requirements.validator import (
    ParsedCriterion, hint_ids, validate_response,
)
from services.s1_two_pass.schema import (
    BASIS_SETTING_ONLY, BASIS_TARGETS, BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED, PASS_A_RESTRICTION_KINDS,
    PROMPT_PENDING, S1A_INPUT_VERSION, S1A_PROMPT_VERSION, S1V4_VERSION, TARGET_BASES,
)

# the neutral criterion kinds of the v3 input (same values; the v3 classifier module, which holds the model call,
# is never imported here)
INPUT_KINDS = {KIND_YEARS_AND_ROLES: "years_with_targets", KIND_YEARS_ONLY: "years_only",
               KIND_ROLE_ONLY: "single_target"}

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


def pass_a_cache_key(req: PassARequest, *, prompt_fingerprint: str = PROMPT_PENDING, model: str = "") -> str:
    return sha256(f"s1a|{S1V4_VERSION}|{req.input_hash}|{S1A_PROMPT_VERSION}:{prompt_fingerprint}|{model}")


@dataclass(frozen=True)
class PassACriterion:
    parsed: ParsedCriterion          # s1-5.x parse (settings always ())
    target_basis: str


@dataclass
class PassAValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    results: dict[str, PassACriterion] = field(default_factory=dict)


def validate_pass_a(raw: str, jd: JDText, criteria: list[CriterionInput],
                    durations: dict[str, tuple[int, DurationMatch]]) -> PassAValidation:
    errors: list[str] = []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return PassAValidation(False, [f"response is not valid JSON: {exc}"])
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return PassAValidation(False, ['response must be an object with a "criteria" list'])
    by_id = {c.criterion_id: c for c in criteria}
    basis: dict[str, str] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("criterion_id")
        w = f"criterion {cid}"
        for k in PASS_A_FORBIDDEN_KEYS:
            if k in it and it[k] not in (None, []):
                errors.append(f"{w}: the target pass never returns {k!r} (contexts are read separately)")
        for r in it.get("restrictions") or []:
            if isinstance(r, dict) and r.get("kind") not in PASS_A_RESTRICTION_KINDS:
                errors.append(f"{w}: restriction kind must be one of {list(PASS_A_RESTRICTION_KINDS)}, got "
                              f"{r.get('kind')!r} (a context is never a target)")
        b = it.get("target_basis")
        if b not in TARGET_BASES:
            errors.append(f"{w}: target_basis is required: one of {list(TARGET_BASES)}")
        elif cid in by_id:
            basis[cid] = b
    v = validate_response(raw, jd, criteria, durations)
    errors += v.errors
    results: dict[str, PassACriterion] = {}
    for cid, pc in v.results.items():
        b, c = basis.get(cid), by_id[cid]
        if b is None:
            continue
        hinted = bool(hint_ids(c))
        rf = [r for r in pc.restrictions if r.kind in ("role", "function")]
        vague = [r for r in pc.restrictions if r.kind == "vague"]
        w = f"criterion {cid}"
        if hinted:
            want = (BASIS_TARGETS,)
        elif rf:
            want = (BASIS_TARGETS,)
        elif vague:
            want = (BASIS_UNSPECIFIED,)
        else:
            want = (BASIS_TOTAL_EXPERIENCE, BASIS_SETTING_ONLY)
        if b not in want:
            errors.append(f"{w}: target_basis {b!r} does not match the returned targets/restrictions "
                          f"(expected one of {list(want)}); a target basis is never inferred from an empty list")
            continue
        if b == BASIS_SETTING_ONLY:
            pc = replace(pc, policy=POLICY_SECTOR, relevance_basis="sector")
        results[cid] = PassACriterion(pc, b)
    ok = not errors and len(results) == len(criteria)
    return PassAValidation(ok, errors, results if ok else {})
