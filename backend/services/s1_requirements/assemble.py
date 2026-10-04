"""
S1 deterministic assembly (validated AI labels -> S1Artifact) and S2 views.

Field authority (see schema.py):
  targets         recruiter (explicit) > hint found verbatim INSIDE one of the
                  criterion's requirement spans (jd_verified; a match elsewhere
                  in the JD does not count) > hint explicitly mapped by the
                  validated AI output to a verbatim span inside a requirement
                  span (jd_asserted) > otherwise original_ai + target_not_in_jd;
                  s1-5.2: a mapping whose alignment uses a model-labelled "form"
                  pair is NOT trust-bearing: original_ai + equivalence_unverified,
                  the candidate kept in audit.target_mappings (used false, trust
                  "unverified_form"); never jd_asserted, never an S2 view.
                  s1-5.2.2: an abbreviation EXPANSION (acronym <-> full form) is
                  trust-bearing only when a requirement span holds the JD's own
                  definition of it ("Full Form (ACR)" / "ACR (Full Form)",
                  jd_text.abbreviation_definitions); otherwise the same
                  candidate outcome with trust "unverified_abbreviation". The
                  same acronym written differently (PM / P.M.) is not an
                  expansion and keeps its trust. Definitions used are audited in
                  target_mappings[].jd_definitions.
                  hint-less criteria: AI-selected verbatim JD spans (jd_asserted)
  required_years  recruiter (explicit) > JD duration span whose parsed lower
                  bound equals the hint (jd_verified); different value ->
                  JD value (jd_asserted) + n_mismatch; no JD span -> hint
                  (original_ai) + n_not_in_jd. The AI never supplies N: it only
                  picks a candidate span id; the number comes from the parser.
  setting         verbatim JD span inside a requirement span (jd_asserted) only;
                  never domain_knowledge or any analysis_json field
  policy / type   s1_interpreted; the policy is DERIVED (validator) from the target type labels or,
                  for criteria without analysis targets, from relevance_basis (never the model's policy);
                  audit.policy_derivation = from_types | relevance_basis; relevance_basis "unspecified"
                  always adds ambiguous_relevance (needs_confirmation)
  requirement_text  JD requirement spans (verbatim, in JD order) unless a
                  recruiter field governs the criterion or no span exists, in
                  which case it is display_text.

s2_views(): the unchanged experience_accounting.RequirementSpec. mixed ->
two homogeneous views (explicit_role over role targets, functional over
function targets) sharing criterion_id, spec_version, N, setting and text.
No S2 call and no S2 result combination happens here. A compound requirement
or an unverified equivalence has no view at all, even with
require_resolved=False.
"""
from __future__ import annotations

from typing import Mapping

from services.experience_accounting import RequirementSpec
from services.s1_requirements.criteria import KIND_ROLE_ONLY, KIND_YEARS_AND_ROLES, CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText, abbreviation_definitions, acronym_key
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_RELEVANCE, AMB_CONFLICTING_REQUIREMENTS, AMB_REQUIREMENT_NOT_IN_JD, BASIS_UNSPECIFIED,
    BIZ_COMPOUND_REQUIREMENT, BIZ_EQUIVALENCE_UNVERIFIED, BIZ_N_MISMATCH, BIZ_N_NOT_IN_JD, BIZ_TARGET_NOT_IN_JD,
    FIELD_MIN_YEARS, REL_ABBREVIATION, REL_FORM, TRUST_BEARING, TRUST_UNVERIFIED_ABBREVIATION,
    TRUST_UNVERIFIED_FORM, UNVERIFIED_RELATIONS,
    FIELD_ROLES, POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_PURE_DURATION, POLICY_SECTOR,
    PROV_JD_ASSERTED, PROV_JD_VERIFIED, PROV_ORIGINAL_AI, PROV_S1_INTERPRETED, RECRUITER_FIELDS,
    RECRUITER_PROVENANCES, REASON_KINDS, RETRYABLE_REASONS, STATUS_FAILED_TECHNICAL,
    STATUS_FAILED_VALIDATION, STATUS_NEEDS_CONFIRMATION, STATUS_PENDING, STATUS_RESOLVED, TARGET_FUNCTION,
    TARGET_ROLE, KIND_TECHNICAL, REASON_VALIDATION_FAILED, Reason, RequiredYears, S1Artifact, Setting, Span,
    Target,
)
from services.s1_requirements.validator import ParsedCriterion, tokens


class S1ViewError(ValueError):
    pass


def check_recruiter_fields(recruiter_fields: Mapping[str, str] | None) -> dict[str, str]:
    """Explicit caller-supplied recruiter provenance only; nothing is inferred."""
    rf = dict(recruiter_fields or {})
    for k, v in rf.items():
        if k not in RECRUITER_FIELDS:
            raise ValueError(f"unknown recruiter field {k!r} (allowed: {list(RECRUITER_FIELDS)})")
        if v not in RECRUITER_PROVENANCES:
            raise ValueError(f"recruiter field {k!r}: provenance must be one of {list(RECRUITER_PROVENANCES)}")
    return rf


def _governing(c: CriterionInput, rf: dict[str, str]) -> dict[str, str]:
    """Recruiter provenance for the fields that actually feed this criterion."""
    out = {}
    if c.has_years and FIELD_MIN_YEARS in rf:
        out["required_years"] = rf[FIELD_MIN_YEARS]
    if c.kind in (KIND_YEARS_AND_ROLES, KIND_ROLE_ONLY) and FIELD_ROLES in rf:
        out["targets"] = rf[FIELD_ROLES]
    return out


def _base(c: CriterionInput, run: dict) -> dict:
    return dict(criterion_id=c.criterion_id, job_id=c.job_id, source_path=c.source_path,
                display_text=c.display_text, required=c.required, input_hash=run.get("input_hash", ""),
                prompt_version=run.get("prompt_version", ""), prompt_fingerprint=run.get("prompt_fingerprint", ""),
                model=run.get("model", ""))


def _hint_audit(c: CriterionInput, rf: dict[str, str]) -> dict:
    return {"source_kind": c.kind, "min_years_hint": c.min_years, "target_hints": list(c.target_hints),
            "requirement_type": c.requirement_type, "recruiter_fields": _governing(c, rf)}


def pending_artifact(c: CriterionInput, *, recruiter_fields: Mapping[str, str] | None = None) -> S1Artifact:
    rf = check_recruiter_fields(recruiter_fields)
    return S1Artifact(requirement_text=c.display_text, spec_status=STATUS_PENDING,
                      audit=_hint_audit(c, rf), **_base(c, {}))


def failed_artifact(c: CriterionInput, reason: str, *, run: dict, validation: dict | None = None,
                    recruiter_fields: Mapping[str, str] | None = None) -> S1Artifact:
    rf = check_recruiter_fields(recruiter_fields)
    if REASON_KINDS.get(reason) == KIND_TECHNICAL:
        status = STATUS_FAILED_TECHNICAL
    elif reason == REASON_VALIDATION_FAILED:
        status = STATUS_FAILED_VALIDATION
    else:
        raise ValueError(f"{reason!r} is not a failure reason")
    return S1Artifact(requirement_text=c.display_text, spec_status=status, reasons=(Reason(reason),),
                      retryable=reason in RETRYABLE_REASONS,
                      validation=validation or {"errors": [], "repair_errors": []},
                      audit={**_hint_audit(c, rf), "call": run.get("call", {})}, **_base(c, run))


def assemble_artifact(c: CriterionInput, pc: ParsedCriterion, jd: JDText,
                      durations: dict[str, tuple[int, DurationMatch]], *, run: dict,
                      recruiter_fields: Mapping[str, str] | None = None,
                      validation: dict | None = None) -> S1Artifact:
    rf = check_recruiter_fields(recruiter_fields)
    gov = _governing(c, rf)
    reasons: list[Reason] = []
    req = tuple(sorted(set(pc.requirement_spans), key=lambda s: (s.line, s.start, s.end)))

    # targets
    targets: list[Target] = []
    tprov: dict[str, str] = {}
    j = 0
    # s1-5.2.2: the abbreviations the JD itself defines inside this criterion's requirement spans
    defs = [(r.line, d) for r in req for d in abbreviation_definitions(r.text)]
    trust = {pt.hint_id: _assess(pt, defs) for pt in pc.targets if pt.hint_id and pt.span is not None}
    for pt in pc.targets:
        if pt.hint_id:
            # only a match INSIDE this criterion's requirement spans counts; elsewhere in the JD does not
            inside = next((s for s in jd.find(pt.text) if any(s.within(r) for r in req)), None)
            if "targets" in gov:
                prov, span = gov["targets"], inside or pt.span
            elif inside is not None:
                prov, span = PROV_JD_VERIFIED, inside
            elif pt.span is not None and trust[pt.hint_id]["trust"] != TRUST_BEARING:
                # s1-5.2 / s1-5.2.2: a structurally valid mapping that rests on a model-labelled grammatical form or
                # on an abbreviation expansion the JD does not define is only a candidate (kept in the audit): the
                # target stays original_ai and the criterion unconfirmed
                prov, span = PROV_ORIGINAL_AI, None
                reasons.append(Reason(BIZ_EQUIVALENCE_UNVERIFIED, f"targets.{pt.hint_id}",
                                      _unverified_detail(pt, trust[pt.hint_id])))
            elif pt.span is not None:                  # validated explicit AI mapping inside a requirement span
                prov, span = PROV_JD_ASSERTED, pt.span
            else:
                prov, span = PROV_ORIGINAL_AI, None
                reasons.append(Reason(BIZ_TARGET_NOT_IN_JD, f"targets.{pt.hint_id}", pt.text))
            t = Target(pt.text, pt.type, prov, span, pt.hint_id)
        else:
            j += 1
            t = Target(pt.text, pt.type, PROV_JD_ASSERTED, pt.span, f"J{j}")
        targets.append(t)
        tprov[t.target_id] = t.provenance

    # s1-5 compound detection (deterministic, parser-based): >= 2 distinct duration thresholds inside this
    # criterion's requirement statement (e.g. "7 years overall, including 3 years as X") cannot be one N.
    # Unless the AI reports them as conflicting versions, flag compound_requirement: needs_confirmation, no S2
    # view, N not collapsed; every component is kept in the audit (future: components[] + AND combiner).
    in_req = [{"id": did, "line": ln, "text": m.text, "years": m.years, "bound": m.bound}
              for did, (ln, m) in sorted(durations.items(), key=lambda kv: int(kv[0][1:]))
              if any(Span(ln, m.start, m.end, m.text).within(r) for r in req)]
    compound = len(in_req) >= 2 and AMB_CONFLICTING_REQUIREMENTS not in pc.ambiguity
    if compound:
        reasons.append(Reason(BIZ_COMPOUND_REQUIREMENT, "required_years",
                              " + ".join(d["text"] for d in in_req)))

    # N — parser value only; the AI just names the span
    ry = None
    if c.has_years and not compound:
        hint = float(c.min_years)
        line_m = durations.get(pc.duration_id) if pc.duration_id else None
        span = parsed = None
        if line_m:
            line, m = line_m
            span, parsed = Span(line, m.start, m.end, m.text), m.to_dict()
        if "required_years" in gov:
            ry = RequiredYears(hint, gov["required_years"], span, parsed, hint)
        elif line_m is None:
            ry = RequiredYears(hint, PROV_ORIGINAL_AI, None, None, hint)
            reasons.append(Reason(BIZ_N_NOT_IN_JD, "required_years", f"hint {c.min_years}"))
        elif line_m[1].years == hint:
            ry = RequiredYears(hint, PROV_JD_VERIFIED, span, parsed, hint)
        else:
            ry = RequiredYears(line_m[1].years, PROV_JD_ASSERTED, span, parsed, hint)
            reasons.append(Reason(BIZ_N_MISMATCH, "required_years",
                                  f"JD {line_m[1].text!r} = {line_m[1].years} years; analysis hint {c.min_years}"))

    setting = Setting(pc.setting.text, PROV_JD_ASSERTED, pc.setting) if pc.setting else None

    fully_recruiter = bool(gov) and (("targets" in gov) or not c.target_hints) and \
        (("required_years" in gov) or not c.has_years)
    for code in pc.ambiguity:
        if code == AMB_REQUIREMENT_NOT_IN_JD and fully_recruiter:
            continue                                   # recruiter value outranks the JD
        reasons.append(Reason(code, "ai"))
    if pc.relevance_basis == BASIS_UNSPECIFIED and AMB_AMBIGUOUS_RELEVANCE not in pc.ambiguity:
        # "relevant" without saying what: never a trusted pure-duration requirement
        reasons.append(Reason(AMB_AMBIGUOUS_RELEVANCE, "relevance_basis", BASIS_UNSPECIFIED))

    if gov or not req:
        requirement_text = c.display_text
        rt_prov = (gov.get("targets") or gov.get("required_years")) if gov else PROV_ORIGINAL_AI
    else:
        requirement_text = "\n".join(s.text for s in req)
        rt_prov = PROV_JD_ASSERTED

    field_provenance = {
        "policy": PROV_S1_INTERPRETED,
        "target_types": PROV_S1_INTERPRETED if targets else None,
        "targets": tprov,
        "required_years": ry.provenance if ry else None,
        "setting": setting.provenance if setting else None,
        "requirement_text": rt_prov,
        "display_text": gov.get("targets") or gov.get("required_years") or PROV_ORIGINAL_AI,
    }
    # audit only (no effect on status, provenance or views): every AI mapping and every jd_asserted target
    target_mappings = [
        {"target_id": t.target_id, "target_text": pt.text, "mapped_text": pt.span.text, "line": pt.span.line,
         "start": pt.span.start, "end": pt.span.end, "used": t.provenance == PROV_JD_ASSERTED, "match": pt.match,
         "alignment": [dict(x) for x in pt.alignment], "jd_extra": [dict(x) for x in pt.jd_extra],
         "relations": sorted({x.get("relation") for x in pt.alignment}),
         "trust": trust[pt.hint_id]["trust"],
         "jd_definitions": [dict(x) for x in trust[pt.hint_id]["definitions"]]}
        for pt, t in zip(pc.targets, targets) if pt.hint_id and pt.span is not None]
    if any(m["trust"] != TRUST_BEARING and m["used"] for m in target_mappings):
        # invariant: an unverified (form) mapping never establishes a target
        raise ValueError(f"criterion {c.criterion_id}: an unverified mapping cannot be jd_asserted")
    review_required = [t.target_id for t in targets if t.provenance == PROV_JD_ASSERTED]
    audit = {**_hint_audit(c, rf), "jd_sha256": jd.text_sha256, "duration_candidates_in_requirement": in_req,
             "target_mappings": target_mappings, "review_required": review_required,
             "target_matches": {t.target_id: pt.match for pt, t in zip(pc.targets, targets) if pt.hint_id},
             "ai": {"duration": pc.duration_id, "ambiguity": list(pc.ambiguity),
                    "note": pc.note, "relevance_basis": pc.relevance_basis, "model_policy": pc.model_policy},
             "policy_derivation": pc.policy_derivation,
             "restrictions": [{"line": r.span.line, "text": r.text, "kind": r.kind} for r in pc.restrictions],
             "call": run.get("call", {})}
    if compound:
        audit["compound"] = {"durations": in_req, "selected_duration": pc.duration_id,
                             "targets": [{"target_id": t.target_id, "text": t.text, "type": t.type}
                                         for t in targets],
                             "restrictions": audit["restrictions"]}
    if pc.statement_anchored and not reasons and not gov:
        # invariant: a model-declared statement (or a lone restriction anchor) never resolves a criterion
        raise ValueError(f"criterion {c.criterion_id}: statement-anchored requirement cannot resolve")
    audit["requirement_anchor"] = None if not req else pc.anchor_kind
    status = STATUS_NEEDS_CONFIRMATION if reasons else STATUS_RESOLVED
    # s1-5.1 deterministic withdrawal: audit it, and it may only ever narrow evidence
    audit["span_normalized"] = bool(pc.normalized)          # s1-5.2.2 deterministic duplicate-representation
    if pc.normalized:
        audit["span_normalizations"] = [dict(x) for x in pc.normalized]
    audit["alignment_withdrawn"] = bool(pc.withdrawn)
    if pc.withdrawn:
        audit["withdrawals"] = [dict(x) for x in pc.withdrawn]
        gone = {x["hint"] for x in pc.withdrawn}
        if any(t.target_id in gone and t.provenance == PROV_JD_ASSERTED for t in targets) or (
                status == STATUS_RESOLVED and "targets" not in gov):
            raise ValueError(f"criterion {c.criterion_id}: a withdrawn equivalent claim cannot establish a target")
    return S1Artifact(requirement_text=requirement_text, spec_status=status, policy=pc.policy,
                      targets=tuple(targets), setting=setting, required_years=ry, reasons=tuple(reasons),
                      field_provenance=field_provenance, requirement_spans=req,
                      validation=validation or {"errors": [], "repair_errors": []}, audit=audit,
                      **_base(c, run))


def _view(art: S1Artifact, policy: str, texts: tuple[str, ...], spans: tuple[str, ...],
          setting: str | None) -> RequirementSpec:
    return RequirementSpec(
        policy=policy, required_years=art.required_years.value if art.required_years else None,
        targets=texts, setting=setting, spec_version=art.spec_version,
        criterion_id=art.criterion_id, criterion_text=art.requirement_text, source_spans=spans)


def _tv(art: S1Artifact, policy: str, targets: tuple[Target, ...], setting: str | None) -> RequirementSpec:
    return _view(art, policy, tuple(t.text for t in targets),
                 tuple(t.jd_span.text if t.jd_span else t.text for t in targets), setting)


def _expansion(pair: dict) -> tuple[str, tuple[str, ...]] | None:
    """(acronym key, canonical full-form words) of an abbreviation pair that EXPANDS an acronym; None for the
    same acronym written differently (PM / P.M.: one canonical word, not an expansion)."""
    h, j = pair.get("hint") or "", pair.get("jd") or ""
    if tokens(h) == tokens(j):
        return None
    if len(tokens(h)) == 1 and acronym_key(h):
        return acronym_key(h), tuple(tokens(j))
    if len(tokens(j)) == 1 and acronym_key(j):
        return acronym_key(j), tuple(tokens(h))
    return "", tuple(tokens(h)) + tuple(tokens(j))           # not provable: never matches a definition


def _assess(pt, defs) -> dict:
    """Trust of a validated mapping. FORM pairs are never trust-bearing (s1-5.2). An abbreviation expansion is
    trust-bearing only when a requirement span holds the JD's own definition of exactly that acronym and full
    form (s1-5.2.2); the definition is kept for the audit."""
    form = [x for x in pt.alignment if x.get("relation") in UNVERIFIED_RELATIONS]
    abbr, used = [], []
    for x in pt.alignment:
        if x.get("relation") != REL_ABBREVIATION:
            continue
        e = _expansion(x)
        if e is None:                       # the same acronym (PM / P.M.): trust unchanged; audit any definition
            key = "".join(tokens(x.get("jd") or ""))
            for ln, d in defs:
                rec = {"line": ln, **d.to_dict()}
                if d.acronym == key and rec not in used:
                    used.append(rec)
            continue
        d = next(({"line": ln, **d.to_dict()} for ln, d in defs if (d.acronym, d.full_words) == e), None)
        if d is None:
            abbr.append(x)
        elif d not in used:
            used.append(d)
    label = TRUST_UNVERIFIED_FORM if form else TRUST_UNVERIFIED_ABBREVIATION if abbr else TRUST_BEARING
    return {"trust": label, "form": form, "abbreviation": abbr, "definitions": used}


def _unverified_detail(pt, a: dict) -> str:
    def pairs(xs):
        return "; ".join(f"{x.get('hint')}→{x.get('jd')}" for x in xs)
    parts = []
    if a["form"]:
        parts.append(f"unverified grammatical-form equivalence: {pairs(a['form'])}")
    if a["abbreviation"]:
        parts.append(f"unverified abbreviation, not defined in the JD: {pairs(a['abbreviation'])}")
    return f"{pt.text!r} ~ {pt.span.text!r} ({'; '.join(parts)})"


def s2_views(art: S1Artifact, *, require_resolved: bool = True) -> list[RequirementSpec]:
    """Deterministic RequirementSpec views of one artifact (no S2 execution).

    sector: the setting span is the S2 target (targets=(setting,), setting=None)."""
    allowed = (STATUS_RESOLVED,) if require_resolved else (STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION)
    if any(r.code == BIZ_COMPOUND_REQUIREMENT for r in art.reasons):
        # one RequirementSpec has one N: a compound requirement has no faithful S2 view yet
        raise S1ViewError(f"criterion {art.criterion_id}: compound requirement has no S2 view")
    if any(r.code == BIZ_EQUIVALENCE_UNVERIFIED for r in art.reasons):
        # s1-5.2: an unverified equivalence must never become experience evidence, not even in a preview
        raise S1ViewError(f"criterion {art.criterion_id}: unverified equivalence has no S2 view")
    if art.spec_status not in allowed:
        raise S1ViewError(f"criterion {art.criterion_id}: no S2 view for status {art.spec_status!r}")
    setting = art.setting.text if art.setting else None
    roles = tuple(t for t in art.targets if t.type == TARGET_ROLE)
    funcs = tuple(t for t in art.targets if t.type == TARGET_FUNCTION)
    if art.policy == POLICY_EXPLICIT_ROLE:
        return [_tv(art, POLICY_EXPLICIT_ROLE, roles, setting)]
    if art.policy == POLICY_FUNCTIONAL:
        return [_tv(art, POLICY_FUNCTIONAL, funcs, setting)]
    if art.policy == POLICY_MIXED:
        return [_tv(art, POLICY_EXPLICIT_ROLE, roles, setting), _tv(art, POLICY_FUNCTIONAL, funcs, setting)]
    if art.policy == POLICY_SECTOR:
        return [_view(art, POLICY_SECTOR, (art.setting.text,), (art.setting.jd_span.text,), None)]
    if art.policy == POLICY_PURE_DURATION:
        return [_view(art, POLICY_PURE_DURATION, (), (), None)]
    raise S1ViewError(f"criterion {art.criterion_id}: unsupported policy {art.policy!r}")
