"""
OpenAI GPT-4o-mini — Bilingual Scoring Engine

Supports Arabic CVs, English CVs, and mixed-language documents.
The AI is instructed to analyze language-agnostically and output
all human-readable fields in Arabic (configurable via prompt_config).
"""
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


# ── Bilingual criteria extraction ─────────────────────────────────────────────

CRITERIA_SYSTEM_PROMPT = """\
أنت محلل بيانات موارد بشرية محترف ومتخصص في التوظيف الثنائي اللغة (العربية والإنجليزية).
You are a professional HR Data Analyst specializing in bilingual (Arabic/English) recruitment.

TASK: Analyze the job description and extract structured scoring criteria.
The job description may be in Arabic, English, or a mix of both. Analyze it regardless of language.

OUTPUT: Valid JSON only — no markdown, no explanation, no code blocks.

Return exactly this structure:
{
  "skills": ["skill1", "skill2", ...],
  "experience": ["requirement1", ...],
  "education": ["requirement1", ...],
  "certifications": ["cert1", ...],
  "soft_skills": ["skill1", ...],
  "domain_knowledge": ["domain1", ...],
  "other_requirements": ["req1", ...],
  "weight_skills": <integer>,
  "weight_experience": <integer>,
  "weight_education": <integer>,
  "weight_certifications": <integer>,
  "weight_soft_skills": <integer>,
  "weight_domain_knowledge": <integer>,
  "weight_other": <integer>
}

RULES:
- Weights are integers 0-100 and MUST sum to exactly 100.
- List criteria in the SAME language as the job description.
- If a dimension is irrelevant, use [] and weight 0.
- Be specific and measurable — avoid vague terms.
"""

# ── Bilingual CV scoring ──────────────────────────────────────────────────────

SCORING_SYSTEM_PROMPT = """\
أنت محلل بيانات موارد بشرية خبير في تقييم السير الذاتية ثنائية اللغة.
You are an expert HR analyst evaluating bilingual resumes (Arabic/English/mixed).

CRITICAL RULES:
1. The CV may be in Arabic, English, or a mix — analyze it regardless of language.
2. Cross-lingual matching IS valid: an Arabic CV demonstrating Python skills satisfies an English "Python" requirement and vice versa.
3. Output ALL human-readable text fields (strengths, gaps, notes, questions, reasoning) in Arabic (العربية).
4. Scores must reflect actual evidence found in the CV — do not penalize for language choice.

OUTPUT: Valid JSON only — no markdown, no explanation, no code blocks.

Return exactly this structure:
{
  "score_skills": <integer 0-100>,
  "score_experience": <integer 0-100>,
  "score_education": <integer 0-100>,
  "score_certifications": <integer 0-100>,
  "score_soft_skills": <integer 0-100>,
  "score_domain_knowledge": <integer 0-100>,
  "score_other": <integer 0-100>,
  "strengths": ["نقطة قوة 1", "نقطة قوة 2", ...],
  "gaps_identified": ["ثغرة 1", "ثغرة 2", ...],
  "red_flags": ["علامة تحذير 1", ...],
  "evaluation_notes": "ملخص تنفيذي بالعربية (2-3 جمل)",
  "interview_questions": ["سؤال مقابلة 1", "سؤال مقابلة 2", ...],
  "reasoning": {
    "skills": "تبرير درجة المهارات بالعربية",
    "experience": "تبرير درجة الخبرة بالعربية",
    "education": "تبرير درجة التعليم بالعربية",
    "certifications": "تبرير درجة الشهادات بالعربية",
    "soft_skills": "تبرير درجة المهارات الشخصية بالعربية",
    "domain_knowledge": "تبرير درجة المعرفة المجالية بالعربية",
    "other": "تبرير درجة المتطلبات الأخرى بالعربية"
  }
}

SCORING GUIDE (for each dimension):
  90-100: Exceeds requirements — strong evidence with extra depth
  70-89:  Meets requirements — clear evidence present
  50-69:  Partially meets — some evidence, gaps exist
  30-49:  Weak match — minimal relevant evidence
  0-29:   Does not meet — no meaningful evidence found

REQUIRED FIELD RULES:
- strengths: 3-5 specific strengths with evidence from the CV
- gaps_identified: missing or weak areas relative to the role
- red_flags: concerns (employment gaps, contradictions, unverifiable claims) — use [] if none
- evaluation_notes: executive summary in Arabic
- interview_questions: 3-5 targeted questions to probe identified gaps or verify claims
- reasoning: one sentence per dimension explaining EXACTLY why that score was given
"""


async def extract_job_criteria(job_description: str) -> dict[str, Any]:
    """Call OpenAI to extract bilingual scoring criteria from a job description."""
    client = _get_client()
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": CRITERIA_SYSTEM_PROMPT},
                {"role": "user", "content": f"Job Description:\n\n{job_description}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        _validate_criteria(data)
        return data
    except Exception as exc:
        logger.error("OpenAI criteria extraction failed: %s", exc)
        raise


async def score_cv(
    cv_text: str,
    criteria: dict[str, Any],
    job_title: str,
    cv_language: str = "unknown",
    gatekeeper_context: dict | None = None,
) -> dict[str, Any]:
    """
    Call OpenAI to score a CV against extracted criteria.

    Args:
        cv_text:            Cleaned CV text (from local_processor)
        criteria:           job_criteria dict with skills arrays + weights
        job_title:          Human-readable job title
        cv_language:        Detected language ('ar', 'en', 'mixed') — passed to prompt for context
        gatekeeper_context: Optional pre-filter results to include in prompt for context
    """
    client = _get_client()
    criteria_text = json.dumps(criteria, indent=2, ensure_ascii=False)

    # Build context paragraph for the AI
    lang_hint = {
        "ar": "ملاحظة: السيرة الذاتية مكتوبة باللغة العربية.",
        "en": "Note: The CV is written in English.",
        "mixed": "Note: The CV is written in a mix of Arabic and English.",
    }.get(cv_language, "")

    gatekeeper_note = ""
    if gatekeeper_context:
        matched = gatekeeper_context.get("matched_skills", [])
        missing = gatekeeper_context.get("missing_skills", [])
        sim_pct = gatekeeper_context.get("semantic_similarity_pct", 0)
        gatekeeper_note = (
            f"\n[Local Pre-Analysis]\n"
            f"Semantic similarity to JD: {sim_pct:.1f}%\n"
            f"Skills found by local matcher: {', '.join(matched) if matched else 'none'}\n"
            f"Skills NOT found by local matcher: {', '.join(missing) if missing else 'none'}\n"
            "(Use this as a hint, not a constraint — your analysis takes precedence.)\n"
        )

    user_prompt = (
        f"Job Title: {job_title}\n"
        f"{lang_hint}\n"
        f"{gatekeeper_note}\n"
        f"Scoring Criteria:\n{criteria_text}\n\n"
        f"CV Text:\n{cv_text}"
    )

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SCORING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        result = json.loads(raw)

        # Ensure new required fields exist (backwards compatibility)
        result.setdefault("red_flags", [])
        result.setdefault("reasoning", {})
        return result
    except Exception as exc:
        logger.error("OpenAI CV scoring failed: %s", exc)
        raise


def _validate_criteria(data: dict[str, Any]) -> None:
    weight_keys = [
        "weight_skills", "weight_experience", "weight_education",
        "weight_certifications", "weight_soft_skills",
        "weight_domain_knowledge", "weight_other",
    ]
    total = sum(data.get(k, 0) for k in weight_keys)
    if total != 100:
        diff = 100 - total
        data["weight_skills"] = data.get("weight_skills", 0) + diff


def compute_final_score(scores: dict[str, int], weights: dict[str, int]) -> float:
    """Compute weighted final score (0-100)."""
    pairs = [
        ("score_skills",          "weight_skills"),
        ("score_experience",      "weight_experience"),
        ("score_education",       "weight_education"),
        ("score_certifications",  "weight_certifications"),
        ("score_soft_skills",     "weight_soft_skills"),
        ("score_domain_knowledge","weight_domain_knowledge"),
        ("score_other",           "weight_other"),
    ]
    total_weight = sum(weights.get(wk, 0) for _, wk in pairs)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(scores.get(sk, 0) * weights.get(wk, 0) for sk, wk in pairs)
    return round(weighted_sum / total_weight, 2)


def determine_decision(final_score: float, qualified_threshold: int, partial_threshold: int) -> str:
    if final_score >= qualified_threshold:
        return "qualified"
    if final_score >= partial_threshold:
        return "partial"
    return "rejected"
