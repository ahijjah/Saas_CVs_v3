"""
Celery task: extract AI scoring criteria for a job asynchronously.

Flow:
  1. Mark job_criteria row as 'processing'
  2. Call OpenAI via extract_job_criteria()
  3. Flatten nested result to flat arrays for scoring pipeline
  4. Write analysis_json + flat arrays + weights to DB, mark 'completed'
  5. On max-retry failure: mark 'failed' with error message
"""
import asyncio
import json
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="workers.criteria_worker.extract_criteria_task",
)
def extract_criteria_task(self, job_id: str, description: str) -> None:
    """Background task: extract AI criteria and update job_criteria row."""
    try:
        asyncio.run(_extract_async(job_id, description))
    except Exception as exc:
        logger.error("extract_criteria_task failed for job %s: %s", job_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            asyncio.run(_mark_failed(job_id, str(exc)))


async def _extract_async(job_id: str, description: str) -> None:
    from database import AsyncSessionLocal, set_rls_context
    from services.ai_service import extract_job_criteria, flatten_criteria_for_scoring, load_active_prompt
    from config import get_settings
    from sqlalchemy import text

    settings = get_settings()

    async with AsyncSessionLocal() as db:
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

        # Load active DB prompt; falls back to hardcoded default if none active
        criteria_prompt = await load_active_prompt(db, "criteria_extraction")

    analysis = await extract_job_criteria(description, prompt_override=criteria_prompt)
    flat = flatten_criteria_for_scoring(analysis)

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(
            text("""
                UPDATE job_criteria SET
                    analysis_json              = CAST(:aj AS jsonb),
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
                    criteria_extraction_status = 'completed',
                    criteria_extracted_at      = now(),
                    criteria_extraction_error  = NULL
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
                "jid":                job_id,
            },
        )
        await db.commit()
    logger.info("Criteria extraction completed for job %s", job_id)


async def _mark_failed(job_id: str, error: str) -> None:
    from database import AsyncSessionLocal, set_rls_context
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, "", "super_admin")
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
    logger.error("Criteria extraction permanently failed for job %s: %s", job_id, error)
