"""
S1 two-pass deterministic assembly, fail-closed guard and S2 views (no model call anywhere in this module).

Flow per job (all-or-nothing per pass, like v3):
  Pass A answer --validate_pass_a--> s1-5.x assembly (targets, N, compound, provenance)  -> TargetFrame (frozen)
  Pass B answer --validate_pass_b(frames)--> contexts + scope + target_gap               -> S1ArtifactV4

Fail-closed rules:
  F1 a failed Pass A is never "no target": target_failed artefact, no policy, no targets, no Pass B, no view.
  F2 a no-target reading exists only as an explicit target_basis (validated in pass_a); an empty list never
     implies it.
  F3 (absent policy "corroborated") total_experience / setting_only may resolve only when Pass B succeeded with
     an explicit empty target_gap and a consistent structure (no context for total_experience; >= 1 scope-"all"
     context and nothing else for setting_only); otherwise target_unconfirmed.
  F4 a recruiter-confirmed target basis (FIELD_TARGET_BASIS) or recruiter-governed targets satisfy F3; a
     target_gap under recruiter-governed targets is kept as audit only.
  F5 (absent policy "strict", the DEFAULT) total_experience / setting_only never resolve automatically:
     target_unconfirmed unless F4.
  F6 repairs are never the sole source of a no-target reading (inherited: services.s1_requirements.repair; the
     Pass A / Pass B call orchestration with repair arrives with the prompts).
Structural conflicts (-> needs_confirmation, never auto-resolved, never an S2 view):
  target_gap non-empty                         -> target_unconfirmed
  part_duration scope without a compound       -> context_structure_conflict
  setting_only without a scope-"all" context   -> context_structure_conflict
  total_experience with any context            -> context_structure_conflict (a part_duration context of a
                                                  detected compound is explained by the compound)
  one_alternative / softened scope             -> ambiguous_context_scope (no context applied)
  compound + a scope-"all" context             -> compound_requirement blocks; the context is audit only
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from services.experience_accounting import RequirementSpec
from services.s1_requirements.assemble import assemble_artifact, check_recruiter_fields, failed_artifact
from services.s1_requirements.criteria import CriterionInput, enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_CONTEXT_SCOPE, BIZ_COMPOUND_REQUIREMENT, BIZ_EQUIVALENCE_UNVERIFIED, CONTEXT_RESOLVED,
    POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_PURE_DURATION, POLICY_SECTOR,
    PROV_JD_ASSERTED, PROV_RECRUITER_CONFIRMED, PROV_RECRUITER_EDITED, RECRUITER_PROVENANCES,
    REASON_VALIDATION_FAILED, STATUS_FAILED_TECHNICAL, STATUS_FAILED_VALIDATION, STATUS_NEEDS_CONFIRMATION,
    STATUS_RESOLVED, TARGET_FUNCTION, TARGET_ROLE, ContextResolution, Setting, Span,
)
from services.s1_two_pass.pass_a import PassACriterion, build_pass_a_request, validate_pass_a
from services.s1_two_pass.pass_b import PassBCriterion, TargetFrame, build_pass_b_request, validate_pass_b
from services.s1_two_pass.schema import (
    ABSENT_BASES, ABSENT_CORROBORATED, ABSENT_POLICIES, ABSENT_STRICT, BASIS_SETTING_ONLY, BASIS_TARGETS,
    BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED, BIZ_CONTEXT_STRUCTURE_CONFLICT, BIZ_TARGET_UNCONFIRMED,
    CONTEXT_FAILED_TECHNICAL, CONTEXT_FAILED_VALIDATION, CONTEXT_OK, CONTEXT_SKIPPED, DEFAULT_ABSENT_POLICY,
    FIELD_TARGET_BASIS, S1V4_VERSION, SCOPE_ALL, SCOPE_ONE_ALTERNATIVE, SCOPE_PART_DURATION, SCOPE_SOFTENED,
    TARGET_ABSENT_CLAIMED, TARGET_ABSENT_CORROBORATED, TARGET_FAILED, TARGET_FIXED, TARGET_RECRUITER_SET,
    TARGET_UNSPECIFIED, ContextCandidate, ReasonV4, S1ArtifactV4,
)

# s2 view block codes (v3 codes reused where the meaning is the same)
VIEW_CONTEXT_UNRESOLVED = "context_unresolved"
VIEW_CONTEXT_UNCONFIRMED = "context_unconfirmed"
VIEW_MULTI_CONTEXT_UNSUPPORTED = "multi_context_unsupported"
VIEW_CONTEXT_SCOPE_AMBIGUOUS = "context_scope_ambiguous"
VIEW_CONTEXT_UNAVAILABLE = "context_unavailable"
VIEW_TARGET_UNCONFIRMED = "target_unconfirmed"
VIEW_STRUCTURE_CONFLICT = "context_structure_conflict"
VIEW_FAILED = "failed"
VIEW_COMPOUND = "compound_requirement"
VIEW_EQUIVALENCE = "equivalence_unverified"


class S1V4ViewError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def split_recruiter_fields(recruiter_fields: Mapping[str, str] | None) -> tuple[dict, str | None]:
    """(v3 recruiter fields, recruiter provenance of the target basis or None)."""
    rf = dict(recruiter_fields or {})
    basis = rf.pop(FIELD_TARGET_BASIS, None)
    if basis is not None and basis not in RECRUITER_PROVENANCES:
        raise ValueError(f"{FIELD_TARGET_BASIS}: provenance must be one of {list(RECRUITER_PROVENANCES)}")
    return check_recruiter_fields(rf), basis


@dataclass(frozen=True)
class FrozenTarget:
    """Pass A assembled through the frozen s1-5.x assembly (contexts never involved)."""
    criterion: CriterionInput
    artefact: object               # services.s1_requirements.schema.S1Artifact (v3 shape, settings ())
    frame: TargetFrame


def freeze_targets(c: CriterionInput, pa: PassACriterion, jd: JDText, durations: dict, *, run: dict,
                   recruiter_fields: dict) -> FrozenTarget:
    pc = replace(pa.parsed, settings=())
    art = assemble_artifact(c, pc, jd, durations, run=run, recruiter_fields=recruiter_fields)
    # s1a-1.2: the model's names_role_or_work judgement is AUDIT ONLY here (never semantics; hints and recruiter
    # fields keep governing targets and basis)
    art = replace(art, audit={**art.audit, "names_role_or_work": pa.names_role_or_work})
    dur = None
    if pc.duration_id and pc.duration_id in durations:
        ln, m = durations[pc.duration_id]
        dur = Span(ln, m.start, m.end, m.text)
    return FrozenTarget(c, art, TargetFrame(c.criterion_id, pa.target_basis, art.targets, art.requirement_spans, dur))


def _v4_reasons(reasons) -> list[ReasonV4]:
    return [ReasonV4(r.code, r.field, r.detail) for r in reasons]


def _add(reasons: list[ReasonV4], code: str, field: str, detail: str) -> None:
    if not any(r.code == code and r.field == field for r in reasons):
        reasons.append(ReasonV4(code, field, detail))


def failed_v4(c: CriterionInput, reason: str, *, run: dict, validation: dict | None = None,
              recruiter_fields: dict | None = None) -> S1ArtifactV4:
    """F1: a failed target pass is a failure, never a no-target reading."""
    art = failed_artifact(c, reason, run=run, validation=validation, recruiter_fields=recruiter_fields)
    return S1ArtifactV4(
        criterion_id=c.criterion_id, job_id=c.job_id, source_path=c.source_path, display_text=c.display_text,
        requirement_text=c.display_text, required=c.required, spec_status=art.spec_status,
        target_state=TARGET_FAILED, context_pass=CONTEXT_SKIPPED, reasons=tuple(_v4_reasons(art.reasons)),
        retryable=art.retryable, validation=dict(art.validation), audit=dict(art.audit),
        versions=_versions(run))


def _versions(run: dict) -> dict:
    return {"s1": S1V4_VERSION, "pass_a": dict(run.get("pass_a") or {}), "pass_b": dict(run.get("pass_b") or {})}


def assemble_v4(ft: FrozenTarget, pb: PassBCriterion | None, context_status: str, *, run: dict,
                target_basis_recruiter: str | None = None,
                absent_policy: str = DEFAULT_ABSENT_POLICY) -> S1ArtifactV4:
    if absent_policy not in ABSENT_POLICIES:
        raise ValueError(f"unknown absent-target policy {absent_policy!r}")
    if (pb is None) != (context_status != CONTEXT_OK):
        raise ValueError("a context result exists exactly when the context pass succeeded")
    a, f = ft.artefact, ft.frame
    basis = f.target_basis
    reasons = _v4_reasons(a.reasons)
    compound = any(r.code == BIZ_COMPOUND_REQUIREMENT for r in a.reasons)
    gov_targets = a.field_provenance.get("targets") in RECRUITER_PROVENANCES or (
        isinstance(a.field_provenance.get("targets"), dict) and a.targets
        and all(t.provenance in RECRUITER_PROVENANCES for t in a.targets))
    audit = dict(a.audit)

    # contexts (Pass B)
    candidates: list[ContextCandidate] = []
    cspans: tuple[Span, ...] = ()
    gap: tuple[Span, ...] = ()
    if pb is not None:
        candidates = [ContextCandidate(x.span.text, x.span, x.scope, x.applies_to) for x in pb.contexts]
        cspans, gap = pb.context_spans, pb.target_gap
        audit["context_pass"] = {"excluded": [dict(x) for x in pb.excluded], "note": pb.note,
                                 "target_gap": [s.to_dict() for s in gap]}
    scopes = {x.scope for x in candidates}
    all_scope = [x for x in candidates if x.scope == SCOPE_ALL]
    structure_conflict = False
    if SCOPE_ONE_ALTERNATIVE in scopes or SCOPE_SOFTENED in scopes:
        _add(reasons, AMB_AMBIGUOUS_CONTEXT_SCOPE, "contexts", "a context restricts only one alternative or is "
                                                               "only softened; it is not applied")
    if SCOPE_PART_DURATION in scopes and not compound:
        structure_conflict = True
        _add(reasons, BIZ_CONTEXT_STRUCTURE_CONFLICT, "contexts",
             "a context for part of the duration, but the requirement has a single duration")
    if compound and all_scope:
        audit["compound_context"] = [x.text for x in all_scope]       # compound blocks; never applied
    if pb is not None and basis == BASIS_SETTING_ONLY and not all_scope:
        structure_conflict = True
        _add(reasons, BIZ_CONTEXT_STRUCTURE_CONFLICT, "contexts",
             "the target pass found only a setting, but the context pass found no context for all of it")
    explained = [x for x in candidates if not (compound and x.scope == SCOPE_PART_DURATION)]
    if pb is not None and basis == BASIS_TOTAL_EXPERIENCE and explained:
        structure_conflict = True
        _add(reasons, BIZ_CONTEXT_STRUCTURE_CONFLICT, "contexts",
             "the target pass found general experience, but the context pass found a context")
    if gap:
        if gov_targets:
            audit["target_gap_under_recruiter_targets"] = [s.text for s in gap]
        else:
            _add(reasons, BIZ_TARGET_UNCONFIRMED, "targets",
                 "phrases restricting what experience counts are missing from the targets: "
                 + "; ".join(repr(s.text) for s in gap))

    # target state + F3/F4/F5
    if basis == BASIS_TARGETS:
        state = TARGET_FIXED
    elif basis == BASIS_UNSPECIFIED:
        state = TARGET_UNSPECIFIED
    elif target_basis_recruiter in RECRUITER_PROVENANCES:
        state = TARGET_RECRUITER_SET                                   # F4
    else:
        corroborated = (absent_policy == ABSENT_CORROBORATED and pb is not None and not gap
                        and not structure_conflict and scopes <= {SCOPE_ALL}
                        and (basis != BASIS_SETTING_ONLY or bool(all_scope)))
        if corroborated:
            state = TARGET_ABSENT_CORROBORATED                         # F3
        else:
            state = TARGET_ABSENT_CLAIMED
            _add(reasons, BIZ_TARGET_UNCONFIRMED, "target_basis",
                 f"{basis} is not established automatically ("
                 + ("strict policy" if absent_policy == ABSENT_STRICT else "not corroborated by the context pass")
                 + ")")
    assert basis in (BASIS_TARGETS, BASIS_UNSPECIFIED) + ABSENT_BASES

    settings = () if compound else tuple(Setting(x.text, PROV_JD_ASSERTED, x.jd_span) for x in all_scope)
    req = tuple(sorted(set(a.requirement_spans) | set(cspans), key=lambda s: (s.line, s.start, s.end)))
    requirement_text = a.requirement_text
    if a.field_provenance.get("requirement_text") == PROV_JD_ASSERTED and cspans:
        requirement_text = "\n".join(s.text for s in req)
    fp = {**a.field_provenance, "settings": PROV_JD_ASSERTED if settings else None,
          "context_candidates": PROV_JD_ASSERTED if candidates else None,
          "target_basis": target_basis_recruiter or "s1_interpreted"}
    status = STATUS_NEEDS_CONFIRMATION if reasons else STATUS_RESOLVED
    return S1ArtifactV4(
        criterion_id=a.criterion_id, job_id=a.job_id, source_path=a.source_path, display_text=a.display_text,
        requirement_text=requirement_text, required=a.required, spec_status=status, target_state=state,
        context_pass=context_status, policy=a.policy, target_basis=basis, targets=a.targets,
        required_years=a.required_years, settings=settings, context_candidates=tuple(candidates),
        target_gap=gap, context_spans=cspans, requirement_spans=req, reasons=tuple(reasons),
        field_provenance=fp, validation=dict(a.validation), audit=audit, versions=_versions(run))


@dataclass
class V4JobResult:
    job_id: str
    artifacts: list[S1ArtifactV4]
    pass_a: dict
    pass_b: dict


def run_scripted_job(job_id: str, jd_text: str, analysis_json: dict | None, pass_a_raw: str | None,
                     pass_b_raw: str | None = None, *, recruiter_fields: Mapping[str, str] | None = None,
                     pass_a_failure: str | None = None, pass_b_failure: str | None = None,
                     absent_policy: str = DEFAULT_ABSENT_POLICY, run: dict | None = None) -> V4JobResult:
    """Deterministic two-pass assembly from GIVEN answers (no client, no model, no cache). ``*_failure`` is a
    technical failure reason of that pass (e.g. "ai_unavailable"); a Pass A answer that fails validation makes
    every criterion failed_validation; a Pass B answer that fails validation leaves the targets and marks the
    context pass failed_validation (never "no context")."""
    run = dict(run or {})
    rf, basis_rf = split_recruiter_fields(recruiter_fields)
    criteria = enumerate_experience_criteria(job_id, analysis_json)
    jd = JDText(jd_text)
    req_a = build_pass_a_request(jd, criteria)
    meta_a = {"input_hash": req_a.input_hash, "status": "ok", "errors": []}
    if pass_a_failure is not None:
        meta_a["status"] = pass_a_failure
        return V4JobResult(str(job_id), [failed_v4(c, pass_a_failure, run=run, recruiter_fields=rf)
                                         for c in criteria], meta_a, {"status": CONTEXT_SKIPPED})
    va = validate_pass_a(pass_a_raw, jd, criteria, req_a.durations)
    if not va.ok:
        meta_a.update(status=REASON_VALIDATION_FAILED, errors=va.errors)
        return V4JobResult(str(job_id), [failed_v4(c, REASON_VALIDATION_FAILED, run=run,
                                                   validation={"errors": va.errors, "repair_errors": []},
                                                   recruiter_fields=rf) for c in criteria],
                           meta_a, {"status": CONTEXT_SKIPPED})
    frozen = [freeze_targets(c, va.results[c.criterion_id], jd, req_a.durations, run=run, recruiter_fields=rf)
              for c in criteria]
    req_b = build_pass_b_request(jd, [f.frame for f in frozen])
    meta_b = {"input_hash": req_b.input_hash, "status": CONTEXT_OK, "errors": []}
    results: dict[str, PassBCriterion] = {}
    if pass_b_failure is not None:
        meta_b["status"] = CONTEXT_FAILED_TECHNICAL
    else:
        vb = validate_pass_b(pass_b_raw, jd, [f.frame for f in frozen])
        if vb.ok:
            results = vb.results
        else:
            meta_b.update(status=CONTEXT_FAILED_VALIDATION, errors=vb.errors)
    status = meta_b["status"]
    arts = [assemble_v4(f, results.get(f.criterion.criterion_id) if status == CONTEXT_OK else None, status,
                        run=run, target_basis_recruiter=basis_rf, absent_policy=absent_policy) for f in frozen]
    return V4JobResult(str(job_id), arts, meta_a, meta_b)


# ── input to the (QC-side) deterministic resolver, and its result ─────────────────────────────────────────────

def s1_reading(art: S1ArtifactV4) -> dict:
    """Plain-data S1 reading for the context resolver (S1 never imports the QC service)."""
    ok = art.context_pass == CONTEXT_OK and art.target_state != TARGET_FAILED
    return {"status": "ok" if ok else "unavailable",
            "settings": [s.text for s in art.settings],
            "candidates": [{"text": c.text, "scope": c.scope} for c in art.context_candidates],
            "compound": any(r.code == BIZ_COMPOUND_REQUIREMENT for r in art.reasons)}


def with_resolution(art: S1ArtifactV4, resolution: dict) -> S1ArtifactV4:
    return replace(art, context_resolution=ContextResolution.from_dict(resolution))


# ── S2 views (fail closed, regardless of require_resolved) ────────────────────────────────────────────────────

def _view(art: S1ArtifactV4, policy: str, texts, spans, setting) -> RequirementSpec:
    return RequirementSpec(policy=policy, required_years=art.required_years.value if art.required_years else None,
                           targets=tuple(texts), setting=setting, spec_version=art.spec_version,
                           criterion_id=art.criterion_id, criterion_text=art.requirement_text,
                           source_spans=tuple(spans))


def s2_views_v4(art: S1ArtifactV4, *, require_resolved: bool = True) -> list[RequirementSpec]:
    cid = art.criterion_id
    codes = {r.code for r in art.reasons}
    if art.target_state == TARGET_FAILED:
        raise S1V4ViewError(f"criterion {cid}: target pass failed; no S2 view", VIEW_FAILED)
    for code, view in ((BIZ_COMPOUND_REQUIREMENT, VIEW_COMPOUND), (BIZ_EQUIVALENCE_UNVERIFIED, VIEW_EQUIVALENCE),
                       (BIZ_TARGET_UNCONFIRMED, VIEW_TARGET_UNCONFIRMED),
                       (BIZ_CONTEXT_STRUCTURE_CONFLICT, VIEW_STRUCTURE_CONFLICT)):
        if code in codes:
            raise S1V4ViewError(f"criterion {cid}: {code}; no S2 view", view)
    if art.target_state in (TARGET_ABSENT_CLAIMED, TARGET_UNSPECIFIED):        # regardless of require_resolved
        raise S1V4ViewError(f"criterion {cid}: target not established; no S2 view", VIEW_TARGET_UNCONFIRMED)
    if art.context_pass != CONTEXT_OK:
        raise S1V4ViewError(f"criterion {cid}: context pass {art.context_pass}; no S2 view", VIEW_CONTEXT_UNAVAILABLE)
    cr = art.context_resolution
    if cr is None:
        raise S1V4ViewError(f"criterion {cid}: qualifying context not resolved; no S2 view", VIEW_CONTEXT_UNRESOLVED)
    if cr.status != CONTEXT_RESOLVED or cr.effective is None:
        raise S1V4ViewError(f"criterion {cid}: context {cr.status} ({cr.detail}); no S2 view", VIEW_CONTEXT_UNCONFIRMED)
    contexts = cr.effective.contexts
    if len(contexts) > 1:
        raise S1V4ViewError(f"criterion {cid}: {len(contexts)} contexts; S2 takes one", VIEW_MULTI_CONTEXT_UNSUPPORTED)
    recruiter = cr.effective.provenance in (PROV_RECRUITER_EDITED, PROV_RECRUITER_CONFIRMED)
    if AMB_AMBIGUOUS_CONTEXT_SCOPE in codes and not recruiter:
        raise S1V4ViewError(f"criterion {cid}: context scope ambiguous; no S2 view", VIEW_CONTEXT_SCOPE_AMBIGUOUS)
    allowed = (STATUS_RESOLVED,) if require_resolved else (STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION)
    status = art.spec_status
    if status == STATUS_NEEDS_CONFIRMATION and recruiter and codes == {AMB_AMBIGUOUS_CONTEXT_SCOPE}:
        status = STATUS_RESOLVED            # the recruiter decision answers exactly this one reason (status check only)
    if status not in allowed:
        raise S1V4ViewError(f"criterion {cid}: no S2 view for status {art.spec_status!r}", VIEW_CONTEXT_UNCONFIRMED)
    setting = contexts[0] if contexts else None
    roles = [t for t in art.targets if t.type == TARGET_ROLE]
    funcs = [t for t in art.targets if t.type == TARGET_FUNCTION]

    def tv(policy, ts):
        return _view(art, policy, [t.text for t in ts], [t.jd_span.text if t.jd_span else t.text for t in ts], setting)
    if art.policy == POLICY_EXPLICIT_ROLE:
        return [tv(POLICY_EXPLICIT_ROLE, roles)]
    if art.policy == POLICY_FUNCTIONAL:
        return [tv(POLICY_FUNCTIONAL, funcs)]
    if art.policy == POLICY_MIXED:
        return [tv(POLICY_EXPLICIT_ROLE, roles), tv(POLICY_FUNCTIONAL, funcs)]
    if art.policy == POLICY_SECTOR:
        if setting is None:
            raise S1V4ViewError(f"criterion {cid}: a sector criterion needs one effective context", VIEW_STRUCTURE_CONFLICT)
        return [_view(art, POLICY_SECTOR, [setting], [setting], None)]
    if art.policy == POLICY_PURE_DURATION:
        if setting is not None:
            raise S1V4ViewError(f"criterion {cid}: a context cannot ride on pure duration", VIEW_STRUCTURE_CONFLICT)
        return [_view(art, POLICY_PURE_DURATION, [], [], None)]
    raise S1V4ViewError(f"criterion {cid}: unsupported policy {art.policy!r}", VIEW_FAILED)


__all__ = ["run_scripted_job", "assemble_v4", "freeze_targets", "failed_v4", "s1_reading", "with_resolution",
           "s2_views_v4", "S1V4ViewError", "split_recruiter_fields", "V4JobResult"]
