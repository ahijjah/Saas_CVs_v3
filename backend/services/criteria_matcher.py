"""
Layer 2 — Criteria Matching dataclasses and CriteriaMatchEngine.

Dataclasses (Batch 2A-1): CriterionMatch, GapCandidate, MatchResult.
CriteriaMatchEngine (Batch 2A-5): rule-based matching of CVFacts against
analysis_json job criteria.

No LLM calls, no DB access, no scoring changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Optional

from snowballstemmer import EnglishStemmer

from services.cv_evidence import (
    CVFacts,
    EDUCATION_LEVELS,
    MatchMethod,
)

logger = logging.getLogger(__name__)


# ── Type aliases ──────────────────────────────────────────────────────────────

MatchStatus = Literal["MATCHED", "PARTIAL", "ABSENT"]
GapSeverity = Literal["BLOCKING", "SIGNIFICANT", "MINOR"]
Dimension = Literal[
    "skills",
    "experience",
    "education",
    "certifications",
    "soft_skills",
    "domain_knowledge",
    "other",
]


# ── Criterion-level results ───────────────────────────────────────────────────

@dataclass
class CriterionMatch:
    """Result of matching a single job criterion against CVFacts.

    One CriterionMatch is produced for every item in the job criteria
    (required skills, preferred skills, experience bullets, education
    requirements, certifications, domain keywords, other requirements).

    status semantics:
      MATCHED  — strong evidence found (confidence ≥ 0.60).
      PARTIAL  — weak or inferred evidence only (0.20 ≤ confidence < 0.60).
      ABSENT   — no credible evidence found (confidence < 0.20).
    """

    # ── Criterion identity ────────────────────────────────────────────────
    criterion_text: str
    """Exact criterion text from analysis_json (e.g. 'Microsoft Excel')."""

    dimension: Dimension
    """Which scoring dimension this criterion belongs to."""

    required: bool
    """True = mandatory criterion (from analysis_json.skills.required or
    equivalent).  False = preferred / nice-to-have."""

    # ── Match verdict ─────────────────────────────────────────────────────
    status: MatchStatus
    """MATCHED | PARTIAL | ABSENT."""

    confidence: float
    """Aggregate confidence 0.0–1.0.
    Computed as: max(evidence_confidence)*0.7 + mean(evidence_confidence)*0.3
    when multiple evidence items exist; equals the single item's confidence
    when only one item is found."""

    match_method: MatchMethod
    """Primary method that produced this match:
      'exact'      — token-for-token match
      'normalised' — variant spelling / abbreviation
      'fuzzy'      — rapidfuzz partial_ratio ≥ 80
      'semantic'   — embedding cosine similarity ≥ 0.72
      'inferred'   — contextual inference
      'absent'     — no match found"""

    # ── Evidence ──────────────────────────────────────────────────────────
    supporting_evidence: list[str] = field(default_factory=list)
    """CV text spans that support the match (≤200 chars each)."""

    evidence_confidence: list[float] = field(default_factory=list)
    """Per-item confidence score, parallel to supporting_evidence."""

    # ── Partial match explanation ─────────────────────────────────────────
    partial_reason: str = ""
    """Human-readable explanation of a PARTIAL match.
    E.g. '2 of 5 required years found' or 'related field, not exact match'."""

    # ── Cross-lingual metadata ────────────────────────────────────────────
    matched_via_translation: bool = False
    """True when the criterion was matched through the bilingual skill map
    (e.g. Arabic CV term matched to an English criterion)."""

    original_cv_term: str = ""
    """The raw CV term that produced the match, before normalisation.
    E.g. 'إكسل' matched to 'Microsoft Excel'."""


@dataclass
class GapCandidate:
    """A criterion that is ABSENT or PARTIAL — a potential scoring gap.

    GapCandidates are derived from CriterionMatch results by the
    CriteriaMatchEngine and are the structured predecessor to the current
    gaps_identified text list in the AI result.

    The suppressed flag indicates that compensating_evidence from other
    dimensions is strong enough to offset the gap.  Suppressed gaps are
    not shown to recruiters but are retained in the audit record.
    """

    criterion: CriterionMatch
    """The underlying CriterionMatch that produced this gap."""

    severity: GapSeverity
    """BLOCKING   — mandatory criterion, ABSENT, no credible compensation.
    SIGNIFICANT — mandatory criterion, PARTIAL, or ABSENT with weak compensation.
    MINOR       — preferred criterion ABSENT, or mandatory with strong compensation."""

    compensating_evidence: list[str] = field(default_factory=list)
    """Evidence from other dimensions that partially offsets this gap.
    E.g. 10 years of experience compensating for a missing certification."""

    compensating_confidence: float = 0.0
    """Aggregate confidence of the compensating evidence (0.0–1.0).
    0.0 = no compensation found.  ≥ 0.70 = strong enough to suppress."""

    suppressed: bool = False
    """True when compensating_confidence ≥ 0.70 and severity != BLOCKING.
    Suppressed gaps are excluded from recruiter-facing output."""

    suppression_reason: str = ""
    """Explanation of why the gap is suppressed.
    E.g. '10 years relevant experience compensates for missing PMP cert'."""


# ── Aggregate matching result ─────────────────────────────────────────────────

@dataclass
class MatchResult:
    """Complete Layer 2 output for a single application against a job.

    One MatchResult is produced per scoring run and persisted to
    application_scores.match_results_json.

    In Phase 2A this is stored silently alongside the existing LLM scoring
    result and has no effect on recruiter-facing output.  In Phase 3 it will
    provide the algorithmic_scores baseline that bounds LLM scoring deltas.
    """

    # ── Input references ──────────────────────────────────────────────────
    application_id: str
    """UUID of the application being scored."""

    job_id: str
    """UUID of the job the application is being scored against."""

    criteria_version: str
    """SHA-256 hash (first 16 chars) of the serialised criteria dict.
    Used to detect stale cached MatchResults when criteria are edited."""

    # ── Criterion-level results ───────────────────────────────────────────
    matches: list[CriterionMatch] = field(default_factory=list)
    """One CriterionMatch per criterion in the job criteria."""

    gap_candidates: list[GapCandidate] = field(default_factory=list)
    """Gaps derived from ABSENT/PARTIAL matches, ordered by severity."""

    # ── Aggregate statistics ──────────────────────────────────────────────
    required_match_pct: float = 0.0
    """Percentage of mandatory criteria with status MATCHED (0.0–100.0)."""

    preferred_match_pct: float = 0.0
    """Percentage of preferred criteria with status MATCHED (0.0–100.0)."""

    partial_match_pct: float = 0.0
    """Percentage of all criteria with status PARTIAL (0.0–100.0)."""

    blocking_gap_count: int = 0
    """Number of mandatory criteria with status ABSENT and no compensation."""

    # ── Algorithmic score baseline ─────────────────────────────────────────
    algorithmic_scores: dict[str, float] = field(default_factory=dict)
    """Per-dimension algorithmic score 0.0–100.0, computed from CriterionMatch
    results.  Used in Phase 3 as the floor/anchor for LLM scoring deltas.
    Keys: 'skills', 'experience', 'education', 'certifications',
          'soft_skills', 'domain_knowledge', 'other'."""

    # ── Metadata ──────────────────────────────────────────────────────────
    matcher_version: str = "0.0.0"
    """Semver of the CriteriaMatchEngine that produced this record."""

    matching_method_summary: dict[str, int] = field(default_factory=dict)
    """Count of each MatchMethod used: {'exact': 3, 'fuzzy': 2, 'absent': 1}."""


# ── CriteriaMatchEngine — Batch 2A-5 ─────────────────────────────────────────

_MATCHER_VERSION = "1.1.0"

# All dimensions that always appear in algorithmic_scores.
_ALL_DIMENSIONS: tuple[str, ...] = (
    "skills", "experience", "education", "certifications",
    "soft_skills", "domain_knowledge", "other",
)

# ---------------------------------------------------------------------------
# Skill synonym map
# Keys and values are lowercase.  Maps common abbreviations/variants and
# Arabic terms to the canonical normalised form used in CVFacts.skill_names.
# ---------------------------------------------------------------------------
_SKILL_SYNONYMS: dict[str, str] = {
    # Microsoft Office short forms
    "excel":                    "microsoft excel",
    "ms excel":                 "microsoft excel",
    "إكسل":                     "microsoft excel",
    "word":                     "microsoft word",
    "ms word":                  "microsoft word",
    "وورد":                     "microsoft word",
    "powerpoint":               "microsoft powerpoint",
    "ppt":                      "microsoft powerpoint",
    "ms powerpoint":            "microsoft powerpoint",
    "باوربوينت":                "microsoft powerpoint",
    "ms office":                "microsoft office",
    "office 365":               "microsoft office",
    "office365":                "microsoft office",
    # Database
    "postgres":                 "postgresql",
    "mongo":                    "mongodb",
    "mssql":                    "sql server",
    # Cloud
    "gcp":                      "google cloud",
    "amazon web services":      "aws",
    # JavaScript ecosystem
    "react.js":                 "react",
    "reactjs":                  "react",
    "ريأكت":                    "react",
    "vue.js":                   "vue.js",
    "vuejs":                    "vue.js",
    "node.js":                  "node.js",
    "nodejs":                   "node.js",
    "next.js":                  "next.js",
    "nextjs":                   "next.js",
    # Data science
    "sklearn":                  "scikit-learn",
    "pyspark":                  "apache spark",
    # DevOps
    "k8s":                      "kubernetes",
    # Arabic programming terms
    "بايثون":                   "python",
    "جافا":                     "java",
    "جافاسكريبت":               "javascript",
    "فوتوشوب":                  "adobe photoshop",
    "قواعد البيانات":           "sql",
}

# Broad / umbrella skill criteria → list of specific CV skills that satisfy them.
# When a criterion matches a key (exact substring), the engine checks whether the
# CV contains any of the listed constituent skills.  Only skills genuinely implied
# by "computer literacy" are included; Python / Docker are NOT (see test).
_BROAD_SKILL_MAP: dict[str, list[str]] = {
    "computer literacy": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "microsoft outlook", "office 365",
        "ms office", "erp", "crm", "sap", "oracle database",
        "sharepoint", "google sheets", "sql",
    ],
    "computer skills": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "erp", "crm", "sap", "sql",
    ],
    "it skills": [
        "microsoft office", "microsoft excel", "microsoft word",
        "erp", "crm", "sap",
    ],
    "digital literacy": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "erp", "crm",
    ],
    "office suite": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "microsoft outlook",
    ],
    "ms office proficiency": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "microsoft outlook",
    ],
    "proficiency in microsoft office": [
        "microsoft office", "microsoft excel", "microsoft word",
        "microsoft powerpoint", "microsoft outlook",
    ],
}

# Soft skill keywords to scan in criteria text → category label.
_SOFT_SKILL_INDICATORS: dict[str, list[str]] = {
    "leadership":      ["leadership", "team management", "supervise", "manage team", "manage staff"],
    "communication":   ["communication", "presentation", "report writing", "liaison"],
    "teamwork":        ["teamwork", "collaboration", "cross-functional", "work with team"],
    "problem_solving": ["problem solving", "analytical", "troubleshooting", "critical thinking"],
    "time_management": ["time management", "deadline", "multitasking", "prioritis", "prioritiz"],
    "adaptability":    ["adaptability", "flexible", "fast-paced", "adapt"],
}

# Maps requirement keywords to CV evidence terms that satisfy them when a
# direct fuzzy match falls below threshold.  Used in _match_other_requirements.
_REQUIREMENT_EVIDENCE_MAP: dict[str, list[str]] = {
    "confidential":    ["confidentiality", "information security", "data protection", "compliance"],
    "non-disclosure":  ["confidentiality", "information security", "non-disclosure"],
    "privacy":         ["confidentiality", "data protection", "information security"],
    "data protection": ["data protection", "information security", "compliance", "confidentiality"],
    "compliance":      ["compliance", "information security", "data protection"],
    "reporting":       ["reporting", "data analysis", "documentation"],
    "data management": ["data management", "records management", "database", "data entry"],
    "customer":        ["customer service", "customer", "client"],
    "banking":         ["banking", "finance", "financial services"],
    "archiving":       ["archiving", "records management", "document control", "filing system"],
}

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace — keeps Arabic chars and symbols."""
    return _WS_RE.sub(" ", text.lower().strip())


_STEMMER = EnglishStemmer()


def _stem_word(word: str) -> str:
    """Apply Porter stemming to a single word. Used for domain-keyword matching.

    Handles word-form variants (e.g., support/supported/supporting) so that
    keyword intersection works across different tenses and aspects.
    Only used for the domain-keyword intersection check, not for fuzzy matching.
    """
    return _STEMMER.stemWord(word)


def _canonicalize(text: str) -> str:
    """Normalize then apply synonym expansion."""
    norm = _normalize_text(text)
    return _SKILL_SYNONYMS.get(norm, norm)


def _confidence_to_status(confidence: float) -> "MatchStatus":
    if confidence >= 0.60:
        return "MATCHED"
    if confidence >= 0.20:
        return "PARTIAL"
    return "ABSENT"


def _criteria_version(criteria: dict) -> str:
    canonical = json.dumps(criteria, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _method_summary(matches: list["CriterionMatch"]) -> dict[str, int]:
    return dict(Counter(m.match_method for m in matches))


def _compute_stats(
    matches: list["CriterionMatch"],
) -> tuple[float, float, float, int]:
    """Returns (required_match_pct, preferred_match_pct, partial_match_pct, blocking_gap_count)."""
    required = [m for m in matches if m.required]
    preferred = [m for m in matches if not m.required]

    req_matched = sum(1 for m in required if m.status == "MATCHED")
    req_pct = round(req_matched / len(required) * 100, 1) if required else 0.0

    pref_matched = sum(1 for m in preferred if m.status == "MATCHED")
    pref_pct = round(pref_matched / len(preferred) * 100, 1) if preferred else 0.0

    partial = sum(1 for m in matches if m.status == "PARTIAL")
    partial_pct = round(partial / len(matches) * 100, 1) if matches else 0.0

    blocking = sum(1 for m in required if m.status == "ABSENT")
    return req_pct, pref_pct, partial_pct, blocking


def _build_gap_candidates(
    matches: list["CriterionMatch"],
) -> list["GapCandidate"]:
    _ORDER = {"BLOCKING": 0, "SIGNIFICANT": 1, "MINOR": 2}
    gaps: list[GapCandidate] = []

    for m in matches:
        if m.status == "ABSENT":
            severity: GapSeverity = "BLOCKING" if m.required else "MINOR"
            gaps.append(GapCandidate(criterion=m, severity=severity))
        elif m.status == "PARTIAL":
            severity = "SIGNIFICANT" if m.required else "MINOR"
            gaps.append(GapCandidate(criterion=m, severity=severity))

    gaps.sort(key=lambda g: _ORDER.get(g.severity, 3))
    return gaps


def _compute_algorithmic_scores(
    matches: list["CriterionMatch"],
) -> dict[str, float]:
    """Weighted average confidence per dimension, scaled to 0–100.

    Required criteria carry weight 2; preferred carry weight 1.
    All seven standard dimensions always appear in the result.
    """
    by_dim: dict[str, list[CriterionMatch]] = {}
    for m in matches:
        by_dim.setdefault(m.dimension, []).append(m)

    scores: dict[str, float] = {}
    for dim, dim_matches in by_dim.items():
        total_w = sum(2.0 if m.required else 1.0 for m in dim_matches)
        if total_w == 0:
            scores[dim] = 0.0
        else:
            weighted = sum(
                m.confidence * (2.0 if m.required else 1.0)
                for m in dim_matches
            )
            scores[dim] = round(weighted / total_w * 100, 1)

    for dim in _ALL_DIMENSIONS:
        scores.setdefault(dim, 0.0)

    return scores


# ---------------------------------------------------------------------------
# Dimension-specific matching functions
# ---------------------------------------------------------------------------

def _match_skill_criterion(
    criterion: str,
    cv_facts: CVFacts,
    required: bool,
) -> "CriterionMatch":
    """Match one skill criterion against all CVFacts skills."""
    crit_canonical = _canonicalize(criterion)

    # Build canonical → SkillEvidence lookup from CVFacts
    skill_lookup: dict[str, "SkillEvidence"] = {}  # noqa: F821
    from services.cv_evidence import SkillEvidence  # local import to avoid circularity hint
    for ev in cv_facts.skills:
        skill_lookup[_canonicalize(ev.skill_name)] = ev

    # 1. Exact canonical match (handles synonym expansion, e.g. "excel" → "microsoft excel")
    if crit_canonical in skill_lookup:
        ev = skill_lookup[crit_canonical]
        via_translation = (ev.language == "ar")
        return CriterionMatch(
            criterion_text=criterion,
            dimension="skills",
            required=required,
            status=_confidence_to_status(ev.confidence),
            confidence=ev.confidence,
            match_method="exact" if not via_translation else "normalised",
            supporting_evidence=[ev.context_snippet[:200]] if ev.context_snippet else [],
            evidence_confidence=[ev.confidence],
            matched_via_translation=via_translation,
            original_cv_term=ev.raw_text if via_translation else "",
        )

    # 2. Fuzzy match — conservative threshold (85) guards against broad-term false positives
    try:
        from rapidfuzz import fuzz
        best_score = 0
        best_ev = None
        for canonical_name, ev in skill_lookup.items():
            score = fuzz.token_set_ratio(crit_canonical, canonical_name)
            if score > best_score:
                best_score = score
                best_ev = ev
        if best_score >= 85 and best_ev is not None:
            fuzzy_conf = best_ev.confidence * (best_score / 100)
            return CriterionMatch(
                criterion_text=criterion,
                dimension="skills",
                required=required,
                status=_confidence_to_status(fuzzy_conf),
                confidence=round(fuzzy_conf, 3),
                match_method="fuzzy",
                supporting_evidence=[best_ev.context_snippet[:200]] if best_ev.context_snippet else [],
                evidence_confidence=[round(fuzzy_conf, 3)],
            )
    except ImportError:
        pass

    # 3. Broad / umbrella skill matching — e.g. "computer literacy" satisfied by
    #    the presence of any constituent office/ERP skill.
    #    Checked AFTER fuzzy so a direct fuzzy hit takes priority.
    for broad_key, sub_skills in _BROAD_SKILL_MAP.items():
        if broad_key not in crit_canonical and crit_canonical not in broad_key:
            continue
        matched_subs = [s for s in sub_skills if s in skill_lookup]
        if matched_subs:
            # Confidence scales with how many constituent skills were found.
            confidence = round(min(0.80, 0.50 + len(matched_subs) * 0.10), 3)
            ev = skill_lookup[matched_subs[0]]
            return CriterionMatch(
                criterion_text=criterion,
                dimension="skills",
                required=required,
                status=_confidence_to_status(confidence),
                confidence=confidence,
                match_method="inferred",
                supporting_evidence=[ev.context_snippet[:200]] if ev.context_snippet else [matched_subs[0]],
                evidence_confidence=[confidence],
                partial_reason=f"Inferred from: {', '.join(matched_subs[:3])}",
            )
        break  # broad key matched the concept but no sub-skills found → ABSENT

    # 4. Absent
    return CriterionMatch(
        criterion_text=criterion,
        dimension="skills",
        required=required,
        status="ABSENT",
        confidence=0.0,
        match_method="absent",
    )


def _match_experience(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    exp_criteria = criteria.get("experience", {})
    if not isinstance(exp_criteria, dict):
        return []
    min_years = exp_criteria.get("minimum_years", 0)
    if not min_years or min_years <= 0:
        return []

    actual = cv_facts.total_experience_years
    criterion_text = f"Minimum {min_years} years experience"
    requirement_type = exp_criteria.get("requirement_type")
    is_required = requirement_type != "preferred" if requirement_type else True

    # ── Numeric threshold check ───────────────────────────────────────────
    if actual >= min_years:
        numeric_passed = True
        numeric_confidence = 0.90
    elif actual >= min_years * 0.6:
        numeric_passed = False
        ratio = actual / min_years
        numeric_confidence = round(0.30 + ratio * 0.25, 3)
    else:
        numeric_passed = False
        numeric_confidence = round(min(0.15, actual / min_years * 0.30), 3) if actual > 0 else 0.0

    # ── Relevance check: UNCONDITIONAL when job specifies relevant_roles or key_responsibilities
    # Apply whenever there is data to check against, regardless of criterion wording.
    # Fall back to pure numeric only if both are empty/missing.
    key_responsibilities = exp_criteria.get("key_responsibilities", []) or []
    relevant_roles = exp_criteria.get("relevant_roles", []) or []
    has_relevance_data = bool(key_responsibilities or relevant_roles)

    # Bug A: previously gated on `numeric_passed` alone, so a date-less CV
    # (actual == 0.0, e.g. extracted via the dateless fallback) never even
    # reached the relevance check — it fell straight to the numeric-only
    # fallback below and landed on a flat ABSENT/0.0, regardless of how
    # relevant the candidate's actual role/responsibility text was. Opening
    # this to `actual == 0.0` as well lets a genuinely relevant date-less
    # candidate earn partial credit instead of being zeroed out solely for
    # lacking a parseable date range. Scoped to exactly `actual == 0.0` (not
    # "insufficient years") — that's the case this was root-caused against;
    # the insufficient-but-nonzero-years case is unchanged, existing behavior.
    is_dateless = actual == 0.0
    if has_relevance_data and (numeric_passed or is_dateless):
        # Build candidate's text pool from roles and responsibilities
        candidate_texts = []
        for exp_entry in cv_facts.experience:
            if exp_entry.role_title:
                candidate_texts.append(_normalize_text(exp_entry.role_title))
            for resp in exp_entry.responsibilities:
                candidate_texts.append(_normalize_text(resp))

        # Combine job's relevant_roles and key_responsibilities for comparison
        job_requirements = list(relevant_roles) + list(key_responsibilities)

        # Check for meaningful overlap: require either:
        #   A) High fuzzy match (65%+) AND domain-keyword intersection, OR
        #   B) Direct domain keyword overlap (e.g., both mention "HR", "recruitment", etc.)
        #
        # LIMITATION (known, accepted for current scope):
        # This is a terminology-dependent heuristic, NOT semantic matching.
        # It correctly filters same-role/different-domain false positives
        # (e.g., "Laboratory Coordinator" vs "HR Coordinator") when both texts
        # explicitly share a known keyword. However, it can silently pass through
        # unrelated candidates via the 85% fuzzy-score fallback for domains/synonyms
        # outside this keyword list (e.g., "accountant" vs "accounting" don't match
        # as tokens, but fuzzy score is high enough). If stricter cross-domain
        # filtering becomes critical, consider extending the compute_semantic_similarity()
        # approach used for soft skills (Mechanism B) here as a future improvement.
        #
        # FIX (Bug H): Domain keywords now use Porter stemming for token matching
        # to handle word-form variants (e.g., "support" matches "supported", "supporting").
        # Stemming is applied ONLY to the keyword intersection check; full text passed
        # to fuzzy matching is unchanged.
        has_relevance = False
        # Domain keywords: used to disambiguate roles with same generic titles
        # (e.g., "Manager" in HR vs Accounting; "Coordinator" in logistics vs HR).
        # Supports ~25 major business domains. When a job requirement and candidate
        # role share a domain keyword, fuzzy matching uses a lower threshold (65%)
        # because context is clear. When neither has keywords, threshold is 85%
        # (higher bar to avoid false positives like "Laboratory Coordinator" vs "HR Coordinator").
        domain_keywords = {
            # People/Organization
            "hr", "recruitment", "hiring", "staffing", "payroll", "employee",
            "training", "development", "organizational", "culture", "talent",
            # Finance/Accounting
            "finance", "accounting", "audit", "tax", "budget", "investment",
            "banking", "treasury", "credit", "loan", "mortgage", "forecasting",
            # Sales/Marketing
            "sales", "marketing", "advertising", "brand", "campaign", "customer acquisition",
            "lead generation", "account management", "business development",
            # Operations/Logistics
            "operations", "logistics", "supply chain", "procurement", "inventory",
            "warehouse", "distribution", "manufacturing", "production", "quality",
            # IT/Technology
            "it", "ict", "developer", "developer", "engineering", "infrastructure",
            "database", "network", "security", "software", "devops", "cloud",
            # Customer-facing
            "customer service", "support", "customer success", "call center",
            "helpdesk", "technical support", "onboarding",
            # Legal/Compliance
            "legal", "compliance", "risk", "audit", "governance", "regulatory",
            "contract", "litigation", "attorney",
            # Healthcare
            "healthcare", "medical", "clinical", "nursing", "pharmacy", "dentistry",
            "therapy", "patient", "hospital", "physician",
            # Education
            "education", "teaching", "training", "academic", "curriculum", "instructor",
            # Construction/Real Estate
            "construction", "real estate", "architecture", "engineering", "property",
            "real estate", "property management",
            # Data/Analytics
            "data", "analytics", "business intelligence", "reporting", "statistical",
            "data science", "machine learning",
        }
        # Pre-stem domain keywords: convert each keyword (or its tokens for multi-word)
        # into stemmed form for consistent intersection checks
        stemmed_keywords = set()
        for kw in domain_keywords:
            # Multi-word phrases: stem each word and rejoin
            if " " in kw:
                stemmed_kw = " ".join(_stem_word(w) for w in kw.split())
            else:
                stemmed_kw = _stem_word(kw)
            stemmed_keywords.add(stemmed_kw)

        if candidate_texts and job_requirements:
            try:
                from rapidfuzz import fuzz
                for job_req in job_requirements:
                    job_norm = _normalize_text(job_req)
                    # Stem tokens before keyword intersection check
                    job_tokens_stemmed = {_stem_word(t) for t in job_norm.split()}
                    job_keywords = job_tokens_stemmed & stemmed_keywords

                    for cand_text in candidate_texts:
                        # Stem tokens before keyword intersection check
                        cand_tokens_stemmed = {_stem_word(t) for t in cand_text.split()}
                        cand_keywords = cand_tokens_stemmed & stemmed_keywords

                        # Check for domain keyword intersection
                        if job_keywords and cand_keywords and (job_keywords & cand_keywords):
                            # Both have domain keywords and they overlap — strong signal
                            score = fuzz.token_set_ratio(job_norm, cand_text)
                            if score >= 65:
                                has_relevance = True
                                break
                        # Fallback: very high fuzzy score alone (90%+) suggests domain match
                        elif not job_keywords and not cand_keywords:
                            # Neither has domain keyword; use lower threshold for generic terms
                            score = fuzz.token_set_ratio(job_norm, cand_text)
                            if score >= 85:  # Higher bar when no domain keywords present
                                has_relevance = True
                                break

                    if has_relevance:
                        break
            except ImportError:
                # Fallback: simple token overlap with domain keyword check
                for job_req in job_requirements:
                    job_tokens = set(_normalize_text(job_req).split())
                    # Stem tokens before keyword intersection check
                    job_tokens_stemmed = {_stem_word(t) for t in job_tokens}
                    job_keywords = job_tokens_stemmed & stemmed_keywords

                    for cand_text in candidate_texts:
                        cand_tokens = set(cand_text.split())
                        # Stem tokens before keyword intersection check
                        cand_tokens_stemmed = {_stem_word(t) for t in cand_tokens}
                        cand_keywords = cand_tokens_stemmed & stemmed_keywords

                        if job_keywords and cand_keywords and (job_keywords & cand_keywords):
                            overlap = len(job_tokens & cand_tokens)
                            if overlap >= 2:
                                has_relevance = True
                                break

                    if has_relevance:
                        break

        if not has_relevance:
            if numeric_passed:
                # Sufficient years, but role/responsibility text doesn't look
                # relevant — downgrade to PARTIAL even though duration passed.
                status = "PARTIAL"
                confidence = 0.45
                partial_reason = f"{actual:.0f} years total experience, but relevance to role not verified"
                evidence = [f"{actual:.1f} years total experience extracted from CV"]
            else:
                # Date-less AND no relevance found either — no positive signal
                # in either dimension, stay at the existing numeric floor
                # (0.0 when actual == 0.0) rather than inventing one.
                status = "ABSENT"
                confidence = numeric_confidence
                partial_reason = "No verifiable years and no clear role relevance found in CV"
                evidence = []
            return [CriterionMatch(
                criterion_text=criterion_text,
                dimension="experience",
                required=is_required,
                status=status,
                confidence=confidence,
                match_method="inferred",
                supporting_evidence=evidence,
                evidence_confidence=[confidence] if evidence else [],
                partial_reason=partial_reason,
            )]

        if is_dateless:
            # has_relevance is True here: role/responsibility text genuinely
            # overlaps the job's requirements, but duration couldn't be
            # verified from the CV — partial credit, not a full MATCHED
            # (which requires an actually-verified numeric threshold).
            status = "PARTIAL"
            confidence = 0.55
            partial_reason = "Experience duration not extractable from CV, but role/responsibilities are relevant to this position"
            evidence = ["Relevant experience found (duration not specified in CV)"]
            return [CriterionMatch(
                criterion_text=criterion_text,
                dimension="experience",
                required=is_required,
                status=status,
                confidence=confidence,
                match_method="inferred",
                supporting_evidence=evidence,
                evidence_confidence=[confidence],
                partial_reason=partial_reason,
            )]
        # else: numeric_passed and has_relevance -> fall through to the
        # existing numeric MATCHED path below, unchanged.

    # ── Fallback: pure numeric comparison (no relevance data available) ───
    if numeric_passed:
        status, confidence, partial_reason = "MATCHED", numeric_confidence, ""
    elif actual >= min_years * 0.6:
        status, partial_reason = "PARTIAL", f"{actual:.0f} of {min_years} required years found"
        confidence = numeric_confidence
    else:
        confidence = numeric_confidence
        status = "ABSENT"
        partial_reason = f"{actual:.0f} of {min_years} required years found" if actual > 0 else ""

    evidence = [f"{actual:.1f} years total experience extracted from CV"] if actual > 0 else []
    return [CriterionMatch(
        criterion_text=criterion_text,
        dimension="experience",
        required=is_required,
        status=status,
        confidence=confidence,
        match_method="inferred",
        supporting_evidence=evidence,
        evidence_confidence=[confidence] if evidence else [],
        partial_reason=partial_reason,
    )]


def _match_education(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    """
    Match education criteria against CV facts.

    Unified matching when field_of_study is present: produces ONE criterion combining
    level + field assessment. When field_of_study is absent: produces level-only criterion.

    Unified criterion (when field_of_study required):
    - Criterion text: "Bachelor's degree in Islamic Studies"
    - Status: MATCHED (fuzzy >= 90), PARTIAL (fuzzy < 90), ABSENT (level not met or level met but no CV fields with matched severity)
    - Confidence: continuous based on fuzzy score, with severity multiplier for credit calculation

    Level-only criterion (when field_of_study absent):
    - Criterion text: "Minimum education: Bachelor's"
    - Status: MATCHED (actual >= required), PARTIAL (within 2 levels), ABSENT (below)
    """
    logger.warning("_match_education called with criteria: %s", criteria)
    edu_criteria = criteria.get("education", {})
    if not isinstance(edu_criteria, dict):
        return []
    min_level = edu_criteria.get("minimum_level", "None")
    required_fields = edu_criteria.get("fields_of_study", []) or []

    if not min_level or min_level == "None":
        return []

    matches: list[CriterionMatch] = []
    levels = list(EDUCATION_LEVELS)
    try:
        required_idx = levels.index(min_level)
    except ValueError:
        return []

    highest = cv_facts.highest_education_level
    try:
        actual_idx = levels.index(highest)
    except ValueError:
        actual_idx = 0

    evidence = [e.raw_text[:150] for e in cv_facts.education[:2] if e.raw_text]

    # ── Unified criterion when field_of_study is present ─────────────────────
    if required_fields:
        # Determine level status first
        if actual_idx >= required_idx:
            level_met = True
            level_status = "MATCHED"
        elif actual_idx >= required_idx - 2 and actual_idx > 0:
            level_met = True
            level_status = "PARTIAL"
        else:
            level_met = False
            level_status = "ABSENT"

        # If level not met, unified criterion is ABSENT
        if not level_met:
            matches.append(CriterionMatch(
                criterion_text=_build_education_criterion_text(min_level, required_fields),
                dimension="education",
                required=True,
                status="ABSENT",
                confidence=0.0,
                match_method="absent",
                supporting_evidence=evidence,
                evidence_confidence=[],
                partial_reason=f"CV shows {highest}; {min_level} degree required",
            ))
            logger.warning("_match_education (unified): level not met, returning ABSENT")
            return matches

        # Level met: assess field match
        cv_fields = [_normalize_text(e.field_of_study) for e in cv_facts.education if e.field_of_study]

        if not cv_fields:
            # Level met but no CV field data: unpenalized MATCHED (data gap, not real mismatch)
            matches.append(CriterionMatch(
                criterion_text=_build_education_criterion_text(min_level, required_fields),
                dimension="education",
                required=True,
                status="MATCHED",
                confidence=0.90,
                match_method="exact",
                supporting_evidence=evidence,
                evidence_confidence=[0.90] * len(evidence),
                partial_reason="",
            ))
            logger.warning("_match_education (unified): level met, no CV field data (unpenalized)")
            return matches

        # Level met + CV fields exist: compute field match with continuous scoring
        best_score = 0
        best_match = None
        matched_cv_field = None

        try:
            from rapidfuzz import fuzz
            for req_field in required_fields:
                req_norm = _normalize_text(req_field)
                for cv_field in cv_fields:
                    score = fuzz.token_set_ratio(req_norm, cv_field)
                    if score > best_score:
                        best_score = score
                        best_match = req_field
                        matched_cv_field = cv_field
        except ImportError:
            # Fallback: simple token overlap
            for req_field in required_fields:
                req_tokens = set(_normalize_text(req_field).split())
                for cv_field in cv_fields:
                    cv_tokens = set(cv_field.split())
                    overlap = len(req_tokens & cv_tokens)
                    if overlap > best_score:
                        best_score = overlap
                        best_match = req_field
                        matched_cv_field = cv_field
            best_score = min(100, best_score * 20)  # Normalize to 0-100 scale

        # Unified status/confidence with continuous severity scaling
        # Status: MATCHED if fuzzy >= 90, else PARTIAL (never ABSENT for level-met case)
        status = "MATCHED" if best_score >= 90 else "PARTIAL"

        # Confidence: continuous based on fuzzy score
        # Severity multiplier (for effective_credit): max(0.15, fuzzy/100)
        # Confidence itself: base * multiplier, where base is typically 0.90
        severity_multiplier = max(0.15, best_score / 100.0)
        confidence = 0.90 * severity_multiplier

        # Explain mismatch if PARTIAL
        if status == "PARTIAL":
            if best_score >= 70:
                partial_reason = f"Degree level satisfied but field only partially aligned: CV shows {matched_cv_field}; {best_match} preferred (match: {best_score}%)"
            else:
                partial_reason = f"Degree level satisfied but in an unrelated field: CV shows {matched_cv_field}; {best_match} required (match: {best_score}%)"
        else:
            partial_reason = ""

        matches.append(CriterionMatch(
            criterion_text=_build_education_criterion_text(min_level, required_fields),
            dimension="education",
            required=True,
            status=status,
            confidence=confidence,
            match_method="exact" if status == "MATCHED" else "fuzzy",
            supporting_evidence=evidence,
            evidence_confidence=[confidence] * len(evidence),
            partial_reason=partial_reason,
            # Store severity_multiplier for downstream use in effective_credit calculation
            # (not a standard CriterionMatch field, but added as needed for scoring)
        ))
        logger.warning("_match_education (unified): status=%s, fuzzy_score=%s, confidence=%s, severity_multiplier=%s",
                      status, best_score, confidence, severity_multiplier)

    else:
        # ── Level-only criterion when field_of_study is absent (existing behavior) ─────────
        if actual_idx >= required_idx:
            level_status, level_confidence, level_reason = "MATCHED", 0.90, ""
            level_method = "exact"
        elif actual_idx >= required_idx - 2 and actual_idx > 0:
            level_status, level_confidence = "PARTIAL", 0.55
            level_reason = f"CV shows {highest}; {min_level} required"
            level_method = "inferred"
        else:
            level_confidence = 0.10 if actual_idx > 0 else 0.0
            level_status = "ABSENT"
            level_reason = f"CV shows {highest}; {min_level} required"
            level_method = "absent" if level_confidence < 0.20 else "inferred"

        matches.append(CriterionMatch(
            criterion_text=f"Minimum education: {min_level}",
            dimension="education",
            required=True,
            status=level_status,
            confidence=level_confidence,
            match_method=level_method,
            supporting_evidence=evidence,
            evidence_confidence=[level_confidence] * len(evidence),
            partial_reason=level_reason,
        ))
        logger.warning("_match_education (level-only): status=%s, confidence=%s", level_status, level_confidence)

    return matches


def _build_education_criterion_text(min_level: str, required_fields: list[str]) -> str:
    """Build unified education criterion text from level and fields.

    Format: "Bachelor's degree in Islamic Studies" or
            "Master's degree in Computer Science, MIS, or Computer Engineering"
    """
    if len(required_fields) == 1:
        return f"{min_level} degree in {required_fields[0]}"
    else:
        # Multiple fields: join with comma, last with "or"
        if len(required_fields) == 2:
            fields_str = f"{required_fields[0]} or {required_fields[1]}"
        else:
            fields_str = f"{', '.join(required_fields[:-1])}, or {required_fields[-1]}"
        return f"{min_level} degree in {fields_str}"


def _match_certifications(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    cert_criteria = criteria.get("certifications", [])
    if not isinstance(cert_criteria, list):
        return []

    cv_certs = cv_facts.certifications
    cv_norm_names = [_normalize_text(c.name) for c in cv_certs]
    matches: list[CriterionMatch] = []

    for cert_criterion in cert_criteria:
        crit_norm = _normalize_text(cert_criterion)

        # 1. Direct normalised match
        if crit_norm in cv_norm_names:
            idx = cv_norm_names.index(crit_norm)
            ev = cv_certs[idx]
            matches.append(CriterionMatch(
                criterion_text=cert_criterion,
                dimension="certifications",
                required=True,
                status="MATCHED",
                confidence=0.90,
                match_method="exact",
                supporting_evidence=[ev.raw_text[:150]] if ev.raw_text else [],
                evidence_confidence=[0.90],
            ))
            continue

        # 2. Fuzzy match
        best_score = 0
        best_raw = ""
        try:
            from rapidfuzz import fuzz
            for i, cv_name in enumerate(cv_norm_names):
                score = fuzz.token_set_ratio(crit_norm, cv_name)
                if score > best_score:
                    best_score = score
                    best_raw = cv_certs[i].raw_text
        except ImportError:
            pass

        if best_score >= 80:
            conf = 0.80 if best_score >= 92 else 0.65
            matches.append(CriterionMatch(
                criterion_text=cert_criterion,
                dimension="certifications",
                required=True,
                status=_confidence_to_status(conf),
                confidence=conf,
                match_method="fuzzy",
                supporting_evidence=[best_raw[:150]] if best_raw else [],
                evidence_confidence=[conf],
            ))
        else:
            matches.append(CriterionMatch(
                criterion_text=cert_criterion,
                dimension="certifications",
                required=True,
                status="ABSENT",
                confidence=0.0,
                match_method="absent",
            ))

    return matches


def _match_domain_knowledge(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    domain_criteria = criteria.get("domain_knowledge", [])
    if not isinstance(domain_criteria, list):
        return []

    cv_domains = {_normalize_text(d.domain_term): d for d in cv_facts.domain_signals}
    matches: list[CriterionMatch] = []

    for domain_criterion in domain_criteria:
        crit_norm = _normalize_text(domain_criterion)

        if crit_norm in cv_domains:
            sig = cv_domains[crit_norm]
            snippet = sig.context_snippets[0] if sig.context_snippets else ""
            matches.append(CriterionMatch(
                criterion_text=domain_criterion,
                dimension="domain_knowledge",
                required=True,
                status="MATCHED",
                confidence=0.85,
                match_method="exact",
                supporting_evidence=[snippet[:150]] if snippet else [],
                evidence_confidence=[0.85],
            ))
            continue

        # Fuzzy fallback
        best_score = 0
        best_sig = None
        try:
            from rapidfuzz import fuzz
            for term, sig in cv_domains.items():
                score = fuzz.token_set_ratio(crit_norm, term)
                if score > best_score:
                    best_score = score
                    best_sig = sig
        except ImportError:
            pass

        if best_score >= 75 and best_sig is not None:
            conf = 0.72 if best_score >= 88 else 0.55
            snippet = best_sig.context_snippets[0] if best_sig.context_snippets else ""
            matches.append(CriterionMatch(
                criterion_text=domain_criterion,
                dimension="domain_knowledge",
                required=True,
                status=_confidence_to_status(conf),
                confidence=conf,
                match_method="fuzzy",
                supporting_evidence=[snippet[:150]] if snippet else [],
                evidence_confidence=[conf],
            ))
        else:
            matches.append(CriterionMatch(
                criterion_text=domain_criterion,
                dimension="domain_knowledge",
                required=True,
                status="ABSENT",
                confidence=0.0,
                match_method="absent",
            ))

    return matches


def _match_soft_skills(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    """Match soft skills from criteria requirements + CV-detected signals.

    Phase 1: criteria-driven — scan key_responsibilities / other_requirements
    for soft skill keywords and produce a match for each category found.

    Phase 2: signal-based fallback — always credit soft skill signals detected
    in CVFacts even when the criteria don't explicitly name them.  This ensures
    the soft_skills algorithmic score is non-zero whenever the extractor found
    evidence (e.g. communication signals).  All fallback matches are preferred
    (required=False) so they never contribute to blocking_gap_count.
    """
    exp_block = criteria.get("experience", {})
    key_responsibilities: list[str] = (
        exp_block.get("key_responsibilities", []) if isinstance(exp_block, dict) else []
    )
    other_req: list[str] = criteria.get("other_requirements", []) or []
    combined_text = " ".join(key_responsibilities + other_req).lower()

    cv_soft = {s.soft_skill_category: s for s in cv_facts.soft_skill_signals}
    matches: list[CriterionMatch] = []
    covered_categories: set[str] = set()

    # Phase 1 — criteria-driven matching
    if combined_text.strip():
        for category, keywords in _SOFT_SKILL_INDICATORS.items():
            if not any(kw in combined_text for kw in keywords):
                continue
            covered_categories.add(category)
            criterion_text = f"{category.replace('_', ' ').title()} skills"
            if category in cv_soft:
                ev = cv_soft[category]
                matches.append(CriterionMatch(
                    criterion_text=criterion_text,
                    dimension="soft_skills",
                    required=False,
                    status=_confidence_to_status(ev.confidence),
                    confidence=ev.confidence,
                    match_method="inferred",
                    supporting_evidence=[ev.evidence_phrase[:150]] if ev.evidence_phrase else [],
                    evidence_confidence=[ev.confidence],
                ))
            else:
                matches.append(CriterionMatch(
                    criterion_text=criterion_text,
                    dimension="soft_skills",
                    required=False,
                    status="ABSENT",
                    confidence=0.0,
                    match_method="absent",
                ))

    # Phase 2 — signal-based fallback (for categories not yet covered above)
    for sig in cv_facts.soft_skill_signals:
        if sig.soft_skill_category in covered_categories:
            continue
        matches.append(CriterionMatch(
            criterion_text=f"{sig.soft_skill_category.replace('_', ' ').title()} skills",
            dimension="soft_skills",
            required=False,
            status=_confidence_to_status(sig.confidence),
            confidence=sig.confidence,
            match_method="inferred",
            supporting_evidence=[sig.evidence_phrase[:150]] if sig.evidence_phrase else [],
            evidence_confidence=[sig.confidence],
        ))

    return matches


def _match_other_requirements(
    cv_facts: CVFacts,
    criteria: dict,
) -> list["CriterionMatch"]:
    other_criteria = criteria.get("other_requirements", [])
    if not isinstance(other_criteria, list) or not other_criteria:
        return []

    # Build token pool from all CV evidence for best-effort matching
    cv_pool = set()
    cv_pool.update(_normalize_text(s) for s in cv_facts.skill_names_normalised)
    cv_pool.update(_normalize_text(d.domain_term) for d in cv_facts.domain_signals)
    cv_pool.update(_normalize_text(c.name) for c in cv_facts.certifications)
    cv_pool.discard("")

    matches: list[CriterionMatch] = []

    for req in other_criteria:
        req_norm = _normalize_text(req)
        best_score = 0
        try:
            from rapidfuzz import fuzz
            for item in cv_pool:
                score = fuzz.token_set_ratio(req_norm, item)
                if score > best_score:
                    best_score = score
        except ImportError:
            pass

        if best_score >= 75:
            conf = round(min(0.70, best_score / 100 * 0.82), 3)
            matches.append(CriterionMatch(
                criterion_text=req,
                dimension="other",
                required=True,
                status=_confidence_to_status(conf),
                confidence=conf,
                match_method="fuzzy",
            ))
            continue

        # Transferable evidence: check if any requirement keyword maps to
        # CV evidence terms that are present in cv_pool.
        transferred_ev: str = ""
        for ev_key, ev_terms in _REQUIREMENT_EVIDENCE_MAP.items():
            if ev_key not in req_norm:
                continue
            for ev_term in ev_terms:
                if _normalize_text(ev_term) in cv_pool:
                    transferred_ev = ev_term
                    break
            if transferred_ev:
                break

        if transferred_ev:
            conf = 0.55  # moderate confidence — inferred, not direct
            matches.append(CriterionMatch(
                criterion_text=req,
                dimension="other",
                required=True,
                status=_confidence_to_status(conf),
                confidence=conf,
                match_method="inferred",
                supporting_evidence=[f"Inferred from: {transferred_ev}"],
                evidence_confidence=[conf],
                partial_reason=f"Transferable evidence: {transferred_ev}",
            ))
        else:
            matches.append(CriterionMatch(
                criterion_text=req,
                dimension="other",
                required=True,
                status="ABSENT",
                confidence=0.0,
                match_method="absent",
            ))

    return matches


# ---------------------------------------------------------------------------
# Public engine class
# ---------------------------------------------------------------------------

class CriteriaMatchEngine:
    """Rule-based engine that matches a CVFacts object against job criteria.

    Input:  CVFacts (from CVFactsExtractor) + analysis_json-compatible criteria dict.
    Output: MatchResult with per-criterion verdicts, gap candidates, and
            algorithmic_scores per dimension.

    No LLM calls.  No DB access.  No changes to existing scoring flow.
    Safe to call multiple times — all state is local to ``match()``.
    """

    VERSION = _MATCHER_VERSION

    def match(
        self,
        cv_facts: CVFacts,
        criteria: dict,
        application_id: str = "",
        job_id: str = "",
    ) -> MatchResult:
        """Match CVFacts against job criteria and return a MatchResult.

        Parameters
        ----------
        cv_facts:
            Structured CV evidence from CVFactsExtractor.
        criteria:
            analysis_json-compatible dict with keys:
            ``skills`` (required/preferred lists),
            ``experience`` (minimum_years, key_responsibilities),
            ``education`` (minimum_level),
            ``certifications``, ``domain_knowledge``, ``other_requirements``.
        application_id:
            UUID of the application (stored for traceability).
        job_id:
            UUID of the job (stored for traceability).

        Returns
        -------
        MatchResult
        """
        if not isinstance(criteria, dict):
            criteria = {}

        all_matches: list[CriterionMatch] = []

        # ── Skills ───────────────────────────────────────────────────────
        skills_block = criteria.get("skills", {}) or {}
        required_skills: list[str] = skills_block.get("required", []) or []
        preferred_skills: list[str] = skills_block.get("preferred", []) or []

        for skill in required_skills:
            all_matches.append(_match_skill_criterion(skill, cv_facts, required=True))
        for skill in preferred_skills:
            all_matches.append(_match_skill_criterion(skill, cv_facts, required=False))

        # ── Experience ────────────────────────────────────────────────────
        all_matches.extend(_match_experience(cv_facts, criteria))

        # ── Education ─────────────────────────────────────────────────────
        all_matches.extend(_match_education(cv_facts, criteria))

        # ── Certifications ────────────────────────────────────────────────
        all_matches.extend(_match_certifications(cv_facts, criteria))

        # ── Domain knowledge ──────────────────────────────────────────────
        all_matches.extend(_match_domain_knowledge(cv_facts, criteria))

        # ── Soft skills (inferred from key_responsibilities) ──────────────
        all_matches.extend(_match_soft_skills(cv_facts, criteria))

        # ── Other requirements ────────────────────────────────────────────
        all_matches.extend(_match_other_requirements(cv_facts, criteria))

        # ── Aggregate ─────────────────────────────────────────────────────
        req_pct, pref_pct, partial_pct, blocking = _compute_stats(all_matches)
        gaps = _build_gap_candidates(all_matches)
        algo_scores = _compute_algorithmic_scores(all_matches)
        summary = _method_summary(all_matches)

        return MatchResult(
            application_id=application_id,
            job_id=job_id,
            criteria_version=_criteria_version(criteria),
            matches=all_matches,
            gap_candidates=gaps,
            required_match_pct=req_pct,
            preferred_match_pct=pref_pct,
            partial_match_pct=partial_pct,
            blocking_gap_count=blocking,
            algorithmic_scores=algo_scores,
            matcher_version=self.VERSION,
            matching_method_summary=summary,
        )
