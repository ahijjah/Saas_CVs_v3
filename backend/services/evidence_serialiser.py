"""
Serialisation helpers for Layer 1 (CVFacts) and Layer 2 (MatchResult).

Public API
──────────
  cvfacts_to_dict(facts)         → JSON-safe dict  (CVFacts → dict)
  cvfacts_from_dict(data)        → CVFacts          (dict → CVFacts)
  matchresult_to_dict(result)    → JSON-safe dict  (MatchResult → dict)
  matchresult_from_dict(data)    → MatchResult      (dict → MatchResult)

Design principles
─────────────────
• Serialisation (to_dict) uses dataclasses.asdict() — handles all nesting
  automatically, produces only JSON-safe primitives.  A _schema key is
  injected so consumers can detect the format version.

• Deserialisation (from_dict) is fault-tolerant: missing keys fall back to
  dataclass defaults; unexpected keys are silently ignored; wrong-typed
  scalars are coerced.  Raises SerialisationError only when the top-level
  argument is not a dict (cannot be recovered from).

• Arabic / mixed-language text is never transformed — string values are
  extracted as-is from the source dict.

• Nested dataclasses (CVFacts → SkillEvidence list, etc.) are reconstructed
  by explicit per-type factory functions so type safety is preserved at each
  level.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, TypeVar

from services.cv_evidence import (
    CVFacts,
    CertificationEvidence,
    DomainSignal,
    EducationEvidence,
    ExperienceEvidence,
    SkillEvidence,
    SoftSkillSignal,
)
from services.criteria_matcher import (
    CriterionMatch,
    GapCandidate,
    MatchResult,
)
from services.llm_criteria_mapper import (
    LLMCriterionAssessment,
    LLMMatchResult,
)

T = TypeVar("T")

# ── Schema version constants ──────────────────────────────────────────────────

_CVFACTS_SCHEMA: str = "2a.1"
_MATCHRESULT_SCHEMA: str = "2a.1"


# ── Public exception ──────────────────────────────────────────────────────────

class SerialisationError(ValueError):
    """Raised when a value cannot be deserialised into the target dataclass.

    Only raised when the top-level input is not a dict (e.g. None, a string,
    or a list).  Missing or wrong-typed *keys* are handled with safe defaults
    rather than raising.
    """


# ── Private scalar coercion helpers ──────────────────────────────────────────

def _s(d: dict, key: str, default: str = "") -> str:
    """Extract a string field, coercing non-string values and using default for absent keys."""
    v = d.get(key, default)
    if v is None:
        return default
    return str(v)


def _f(d: dict, key: str, default: float = 0.0) -> float:
    """Extract a float field with fallback to default on absent or unconvertible values."""
    v = d.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _b(d: dict, key: str, default: bool = False) -> bool:
    """Extract a bool field, accepting JSON booleans and integer 0/1."""
    v = d.get(key, default)
    if isinstance(v, bool):
        return v
    return bool(v)


def _i(d: dict, key: str, default: int = 0) -> int:
    """Extract an int field with fallback to default."""
    v = d.get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _i_or_none(d: dict, key: str) -> int | None:
    """Extract an int-or-None field (used for optional year fields)."""
    v = d.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── Private list coercion helpers ─────────────────────────────────────────────

def _str_list(d: dict, key: str) -> list[str]:
    """Extract a list of strings, coercing items and ignoring None values."""
    raw = d.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item is not None]


def _float_list(d: dict, key: str) -> list[float]:
    """Extract a list of floats, silently dropping unconvertible items."""
    raw = d.get(key)
    if not isinstance(raw, list):
        return []
    result: list[float] = []
    for item in raw:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            pass
    return result


def _dict_of_float(d: dict, key: str) -> dict[str, float]:
    """Extract a dict[str, float], coercing values and ignoring non-convertible items."""
    raw = d.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _dict_of_int(d: dict, key: str) -> dict[str, int]:
    """Extract a dict[str, int], coercing values and ignoring non-convertible items."""
    raw = d.get(key)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            pass
    return out


def _nested_list(
    d: dict,
    key: str,
    factory: Callable[[dict], T],
) -> list[T]:
    """Extract a list of nested dataclasses, skipping malformed items silently."""
    raw = d.get(key)
    if not isinstance(raw, list):
        return []
    out: list[T] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(factory(item))
        except Exception:
            pass  # skip individual malformed items; preserve the rest
    return out


# ── Private per-type reconstruction factories ─────────────────────────────────

def _skill_from_dict(d: dict) -> SkillEvidence:
    return SkillEvidence(
        skill_name=_s(d, "skill_name"),
        raw_text=_s(d, "raw_text"),
        explicit=_b(d, "explicit"),
        confidence=_f(d, "confidence"),
        context_snippet=_s(d, "context_snippet"),
        language=_s(d, "language", "en"),          # type: ignore[arg-type]
        section_hint=_s(d, "section_hint", "other"),
        inference_basis=_s(d, "inference_basis"),
    )


def _experience_from_dict(d: dict) -> ExperienceEvidence:
    return ExperienceEvidence(
        employer=_s(d, "employer"),
        role_title=_s(d, "role_title"),
        years=_f(d, "years"),
        responsibilities=_str_list(d, "responsibilities"),
        domain_signals=_str_list(d, "domain_signals"),
        skill_signals=_str_list(d, "skill_signals"),
        raw_text=_s(d, "raw_text"),
    )


def _education_from_dict(d: dict) -> EducationEvidence:
    return EducationEvidence(
        degree_level=_s(d, "degree_level", "None"),
        field_of_study=_s(d, "field_of_study"),
        institution=_s(d, "institution"),
        year=_i_or_none(d, "year"),
        raw_text=_s(d, "raw_text"),
        inferred=bool(d.get("inferred", False)),
        basis=_s(d, "basis", ""),
        confidence=float(d.get("confidence", 1.0)),
    )


def _certification_from_dict(d: dict) -> CertificationEvidence:
    return CertificationEvidence(
        name=_s(d, "name"),
        raw_text=_s(d, "raw_text"),
        issuer=_s(d, "issuer"),
        year=_i_or_none(d, "year"),
    )


def _soft_skill_from_dict(d: dict) -> SoftSkillSignal:
    return SoftSkillSignal(
        soft_skill_category=_s(d, "soft_skill_category", "other"),
        evidence_phrase=_s(d, "evidence_phrase"),
        confidence=_f(d, "confidence"),
        inference_basis=_s(d, "inference_basis"),
    )


def _domain_signal_from_dict(d: dict) -> DomainSignal:
    return DomainSignal(
        domain_term=_s(d, "domain_term"),
        frequency=_i(d, "frequency", 1),
        context_snippets=_str_list(d, "context_snippets"),
    )


def _criterion_match_from_dict(d: dict) -> CriterionMatch:
    return CriterionMatch(
        criterion_text=_s(d, "criterion_text"),
        dimension=_s(d, "dimension", "skills"),    # type: ignore[arg-type]
        required=_b(d, "required", True),
        status=_s(d, "status", "ABSENT"),          # type: ignore[arg-type]
        confidence=_f(d, "confidence"),
        match_method=_s(d, "match_method", "absent"),  # type: ignore[arg-type]
        supporting_evidence=_str_list(d, "supporting_evidence"),
        evidence_confidence=_float_list(d, "evidence_confidence"),
        partial_reason=_s(d, "partial_reason"),
        matched_via_translation=_b(d, "matched_via_translation"),
        original_cv_term=_s(d, "original_cv_term"),
    )


def _gap_candidate_from_dict(d: dict) -> GapCandidate:
    raw_criterion = d.get("criterion")
    if isinstance(raw_criterion, dict):
        criterion = _criterion_match_from_dict(raw_criterion)
    else:
        # Fallback: construct a minimal placeholder criterion so the
        # GapCandidate is at least structurally valid.
        criterion = CriterionMatch(
            criterion_text="",
            dimension="skills",
            required=True,
            status="ABSENT",
            confidence=0.0,
            match_method="absent",
        )
    return GapCandidate(
        criterion=criterion,
        severity=_s(d, "severity", "SIGNIFICANT"),  # type: ignore[arg-type]
        compensating_evidence=_str_list(d, "compensating_evidence"),
        compensating_confidence=_f(d, "compensating_confidence"),
        suppressed=_b(d, "suppressed"),
        suppression_reason=_s(d, "suppression_reason"),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def cvfacts_to_dict(facts: CVFacts) -> dict[str, Any]:
    """Convert a CVFacts dataclass to a JSON-safe dictionary.

    Adds a top-level ``_schema`` key with the current serialisation format
    version so consumers can detect and handle format changes.

    All nested dataclasses (SkillEvidence, ExperienceEvidence, …) are
    recursively converted by dataclasses.asdict().  The result contains only
    Python primitives (str, int, float, bool, list, dict, None) and is safe
    to pass directly to json.dumps() or a JSONB column.
    """
    d = dataclasses.asdict(facts)
    d["_schema"] = _CVFACTS_SCHEMA
    return d


def cvfacts_from_dict(data: Any) -> CVFacts:
    """Reconstruct a CVFacts from a previously serialised dict.

    Raises SerialisationError when ``data`` is not a dict.
    Missing or wrong-typed fields fall back to their dataclass defaults
    rather than raising, so older records with partial schemas are safe.
    """
    if not isinstance(data, dict):
        raise SerialisationError(
            f"cvfacts_from_dict expects a dict, got {type(data).__name__!r}"
        )
    return CVFacts(
        language=_s(data, "language", "en"),          # type: ignore[arg-type]
        total_char_count=_i(data, "total_char_count"),
        skills=_nested_list(data, "skills", _skill_from_dict),
        experience=_nested_list(data, "experience", _experience_from_dict),
        education=_nested_list(data, "education", _education_from_dict),
        certifications=_nested_list(data, "certifications", _certification_from_dict),
        soft_skill_signals=_nested_list(data, "soft_skill_signals", _soft_skill_from_dict),
        domain_signals=_nested_list(data, "domain_signals", _domain_signal_from_dict),
        total_experience_years=_f(data, "total_experience_years"),
        highest_education_level=_s(data, "highest_education_level", "None"),
        skill_names_normalised=_str_list(data, "skill_names_normalised"),
        extractor_version=_s(data, "extractor_version", "0.0.0"),
        extraction_method=_s(data, "extraction_method", "rule_based_v1"),
        extraction_warnings=_str_list(data, "extraction_warnings"),
    )


def matchresult_to_dict(result: MatchResult) -> dict[str, Any]:
    """Convert a MatchResult dataclass to a JSON-safe dictionary.

    Adds a top-level ``_schema`` key.  All nested CriterionMatch and
    GapCandidate objects are recursively converted via dataclasses.asdict().
    """
    d = dataclasses.asdict(result)
    d["_schema"] = _MATCHRESULT_SCHEMA
    return d


def matchresult_from_dict(data: Any) -> MatchResult:
    """Reconstruct a MatchResult from a previously serialised dict.

    Raises SerialisationError when ``data`` is not a dict.
    CriterionMatch and GapCandidate lists are reconstructed explicitly so
    nested type safety is maintained.
    """
    if not isinstance(data, dict):
        raise SerialisationError(
            f"matchresult_from_dict expects a dict, got {type(data).__name__!r}"
        )
    return MatchResult(
        application_id=_s(data, "application_id"),
        job_id=_s(data, "job_id"),
        criteria_version=_s(data, "criteria_version"),
        matches=_nested_list(data, "matches", _criterion_match_from_dict),
        gap_candidates=_nested_list(data, "gap_candidates", _gap_candidate_from_dict),
        required_match_pct=_f(data, "required_match_pct"),
        preferred_match_pct=_f(data, "preferred_match_pct"),
        partial_match_pct=_f(data, "partial_match_pct"),
        blocking_gap_count=_i(data, "blocking_gap_count"),
        algorithmic_scores=_dict_of_float(data, "algorithmic_scores"),
        matcher_version=_s(data, "matcher_version", "0.0.0"),
        matching_method_summary=_dict_of_int(data, "matching_method_summary"),
    )


# ── Layer 3: LLMMatchResult serialisation ─────────────────────────────────────

_LLM_MATCHRESULT_SCHEMA: str = "llm_match_result_v1"


def _llm_assessment_from_dict(d: dict) -> LLMCriterionAssessment:
    return LLMCriterionAssessment(
        criterion_text=_s(d, "criterion_text"),
        dimension=_s(d, "dimension", "other"),
        required=_b(d, "required", True),
        status=_s(d, "status", "ABSENT"),
        confidence=_f(d, "confidence"),
        supporting_evidence=_str_list(d, "supporting_evidence"),
        match_reason=_s(d, "match_reason"),
        match_type=_s(d, "match_type", "missing"),
        criterion_class=_s(d, "criterion_class", "other"),
        risk_flags=_str_list(d, "risk_flags"),
        prompt_code=_s(d, "prompt_code"),
        prompt_version=_s(d, "prompt_version"),
        llm_model=_s(d, "llm_model"),
    )


def llm_matchresult_to_dict(result: LLMMatchResult) -> dict[str, Any]:
    """Convert an LLMMatchResult dataclass to a JSON-safe dictionary.

    Adds a top-level ``_schema`` key (``llm_match_result_v1``) so consumers
    can detect the format version.  All nested LLMCriterionAssessment objects
    are recursively converted via dataclasses.asdict().
    """
    d = dataclasses.asdict(result)
    d["_schema"] = _LLM_MATCHRESULT_SCHEMA
    return d


def llm_matchresult_from_dict(data: Any) -> LLMMatchResult:
    """Reconstruct an LLMMatchResult from a previously serialised dict.

    Raises SerialisationError when ``data`` is not a dict.
    Missing or wrong-typed fields fall back to their dataclass defaults.
    """
    if not isinstance(data, dict):
        raise SerialisationError(
            f"llm_matchresult_from_dict expects a dict, got {type(data).__name__!r}"
        )
    return LLMMatchResult(
        application_id=_s(data, "application_id"),
        job_id=_s(data, "job_id"),
        assessments=_nested_list(data, "assessments", _llm_assessment_from_dict),
        processing_ms=_i(data, "processing_ms"),
        created_at=_s(data, "created_at"),
        prompt_code=_s(data, "prompt_code"),
        prompt_version=_s(data, "prompt_version"),
        model=_s(data, "model"),
        mapper_version=_s(data, "mapper_version", "0.0.0"),
        total_criteria=_i(data, "total_criteria"),
        matched_count=_i(data, "matched_count"),
        partial_count=_i(data, "partial_count"),
        absent_count=_i(data, "absent_count"),
        high_confidence_count=_i(data, "high_confidence_count"),
        low_confidence_count=_i(data, "low_confidence_count"),
    )
