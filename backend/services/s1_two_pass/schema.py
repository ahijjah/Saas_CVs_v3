"""
S1 two-pass architecture — artefact schema s1_requirement_spec_v4 (SHADOW ONLY; nothing in production imports it).

  PASS A (target)   WHAT experience a criterion measures: targets, an explicit target_basis, requirement spans,
                    duration. Never contexts.
  PASS B (context)  WHERE the already-fixed targets must have been gained: contexts, each with a REQUIRED scope
                    and applies_to, extra context sentences, and a REQUIRED target_gap. Never changes targets.

The v3 package (services.s1_requirements, s1-6.x) stays untouched for audit and replay; its value types (Span,
Target, Setting, RequiredYears, ContextResolution) are reused, its artefact is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.s1_requirements.schema import (
    AMBIGUITY_CODES, BUSINESS_CODES, CONTRACT_CODES, KIND_AMBIGUITY, KIND_BUSINESS, KIND_CONTRACT, KIND_TECHNICAL,
    MAX_SETTINGS, PROVENANCES, S1_POLICIES, STATUS_FAILED_TECHNICAL, STATUS_FAILED_VALIDATION,
    STATUS_NEEDS_CONFIRMATION, STATUS_RESOLVED, STATUSES, TECHNICAL_CODES, ContextResolution, RequiredYears,
    Setting, Span, Target, canonical, check_settings, sha256,
)

S1V4_SCHEMA = "s1_requirement_spec_v4"
S1V4_VERSION = "2.0.0"
S1A_INPUT_VERSION = "s1a-in-1"
S1B_INPUT_VERSION = "s1b-in-1"
S1A_PROMPT_VERSION = "s1a-1.3"          # Pass A prompt: services/s1_two_pass/prompts/s1a-1.3.txt (pinned SHA;
                                         # s1a-1.1 kept runnable for exact replay, s1a-1.0 / s1a-1.2 audit only)
S1B_PROMPT_VERSION = "s1b-1.0"          # prompt NOT implemented yet (Step 3)
PROMPT_PENDING = "pending"               # fingerprint placeholder until the Pass B prompt exists

# Pass A model call (same pinned settings as the v3 S1 call; one main call + at most one repair call)
S1A_PROMPT_CODE = "recruitment.experience_target_pass"
S1A_MODEL = "gpt-4o-mini"
S1A_TEMPERATURE = 0.0
S1A_MAX_TOKENS = 4000

# ── Pass A: target basis (required, explicit; never inferred from an empty list) ────────────────────────────
BASIS_TARGETS = "targets"                       # one or more role/function targets
BASIS_TOTAL_EXPERIENCE = "total_experience"     # general experience, no role/function
BASIS_SETTING_ONLY = "setting_only"             # no role/function, only WHERE (s1a-1.3: verbatim where_evidence,
                                                # audit only, never a setting)
BASIS_UNSPECIFIED = "unspecified"               # "relevant" without saying what
TARGET_BASES = (BASIS_TARGETS, BASIS_TOTAL_EXPERIENCE, BASIS_SETTING_ONLY, BASIS_UNSPECIFIED)
ABSENT_BASES = (BASIS_TOTAL_EXPERIENCE, BASIS_SETTING_ONLY)
PASS_A_RESTRICTION_KINDS = ("role", "function", "vague")

# target state of an assembled criterion
TARGET_FIXED = "target_fixed"
TARGET_UNSPECIFIED = "target_unspecified"
TARGET_ABSENT_CLAIMED = "target_absent_claimed"           # declared no-target reading, not (yet) established
TARGET_ABSENT_CORROBORATED = "target_absent_corroborated"  # F3: Pass B independently confirmed (non-strict mode)
TARGET_RECRUITER_SET = "recruiter_set"                     # F4: a recruiter confirmed the target / target basis
TARGET_FAILED = "target_failed"
TARGET_STATES = (TARGET_FIXED, TARGET_UNSPECIFIED, TARGET_ABSENT_CLAIMED, TARGET_ABSENT_CORROBORATED,
                 TARGET_RECRUITER_SET, TARGET_FAILED)

# ── Pass B: context scope (required per context) ────────────────────────────────────────────────────────────
SCOPE_ALL = "all"
SCOPE_ONE_ALTERNATIVE = "one_alternative"
SCOPE_PART_DURATION = "part_duration"
SCOPE_SOFTENED = "softened"
CONTEXT_SCOPES = (SCOPE_ALL, SCOPE_ONE_ALTERNATIVE, SCOPE_PART_DURATION, SCOPE_SOFTENED)
EXCLUDED_WHY = ("generic_environment", "employer", "vacancy_location", "duty", "tool", "other")
CONTEXT_WINDOW = 3                              # extra context sentence: <= 3 lines AFTER a Pass A requirement span

# context pass status
CONTEXT_OK = "ok"
CONTEXT_FAILED_TECHNICAL = "failed_technical"
CONTEXT_FAILED_VALIDATION = "failed_validation"
CONTEXT_SKIPPED = "skipped"                     # Pass A failed: no Pass B for this criterion
CONTEXT_PASS_STATUSES = (CONTEXT_OK, CONTEXT_FAILED_TECHNICAL, CONTEXT_FAILED_VALIDATION, CONTEXT_SKIPPED)

# absent-target policy (F5 strict is the initial setting; F3 corroboration only after evaluation)
ABSENT_STRICT = "strict"
ABSENT_CORROBORATED = "corroborated"
ABSENT_POLICIES = (ABSENT_STRICT, ABSENT_CORROBORATED)
DEFAULT_ABSENT_POLICY = ABSENT_STRICT

# recruiter field that may confirm a target basis (F4); v3 recruiter fields remain valid
FIELD_TARGET_BASIS = "experience.target_basis"

# ── reasons (v3 codes + v4 business codes) ──────────────────────────────────────────────────────────────────
BIZ_TARGET_UNCONFIRMED = "target_unconfirmed"                 # F3/F5/gap: the target is not established
BIZ_CONTEXT_STRUCTURE_CONFLICT = "context_structure_conflict"  # Pass A and Pass B disagree structurally
V4_BUSINESS_CODES = BUSINESS_CODES + (BIZ_TARGET_UNCONFIRMED, BIZ_CONTEXT_STRUCTURE_CONFLICT)
V4_REASON_KINDS = {**{c: KIND_AMBIGUITY for c in AMBIGUITY_CODES},
                   **{c: KIND_BUSINESS for c in V4_BUSINESS_CODES},
                   **{c: KIND_CONTRACT for c in CONTRACT_CODES},
                   **{c: KIND_TECHNICAL for c in TECHNICAL_CODES}}


@dataclass(frozen=True)
class ReasonV4:
    code: str
    field: str | None = None
    detail: str | None = None

    def __post_init__(self):
        if self.code not in V4_REASON_KINDS:
            raise ValueError(f"unknown S1 v4 reason code {self.code!r}")

    @property
    def kind(self) -> str:
        return V4_REASON_KINDS[self.code]

    def to_dict(self) -> dict:
        return {"code": self.code, "kind": self.kind, "field": self.field, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict) -> "ReasonV4":
        return cls(d["code"], d.get("field"), d.get("detail"))


@dataclass(frozen=True)
class ContextCandidate:
    """One Pass B context: a verbatim JD span with its required scope and the target ids it restricts."""
    text: str
    jd_span: Span
    scope: str
    applies_to: tuple[str, ...]
    provenance: str = "jd_asserted"

    def __post_init__(self):
        if self.scope not in CONTEXT_SCOPES:
            raise ValueError(f"unknown context scope {self.scope!r}")
        if self.provenance not in PROVENANCES:
            raise ValueError(f"unknown provenance {self.provenance!r}")

    def to_dict(self) -> dict:
        return {"text": self.text, "jd_span": self.jd_span.to_dict(), "scope": self.scope,
                "applies_to": list(self.applies_to), "provenance": self.provenance}

    @classmethod
    def from_dict(cls, d: dict) -> "ContextCandidate":
        return cls(d["text"], Span.from_dict(d["jd_span"]), d["scope"], tuple(d.get("applies_to") or ()),
                   d.get("provenance", "jd_asserted"))


@dataclass(frozen=True)
class S1ArtifactV4:
    criterion_id: str
    job_id: str
    source_path: str
    display_text: str
    requirement_text: str
    required: bool
    spec_status: str
    target_state: str
    context_pass: str
    policy: str | None = None
    target_basis: str | None = None
    targets: tuple[Target, ...] = ()
    required_years: RequiredYears | None = None
    settings: tuple[Setting, ...] = ()                       # scope-"all" contexts only (S1's applied reading)
    context_candidates: tuple[ContextCandidate, ...] = ()    # every Pass B context, any scope (audit + resolver)
    target_gap: tuple[Span, ...] = ()
    context_spans: tuple[Span, ...] = ()
    requirement_spans: tuple[Span, ...] = ()
    reasons: tuple[ReasonV4, ...] = ()
    retryable: bool = False
    field_provenance: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)
    context_resolution: ContextResolution | None = None
    schema: str = S1V4_SCHEMA

    def __post_init__(self):
        if self.spec_status not in STATUSES:
            raise ValueError(f"unknown S1 status {self.spec_status!r}")
        if self.target_state not in TARGET_STATES:
            raise ValueError(f"unknown target state {self.target_state!r}")
        if self.context_pass not in CONTEXT_PASS_STATUSES:
            raise ValueError(f"unknown context pass status {self.context_pass!r}")
        if self.policy is not None and self.policy not in S1_POLICIES:
            raise ValueError(f"unknown S1 policy {self.policy!r}")
        if self.target_basis is not None and self.target_basis not in TARGET_BASES:
            raise ValueError(f"unknown target basis {self.target_basis!r}")
        check_settings(self.settings)
        if len(self.context_candidates) > MAX_SETTINGS:
            raise ValueError(f"at most {MAX_SETTINGS} contexts")
        all_scope = {(c.text, c.jd_span) for c in self.context_candidates if c.scope == SCOPE_ALL}
        if any((s.text, s.jd_span) not in all_scope for s in self.settings):
            raise ValueError("settings must be scope-'all' context candidates")
        kinds = {r.kind for r in self.reasons}
        failed = self.spec_status in (STATUS_FAILED_TECHNICAL, STATUS_FAILED_VALIDATION)
        if failed != (self.target_state == TARGET_FAILED):
            raise ValueError("a failed artefact has target_state target_failed and vice versa")
        if failed and (self.policy is not None or self.targets or self.settings or self.context_candidates):
            raise ValueError("a failed artefact carries no policy, targets or contexts")
        if failed and self.context_pass != CONTEXT_SKIPPED:
            raise ValueError("no context pass for a failed target pass")
        if self.spec_status in (STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION) and (self.policy is None
                                                                                  or self.target_basis is None):
            raise ValueError(f"a {self.spec_status} artefact needs a policy and a target basis")
        if self.spec_status == STATUS_RESOLVED and self.reasons:
            raise ValueError("a resolved artefact carries no reasons")
        if self.spec_status == STATUS_NEEDS_CONFIRMATION and (
                not self.reasons or not kinds <= {KIND_BUSINESS, KIND_AMBIGUITY}):
            raise ValueError("needs_confirmation requires business/ambiguity reasons only")
        if self.spec_status == STATUS_FAILED_TECHNICAL and kinds != {KIND_TECHNICAL}:
            raise ValueError("failed_technical requires exactly technical reasons")
        if self.spec_status == STATUS_FAILED_VALIDATION and kinds != {KIND_CONTRACT}:
            raise ValueError("failed_validation requires a contract reason")
        if self.spec_status == STATUS_RESOLVED and self.target_state in (TARGET_ABSENT_CLAIMED, TARGET_UNSPECIFIED):
            # F2/F3/F5: an unestablished target never resolves on its own
            raise ValueError(f"a {self.target_state} criterion cannot be resolved")

    def semantic_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id, "display_text": self.display_text,
            "requirement_text": self.requirement_text, "policy": self.policy, "target_basis": self.target_basis,
            "target_state": self.target_state, "targets": [t.to_dict() for t in self.targets],
            "required_years": self.required_years.to_dict() if self.required_years else None,
            "settings": [s.to_dict() for s in self.settings],
            "context_candidates": [c.to_dict() for c in self.context_candidates],
            "spec_status": self.spec_status, "context_pass": self.context_pass,
            "context_resolution": self.context_resolution.semantic_dict() if self.context_resolution else None,
        }

    @property
    def content_hash(self) -> str:
        return sha256(canonical(self.semantic_dict()))

    @property
    def spec_version(self) -> str:
        return f"s1v4-{S1V4_VERSION}-{self.content_hash[:12]}"

    def to_dict(self) -> dict:
        return {
            "_schema": self.schema, "spec_version": self.spec_version, "criterion_id": self.criterion_id,
            "job_id": self.job_id, "source_path": self.source_path, "display_text": self.display_text,
            "requirement_text": self.requirement_text, "required": self.required, "spec_status": self.spec_status,
            "target_state": self.target_state, "context_pass": self.context_pass, "policy": self.policy,
            "target_basis": self.target_basis, "targets": [t.to_dict() for t in self.targets],
            "required_years": self.required_years.to_dict() if self.required_years else None,
            "settings": [s.to_dict() for s in self.settings],
            "context_candidates": [c.to_dict() for c in self.context_candidates],
            "target_gap": [s.to_dict() for s in self.target_gap],
            "context_spans": [s.to_dict() for s in self.context_spans],
            "requirement_spans": [s.to_dict() for s in self.requirement_spans],
            "reasons": [r.to_dict() for r in self.reasons], "retryable": self.retryable,
            "field_provenance": dict(self.field_provenance), "validation": dict(self.validation),
            "audit": dict(self.audit), "versions": dict(self.versions),
            "context_resolution": self.context_resolution.to_dict() if self.context_resolution else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "S1ArtifactV4":
        if d.get("_schema") != S1V4_SCHEMA:          # v3 (or older) objects are never read as v4
            raise ValueError(f"not an {S1V4_SCHEMA} object")
        return cls(
            criterion_id=d["criterion_id"], job_id=d["job_id"], source_path=d["source_path"],
            display_text=d["display_text"], requirement_text=d["requirement_text"], required=bool(d["required"]),
            spec_status=d["spec_status"], target_state=d["target_state"], context_pass=d["context_pass"],
            policy=d.get("policy"), target_basis=d.get("target_basis"),
            targets=tuple(Target.from_dict(t) for t in d.get("targets") or []),
            required_years=RequiredYears.from_dict(d.get("required_years")),
            settings=tuple(Setting.from_dict(s) for s in d.get("settings") or []),
            context_candidates=tuple(ContextCandidate.from_dict(c) for c in d.get("context_candidates") or []),
            target_gap=tuple(Span.from_dict(s) for s in d.get("target_gap") or []),
            context_spans=tuple(Span.from_dict(s) for s in d.get("context_spans") or []),
            requirement_spans=tuple(Span.from_dict(s) for s in d.get("requirement_spans") or []),
            reasons=tuple(ReasonV4.from_dict(r) for r in d.get("reasons") or []),
            retryable=bool(d.get("retryable")), field_provenance=dict(d.get("field_provenance") or {}),
            validation=dict(d.get("validation") or {}), audit=dict(d.get("audit") or {}),
            versions=dict(d.get("versions") or {}),
            context_resolution=ContextResolution.from_dict(d.get("context_resolution")))
