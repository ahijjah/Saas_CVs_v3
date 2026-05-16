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


# ── Gatekeeper result ─────────────────────────────────────────────────────────

@dataclass
class GatekeeperResult:
    """Complete output of the local pre-filtering stage."""
    cv_language: str                      # 'ar' | 'en' | 'mixed'
    jd_language: str
    semantic_similarity: float            # 0.0 – 1.0
    semantic_similarity_pct: float        # 0.0 – 100.0
    skill_match_ratio: float              # 0.0 – 100.0
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    gatekeeper_passed: bool = True        # False → skip LLM, mark low_match
    rejection_reason: Optional[str] = None
    cleaned_cv_text: str = ""


def run_gatekeeper(
    cv_text: str,
    job_description: str,
    required_skills: list[str],
    *,
    semantic_threshold: float = 0.40,    # below this → reject without LLM
    skill_threshold: float = 80.0,       # rapidfuzz partial_ratio threshold (0–100)
    min_skill_ratio: float = 0.0,        # min % of required skills that must match (0–100); 0 = disabled
) -> GatekeeperResult:
    """
    Run the full local pre-filtering pipeline.

    Decision logic (all conditions must be satisfied to pass):
      1. semantic_similarity >= semantic_threshold
      2. skill_match_ratio >= min_skill_ratio  (only checked when min_skill_ratio > 0
         AND required_skills is non-empty)
      → gatekeeper_passed = True  → proceed to LLM scoring

    All three parameters are runtime-configurable via system_config (Super Admin).
    Cost impact: ~40-60% of CVs never reach the LLM in typical hiring funnels.
    """
    cleaned_cv = clean_text(cv_text)
    cleaned_jd = clean_text(job_description)

    cv_lang = detect_language(cleaned_cv)
    jd_lang = detect_language(cleaned_jd)

    semantic_sim = compute_semantic_similarity(cleaned_cv, cleaned_jd)
    semantic_pct = round(semantic_sim * 100, 2)

    skill_result = match_skills_bilingual(required_skills, cleaned_cv, skill_threshold)
    skill_ratio = skill_result["match_ratio"]

    reasons: list[str] = []

    sim_passed = semantic_sim >= semantic_threshold
    if not sim_passed:
        reasons.append(
            f"Semantic similarity {semantic_pct:.1f}% is below the "
            f"threshold of {semantic_threshold * 100:.0f}%."
        )

    # Skill ratio gate: active only when min_skill_ratio > 0 and skills exist
    skill_ratio_passed = True
    if min_skill_ratio > 0 and required_skills:
        skill_ratio_passed = skill_ratio >= min_skill_ratio
        if not skill_ratio_passed:
            reasons.append(
                f"Skill match ratio {skill_ratio:.1f}% is below the "
                f"minimum of {min_skill_ratio:.0f}% "
                f"({len(skill_result['matched'])}/{len(required_skills)} skills found)."
            )

    passed = sim_passed and skill_ratio_passed
    reason: Optional[str] = (
        " ".join(reasons) + " CV content is not sufficiently related to the job description."
        if reasons else None
    )

    return GatekeeperResult(
        cv_language=cv_lang,
        jd_language=jd_lang,
        semantic_similarity=semantic_sim,
        semantic_similarity_pct=semantic_pct,
        skill_match_ratio=skill_ratio,
        matched_skills=skill_result["matched"],
        missing_skills=skill_result["missing"],
        gatekeeper_passed=passed,
        rejection_reason=reason,
        cleaned_cv_text=cleaned_cv,
    )
