"""
S1 RequirementSpec foundation — artifact schema (s1_requirement_spec_v3).

SHADOW ONLY: nothing in production imports this package, nothing is persisted.

Authority (highest first), applied per FIELD:
  1. recruiter_edited / recruiter_confirmed — only when a caller supplies it
     explicitly; never inferred (e.g. not from original_analysis_json diffs);
  2. JD text — verbatim spans verified deterministically;
  3. untouched analysis_json AI extraction — a hint only (original_ai);
  4. S1 interpretation — semantic labels only (policy, target type).

Status axes:
  business   resolved | needs_confirmation (reasons of kind business/ambiguity)
  technical  failed_technical (ai_unavailable, internal_error, output_truncated,
             exceeds_model_context) — never needs_confirmation
  contract   failed_validation (AI output still invalid after one repair)
  pending    enumerated, not yet classified
These states carry no production scoring behaviour in this phase.

s1-6 / v3 (P4a): ``setting: Setting | None`` became ``settings: tuple[Setting, ...]``: 0..MAX_SETTINGS
experience CONTEXTS (where / in what setting otherwise relevant past experience must have been gained:
geographic scope, organisation type, sector or domain, project type, work setting), each a verbatim JD span
inside a requirement span, distinct, non-overlapping, ordered by JD occurrence; several entries mean AND on
the SAME experience entry; one contiguous restriction is ONE entry and an "or" stays inside one entry.
``context_resolution`` is the slot for the later deterministic agreement with the qualifying-context
analysis (P4c, not implemented): S1 itself never sets it, and without a resolved one no S2 view exists.
A v2 object is never read as v3.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

S1_SCHEMA = "s1_requirement_spec_v3"
S1_VERSION = "1.5.0"                     # enumeration + parser + validator + assembly
S1_INPUT_VERSION = "s1-in-1"             # unchanged by s1-6: the model input carries no qualifying context
S1_PROMPT_CODE = "recruitment.experience_requirement_spec"
S1_PROMPT_VERSION = "s1-6.0"
MAX_SETTINGS = 5                         # experience contexts per criterion
S1_MODEL = "gpt-4o-mini"
S1_TEMPERATURE = 0.0
S1_MAX_TOKENS = 4000

# ── policies / target types ──────────────────────────────────────────────────
POLICY_EXPLICIT_ROLE = "explicit_role"
POLICY_FUNCTIONAL = "functional"
POLICY_MIXED = "mixed"
POLICY_SECTOR = "sector"
POLICY_PURE_DURATION = "pure_duration"
S1_POLICIES = (POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_SECTOR, POLICY_PURE_DURATION)

MATCH_EXACT = "exact"              # complete hint verbatim in a requirement span (verified by code)
MATCH_EQUIVALENT = "equivalent"    # same role/function in other language/form/abbreviation (jd_span required)
MATCH_NONE = "none"                # anything else, or uncertain
MATCHES = (MATCH_EXACT, MATCH_EQUIVALENT, MATCH_NONE)

# s1-4: how a criterion WITHOUT analysis targets restricts relevance (policy is derived from it)
BASIS_TARGETS = "targets"                    # the requirement names roles/functions (JD-selected targets)
BASIS_SECTOR = "sector"                      # only context restrictions (s1-6)          -> sector
BASIS_TOTAL_EXPERIENCE = "total_experience"  # total experience, no restriction        -> pure_duration
BASIS_UNSPECIFIED = "unspecified"            # "relevant" but undefined -> pure_duration + ambiguous_relevance
RELEVANCE_BASES = (BASIS_TARGETS, BASIS_SECTOR, BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED)
# s1-5: relevance_basis is DERIVED from typed restrictions (never a model claim)

# s1-5: typed restrictions (criteria WITHOUT analysis targets): every JD phrase limiting which experience counts
RESTRICTION_ROLE = "role"
RESTRICTION_FUNCTION = "function"
# s1-6: the wire kind "sector" (industry / sector / environment) became "context": the shared experience-context
# concept (geography, organisation type, sector/domain, project type, work setting). Renamed on the wire because
# a geographic or organisation-type restriction labelled "sector" invites the model to drop it (= broadening).
RESTRICTION_CONTEXT = "context"
RESTRICTION_VAGUE = "vague"              # "relevant / related / similar / in the field" without saying what
RESTRICTION_KINDS = (RESTRICTION_ROLE, RESTRICTION_FUNCTION, RESTRICTION_CONTEXT, RESTRICTION_VAGUE)

# s1-5: word alignment of an "equivalent" mapping (one analysis-target word per pair)
REL_SAME = "same"                        # identical word
REL_FORM = "form"                        # same language, other grammatical form; s1-5.2: NOT trust-bearing
UNVERIFIED_RELATIONS = (REL_FORM,)       # a mapping using any of these never establishes a target
TRUST_BEARING = "trust_bearing"          # audit.target_mappings[].trust
TRUST_UNVERIFIED_FORM = "unverified_form"
# s1-5.2.2: an abbreviation EXPANSION (acronym <-> full form) is evidence only when the JD itself defines it in a
# requirement span ("Full Form (ACR)" / "ACR (Full Form)"); initials alone are only structural plausibility
TRUST_UNVERIFIED_ABBREVIATION = "unverified_abbreviation"
REL_TRANSLATION = "translation"          # other language (other script)
REL_ABBREVIATION = "abbreviation"        # an all-capitals acronym and its expansion
ALIGN_RELATIONS = (REL_SAME, REL_FORM, REL_TRANSLATION, REL_ABBREVIATION)
EXTRA_GRAMMATICAL = "grammatical"        # JD word carrying no requirement meaning (of, the, في ...)
EXTRA_MATERIAL = "material"              # JD word adding meaning (a qualifier): never equivalent
JD_EXTRA_KINDS = (EXTRA_GRAMMATICAL, EXTRA_MATERIAL)

TARGET_ROLE = "role"
TARGET_FUNCTION = "function"
TARGET_TYPES = (TARGET_ROLE, TARGET_FUNCTION)

# ── provenance ───────────────────────────────────────────────────────────────
PROV_ORIGINAL_AI = "original_ai"                  # analysis_json value, not found in the JD
PROV_JD_VERIFIED = "jd_verified"                  # analysis_json value confirmed verbatim in the JD
PROV_JD_ASSERTED = "jd_asserted"                  # AI-selected JD span, verified verbatim
PROV_RECRUITER_EDITED = "recruiter_edited"        # explicitly supplied by a caller only
PROV_RECRUITER_CONFIRMED = "recruiter_confirmed"  # explicitly supplied by a caller only
PROV_S1_INTERPRETED = "s1_interpreted"            # S1 semantic label (policy, target type)
PROVENANCES = (PROV_ORIGINAL_AI, PROV_JD_VERIFIED, PROV_JD_ASSERTED,
               PROV_RECRUITER_EDITED, PROV_RECRUITER_CONFIRMED, PROV_S1_INTERPRETED)
RECRUITER_PROVENANCES = (PROV_RECRUITER_EDITED, PROV_RECRUITER_CONFIRMED)

# analysis_json fields a future caller may mark as recruiter-authored.
FIELD_MIN_YEARS = "experience.minimum_years"
FIELD_ROLES = "experience.relevant_roles"
RECRUITER_FIELDS = (FIELD_MIN_YEARS, FIELD_ROLES)

# ── status ───────────────────────────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_FAILED_VALIDATION = "failed_validation"
STATUS_FAILED_TECHNICAL = "failed_technical"
STATUSES = (STATUS_PENDING, STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION,
            STATUS_FAILED_VALIDATION, STATUS_FAILED_TECHNICAL)

# ── reason codes (kind -> codes) ─────────────────────────────────────────────
KIND_AMBIGUITY = "ambiguity"      # reported by the AI
KIND_BUSINESS = "business"        # deterministic authority conflicts
KIND_CONTRACT = "contract"
KIND_TECHNICAL = "technical"

AMB_AMBIGUOUS_RELEVANCE = "ambiguous_relevance"
AMB_MULTIPLE_DURATIONS = "multiple_durations"
AMB_CONFLICTING_REQUIREMENTS = "conflicting_requirements"
AMB_REQUIREMENT_NOT_IN_JD = "requirement_not_in_jd"
# s1-6: a context clearly exists, but it is unclear which target / alternative of the requirement it applies to
# (it restricts only one alternative, or is only softened "preferably / ideally in X"): settings stay [] and the
# criterion is needs_confirmation. Never ambiguous_relevance for this case. Distinct from
# compound_requirement (a nested sub-duration, e.g. "N years overall, including M years in X": the requirement
# STRUCTURE cannot be represented; the context itself is not ambiguous): the two are never coupled.
AMB_AMBIGUOUS_CONTEXT_SCOPE = "ambiguous_context_scope"
AMBIGUITY_CODES = (AMB_AMBIGUOUS_RELEVANCE, AMB_MULTIPLE_DURATIONS,
                   AMB_CONFLICTING_REQUIREMENTS, AMB_REQUIREMENT_NOT_IN_JD, AMB_AMBIGUOUS_CONTEXT_SCOPE)

BIZ_TARGET_NOT_IN_JD = "target_not_in_jd"
BIZ_N_NOT_IN_JD = "n_not_in_jd"
BIZ_N_MISMATCH = "n_mismatch"
BIZ_COMPOUND_REQUIREMENT = "compound_requirement"   # s1-5: >= 2 duration thresholds in one requirement
# s1-5.2: the JD wording WAS found and a structurally valid mapping was proposed, but the equivalence rests on
# a relation the system cannot verify (a model-labelled grammatical "form"): never evidence until confirmed
BIZ_EQUIVALENCE_UNVERIFIED = "equivalence_unverified"
BUSINESS_CODES = (BIZ_TARGET_NOT_IN_JD, BIZ_N_NOT_IN_JD, BIZ_N_MISMATCH, BIZ_COMPOUND_REQUIREMENT,
                  BIZ_EQUIVALENCE_UNVERIFIED)

REASON_VALIDATION_FAILED = "validation_failed"
CONTRACT_CODES = (REASON_VALIDATION_FAILED,)

REASON_AI_UNAVAILABLE = "ai_unavailable"
REASON_INTERNAL_ERROR = "internal_error"
REASON_OUTPUT_TRUNCATED = "output_truncated"
REASON_EXCEEDS_MODEL_CONTEXT = "exceeds_model_context"
TECHNICAL_CODES = (REASON_AI_UNAVAILABLE, REASON_INTERNAL_ERROR,
                   REASON_OUTPUT_TRUNCATED, REASON_EXCEEDS_MODEL_CONTEXT)
RETRYABLE_REASONS = frozenset({REASON_AI_UNAVAILABLE, REASON_INTERNAL_ERROR})

REASON_KINDS = {**{c: KIND_AMBIGUITY for c in AMBIGUITY_CODES},
                **{c: KIND_BUSINESS for c in BUSINESS_CODES},
                **{c: KIND_CONTRACT for c in CONTRACT_CODES},
                **{c: KIND_TECHNICAL for c in TECHNICAL_CODES}}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Span:
    """A verbatim JD span: 1-based line, [start, end) character offsets in that line."""
    line: int
    start: int
    end: int
    text: str

    def to_dict(self) -> dict:
        return {"line": self.line, "start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Span | None":
        return None if d is None else cls(int(d["line"]), int(d["start"]), int(d["end"]), d["text"])

    def within(self, other: "Span") -> bool:
        return self.line == other.line and other.start <= self.start and self.end <= other.end


@dataclass(frozen=True)
class Reason:
    code: str
    field: str | None = None
    detail: str | None = None

    def __post_init__(self):
        if self.code not in REASON_KINDS:
            raise ValueError(f"unknown S1 reason code {self.code!r}")

    @property
    def kind(self) -> str:
        return REASON_KINDS[self.code]

    def to_dict(self) -> dict:
        return {"code": self.code, "kind": self.kind, "field": self.field, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict) -> "Reason":
        return cls(d["code"], d.get("field"), d.get("detail"))


def _check_prov(p: str) -> None:
    if p not in PROVENANCES:
        raise ValueError(f"unknown provenance {p!r}")


@dataclass(frozen=True)
class Target:
    text: str
    type: str                       # role | function
    provenance: str
    jd_span: Span | None = None
    target_id: str = ""             # "T1".. for analysis hints, "J1".. for AI-selected JD spans

    def __post_init__(self):
        if self.type not in TARGET_TYPES:
            raise ValueError(f"unknown target type {self.type!r}")
        _check_prov(self.provenance)
        if not (self.text or "").strip():
            raise ValueError("target text is empty")

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "text": self.text, "type": self.type,
                "provenance": self.provenance, "jd_span": self.jd_span.to_dict() if self.jd_span else None}

    @classmethod
    def from_dict(cls, d: dict) -> "Target":
        return cls(d["text"], d["type"], d["provenance"], Span.from_dict(d.get("jd_span")), d.get("target_id", ""))


@dataclass(frozen=True)
class Setting:
    """One criterion-specific experience context: always a verbatim JD span inside a requirement span."""
    text: str
    provenance: str
    jd_span: Span

    def __post_init__(self):
        _check_prov(self.provenance)

    def to_dict(self) -> dict:
        return {"text": self.text, "provenance": self.provenance, "jd_span": self.jd_span.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Setting":
        return cls(d["text"], d["provenance"], Span.from_dict(d["jd_span"]))


def check_settings(settings: tuple[Setting, ...]) -> None:
    """v3 invariants: at most MAX_SETTINGS, distinct, non-overlapping, ordered by JD occurrence."""
    if len(settings) > MAX_SETTINGS:
        raise ValueError(f"at most {MAX_SETTINGS} settings, got {len(settings)}")
    keys = [(s.jd_span.line, s.jd_span.start, s.jd_span.end) for s in settings]
    if keys != sorted(keys):
        raise ValueError("settings must be ordered by JD occurrence")
    for a, b in zip(settings, settings[1:]):
        if a.jd_span.line == b.jd_span.line and b.jd_span.start < a.jd_span.end:
            raise ValueError(f"settings {a.text!r} and {b.text!r} overlap")


# ── context resolution (slot only; the P4c resolver does not exist yet) ─────────
CONTEXT_RESOLVED = "resolved"
CONTEXT_UNCONFIRMED = "unconfirmed"
CONTEXT_STATUSES = (CONTEXT_RESOLVED, CONTEXT_UNCONFIRMED)
CONTEXT_STATE_IDENTIFIED = "identified"
CONTEXT_STATE_NONE = "none"
RESOLVED_DETAILS = ("agreed", "agreed_none", "recruiter")
UNCONFIRMED_DETAILS = ("disagreement", "uncertain", "unassessed", "qc_failed", "stale", "s1_unavailable")
EFFECTIVE_PROVENANCES = (PROV_RECRUITER_EDITED, PROV_RECRUITER_CONFIRMED, PROV_JD_VERIFIED)


@dataclass(frozen=True)
class EffectiveContext:
    state: str                          # identified | none
    contexts: tuple[str, ...]           # verbatim; () iff none
    provenance: str                     # recruiter_edited | recruiter_confirmed | jd_verified

    def __post_init__(self):
        if self.state not in (CONTEXT_STATE_IDENTIFIED, CONTEXT_STATE_NONE):
            raise ValueError(f"unknown effective context state {self.state!r}")
        if self.provenance not in EFFECTIVE_PROVENANCES:
            raise ValueError(f"effective context provenance must be one of {list(EFFECTIVE_PROVENANCES)}")
        if any(not isinstance(c, str) or not c.strip() for c in self.contexts):
            raise ValueError("effective contexts must be non-blank strings")
        if (self.state == CONTEXT_STATE_IDENTIFIED) != bool(self.contexts):
            raise ValueError("identified needs at least one context; none has no contexts")

    def to_dict(self) -> dict:
        return {"state": self.state, "contexts": list(self.contexts), "provenance": self.provenance}

    @classmethod
    def from_dict(cls, d: dict | None) -> "EffectiveContext | None":
        return None if d is None else cls(d["state"], tuple(d.get("contexts") or ()), d["provenance"])


@dataclass(frozen=True)
class ContextResolution:
    """Outcome of the (future, P4c) deterministic agreement between the qualifying-context analysis and S1.
    resolved <=> effective is set. S1 never creates one; ``record`` is audit only (never semantics)."""
    status: str
    detail: str
    effective: EffectiveContext | None = None
    record: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in CONTEXT_STATUSES:
            raise ValueError(f"unknown context resolution status {self.status!r}")
        allowed = RESOLVED_DETAILS if self.status == CONTEXT_RESOLVED else UNCONFIRMED_DETAILS
        if self.detail not in allowed:
            raise ValueError(f"detail {self.detail!r} is not valid for status {self.status!r}")
        if (self.status == CONTEXT_RESOLVED) != (self.effective is not None):
            raise ValueError("a resolved context resolution has an effective context; an unconfirmed one has none")
        e = self.effective
        if e is not None:
            if self.detail == "recruiter" and e.provenance == PROV_JD_VERIFIED:
                raise ValueError("a recruiter resolution needs recruiter provenance")
            if self.detail in ("agreed", "agreed_none") and e.provenance != PROV_JD_VERIFIED:
                raise ValueError("an agreed resolution has provenance jd_verified")
            if self.detail == "agreed" and e.state != CONTEXT_STATE_IDENTIFIED:
                raise ValueError("agreed means identified")
            if self.detail == "agreed_none" and e.state != CONTEXT_STATE_NONE:
                raise ValueError("agreed_none means none")

    def semantic_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail,
                "effective": self.effective.to_dict() if self.effective else None}

    def to_dict(self) -> dict:
        return {**self.semantic_dict(), "record": dict(self.record)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ContextResolution | None":
        if d is None:
            return None
        return cls(d["status"], d["detail"], EffectiveContext.from_dict(d.get("effective")),
                   dict(d.get("record") or {}))


@dataclass(frozen=True)
class RequiredYears:
    """N. ``value`` is in years (lower bound of a range). ``parsed`` is the
    DurationMatch dict of the JD span, ``analysis_hint`` the analysis_json value."""
    value: float
    provenance: str
    jd_span: Span | None = None
    parsed: dict | None = None
    analysis_hint: float | None = None

    def __post_init__(self):
        _check_prov(self.provenance)
        if not self.value > 0:
            raise ValueError(f"required_years must be > 0, got {self.value!r}")

    def to_dict(self) -> dict:
        return {"value": self.value, "provenance": self.provenance,
                "jd_span": self.jd_span.to_dict() if self.jd_span else None,
                "parsed": self.parsed, "analysis_hint": self.analysis_hint}

    @classmethod
    def from_dict(cls, d: dict | None) -> "RequiredYears | None":
        if d is None:
            return None
        return cls(d["value"], d["provenance"], Span.from_dict(d.get("jd_span")), d.get("parsed"),
                   d.get("analysis_hint"))


@dataclass(frozen=True)
class S1Artifact:
    criterion_id: str
    job_id: str
    source_path: str
    display_text: str               # recruiter-facing D-01 text, verbatim
    requirement_text: str           # text S2 sees (JD requirement spans, or display_text)
    required: bool
    spec_status: str
    policy: str | None = None
    targets: tuple[Target, ...] = ()
    settings: tuple[Setting, ...] = ()       # s1-6: experience contexts (AND, same experience entry)
    required_years: RequiredYears | None = None
    reasons: tuple[Reason, ...] = ()
    retryable: bool = False
    input_hash: str = ""
    s1_version: str = S1_VERSION
    prompt_version: str = S1_PROMPT_VERSION
    prompt_fingerprint: str = ""
    model: str = S1_MODEL
    field_provenance: dict = field(default_factory=dict)
    requirement_spans: tuple[Span, ...] = ()
    validation: dict = field(default_factory=lambda: {"errors": [], "repair_errors": []})
    audit: dict = field(default_factory=dict)
    schema: str = S1_SCHEMA
    context_resolution: ContextResolution | None = None   # P4c slot; None = context unresolved (no S2 view)

    def __post_init__(self):
        check_settings(self.settings)
        if self.spec_status not in STATUSES:
            raise ValueError(f"unknown S1 status {self.spec_status!r}")
        if self.policy is not None and self.policy not in S1_POLICIES:
            raise ValueError(f"unknown S1 policy {self.policy!r}")
        if self.spec_status in (STATUS_RESOLVED, STATUS_NEEDS_CONFIRMATION) and self.policy is None:
            raise ValueError(f"a {self.spec_status} artifact needs a policy")
        kinds = {r.kind for r in self.reasons}
        if self.spec_status == STATUS_RESOLVED and self.reasons:
            raise ValueError("a resolved artifact carries no reasons")
        if self.spec_status == STATUS_NEEDS_CONFIRMATION and (
                not self.reasons or not kinds <= {KIND_BUSINESS, KIND_AMBIGUITY}):
            raise ValueError("needs_confirmation requires business/ambiguity reasons only")
        if self.spec_status == STATUS_FAILED_TECHNICAL and kinds != {KIND_TECHNICAL}:
            raise ValueError("failed_technical requires exactly technical reasons")
        if self.spec_status == STATUS_FAILED_VALIDATION and kinds != {KIND_CONTRACT}:
            raise ValueError("failed_validation requires a contract reason")

    def semantic_dict(self) -> dict:
        """Fields that define the requirement (no run metadata)."""
        return {
            "criterion_id": self.criterion_id, "display_text": self.display_text,
            "requirement_text": self.requirement_text, "policy": self.policy,
            "targets": [t.to_dict() for t in self.targets],
            "settings": [s.to_dict() for s in self.settings],
            "required_years": self.required_years.to_dict() if self.required_years else None,
            "spec_status": self.spec_status,
            "context_resolution": self.context_resolution.semantic_dict() if self.context_resolution else None,
        }

    @property
    def content_hash(self) -> str:
        return sha256(canonical(self.semantic_dict()))

    @property
    def spec_version(self) -> str:
        return f"s1-{self.s1_version}-{self.content_hash[:12]}"

    def to_dict(self) -> dict:
        return {
            "_schema": self.schema, "s1_version": self.s1_version, "spec_version": self.spec_version,
            "criterion_id": self.criterion_id, "job_id": self.job_id, "source_path": self.source_path,
            "display_text": self.display_text, "requirement_text": self.requirement_text,
            "required": self.required, "policy": self.policy,
            "targets": [t.to_dict() for t in self.targets],
            "settings": [s.to_dict() for s in self.settings],
            "required_years": self.required_years.to_dict() if self.required_years else None,
            "spec_status": self.spec_status, "reasons": [r.to_dict() for r in self.reasons],
            "retryable": self.retryable, "input_hash": self.input_hash,
            "prompt_version": self.prompt_version, "prompt_fingerprint": self.prompt_fingerprint,
            "model": self.model, "field_provenance": dict(self.field_provenance),
            "requirement_spans": [s.to_dict() for s in self.requirement_spans],
            "validation": dict(self.validation), "audit": dict(self.audit),
            "context_resolution": self.context_resolution.to_dict() if self.context_resolution else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "S1Artifact":
        if d.get("_schema") != S1_SCHEMA:            # a v2 object ("setting") is never read as v3
            raise ValueError(f"not an {S1_SCHEMA} object")
        if "setting" in d or not isinstance(d.get("settings", []), list):
            raise ValueError(f"{S1_SCHEMA} carries a settings list, never a single setting")
        return cls(
            criterion_id=d["criterion_id"], job_id=d["job_id"], source_path=d["source_path"],
            display_text=d["display_text"], requirement_text=d["requirement_text"],
            required=bool(d["required"]), spec_status=d["spec_status"], policy=d.get("policy"),
            targets=tuple(Target.from_dict(t) for t in d.get("targets") or []),
            settings=tuple(Setting.from_dict(x) for x in d.get("settings") or []),
            required_years=RequiredYears.from_dict(d.get("required_years")),
            reasons=tuple(Reason.from_dict(r) for r in d.get("reasons") or []),
            retryable=bool(d.get("retryable")), input_hash=d.get("input_hash", ""),
            s1_version=d.get("s1_version", S1_VERSION), prompt_version=d.get("prompt_version", ""),
            prompt_fingerprint=d.get("prompt_fingerprint", ""), model=d.get("model", ""),
            field_provenance=dict(d.get("field_provenance") or {}),
            requirement_spans=tuple(Span.from_dict(s) for s in d.get("requirement_spans") or []),
            validation=dict(d.get("validation") or {}), audit=dict(d.get("audit") or {}),
            context_resolution=ContextResolution.from_dict(d.get("context_resolution")))
