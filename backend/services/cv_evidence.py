"""
Layer 1 — CV Evidence Extraction dataclasses.

These dataclasses define the canonical, structured representation of what a CV
contains.  They are produced by the CVFactsExtractor (Batch 2A-4) and persisted
to application_scores.cv_facts_json (Batch 2A-3).

No extraction logic lives here — this file is pure data contracts.
No LLM calls, no DB access, no scoring changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Type aliases ──────────────────────────────────────────────────────────────

MatchMethod = Literal["exact", "normalised", "fuzzy", "semantic", "inferred", "absent"]
LanguageCode = Literal["ar", "en", "mixed"]

# Education levels ordered from lowest to highest for comparison.
EDUCATION_LEVELS: tuple[str, ...] = (
    "None",
    "High School",
    "Diploma",
    "Associate",
    "Bachelor's",
    "Master's",
    "PhD",
)


# ── Evidence atoms ────────────────────────────────────────────────────────────

@dataclass
class SkillEvidence:
    """A single skill claim extracted from the CV.

    Explicit evidence is directly stated (e.g. "Microsoft Excel" in a skills
    list).  Inferred evidence is derived from context (e.g. "prepared monthly
    spreadsheet reports" → Excel).  Both types are valid but carry different
    confidence levels.
    """

    skill_name: str
    """Normalised skill name (diacritics stripped, homoglyphs resolved).
    E.g. "إكسل" is normalised to "Microsoft Excel"."""

    raw_text: str
    """Exact phrase as it appeared in the CV before normalisation."""

    explicit: bool
    """True = directly stated in a skills section or bullet point.
    False = inferred from a responsibility or achievement description."""

    confidence: float
    """Match confidence 0.0–1.0.  See confidence scale in architecture doc."""

    context_snippet: str
    """Up to 200 characters of surrounding CV text providing context."""

    language: LanguageCode
    """Language of the raw_text."""

    section_hint: str
    """Heuristic section label: 'skills' | 'experience' | 'education' | 'other'."""

    inference_basis: str = ""
    """Non-empty only for inferred evidence.
    E.g. 'prepared spreadsheets → Microsoft Excel'."""


@dataclass
class ExperienceEvidence:
    """A single employment or project block extracted from the CV.

    One ExperienceEvidence is created per distinct employer/role entry.
    Years is computed from dates where parseable; 0.0 when dates are absent
    or unparseable.
    """

    employer: str
    """Extracted employer or organisation name."""

    role_title: str
    """Job title or role description."""

    years: float
    """Computed duration in years (0.0 if dates are absent or unparseable)."""

    responsibilities: list[str] = field(default_factory=list)
    """Bullet points or sentences describing what the candidate did."""

    domain_signals: list[str] = field(default_factory=list)
    """Domain keywords found in this block (e.g. 'records management', 'GDPR')."""

    skill_signals: list[str] = field(default_factory=list)
    """Skill names identified within this experience block."""

    raw_text: str = ""
    """Original text block before parsing."""


@dataclass
class EducationEvidence:
    """A single education entry from the CV.

    degree_level is normalised to one of the values in EDUCATION_LEVELS so
    it can be compared algorithmically against job requirements.
    """

    degree_level: str
    """Normalised level: one of EDUCATION_LEVELS values."""

    field_of_study: str
    """Field or major (e.g. 'Business Administration', 'Computer Science')."""

    institution: str
    """University, college, or school name."""

    year: int | None
    """Graduation year (None if not stated or unparseable)."""

    raw_text: str = ""
    """Original text before parsing."""


@dataclass
class CertificationEvidence:
    """A single certification or professional credential found in the CV."""

    name: str
    """Normalised certification name."""

    raw_text: str
    """Original text as it appeared in the CV."""

    issuer: str = ""
    """Issuing body if extractable (e.g. 'Microsoft', 'PMI', 'ISO')."""

    year: int | None = None
    """Year obtained (None if not stated)."""


@dataclass
class SoftSkillSignal:
    """Behavioural evidence phrase suggesting a soft skill.

    Soft skills are rarely listed explicitly; they are inferred from
    responsibility descriptions (e.g. 'led a team of 5' → leadership).
    """

    soft_skill_category: str
    """Standardised category: 'communication' | 'leadership' | 'teamwork' |
    'problem_solving' | 'adaptability' | 'time_management' | 'other'."""

    evidence_phrase: str
    """The CV text that signals this soft skill."""

    confidence: float
    """Inference confidence 0.0–1.0."""

    inference_basis: str = ""
    """Explanation of the inference, e.g. 'led a team of 5 → leadership'."""


@dataclass
class DomainSignal:
    """An industry or domain keyword found in the CV.

    Multiple occurrences of the same term are collapsed into a single
    DomainSignal with frequency > 1 and multiple context_snippets.
    """

    domain_term: str
    """Normalised domain term (e.g. 'records management', 'GDPR', 'ISO 9001')."""

    frequency: int = 1
    """Number of times this term appears in the CV."""

    context_snippets: list[str] = field(default_factory=list)
    """Up to 3 surrounding text windows (≤150 chars each) for evidence."""


# ── Aggregate CV representation ───────────────────────────────────────────────

@dataclass
class CVFacts:
    """Canonical, structured representation of what a CV contains.

    Produced by Layer 1 (CVFactsExtractor, Batch 2A-4) before any LLM call.
    All text fields are normalised: diacritics stripped, homoglyphs resolved,
    whitespace collapsed.

    Persisted to application_scores.cv_facts_json (Batch 2A-3).
    The _version field enables cache invalidation when the extractor changes.
    """

    # ── Language ──────────────────────────────────────────────────────────
    language: LanguageCode
    """Dominant language of the CV text."""

    total_char_count: int
    """Total character count of the normalised CV text."""

    # ── Evidence collections ──────────────────────────────────────────────
    skills: list[SkillEvidence] = field(default_factory=list)
    """All skill evidence items, explicit and inferred, in extraction order."""

    experience: list[ExperienceEvidence] = field(default_factory=list)
    """Employment and project blocks, most recent first where detectable."""

    education: list[EducationEvidence] = field(default_factory=list)
    """Education entries, highest level first where detectable."""

    certifications: list[CertificationEvidence] = field(default_factory=list)
    """Certification and credential entries."""

    soft_skill_signals: list[SoftSkillSignal] = field(default_factory=list)
    """Inferred soft skill signals from responsibility descriptions."""

    domain_signals: list[DomainSignal] = field(default_factory=list)
    """Domain and industry keyword occurrences."""

    # ── Pre-computed aggregates (convenience for Layer 2) ─────────────────
    total_experience_years: float = 0.0
    """Sum of ExperienceEvidence.years across all non-overlapping blocks.
    Computed by extractor; 0.0 if no parseable dates were found."""

    highest_education_level: str = "None"
    """Highest degree level found, using EDUCATION_LEVELS ordering."""

    skill_names_normalised: list[str] = field(default_factory=list)
    """Deduplicated, normalised skill names for fast lookup."""

    # ── Extraction metadata ───────────────────────────────────────────────
    extractor_version: str = "0.0.0"
    """Semver of the CVFactsExtractor that produced this record.
    Used to invalidate cached CVFacts when the extractor changes."""

    extraction_method: str = "rule_based_v1"
    """Extraction strategy: 'rule_based_v1' | 'hybrid_v1' | 'llm_assisted_v1'."""

    extraction_warnings: list[str] = field(default_factory=list)
    """Non-fatal issues encountered during extraction (e.g. 'date unparseable',
    'skills section not found')."""
