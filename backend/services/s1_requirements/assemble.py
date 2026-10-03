"""
S1 deterministic assembly (validated AI labels -> S1Artifact) and S2 views.

Field authority (see schema.py):
  targets         recruiter (explicit) > hint found verbatim INSIDE one of the
                  criterion's requirement spans (jd_verified; a match elsewhere
                  in the JD does not count) > hint explicitly mapped by the
                  validated AI output to a verbatim span inside a requirement
                  span (jd_asserted) > otherwise original_ai + target_not_in_jd;
                  hint-less criteria: AI-selected verbatim JD spans (jd_asserted)
  required_years  recruiter (explicit) > JD duration span whose parsed lower
                  bound equals the hint (jd_verified); different value ->
                  JD value (jd_asserted) + n_mismatch; no JD span -> hint
                  (original_ai) + n_not_in_jd. The AI never supplies N: it only
                  picks a candidate span id; the number comes from the parser.
  setting         verbatim JD span inside a requirement span (jd_asserted) only;
                  never domain_knowledge or any analysis_json field
  policy / type   s1_interpreted
  requirement_text  JD requirement spans (verbatim, in JD order) unless a
                  recruiter field governs the criterion or no span exists, in
                  which case it is display_text.

s2_views(): the unchanged experience_accounting.RequirementSpec. mixed ->
two homogeneous views (explicit_role over role targets, functional over
function targets) sharing criterion_id, spec_version, N, setting and text.
No S2 call and no S2 result combination happens here.
"""
from __future__ import annotations

from typing import Mapping

from services.experience_accounting import RequirementSpec
from services.s1_requirements.criteria import KIND_ROLE_ONLY, KIND_YEARS_AND_ROLES, CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import (
    AMB_REQUIREMENT_NOT_IN_JD, BIZ_N_MISMATCH, BIZ_N_NOT_IN_JD, BIZ_TARGET_NOT_IN_JD, FIELD_MIN_YEARS,
    FIELD_ROLES, POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_PURE_DURATION, POLICY_SECTOR,
    PROV_JD_ASSERTED, PROV_JD_VERIFIED, PROV_ORIGINAL_AI, PROV_S1_INTERPRETED, RECRUITER_FIELDS,
    RECRUITER_PROVENANCES, REASON_KINDS, RETRYABLE_REASONS, STATUS_FAILED_TECHNICAL,
    STATUS_FAILED_VALIDATION, STATUS_NEEDS_CONFIRMATION, STATUS_PENDING, STATUS_RESOLVED, TARGET_FUNCTION,
    TARGET_ROLE, KIND_TECHNICAL, REASON_VALIDATION_FAILED, Reason, RequiredYears, S1Artifact, Setting, Span,
    Target,
)
from services.s1_requirements.validator import ParsedCriterion


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
    for pt in pc.targets:
        if pt.hint_id:
            # only a match INSIDE this criterion's requirement spans counts; elsewhere in the JD does not
            inside = next((s for s in jd.find(pt.text) if any(s.within(r) for r in req)), None)
            if "targets" in gov:
                prov, span = gov["targets"], inside or pt.span
            elif inside is not None:
                prov, span = PROV_JD_VERIFIED, inside
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

    # N — parser value only; the AI just names the span
    ry = None
    if c.has_years:
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
    in_req = [{"id": did, "line": ln, "text": m.text, "years": m.years, "bound": m.bound}
              for did, (ln, m) in sorted(durations.items(), key=lambda kv: int(kv[0][1:]))
              if any(Span(ln, m.start, m.end, m.text).within(r) for r in req)]
    audit = {**_hint_audit(c, rf), "jd_sha256": jd.text_sha256, "duration_candidates_in_requirement": in_req,
             "ai": {"policy": pc.policy, "duration": pc.duration_id, "ambiguity": list(pc.ambiguity),
                    "note": pc.note},
             "call": run.get("call", {})}
    status = STATUS_NEEDS_CONFIRMATION if reasons else STATUS_RESOLVED
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


def s2_views(art: S1Artifact, *, require_resolved: bool = True) -> list[RequirementSpec]:
    """Deterministic RequirementSpec views of one artifact (no S2 execution).

    sector: the setting span is the S2 target (targets=(setting,), setting=None)."""
    allowed = (STATUS_RESOLVED,) if require_resolved else (STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION)
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
