"""Celery task: score a CV against job criteria using OpenAI."""
import asyncio
import json
import logging
from pathlib import Path

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, name="workers.cv_score.score_cv_task")
def score_cv_task(
    self,
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_path: str,
    mime_type: str,
):
    """Score a CV file. Handles DOCX→PDF conversion, text extraction, AI scoring, DB write."""
    try:
        asyncio.run(_score_cv_async(application_id, job_id, tenant_id, file_path, mime_type))
    except Exception as exc:
        logger.error("score_cv_task failed for application %s: %s", application_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            asyncio.run(_mark_failed(application_id, str(exc)))


async def _score_cv_async(
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_path: str,
    mime_type: str,
) -> None:
    from database import AsyncSessionLocal, set_rls_context
    from services.ai_service import compute_final_score, determine_decision, score_cv
    from services.docx_service import convert_docx_to_pdf
    from services.email_service import send_cv_received_email
    from services.pdf_service import extract_text_from_pdf
    from services.threshold_service import get_thresholds
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, tenant_id, "super_admin")

        # Mark processing
        await db.execute(
            text("UPDATE applications SET processing_status = 'processing' WHERE application_id = :aid"),
            {"aid": application_id},
        )
        await db.commit()

        # Read file
        path = Path(file_path)
        file_bytes = path.read_bytes()

        # Convert DOCX → PDF if needed
        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            file_bytes = await convert_docx_to_pdf(file_bytes)

        # Extract text
        cv_text = extract_text_from_pdf(file_bytes)

        # Update file extraction status
        await db.execute(
            text("""
                UPDATE application_files
                SET extracted_text = :text, extraction_status = 'done'
                WHERE application_id = :aid
            """),
            {"text": cv_text, "aid": application_id},
        )

        # Fetch job criteria and title
        criteria_row = await db.execute(
            text("""
                SELECT jc.*, j.title AS job_title
                FROM job_criteria jc
                JOIN jobs j ON j.job_id = jc.job_id
                WHERE jc.job_id = :jid
            """),
            {"jid": job_id},
        )
        criteria = criteria_row.mappings().first()
        if not criteria:
            raise RuntimeError(f"No criteria found for job {job_id}")

        weights = {
            "weight_skills": criteria["weight_skills"],
            "weight_experience": criteria["weight_experience"],
            "weight_education": criteria["weight_education"],
            "weight_certifications": criteria["weight_certifications"],
            "weight_soft_skills": criteria["weight_soft_skills"],
            "weight_domain_knowledge": criteria["weight_domain_knowledge"],
            "weight_other": criteria["weight_other"],
        }
        criteria_dict = {
            "skills": criteria["skills"],
            "experience": criteria["experience"],
            "education": criteria["education"],
            "certifications": criteria["certifications"],
            "soft_skills": criteria["soft_skills"],
            "domain_knowledge": criteria["domain_knowledge"],
            "other_requirements": criteria["other_requirements"],
            **weights,
        }

        from config import get_settings
        cfg = get_settings()

        # Call OpenAI
        ai_result = await score_cv(cv_text, criteria_dict, criteria["job_title"])

        # Compute final score
        final_score = compute_final_score(ai_result, weights)

        # Get thresholds
        q_thresh, p_thresh = await get_thresholds(db, tenant_id, job_id)
        decision = determine_decision(final_score, q_thresh, p_thresh)

        # Write scores
        await db.execute(
            text("""
                INSERT INTO application_scores (
                    application_id,
                    score_skills, score_experience, score_education,
                    score_certifications, score_soft_skills,
                    score_domain_knowledge, score_other,
                    final_score, weights_snapshot, ai_model,
                    strengths, gaps_identified, evaluation_notes,
                    interview_questions, raw_ai_response
                ) VALUES (
                    :aid,
                    :s_skills, :s_exp, :s_edu, :s_cert, :s_soft, :s_domain, :s_other,
                    :final, :weights, :model,
                    :strengths, :gaps, :notes, :questions, :raw
                )
            """),
            {
                "aid": application_id,
                "s_skills": ai_result.get("score_skills", 0),
                "s_exp": ai_result.get("score_experience", 0),
                "s_edu": ai_result.get("score_education", 0),
                "s_cert": ai_result.get("score_certifications", 0),
                "s_soft": ai_result.get("score_soft_skills", 0),
                "s_domain": ai_result.get("score_domain_knowledge", 0),
                "s_other": ai_result.get("score_other", 0),
                "final": final_score,
                "weights": json.dumps(weights),
                "model": cfg.openai_model,
                "strengths": ai_result.get("strengths", []),
                "gaps": ai_result.get("gaps_identified", []),
                "notes": ai_result.get("evaluation_notes"),
                "questions": ai_result.get("interview_questions", []),
                "raw": json.dumps(ai_result),
            },
        )

        # Update application decision + thresholds snapshot
        await db.execute(
            text("""
                UPDATE applications SET
                    decision = :decision,
                    processing_status = 'scored',
                    qualified_threshold_used = :qt,
                    partial_threshold_used = :pt,
                    scored_at = now()
                WHERE application_id = :aid
            """),
            {
                "decision": decision,
                "qt": q_thresh,
                "pt": p_thresh,
                "aid": application_id,
            },
        )
        await db.commit()

        # Send confirmation email to candidate
        app_row = await db.execute(
            text("SELECT candidate_email, candidate_name FROM applications WHERE application_id = :aid"),
            {"aid": application_id},
        )
        app_data = app_row.mappings().first()
        if app_data and app_data["candidate_email"]:
            await send_cv_received_email(
                to_email=app_data["candidate_email"],
                candidate_name=app_data["candidate_name"],
                job_title=criteria["job_title"],
            )

        logger.info(
            "Scored application %s: final=%.2f decision=%s",
            application_id, final_score, decision,
        )


async def _mark_failed(application_id: str, error: str) -> None:
    from database import AsyncSessionLocal, set_rls_context
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(
            text("UPDATE applications SET processing_status = 'failed' WHERE application_id = :aid"),
            {"aid": application_id},
        )
        await db.execute(
            text("""
                UPDATE application_files SET extraction_status = 'failed'
                WHERE application_id = :aid AND extraction_status = 'pending'
            """),
            {"aid": application_id},
        )
        await db.execute(
            text("""
                INSERT INTO email_ingest_log (application_id, status, error_message)
                VALUES (:aid, 'failed', :err)
                ON CONFLICT DO NOTHING
            """),
            {"aid": application_id, "err": error},
        )
        await db.commit()
