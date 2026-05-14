"""
Local Intelligence Layer — Gatekeeper Pipeline

Runs entirely on-device before any OpenAI API call.
Responsibilities:
  1. Bilingual text cleaning (Arabic + English, UTF-8)
  2. Language detection
  3. Semantic similarity scoring via paraphrase-multilingual-MiniLM-L12-v2
  4. Bilingual fuzzy skill matching via rapidfuzz
  5. Gatekeeper decision: skip LLM if similarity < threshold (cost saving)

The sentence-transformer model is loaded once at process startup and cached
in module scope — no re-loading between requests.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Model singleton ───────────────────────────────────────────────────────────
# Loaded lazily on first use so the FastAPI process starts fast.
# Celery workers call ensure_model_loaded() at startup.
_MODEL = None
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def ensure_model_loaded() -> None:
    """Pre-load the embedding model (call at worker/app startup)."""
    global _MODEL
    if _MODEL is None:
        logger.info("Loading sentence-transformer model: %s", _MODEL_NAME)
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(_MODEL_NAME)
        logger.info("Model loaded successfully.")


def _get_model():
    global _MODEL
    if _MODEL is None:
        ensure_model_loaded()
    return _MODEL


# ── Text cleaning ─────────────────────────────────────────────────────────────

# Arabic Unicode block: 0600–06FF
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟ]")
_EXTRA_WHITESPACE = re.compile(r"\s+")
# Keep Arabic letters, Latin letters, digits, common punctuation
_KEEP_CHARS = re.compile(r"[^؀-ۿݐ-ݿ\w\s.,;:()\-]", re.UNICODE)


def clean_text(raw: str) -> str:
    """
    Normalize and clean bilingual text (Arabic/English/mixed).

    Steps:
      - Unicode NFC normalization
      - Strip Arabic diacritics (tashkeel) — they interfere with embeddings
      - Remove non-content characters (emojis, control chars, etc.)
      - Collapse whitespace
      - Strip leading/trailing whitespace
    """
    if not raw:
        return ""

    # NFC normalization
    text = unicodedata.normalize("NFC", raw)

    # Remove Arabic diacritics
    text = _AR_DIACRITICS.sub("", text)

    # Remove characters outside Arabic + Latin blocks
    text = _KEEP_CHARS.sub(" ", text)

    # Collapse whitespace
    text = _EXTRA_WHITESPACE.sub(" ", text).strip()

    return text


def truncate_for_embedding(text: str, max_chars: int = 8000) -> str:
    """
    Truncate text to fit model token limits.
    MiniLM has a 512-token limit; ~8000 chars is a safe upper bound.
    For longer CVs we take the first 4000 + last 4000 chars (head+tail)
    to capture both the summary section and the most recent experience.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...\n" + text[-half:]


# ── Language detection ────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """Return ISO 639-1 language code ('ar', 'en', 'mixed', 'unknown')."""
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 42  # reproducible
        lang = detect(text[:500])  # sample first 500 chars
        if lang == "ar":
            return "ar"
        if lang in ("en", "en-US", "en-GB"):
            return "en"
        return lang
    except Exception:
        # Heuristic fallback: count Arabic vs Latin characters
        ar_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
        total = max(len(text.replace(" ", "")), 1)
        ratio = ar_chars / total
        if ratio > 0.6:
            return "ar"
        if ratio > 0.2:
            return "mixed"
        return "en"


# ── Semantic similarity ───────────────────────────────────────────────────────

def compute_semantic_similarity(text_a: str, text_b: str) -> float:
    """
    Compute cosine similarity between two texts using the multilingual model.
    Returns a float in [0, 1].

    Both Arabic and English texts are handled natively by MiniLM-L12-v2.
    """
    model = _get_model()

    clean_a = truncate_for_embedding(clean_text(text_a))
    clean_b = truncate_for_embedding(clean_text(text_b))

    if not clean_a or not clean_b:
        return 0.0

    embeddings = model.encode([clean_a, clean_b], normalize_embeddings=True)
    # Cosine similarity of normalized vectors = dot product
    similarity = float(np.dot(embeddings[0], embeddings[1]))
    # Clamp to [0, 1] (can be slightly negative for very dissimilar texts)
    return max(0.0, min(1.0, similarity))


# ── Bilingual skill matching ──────────────────────────────────────────────────

# Common Arabic↔English skill equivalences used to boost matching accuracy
_AR_EN_SKILL_MAP: dict[str, list[str]] = {
    "بايثون": ["python"],
    "جافا": ["java"],
    "جافاسكريبت": ["javascript", "js"],
    "ريأكت": ["react", "reactjs"],
    "إدارة المشاريع": ["project management", "pmp"],
    "تعلم الآلة": ["machine learning", "ml"],
    "الذكاء الاصطناعي": ["artificial intelligence", "ai"],
    "قواعد البيانات": ["database", "sql", "databases"],
    "التسويق الرقمي": ["digital marketing"],
    "خدمة العملاء": ["customer service", "customer support"],
    "المبيعات": ["sales"],
    "القيادة": ["leadership"],
    "العمل الجماعي": ["teamwork", "collaboration"],
    "التواصل": ["communication"],
    "حل المشكلات": ["problem solving"],
    "إكسل": ["excel", "microsoft excel"],
    "ووردبريس": ["wordpress"],
    "أدوبي": ["adobe"],
    "فوتوشوب": ["photoshop"],
}

# Build reverse map (EN→AR) from the same table
_EN_AR_SKILL_MAP: dict[str, str] = {}
for ar, en_list in _AR_EN_SKILL_MAP.items():
    for en in en_list:
        _EN_AR_SKILL_MAP[en] = ar


_EDUCATION_KEYWORDS = [
    "bachelor", "master", "degree", "phd", "doctorate", "university",
    "college", "b.sc", "m.sc", "b.eng", "bsc", "msc", "diploma",
    "graduate", "postgraduate", "engineering", "computer science",
    "information technology", "information systems",
    "بكالوريوس", "ماجستير", "دكتوراه", "شهادة", "جامعة", "كلية", "مؤهل",
]

_EXPERIENCE_YEARS_RE = re.compile(
    r'\b(\d+)\s*(?:\+)?\s*(?:years?|yrs?|سنوات?|عام|أعوام)\b',
    re.IGNORECASE,
)


def _normalize_skill(skill: str) -> str:
    """Lowercase, strip, remove diacritics for comparison."""
    s = _AR_DIACRITICS.sub("", skill.lower().strip())
    return _EXTRA_WHITESPACE.sub(" ", s)


def match_skills_bilingual(
    required_skills: list[str],
    cv_text: str,
    threshold: float = 80.0,
) -> dict:
    """
    Match required skills against CV text using rapidfuzz + Arabic↔English map.

    Returns:
        matched: list of skills found
        missing: list of skills not found
        match_ratio: percentage of required skills found (0-100)
    """
    from rapidfuzz import fuzz, process

    cv_lower = _AR_DIACRITICS.sub("", cv_text.lower())

    matched: list[str] = []
    missing: list[str] = []

    for skill in required_skills:
        norm = _normalize_skill(skill)
        found = False

        # 1. Direct substring check (fastest)
        if norm in cv_lower:
            found = True

        # 2. Fuzzy match against 200-char CV windows (handles OCR noise, typos)
        if not found:
            score = fuzz.partial_ratio(norm, cv_lower)
            if score >= threshold:
                found = True

        # 3. Cross-lingual lookup: if AR skill → check EN synonyms in CV
        if not found and norm in _AR_EN_SKILL_MAP:
            for en_equivalent in _AR_EN_SKILL_MAP[norm]:
                if en_equivalent in cv_lower or fuzz.partial_ratio(en_equivalent, cv_lower) >= threshold:
                    found = True
                    break

        # 4. Cross-lingual reverse: if EN skill → check AR synonyms in CV
        if not found and norm in _EN_AR_SKILL_MAP:
            ar_equivalent = _EN_AR_SKILL_MAP[norm]
            ar_norm = _normalize_skill(ar_equivalent)
            cv_ar = _AR_DIACRITICS.sub("", cv_text)  # keep Arabic chars
            if ar_norm in cv_ar or fuzz.partial_ratio(ar_norm, cv_ar) >= threshold:
                found = True

        (matched if found else missing).append(skill)

    match_ratio = (len(matched) / len(required_skills) * 100) if required_skills else 0.0
    return {
        "matched": matched,
        "missing": missing,
        "match_ratio": round(match_ratio, 1),
    }


def build_criteria_comparison_text(
    skills: list[str] | None = None,
    experience: str | None = None,
    education: str | None = None,
    certifications: list[str] | None = None,
    domain_knowledge: str | None = None,
    other_requirements: str | None = None,
) -> str:
    """Build a concise criteria text for semantic similarity instead of the full JD."""
    parts: list[str] = []
    if skills:
        valid = [s for s in skills if s]
        if valid:
            parts.append("Required skills: " + ", ".join(valid))
    if certifications:
        valid = [c for c in certifications if c]
        if valid:
            parts.append("Required certifications: " + ", ".join(valid))
    if experience:
        parts.append("Experience requirements: " + str(experience).strip())
    if education:
        parts.append("Education requirements: " + str(education).strip())
    if domain_knowledge:
        parts.append("Domain knowledge: " + str(domain_knowledge).strip())
    if other_requirements:
        parts.append("Other requirements: " + str(other_requirements).strip())
    return "\n".join(parts)


def _extract_text_keywords(text: str, max_kw: int = 12) -> list[str]:
    """Extract meaningful keyword phrases from a free-text criteria field."""
    if not text:
        return []
    parts = re.split(r'[,\n;•\-–/|]+', text)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = p.strip()
        if 3 <= len(p) <= 80:
            norm = p.lower()
            if norm not in seen:
                seen.add(norm)
                out.append(p)
                if len(out) >= max_kw:
                    break
    return out


def _match_criteria_keywords(
    cv_text: str,
    skills: list[str] | None = None,
    experience: str | None = None,
    certifications: list[str] | None = None,
    domain_knowledge: str | None = None,
    other_requirements: str | None = None,
    threshold: float = 75.0,
) -> dict:
    """Match CV text against all criteria keyword sources (skills, certs, extracted phrases)."""
    required: list[str] = []
    required += [s for s in (skills or []) if s]
    required += [c for c in (certifications or []) if c]
    required += _extract_text_keywords(experience or "", max_kw=8)
    required += _extract_text_keywords(domain_knowledge or "", max_kw=8)
    required += _extract_text_keywords(other_requirements or "", max_kw=6)

    seen: set[str] = set()
    deduped: list[str] = []
    for kw in required:
        norm = kw.lower()
        if norm not in seen:
            seen.add(norm)
            deduped.append(kw)

    if not deduped:
        return {"matched_keywords": [], "missing_keywords": [], "matched_required_count": 0, "total_required_count": 0}

    result = match_skills_bilingual(deduped, cv_text, threshold)
    return {
        "matched_keywords": result["matched"],
        "missing_keywords": result["missing"],
        "matched_required_count": len(result["matched"]),
        "total_required_count": len(deduped),
    }


def _check_override_rules(
    semantic_sim: float,
    semantic_threshold: float,
    matched_required_count: int,
    total_required_count: int,
    cv_text: str,
    matched_skills: list[str],
) -> tuple[bool, Optional[str]]:
    """
    Check if any override condition allows passing despite low semantic similarity.
    Returns (should_pass, override_reason_or_None).

    Rules:
      1. matched_required_count >= 3 (strong keyword coverage)
      2. >= 2 required skills/certifications matched (strong technical indicators)
      3. CV has education signal + experience years + at least 1 required skill
    """
    reasons: list[str] = []

    if matched_required_count >= 3:
        reasons.append(
            f"matched {matched_required_count}/{total_required_count} required criteria keywords"
        )

    if len(matched_skills) >= 2:
        reasons.append(f"matched {len(matched_skills)} required skills/certifications")

    cv_lower = cv_text.lower()
    has_edu = any(kw in cv_lower for kw in _EDUCATION_KEYWORDS)
    has_exp = bool(_EXPERIENCE_YEARS_RE.search(cv_text))
    has_skill = len(matched_skills) >= 1
    if has_edu and has_exp and has_skill:
        reasons.append("CV demonstrates education background, work experience, and at least 1 required skill")

    if reasons:
        return True, (
            "Passed by keyword override despite low semantic similarity "
            f"({semantic_sim * 100:.1f}% vs {semantic_threshold * 100:.0f}% threshold) — "
            + "; ".join(reasons)
        )
    return False, None


# ── Gatekeeper result ─────────────────────────────────────────────────────────

@dataclass
class GatekeeperResult:
    """Complete output of the local pre-filtering stage."""
    cv_language: str
    jd_language: str
    semantic_similarity: float
    semantic_similarity_pct: float
    skill_match_ratio: float
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    matched_required_count: int = 0
    total_required_count: int = 0
    gatekeeper_passed: bool = True
    rejection_reason: Optional[str] = None
    override_applied: bool = False
    override_reason: Optional[str] = None
    cleaned_cv_text: str = ""
    gatekeeper_reason_json: dict = field(default_factory=dict)


def run_gatekeeper(
    cv_text: str,
    job_description: str,
    required_skills: list[str],
    *,
    semantic_threshold: float = 0.40,
    skill_threshold: float = 80.0,
    criteria_skills: list[str] | None = None,
    criteria_experience: str | None = None,
    criteria_education: str | None = None,
    criteria_certifications: list[str] | None = None,
    criteria_domain_knowledge: str | None = None,
    criteria_other_requirements: str | None = None,
) -> GatekeeperResult:
    """
    Run the full local pre-filtering pipeline.

    Semantic similarity is computed against a concise structured criteria text
    (built from job_criteria fields) AND the full job description; the higher
    of the two is used to reduce false negatives caused by long, noisy JDs.

    Decision logic:
      1. If semantic_sim >= threshold → pass (proceed to LLM)
      2. If semantic_sim < threshold → check three override rules:
         a. matched_required_count >= 3
         b. >= 2 required skills/certifications matched
         c. education signal + experience years + >= 1 required skill
      3. If any override fires → pass with reason logged
      4. If no override fires → reject (mark low_match, skip LLM)
    """
    cleaned_cv = clean_text(cv_text)
    cleaned_jd = clean_text(job_description)

    cv_lang = detect_language(cleaned_cv)
    jd_lang = detect_language(cleaned_jd)

    # Semantic similarity: criteria text (primary) vs full JD (secondary)
    criteria_text = build_criteria_comparison_text(
        skills=criteria_skills,
        experience=criteria_experience,
        education=criteria_education,
        certifications=criteria_certifications,
        domain_knowledge=criteria_domain_knowledge,
        other_requirements=criteria_other_requirements,
    )
    sim_jd = compute_semantic_similarity(cleaned_cv, cleaned_jd)
    if criteria_text:
        sim_criteria = compute_semantic_similarity(cleaned_cv, criteria_text)
        semantic_sim = max(sim_jd, sim_criteria)
    else:
        semantic_sim = sim_jd
    semantic_pct = round(semantic_sim * 100, 2)

    # Skill matching (required_skills = skills + certs)
    skill_result = match_skills_bilingual(required_skills, cleaned_cv, skill_threshold)

    # Keyword matching across all criteria fields (broader set)
    kw_result = _match_criteria_keywords(
        cv_text=cleaned_cv,
        skills=criteria_skills,
        experience=criteria_experience,
        certifications=criteria_certifications,
        domain_knowledge=criteria_domain_knowledge,
        other_requirements=criteria_other_requirements,
        threshold=skill_threshold,
    )
    matched_required_count = kw_result["matched_required_count"]
    total_required_count   = kw_result["total_required_count"]
    matched_keywords       = kw_result["matched_keywords"]
    missing_keywords       = kw_result["missing_keywords"]

    sem_passed = semantic_sim >= semantic_threshold
    override_applied = False
    override_reason: Optional[str] = None
    rejection_reason: Optional[str] = None

    if sem_passed:
        passed = True
    else:
        passed, override_reason = _check_override_rules(
            semantic_sim=semantic_sim,
            semantic_threshold=semantic_threshold,
            matched_required_count=matched_required_count,
            total_required_count=total_required_count,
            cv_text=cleaned_cv,
            matched_skills=skill_result["matched"],
        )
        override_applied = passed
        if not passed:
            rejection_reason = (
                f"Semantic similarity {semantic_pct:.1f}% is below threshold "
                f"{semantic_threshold * 100:.0f}% and no override conditions met "
                f"(matched {matched_required_count}/{total_required_count} required keywords)."
            )

    gk_reason_json = {
        "semantic_similarity_pct":  semantic_pct,
        "semantic_threshold_pct":   round(semantic_threshold * 100, 1),
        "semantic_passed":          sem_passed,
        "matched_required_count":   matched_required_count,
        "total_required_count":     total_required_count,
        "override_applied":         override_applied,
        "override_reason":          override_reason,
        "rejection_reason":         rejection_reason,
        "final_decision":           "passed" if passed else "rejected",
    }

    return GatekeeperResult(
        cv_language=cv_lang,
        jd_language=jd_lang,
        semantic_similarity=semantic_sim,
        semantic_similarity_pct=semantic_pct,
        skill_match_ratio=skill_result["match_ratio"],
        matched_skills=skill_result["matched"],
        missing_skills=skill_result["missing"],
        matched_keywords=matched_keywords,
        missing_keywords=missing_keywords,
        matched_required_count=matched_required_count,
        total_required_count=total_required_count,
        gatekeeper_passed=passed,
        rejection_reason=rejection_reason,
        override_applied=override_applied,
        override_reason=override_reason,
        cleaned_cv_text=cleaned_cv,
        gatekeeper_reason_json=gk_reason_json,
    )
