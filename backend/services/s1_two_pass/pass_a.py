"""
PASS A — experience TARGET: wire schema, request builder and strict validator (the prompt is prompt_a.py, the
model call pass_a_runner.py).

Wire (per criterion):
  {"criterion_id", "requirement_spans": [{line, text, experience_requirement?}],
   "targets": [{hint, type, match, jd_span, alignment?, jd_extra?}]           # criteria WITH target hints
   | "restrictions": [{line, text, kind: "role" | "function" | "vague"}],    # criteria WITHOUT target hints
   "target_basis": "targets" | "total_experience" | "setting_only" | "unspecified",
   "where_evidence": [{line, text}],                                         # s1a-1.3, criteria WITHOUT hints
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
Every prompt version is validated, repaired and merged under its OWN contract (CONTRACTS), so a recorded run
replays exactly under the version that produced it. s1a-1.1 is the contract above. s1a-1.3 adds "where_evidence",
the verbatim words of a where / for whom limit (a place, sector, industry, kind of employer or client):
  - REQUIRED exactly when target_basis is "setting_only", [] (or absent) for every other basis and for hinted
    criteria: setting_only is never an unanchored declaration;
  - structural checks only: a list of at most MAX_SETTINGS verbatim spans (whole words, Arabic proclitics as in
    every span), pairwise distinct and non-overlapping, inside the criterion's requirement_spans, overlapping
    neither the duration nor any restriction (the same words are never both WHAT and WHERE);
  - the basis follows from the typed answer: hinted -> targets; role/function -> targets; else where_evidence ->
    setting_only (a vague word may accompany it); else vague -> unspecified; else total_experience. The check
    runs on the raw answer, so a disagreement is reported even when another field is also invalid;
  - audit evidence only: never interpreted, never a target or a setting, never part of the Pass B input; kept on
    the parsed result (PassACriterion.where_evidence -> TargetFrame.where_evidence) for the later reconciliation.
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
    MAX_SETTINGS, POLICY_SECTOR, Span, canonical, sha256,
)
from services.s1_requirements.validator import (
    SCOPE_AMBIGUITY, SCOPE_CRITERION, SCOPE_RESPONSE, SCOPE_RESTRICTIONS, SCOPE_SETTING_PREFIX, SCOPE_SETTINGS, ParsedCriterion,
    ScopedError, _inside, _overlap, _span, hint_ids, validate_response,
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


def pass_a_cache_key(req: PassARequest, *, prompt_fingerprint: str | None = None, model: str = S1A_MODEL,
                     prompt_version: str | None = None) -> str:
    version = S1A_PROMPT_VERSION if prompt_version is None else prompt_version
    fp = pass_a_prompt_fingerprint(prompt_version) if prompt_fingerprint is None else prompt_fingerprint
    return sha256(f"s1a|{S1V4_VERSION}|{req.input_hash}|{version}:{fp}|{model}")


@dataclass(frozen=True)
class PassACriterion:
    parsed: ParsedCriterion          # s1-5.x parse (settings always ())
    target_basis: str
    where_evidence: tuple[Span, ...] = ()    # s1a-1.3: verbatim where words of a setting_only reading (audit only)


@dataclass
class PassAValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)            # every error (audit)
    results: dict[str, PassACriterion] = field(default_factory=dict)
    scoped: list[ScopedError] = field(default_factory=list)    # repair-facing, context-free (see _repair_safe)


SCOPE_TARGET_BASIS = "target_basis"
# s1a-1.1 forensics (CM09/CM19): a mismatch message must never prescribe a basis computed from the model's own
# (possibly mislabelled) restrictions; it sends the model back to the requirement statement instead.
BASIS_HINTED_MESSAGE = 'a criterion with target_hints always has target_basis "targets"'
BASIS_REREAD_MESSAGE = ('Re-read the requirement statement and decide whether it names a role or work (what the '
                        'experience is of). If it does, return each as a role or function restriction, quoting only '
                        'the role or work words, and target_basis "targets". Only when it names no role and no work '
                        'at all may target_basis be "unspecified", "total_experience" or "setting_only".')
# s1a-1.3 (S1-A-1.3 review): the same disagreement message, neutral across all four bases. It never prescribes one
# reading (the s1a-1.1 message offered only the role / function fix, which turned mislabelled where words into
# functions) and names the evidence each basis needs.
BASIS_NEUTRAL_MESSAGE = ('Re-read the requirement statement and give the one reading it supports, with restrictions '
                         'and where_evidence that agree with it: "targets" when it names a role or work (each as a '
                         'role or function restriction quoting only those words; where_evidence []); "setting_only" '
                         'when it names no role and no work but requires the experience to have been gained in a '
                         'particular place, sector or industry, or for a particular kind of employer or client (no '
                         'role or function restriction; those where words quoted in where_evidence); "unspecified" '
                         'when it names no role, no work and no such limit and only asks for relevant, related or '
                         'similar experience (that word as a vague restriction; where_evidence []); '
                         '"total_experience" when it names none of these (restrictions [], where_evidence []).')
NEUTRAL_FIX = "return only role, function or vague restrictions, quoting only the words that name the role or the work"
_CONTEXT_WORDS = ("context", "setting", "sector")

SCOPE_WHERE_EVIDENCE = "where_evidence"
FIELD_WHERE_EVIDENCE = "where_evidence"


@dataclass(frozen=True)
class PassAContract:
    """The version-specific Pass A wire rules (validation, repair message, merge)."""
    prompt_version: str
    where_evidence: bool            # s1a-1.3: where_evidence required exactly for setting_only (audit evidence)
    basis_message: str              # repair instruction for a target_basis / restriction disagreement


CONTRACTS = {
    "s1a-1.1": PassAContract("s1a-1.1", False, BASIS_REREAD_MESSAGE),
    "s1a-1.3": PassAContract("s1a-1.3", True, BASIS_NEUTRAL_MESSAGE),
}


def pass_a_contract(version: str | None = None) -> PassAContract:
    """The contract of the current prompt (default) or of a runnable earlier version (exact replay)."""
    v = S1A_PROMPT_VERSION if version is None else version
    if v not in CONTRACTS:
        raise ValueError(f"Pass A prompt {v!r} has no runnable contract (audit only)")
    return CONTRACTS[v]


def _expected_basis(c: CriterionInput, it: dict, has_where: bool) -> str:
    """s1a-1.3: the basis the typed answer supports (raw kinds, so a disagreement is never masked by another
    error of the same answer)."""
    if hint_ids(c):
        return BASIS_TARGETS
    rl = it.get("restrictions")
    kinds = {r.get("kind") for r in rl if isinstance(r, dict)} if isinstance(rl, list) else set()
    if kinds & {"role", "function"}:
        return BASIS_TARGETS
    if has_where:
        return BASIS_SETTING_ONLY
    if "vague" in kinds:
        return BASIS_UNSPECIFIED
    return BASIS_TOTAL_EXPERIENCE


def _where_checks(it: dict, c: CriterionInput, b, jd: JDText, contract: PassAContract,
                  own: list[ScopedError]) -> tuple[list[Span], bool]:
    """s1a-1.3 checks that need only the raw answer: shape, verbatim spans, count, pairwise overlap, and the basis /
    restriction / where_evidence agreement. -> (located where spans, basis disagreement reported)."""
    cid, w = c.criterion_id, f"criterion {c.criterion_id}"
    we = it.get(FIELD_WHERE_EVIDENCE)
    we = [] if we is None else we
    if not isinstance(we, list):
        own.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), f'{w}: where_evidence must be a list of {{"line", '
                                                              f'"text"}} ([] unless target_basis is "setting_only")'))
    has_where = bool(we)
    want = _expected_basis(c, it, has_where)
    disagree = b in TARGET_BASES and b != want
    if disagree and hint_ids(c):
        own.append(ScopedError(cid, (SCOPE_TARGET_BASIS,), f"{w}: {BASIS_HINTED_MESSAGE}"))
    elif disagree:
        own.append(ScopedError(cid, (SCOPE_TARGET_BASIS, SCOPE_RESTRICTIONS, SCOPE_WHERE_EVIDENCE),
                               f"{w}: target_basis {b!r}, the restrictions and where_evidence disagree. "
                               f"{contract.basis_message}"))
    elif has_where and want == BASIS_TARGETS:
        own.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), (
            f"{w}: a criterion with target_hints has no where_evidence: return []" if hint_ids(c) else
            f'{w}: the restrictions name a role or work, so target_basis is "targets" and where_evidence is []: a '
            f'where or for whom limit never changes a role or work target')))
    spans: list[Span] = []
    if not isinstance(we, list):
        return spans, disagree
    if len(we) > MAX_SETTINGS:
        own.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), f"{w}: at most {MAX_SETTINGS} where_evidence spans "
                                                              f"(got {len(we)})"))
    for j, x in enumerate(we):
        errs: list[str] = []
        sp = _span(jd, x, f"{w} where_evidence[{j}]", errs)
        own.extend(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), m) for m in errs)
        if sp is None:
            continue
        for o in spans:
            if _overlap(sp, o):
                own.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), f"{w}: where_evidence {o.text!r} and "
                                                                      f"{sp.text!r} overlap; quote each limit once"))
        spans.append(sp)
    return spans, disagree


def _where_span_errors(pc: ParsedCriterion, spans: list[Span], durations: dict, contract: PassAContract
                       ) -> list[ScopedError]:
    """s1a-1.3 checks against the parsed criterion: inside the requirement spans, never the duration, never the
    words of a restriction."""
    cid, out = pc.criterion_id, []
    dur = None
    if pc.duration_id and pc.duration_id in durations:
        dl, dm = durations[pc.duration_id]
        dur = Span(dl, dm.start, dm.end, dm.text)
    for sp in spans:
        w = f"criterion {cid} where_evidence {sp.text!r}"
        if not _inside(sp, pc.requirement_spans):
            out.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), f"{w} is not inside one of this criterion's "
                                                                  f"requirement_spans"))
        if dur is not None and _overlap(sp, dur):
            out.append(ScopedError(cid, (SCOPE_WHERE_EVIDENCE,), f"{w} must not include the duration {dur.text!r}"))
        for r in pc.restrictions:
            if _overlap(sp, r.span):
                out.append(ScopedError(cid, (SCOPE_TARGET_BASIS, SCOPE_RESTRICTIONS, SCOPE_WHERE_EVIDENCE),
                                       f"{w} overlaps the {r.kind} restriction {r.text!r}: the same words are never "
                                       f"both what and where. {contract.basis_message}"))
    return out


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
                    durations: dict[str, tuple[int, DurationMatch]], *,
                    contract: PassAContract | None = None) -> PassAValidation:
    ct = pass_a_contract() if contract is None else contract
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
    where: dict[str, list[Span]] = {}           # s1a-1.3: located where_evidence spans
    disagreed: set[str] = set()                 # s1a-1.3: basis disagreement already reported (raw answer)
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
        if ct.where_evidence and cid in by_id and cid not in where:
            where[cid], bad = _where_checks(it, by_id[cid], b, jd, ct, own)
            if bad:
                disagreed.add(cid)
    v = validate_response(raw, jd, criteria, durations)
    results: dict[str, PassACriterion] = {}
    for cid, pc in v.results.items():
        b, c = basis.get(cid), by_id[cid]
        if b is None:
            continue
        if ct.where_evidence:                   # s1a-1.3: agreement already checked on the raw answer
            span_errs = _where_span_errors(pc, where.get(cid, []), durations, ct)
            own.extend(span_errs)
            if span_errs or cid in disagreed:
                continue
            if b == BASIS_SETTING_ONLY:
                pc = replace(pc, policy=POLICY_SECTOR, relevance_basis="sector")
            results[cid] = PassACriterion(pc, b, tuple(where.get(cid, ())))
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
            if hinted:      # the basis follows from the FIXED hints, never from the model's own restrictions
                own.append(ScopedError(cid, (SCOPE_TARGET_BASIS,), f"criterion {cid}: {BASIS_HINTED_MESSAGE}"))
            else:           # never prescribe a basis derived from restrictions that may themselves be wrong
                own.append(ScopedError(cid, (SCOPE_TARGET_BASIS, SCOPE_RESTRICTIONS),
                                       f"criterion {cid}: target_basis {b!r} and the restrictions disagree. "
                                       f"{BASIS_REREAD_MESSAGE}"))
            continue
        if b == BASIS_SETTING_ONLY:
            pc = replace(pc, policy=POLICY_SECTOR, relevance_basis="sector")
        results[cid] = PassACriterion(pc, b)
    scoped = own + _repair_safe(list(v.scoped), flagged)
    errors = [e.message for e in own] + list(v.errors)
    ok = not errors and len(results) == len(criteria)
    return PassAValidation(ok, errors, results if ok else {}, [] if ok else scoped)
