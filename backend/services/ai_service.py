"""OpenAI GPT-4o-mini calls for criteria extraction and CV scoring."""
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


CRITERIA_SYSTEM_PROMPT = """\
You are a professional HR Data Analyst. Analyze the job description provided and extract structured scoring criteria.
Output MUST be valid JSON only — no markdown, no explanation, no code blocks.

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

Rules:
- All weights are integers between 0 and 100. They MUST sum to exactly 100.
- Each array should contain specific, measurable criteria relevant to the role.
- If a dimension is not relevant, use an empty array [] and weight 0.
- Adjust weights to reflect the relative importance of each dimension for THIS specific role.
"""

SCORING_SYSTEM_PROMPT = """\
You are a professional HR Data Analyst. Score the CV against the job criteria provided.
Output MUST be valid JSON only — no markdown, no explanation, no code blocks.

Return exactly this structure:
{
  "score_skills": <integer 0-100>,
  "score_experience": <integer 0-100>,
  "score_education": <integer 0-100>,
  "score_certifications": <integer 0-100>,
  "score_soft_skills": <integer 0-100>,
  "score_domain_knowledge": <integer 0-100>,
  "score_other": <integer 0-100>,
  "strengths": ["strength1", "strength2", ...],
  "gaps_identified": ["gap1", "gap2", ...],
  "evaluation_notes": "brief overall summary paragraph",
  "interview_questions": ["question1", "question2", ...]
}

Rules:
- Scores are integers 0-100 where 100 = perfect match for that dimension.
- strengths: list 3-5 key strengths found in the CV relative to the role.
- gaps_identified: list any notable gaps or missing requirements.
- evaluation_notes: 2-3 sentence executive summary.
- interview_questions: suggest 3-5 targeted questions based on the CV and role.
"""


async def extract_job_criteria(job_description: str) -> dict[str, Any]:
    """Call OpenAI to extract scoring criteria from a job description."""
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


async def score_cv(cv_text: str, criteria: dict[str, Any], job_title: str) -> dict[str, Any]:
    """Call OpenAI to score a CV against extracted criteria."""
    client = _get_client()
    criteria_text = json.dumps(criteria, indent=2)
    user_prompt = (
        f"Job Title: {job_title}\n\n"
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
        return json.loads(raw)
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
        # Auto-correct by distributing remainder to skills weight
        diff = 100 - total
        data["weight_skills"] = data.get("weight_skills", 0) + diff


def compute_final_score(scores: dict[str, int], weights: dict[str, int]) -> float:
    """Compute weighted final score (0-100)."""
    pairs = [
        ("score_skills", "weight_skills"),
        ("score_experience", "weight_experience"),
        ("score_education", "weight_education"),
        ("score_certifications", "weight_certifications"),
        ("score_soft_skills", "weight_soft_skills"),
        ("score_domain_knowledge", "weight_domain_knowledge"),
        ("score_other", "weight_other"),
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
