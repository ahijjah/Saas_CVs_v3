"""
AI Prompt Management — Super Admin only.

Supports versioned prompts for every stage of the CV scoring pipeline.
Each prompt_code has one active version at a time; history is preserved.

Prompt codes used by the pipeline:
  criteria_extraction  — extracts structured hiring criteria from job descriptions
  cv_scoring           — full bilingual 7-dimension CV scoring (Level 3)
  level2_screening     — lightweight binary PASS/REJECT pre-screen (Level 2)

Workers load the active DB prompt at task execution time and fall back to
the hardcoded defaults in ai_service.py if no active version is found.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

router = APIRouter(prefix="/admin/ai-prompts", tags=["ai-prompts"])

VALID_CATEGORIES = {"criteria", "scoring", "screening", "summary", "interview"}

# Hardcoded defaults so "Reset to default" can recreate the original prompts.
# These mirror the constants in ai_service.py.
_DEFAULT_PROMPTS: dict[str, dict] = {
    "criteria_extraction": {
        "prompt_name":     "Job Criteria Extraction",
        "prompt_category": "criteria",
        "model":           "gpt-4o-mini",
        "temperature":     0.20,
        "max_tokens":      4000,
        "output_language": "ar",
        "user_prompt_template": "Job Description:\n\n{job_description}",
        "system_prompt": (
            "أنت محلل بيانات موارد بشرية محترف ومتخصص في التوظيف الثنائي اللغة (العربية والإنجليزية).\n"
            "You are a professional HR Data Analyst specializing in bilingual (Arabic/English) recruitment.\n\n"
            "TASK: Analyze the job description and extract structured hiring criteria.\n"
            "The description may be in Arabic, English, or a mix of both. Analyze it regardless of language.\n\n"
            'OUTPUT: Valid JSON only — no markdown, no explanation, no code blocks.\n\n'
            "Return EXACTLY this structure (do not add or remove keys):\n"
            '{\n'
            '  "skills": {\n'
            '    "required": ["mandatory skill 1", "mandatory skill 2"],\n'
            '    "preferred": ["nice-to-have 1", "nice-to-have 2"]\n'
            '  },\n'
            '  "experience": {\n'
            '    "minimum_years": <integer, 0 if not specified>,\n'
            '    "relevant_roles": ["role title 1", "role title 2"],\n'
            '    "key_responsibilities": ["responsibility 1", "responsibility 2"]\n'
            '  },\n'
            '  "education": {\n'
            "    \"minimum_level\": \"Bachelor's | Master's | PhD | High School | None\",\n"
            '    "fields_of_study": ["field 1", "field 2"]\n'
            '  },\n'
            '  "certifications": ["certification 1", "certification 2"],\n'
            '  "domain_knowledge": ["domain area 1", "domain area 2"],\n'
            '  "other_requirements": ["requirement 1", "requirement 2"],\n'
            '  "scoring_weights": {\n'
            '    "skills": <integer>,\n'
            '    "experience": <integer>,\n'
            '    "education": <integer>,\n'
            '    "certifications": <integer>,\n'
            '    "soft_skills": <integer>,\n'
            '    "domain_knowledge": <integer>,\n'
            '    "other_requirements": <integer>\n'
            '  }\n'
            '}\n\n'
            "RULES:\n"
            "- scoring_weights values are integers and MUST sum to exactly 100.\n"
            "- Assign weight 0 only if a dimension is completely irrelevant to the role.\n"
            "- minimum_years must be an integer (use 0 if not mentioned).\n"
            "- required skills = explicitly mandatory; preferred = stated as advantageous or optional.\n"
            "- Extract criteria in the SAME language as the job description.\n"
            '- If a section has no data, use [] for arrays and "None" for minimum_level.\n'
            "- Be specific and measurable — avoid vague terms like \"good communication skills\"."
        ),
    },
    "cv_scoring": {
        "prompt_name":     "Full CV Scoring (Level 3)",
        "prompt_category": "scoring",
        "model":           "gpt-4o-mini",
        "temperature":     0.20,
        "max_tokens":      3000,
        "output_language": "ar",
        "user_prompt_template": (
            "Job Title: {job_title}\n"
            "{lang_hint}\n"
            "{gatekeeper_note}\n"
            "Scoring Criteria:\n{criteria_text}\n\n"
            "CV Text:\n{cv_text}"
        ),
        "system_prompt": (
            "أنت محلل بيانات موارد بشرية خبير في تقييم السير الذاتية ثنائية اللغة.\n"
            "You are an expert HR analyst evaluating bilingual resumes (Arabic/English/mixed).\n\n"
            "CRITICAL RULES:\n"
            "1. The CV may be in Arabic, English, or a mix — analyze it regardless of language.\n"
            "2. Cross-lingual matching IS valid: an Arabic CV demonstrating Python skills satisfies "
            "an English \"Python\" requirement and vice versa.\n"
            "3. Output ALL human-readable text fields (strengths, gaps, notes, questions, reasoning) in Arabic (العربية).\n"
            "4. Scores must reflect actual evidence found in the CV — do not penalize for language choice.\n\n"
            "OUTPUT: Valid JSON only — no markdown, no explanation, no code blocks.\n\n"
            "Return exactly this structure:\n"
            '{\n'
            '  "candidate_name": "<full name as it appears in the CV header/contact section, or empty string if not found>",\n'
            '  "score_skills": <integer 0-100>,\n'
            '  "score_experience": <integer 0-100>,\n'
            '  "score_education": <integer 0-100>,\n'
            '  "score_certifications": <integer 0-100>,\n'
            '  "score_soft_skills": <integer 0-100>,\n'
            '  "score_domain_knowledge": <integer 0-100>,\n'
            '  "score_other": <integer 0-100>,\n'
            '  "strengths": ["نقطة قوة 1", "نقطة قوة 2", ...],\n'
            '  "gaps_identified": ["ثغرة 1", "ثغرة 2", ...],\n'
            '  "red_flags": ["علامة تحذير 1", ...],\n'
            '  "evaluation_notes": "ملخص تنفيذي بالعربية (2-3 جمل)",\n'
            '  "interview_questions": ["سؤال مقابلة 1", "سؤال مقابلة 2", ...],\n'
            '  "reasoning": {\n'
            '    "skills": "تبرير درجة المهارات بالعربية",\n'
            '    "experience": "تبرير درجة الخبرة بالعربية",\n'
            '    "education": "تبرير درجة التعليم بالعربية",\n'
            '    "certifications": "تبرير درجة الشهادات بالعربية",\n'
            '    "soft_skills": "تبرير درجة المهارات الشخصية بالعربية",\n'
            '    "domain_knowledge": "تبرير درجة المعرفة المجالية بالعربية",\n'
            '    "other": "تبرير درجة المتطلبات الأخرى بالعربية"\n'
            '  }\n'
            '}\n\n'
            "SCORING GUIDE (for each dimension):\n"
            "  90-100: Exceeds requirements — strong evidence with extra depth\n"
            "  70-89:  Meets requirements — clear evidence present\n"
            "  50-69:  Partially meets — some evidence, gaps exist\n"
            "  30-49:  Weak match — minimal relevant evidence\n"
            "  0-29:   Does not meet — no meaningful evidence found\n\n"
            "REQUIRED FIELD RULES:\n"
            "- strengths: 3-5 specific strengths with evidence from the CV\n"
            "- gaps_identified: missing or weak areas relative to the role\n"
            "- red_flags: concerns (employment gaps, contradictions, unverifiable claims) — use [] if none\n"
            "- evaluation_notes: executive summary in Arabic\n"
            "- interview_questions: 3-5 targeted questions to probe identified gaps or verify claims\n"
            "- reasoning: one sentence per dimension explaining EXACTLY why that score was given"
        ),
    },
    "level2_screening": {
        "prompt_name":     "Lightweight Binary Screening (Level 2)",
        "prompt_category": "screening",
        "model":           "gpt-4o-mini",
        "temperature":     0.10,
        "max_tokens":      120,
        "output_language": "en",
        "user_prompt_template": (
            "Job Title: {job_title}\n"
            "Key Requirements: {skills_str}\n\n"
            "CV Excerpt:\n{cv_excerpt}"
        ),
        "system_prompt": (
            "أنت مساعد فرز مبدئي سريع للسير الذاتية.\n"
            "You are a fast CV pre-screener performing a binary qualification check.\n\n"
            "TASK: Decide whether this candidate meets the MINIMUM BAR for the role.\n"
            "The CV excerpt and job requirements are provided. Do NOT do detailed scoring.\n\n"
            "PASS: Candidate has enough relevant background to warrant full evaluation.\n"
            "REJECT: Candidate clearly lacks minimum qualifications (wrong field, zero relevant experience, etc.).\n\n"
            "Be strict but fair. Default to PASS when uncertain — a REJECT here saves a full scoring call.\n\n"
            'OUTPUT: Valid JSON only — no markdown, no explanation.\n'
            '{"decision": "PASS" | "REJECT", "reason": "<one sentence in English>"}'
        ),
    },
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class CreatePromptRequest(BaseModel):
    prompt_code: str
    prompt_name: str
    prompt_category: str
    system_prompt: str
    user_prompt_template: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float = Field(0.20, ge=0, le=2)
    max_tokens: int = Field(2000, ge=1, le=32000)
    output_language: str = "ar"
    notes: str | None = None


class UpdatePromptRequest(BaseModel):
    prompt_name: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    model: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1, le=32000)
    output_language: str | None = None
    notes: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(r) -> dict:
    return {
        "prompt_id":           str(r["prompt_id"]),
        "prompt_code":         r["prompt_code"],
        "prompt_name":         r["prompt_name"],
        "prompt_category":     r["prompt_category"],
        "system_prompt":       r["system_prompt"],
        "user_prompt_template":r["user_prompt_template"] or "",
        "model":               r["model"],
        "temperature":         float(r["temperature"]),
        "max_tokens":          r["max_tokens"],
        "output_language":     r["output_language"],
        "is_active":           r["is_active"],
        "version":             r["version"],
        "notes":               r["notes"] or "",
        "created_at":          r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at":          r["updated_at"].isoformat() if r["updated_at"] else None,
        "updated_by_email":    r["updated_by_email"] or "",
    }


# ── GET /admin/ai-prompts ─────────────────────────────────────────────────────

@router.get("", dependencies=[RequireSuperAdmin])
async def list_prompts(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    rows = await db.execute(text("""
        SELECT prompt_id, prompt_code, prompt_name, prompt_category,
               system_prompt, user_prompt_template, model, temperature,
               max_tokens, output_language, is_active, version, notes,
               created_at, updated_at, updated_by_email
        FROM ai_prompts
        ORDER BY prompt_code, version DESC
    """))

    prompts = [_row_to_dict(r) for r in rows.mappings()]
    return {"success": True, "prompts": prompts}


# ── GET /admin/ai-prompts/{prompt_code} ───────────────────────────────────────

@router.get("/{prompt_code}", dependencies=[RequireSuperAdmin])
async def get_prompt(
    prompt_code: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    rows = await db.execute(text("""
        SELECT prompt_id, prompt_code, prompt_name, prompt_category,
               system_prompt, user_prompt_template, model, temperature,
               max_tokens, output_language, is_active, version, notes,
               created_at, updated_at, updated_by_email
        FROM ai_prompts
        WHERE prompt_code = :code
        ORDER BY version DESC
    """), {"code": prompt_code})

    versions = [_row_to_dict(r) for r in rows.mappings()]
    if not versions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No prompts found for code '{prompt_code}'.")

    return {"success": True, "prompt_code": prompt_code, "versions": versions}


# ── POST /admin/ai-prompts ────────────────────────────────────────────────────

@router.post("", dependencies=[RequireSuperAdmin])
async def create_prompt(
    body: CreatePromptRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    if body.prompt_category not in VALID_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"prompt_category must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    # Determine next version number for this prompt_code
    ver_row = await db.execute(
        text("SELECT COALESCE(MAX(version), 0) AS max_ver FROM ai_prompts WHERE prompt_code = :code"),
        {"code": body.prompt_code},
    )
    next_version = (ver_row.scalar() or 0) + 1

    prompt_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO ai_prompts (
            prompt_id, prompt_code, prompt_name, prompt_category,
            system_prompt, user_prompt_template, model, temperature,
            max_tokens, output_language, is_active, version, notes,
            updated_by, updated_by_email
        ) VALUES (
            :pid, :code, :name, :cat,
            :sys, :usr, :model, :temp,
            :tokens, :lang, FALSE, :ver, :notes,
            :uid, :email
        )
    """), {
        "pid":    prompt_id,
        "code":   body.prompt_code,
        "name":   body.prompt_name,
        "cat":    body.prompt_category,
        "sys":    body.system_prompt,
        "usr":    body.user_prompt_template,
        "model":  body.model,
        "temp":   body.temperature,
        "tokens": body.max_tokens,
        "lang":   body.output_language,
        "ver":    next_version,
        "notes":  body.notes,
        "uid":    current_user.user_id,
        "email":  current_user.email,
    })
    await db.commit()

    return {"success": True, "prompt_id": prompt_id, "version": next_version}


# ── PUT /admin/ai-prompts/{prompt_id} ─────────────────────────────────────────

@router.put("/{prompt_id}", dependencies=[RequireSuperAdmin])
async def update_prompt(
    prompt_id: str,
    body: UpdatePromptRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    row = await db.execute(
        text("SELECT prompt_id, is_active FROM ai_prompts WHERE prompt_id = :pid"),
        {"pid": prompt_id},
    )
    existing = row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Prompt not found.")

    # Build dynamic SET clause from provided fields only
    updates: dict[str, object] = {}
    if body.prompt_name is not None:          updates["prompt_name"] = body.prompt_name
    if body.system_prompt is not None:        updates["system_prompt"] = body.system_prompt
    if body.user_prompt_template is not None: updates["user_prompt_template"] = body.user_prompt_template
    if body.model is not None:                updates["model"] = body.model
    if body.temperature is not None:          updates["temperature"] = body.temperature
    if body.max_tokens is not None:           updates["max_tokens"] = body.max_tokens
    if body.output_language is not None:      updates["output_language"] = body.output_language
    if body.notes is not None:                updates["notes"] = body.notes

    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="No fields provided to update.")

    set_parts = ", ".join(f"{k} = :{k}" for k in updates)
    updates["updated_by"] = current_user.user_id
    updates["updated_by_email"] = current_user.email
    updates["pid"] = prompt_id

    await db.execute(
        text(f"UPDATE ai_prompts SET {set_parts}, updated_by = :updated_by, updated_by_email = :updated_by_email WHERE prompt_id = :pid"),
        updates,
    )
    await db.commit()

    return {"success": True, "prompt_id": prompt_id}


# ── POST /admin/ai-prompts/{prompt_id}/activate ───────────────────────────────

@router.post("/{prompt_id}/activate", dependencies=[RequireSuperAdmin])
async def activate_prompt(
    prompt_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    row = await db.execute(
        text("SELECT prompt_id, prompt_code FROM ai_prompts WHERE prompt_id = :pid"),
        {"pid": prompt_id},
    )
    existing = row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Prompt not found.")

    prompt_code = existing["prompt_code"]

    # Deactivate all versions of this prompt_code, then activate the requested one
    await db.execute(
        text("UPDATE ai_prompts SET is_active = FALSE WHERE prompt_code = :code"),
        {"code": prompt_code},
    )
    await db.execute(
        text("UPDATE ai_prompts SET is_active = TRUE, updated_by = :uid, updated_by_email = :email WHERE prompt_id = :pid"),
        {"uid": current_user.user_id, "email": current_user.email, "pid": prompt_id},
    )
    await db.commit()

    return {"success": True, "prompt_id": prompt_id, "prompt_code": prompt_code, "is_active": True}


# ── POST /admin/ai-prompts/{prompt_code}/reset-default ────────────────────────

@router.post("/{prompt_code}/reset-default", dependencies=[RequireSuperAdmin])
async def reset_to_default(
    prompt_code: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    default = _DEFAULT_PROMPTS.get(prompt_code)
    if not default:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No hardcoded default exists for prompt_code '{prompt_code}'.")

    # Determine next version
    ver_row = await db.execute(
        text("SELECT COALESCE(MAX(version), 0) AS max_ver FROM ai_prompts WHERE prompt_code = :code"),
        {"code": prompt_code},
    )
    next_version = (ver_row.scalar() or 0) + 1

    # Deactivate all existing versions
    await db.execute(
        text("UPDATE ai_prompts SET is_active = FALSE WHERE prompt_code = :code"),
        {"code": prompt_code},
    )

    # Insert new version from hardcoded default, activate it
    prompt_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO ai_prompts (
            prompt_id, prompt_code, prompt_name, prompt_category,
            system_prompt, user_prompt_template, model, temperature,
            max_tokens, output_language, is_active, version, notes,
            updated_by, updated_by_email
        ) VALUES (
            :pid, :code, :name, :cat,
            :sys, :usr, :model, :temp,
            :tokens, :lang, TRUE, :ver,
            'Reset to hardcoded default',
            :uid, :email
        )
    """), {
        "pid":    prompt_id,
        "code":   prompt_code,
        "name":   default["prompt_name"],
        "cat":    default["prompt_category"],
        "sys":    default["system_prompt"],
        "usr":    default.get("user_prompt_template"),
        "model":  default["model"],
        "temp":   default["temperature"],
        "tokens": default["max_tokens"],
        "lang":   default["output_language"],
        "ver":    next_version,
        "uid":    current_user.user_id,
        "email":  current_user.email,
    })
    await db.commit()

    return {
        "success":   True,
        "prompt_id": prompt_id,
        "version":   next_version,
        "message":   f"Reset '{prompt_code}' to hardcoded default (v{next_version}). This version is now active.",
    }
