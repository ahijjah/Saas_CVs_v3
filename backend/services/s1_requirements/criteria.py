"""
S1 criterion enumeration: a deterministic MIRROR of the experience branch of
services.llm_criteria_mapper._flatten_criteria (D-01). It is not imported from
there so that D-01 stays untouched; parity is enforced by tests.

  years > 0 and roles  -> ONE criterion  "Minimum {N} years of experience in a relevant role ({roles})"
                          source_path analysis_json.experience[years_and_roles]
  years > 0, no roles  -> ONE criterion  "Minimum {N} years of relevant experience"
                          source_path analysis_json.experience[years_only]
  roles, no years      -> one criterion per role (required=False)
                          source_path analysis_json.experience.relevant_roles[i]

required: requirement_type != "preferred" when given, else True (role-only: False).
other_requirements / domain_knowledge are NOT experience specs in Phase 1; they
are only reported by out_of_scope_items().
"""
from __future__ import annotations

from dataclasses import dataclass

from services.s1_requirements.schema import sha256

KIND_YEARS_AND_ROLES = "years_and_roles"
KIND_YEARS_ONLY = "years_only"
KIND_ROLE_ONLY = "role_only"


@dataclass(frozen=True)
class CriterionInput:
    criterion_id: str
    job_id: str
    display_text: str
    source_path: str
    kind: str
    required: bool
    min_years: float | None          # analysis_json hint (None when the criterion has no years)
    target_hints: tuple[str, ...]    # analysis_json relevant_roles feeding THIS criterion
    requirement_type: str | None

    @property
    def has_years(self) -> bool:
        return self.min_years is not None


def criterion_id(job_id: str, source_path: str, display_text: str) -> str:
    return sha256(f"{job_id}|experience|{source_path}|{display_text}")[:16]


def enumerate_experience_criteria(job_id: str, analysis_json: dict | None) -> list[CriterionInput]:
    a = analysis_json or {}
    exp = a.get("experience") or {}
    min_years = exp.get("minimum_years", 0)
    raw_roles = list(exp.get("relevant_roles") or [])
    indexed = [(i, str(r)) for i, r in enumerate(raw_roles) if r]
    roles = [r for _, r in indexed]
    has_years = min_years and min_years > 0          # same truthiness/comparison as D-01

    def _mk(text, path, kind, required, years, hints, rtype):
        return CriterionInput(criterion_id(str(job_id), path, text), str(job_id), text, path, kind,
                              required, years, tuple(hints), rtype)

    out: list[CriterionInput] = []
    if has_years:
        rtype = exp.get("requirement_type")
        required = rtype != "preferred" if rtype else True
        if roles:
            roles_str = roles[0] if len(roles) == 1 else f"{', '.join(roles[:-1])} or {roles[-1]}"
            text = f"Minimum {min_years} years of experience in a relevant role ({roles_str})"
            out.append(_mk(text, "analysis_json.experience[years_and_roles]", KIND_YEARS_AND_ROLES,
                           required, min_years, roles, rtype))
        else:
            out.append(_mk(f"Minimum {min_years} years of relevant experience",
                           "analysis_json.experience[years_only]", KIND_YEARS_ONLY, required,
                           min_years, (), rtype))
    elif roles:
        for i, role in indexed:
            out.append(_mk(role, f"analysis_json.experience.relevant_roles[{i}]", KIND_ROLE_ONLY,
                           False, None, (role,), None))
    return out


def out_of_scope_items(analysis_json: dict | None) -> list[dict]:
    """other_requirements are reported, never turned into S1 experience specs."""
    a = analysis_json or {}
    return [{"source_path": f"analysis_json.other_requirements[{i}]", "text": str(t),
             "reason": "phase1_out_of_scope"}
            for i, t in enumerate(a.get("other_requirements") or []) if t]
