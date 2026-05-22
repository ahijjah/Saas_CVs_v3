"""
Celery task: extract AI scoring criteria for a job asynchronously.

Flow:
  1. Mark job_criteria row as 'processing'
  2. Call OpenAI via extract_job_criteria()
  3. Flatten nested result to flat arrays for scoring pipeline
  4. Validate criteria quality (sufficient content or explicitly open/broad role)
  5. Write analysis_json + flat arrays + weights to DB
     - 'completed' if quality check passes
     - 'failed'    if all criteria arrays are empty and role is not open/broad
  6. On max-retry failure: mark 'failed' with error message

Event-loop safety
-----------------
Same pattern as cv_score.py — each task invocation creates its own NullPool
engine + sessionmaker (no shared pool, no cross-loop Future references), owns
its own event loop, and uses an isolated NullPool engine in `_mark_failed`.
"""
import asyncio
import json
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_EMPTY_DESCRIPTION_ERROR = (
    "The job description does not contain enough information for reliable CV scoring. "
    "Please add responsibilities, skills, qualifications, or indicate that the role is "
    "open to all backgrounds."
)

# Keywords that indicate an intentionally open/broad role with no specific requirements.
# Matched case-insensitively against text from other_requirements, domain_knowledge,
# experience.key_responsibilities, and experience.relevant_roles.
_OPEN_ROLE_KEYWORDS = (
    "open to all", "all backgrounds", "no specific", "no requirement",
    "any background", "everyone is welcome", "anyone can apply",
    "no experience required", "open role", "general hire",
    # Arabic equivalents
    "مفتوح", "جميع التخصصات", "لا يشترط", "لا تشترط", "مفتوحة",
)


def _check_criteria_quality(analysis: dict, flat: dict) -> tuple[bool, bool, str | None]:
    """
    Validate that extracted criteria contain enough signal for CV scoring.

    Prefers the structured ``scoreability`` object returned by updated prompts.
    Falls back to keyword heuristics for prompts that do not yet emit it.

    Returns:
        (is_sufficient, is_open_broad, error_message)
        - is_sufficient:   True when scoring can proceed.
        - is_open_broad:   True when the role has no specific requirements but is valid.
        - error_message:   Populated only when is_sufficient is False; used as
                           criteria_extraction_error in the DB.
    """
    scoreability = analysis.get("scoreability")
    if isinstance(scoreability, dict):
        status = (scoreability.get("status") or "").lower()
        reason = scoreability.get("reason") or None

        if status == "insufficient":
            error = reason or _EMPTY_DESCRIPTION_ERROR
            return False, False, error

        if status == "open_broad":
            return True, True, None

        if status == "scoreable":
            counted_keys = (
                "skills", "experience", "education",
                "certifications", "domain_knowledge", "other_requirements",
            )
            total_items = sum(len(flat.get(k) or []) for k in counted_keys)
            if total_items > 0:
                return True, False, None
            # Prompt said scoreable but arrays are empty — fall through to heuristic.

    # ── Keyword fallback (old prompts without scoreability) ──────────────────
    counted_keys = (
        "skills", "experience", "education",
        "certifications", "domain_knowledge", "other_requirements",
    )
    total_items = sum(len(flat.get(k) or []) for k in counted_keys)

    if total_items > 0:
        return True, False, None

    exp = analysis.get("experience") or {}
    candidate_texts: list[str] = []
    for key in ("other_requirements", "domain_knowledge"):
        candidate_texts.extend(str(v) for v in (analysis.get(key) or []))
    candidate_texts.extend(str(v) for v in (exp.get("key_responsibilities") or []))
    candidate_texts.extend(str(v) for v in (exp.get("relevant_roles") or []))

    combined = " ".join(candidate_texts).lower()
    is_open_broad = any(kw in combined for kw in _OPEN_ROLE_KEYWORDS)
    if is_open_broad:
        return True, True, None
    return False, False, _EMPTY_DESCRIPTION_ERROR


def _run_in_fresh_loop(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="workers.criteria_worker.extract_criteria_task",
)
def extract_criteria_task(self, job_id: str, description: str) -> None:
    """Background task: extract AI criteria and update job_criteria row."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from config import get_settings

    cfg = get_settings()
    task_engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    TaskSession = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_extract_async(job_id, description, TaskSession))
    except Exception as exc:
        logger.error("extract_criteria_task failed for job %s (attempt %d/%d): %s",
                     job_id, self.request.retries + 1, self.max_retries + 1, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            _run_in_fresh_loop(_mark_failed(job_id, f"Max retries exceeded: {exc}"))
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        task_engine.dispose()


async def _extract_async(job_id: str, description: str, Session) -> None:
    from database import set_rls_context
    from services.ai_service import extract_job_criteria, flatten_criteria_for_scoring, load_active_prompt
    from config import get_settings
    from sqlalchemy import text
    from datetime import datetime, timezone

    settings = get_settings()

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(
            text("""
                UPDATE job_criteria
                SET criteria_extraction_status = 'processing'
                WHERE job_id = :jid
            """),
            {"jid": job_id},
        )
        await db.commit()

        criteria_prompt = await load_active_prompt(db, "criteria_extraction")

    logger.info("[job:%s] Criteria extraction started.", job_id)

    analysis = await extract_job_criteria(description, prompt_override=criteria_prompt)
    flat = flatten_criteria_for_scoring(analysis)

    is_sufficient, is_open_broad, quality_error = _check_criteria_quality(analysis, flat)

    if is_sufficient:
        final_status = "completed"
        final_error = None
        extracted_at_value = datetime.now(timezone.utc)
        log_suffix = "open/broad role — no specific criteria" if is_open_broad else "OK"
        logger.info("[job:%s] Criteria quality check passed (%s).", job_id, log_suffix)
    else:
        final_status = "failed"
        final_error = quality_error
        extracted_at_value = None
        logger.warning(
            "[job:%s] Criteria quality check failed — all arrays empty, not an open role.",
            job_id,
        )

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(
            text("""
                UPDATE job_criteria SET
                    analysis_json              = CAST(:aj AS jsonb),
                    original_analysis_json     = COALESCE(original_analysis_json, CAST(:aj AS jsonb)),
                    skills                     = :skills,
                    experience                 = :experience,
                    education                  = :education,
                    certifications             = :certifications,
                    soft_skills                = :soft_skills,
                    domain_knowledge           = :domain_knowledge,
                    other_requirements         = :other_requirements,
                    weight_skills              = :weight_skills,
                    weight_experience          = :weight_experience,
                    weight_education           = :weight_education,
                    weight_certifications      = :weight_certifications,
                    weight_soft_skills         = :weight_soft_skills,
                    weight_domain_knowledge    = :weight_domain_knowledge,
                    weight_other               = :weight_other,
                    ai_model                   = :model,
                    ai_generated_at            = now(),
                    criteria_extraction_status = :status,
                    criteria_extracted_at      = COALESCE(:extracted_at, criteria_extracted_at),
                    criteria_extraction_error  = :error
                WHERE job_id = :jid
            """),
            {
                "aj":                 json.dumps(analysis, ensure_ascii=False),
                "skills":             flat["skills"],
                "experience":         flat["experience"],
                "education":          flat["education"],
                "certifications":     flat["certifications"],
                "soft_skills":        flat["soft_skills"],
                "domain_knowledge":   flat["domain_knowledge"],
                "other_requirements": flat["other_requirements"],
                "weight_skills":           flat["weight_skills"],
                "weight_experience":       flat["weight_experience"],
                "weight_education":        flat["weight_education"],
                "weight_certifications":   flat["weight_certifications"],
                "weight_soft_skills":      flat["weight_soft_skills"],
                "weight_domain_knowledge": flat["weight_domain_knowledge"],
                "weight_other":            flat["weight_other"],
                "model":              settings.openai_model,
                "status":             final_status,
                "extracted_at":       extracted_at_value,
                "error":              final_error,
                "jid":                job_id,
            },
        )
        await db.commit()

    if is_sufficient:
        logger.info("[job:%s] Criteria extraction completed.", job_id)
    else:
        logger.warning("[job:%s] Criteria extraction marked failed: empty description.", job_id)


async def _mark_failed(job_id: str, error: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text
    from config import get_settings

    cfg = get_settings()
    fail_engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    Session = async_sessionmaker(fail_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as db:
            await db.execute(text(
                "SELECT set_config('app.current_tenant_id', '', true), "
                "set_config('app.current_role', 'super_admin', true)"
            ))
            await db.execute(
                text("""
                    UPDATE job_criteria SET
                        criteria_extraction_status = 'failed',
                        criteria_extraction_error  = :err
                    WHERE job_id = :jid
                """),
                {"err": error[:2000], "jid": job_id},
            )
            await db.commit()
        logger.error("[job:%s] Criteria extraction permanently failed: %s", job_id, error)
    except Exception as mark_exc:
        logger.error("[job:%s] _mark_failed itself failed: %s", job_id, mark_exc)
    finally:
        await fail_engine.dispose()
