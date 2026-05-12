"""
Celery task: score a CV through the 3-level evaluation pipeline.

Pipeline:
  1. File read + DOCX→PDF conversion
  2. PDF text extraction (PyMuPDF)
  3. Level 1 — Local Gatekeeper (semantic similarity + bilingual skill matching)
     → Below threshold: mark 'low_match', evaluation_stage=1, skip LLM (cost saving)
  4. Level 2 — Lightweight LLM binary screen (PASS/REJECT, ~10x cheaper than full)
     → REJECT: mark 'rejected', evaluation_stage=2, skip full scoring
  5. Level 3 — Full LLM scoring (GPT-4o-mini bilingual)
     → Produces final score, decision, strengths, gaps, candidate name
  6. Write application_scores + update applications table
  7. Send confirmation email to candidate

evaluation_stage in the applications table is updated after each level so the
frontend can show live progress during polling (Level 1→2→3 transitions visible
within the 3-second polling interval).
"""
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
    scoring_overrides: dict | None = None,
):
    """Score a CV file through the 3-level evaluation pipeline."""
    try:
        asyncio.run(_score_cv_async(
            application_id, job_id, tenant_id, file_path, mime_type,
            scoring_overrides or {},
        ))
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
    scoring_overrides: dict,
) -> None:
    from config import get_settings
    from database import AsyncSessionLocal, set_rls_context
    from services.ai_service import (
        compute_final_score,
        determine_decision,
        lightweight_screen_cv,
        load_active_prompt,
        score_cv,
    )
    from services.docx_service import convert_docx_to_pdf
    from services.email_service import send_cv_received_email
    from services.local_processor import run_gatekeeper
    from services.pdf_service import extract_text_from_pdf
    from services.prompt_config import load_prompt_config
    from services.threshold_service import get_thresholds
    from sqlalchemy import text

    cfg = get_settings()

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, tenant_id, "super_admin")

        # Mark processing
        await db.execute(
            text("UPDATE applications SET processing_status = 'processing' WHERE application_id = :aid"),
            {"aid": application_id},
        )
        await db.commit()

        # ── Step 1: File read + conversion ────────────────────────────────────
        path = Path(file_path)
        file_bytes = path.read_bytes()

        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            file_bytes = await convert_docx_to_pdf(file_bytes)

        # ── Step 2: Text extraction ────────────────────────────────────────────
        raw_cv_text = extract_text_from_pdf(file_bytes)

        await db.execute(
            text("""
                UPDATE application_files
                SET extracted_text = :text, extraction_status = 'done'
                WHERE application_id = :aid
            """),
            {"text": raw_cv_text, "aid": application_id},
        )

        # ── Step 3: Fetch job criteria + config ───────────────────────────────
        criteria_row = await db.execute(
            text("""
                SELECT jc.*, j.title AS job_title, j.description AS job_description
                FROM job_criteria jc
                JOIN jobs j ON j.job_id = jc.job_id
                WHERE jc.job_id = :jid
            """),
            {"jid": job_id},
        )
        criteria = criteria_row.mappings().first()
        if not criteria:
            raise RuntimeError(f"No criteria found for job {job_id}")

        prompt_cfg = await load_prompt_config(db, tenant_id, job_id, overrides=scoring_overrides)

        # Load active DB prompts; each falls back to hardcoded default if no active version
        level2_prompt = await load_active_prompt(db, "level2_screening")
        scoring_prompt = await load_active_prompt(db, "cv_scoring")

        weights = {
            "weight_skills":           prompt_cfg.weights.get("weight_skills", criteria["weight_skills"]),
            "weight_experience":       prompt_cfg.weights.get("weight_experience", criteria["weight_experience"]),
            "weight_education":        prompt_cfg.weights.get("weight_education", criteria["weight_education"]),
            "weight_certifications":   prompt_cfg.weights.get("weight_certifications", criteria["weight_certifications"]),
            "weight_soft_skills":      prompt_cfg.weights.get("weight_soft_skills", criteria["weight_soft_skills"]),
            "weight_domain_knowledge": prompt_cfg.weights.get("weight_domain_knowledge", criteria["weight_domain_knowledge"]),
            "weight_other":            prompt_cfg.weights.get("weight_other", criteria["weight_other"]),
        }

        required_skills = list(criteria.get("skills") or []) + list(criteria.get("certifications") or [])

        # ════════════════════════════════════════════════════════════════════════
        # LEVEL 1 — Local Gatekeeper (free, no LLM call)
        # ════════════════════════════════════════════════════════════════════════
        gatekeeper_result = run_gatekeeper(
            cv_text=raw_cv_text,
            job_description=criteria["job_description"],
            required_skills=required_skills,
            semantic_threshold=prompt_cfg.gatekeeper_threshold,
            skill_threshold=cfg.gatekeeper_skill_fuzzy_threshold,
        )

        if prompt_cfg.gatekeeper_enabled and not gatekeeper_result.gatekeeper_passed:
            logger.info(
                "Level 1 REJECTED application %s — similarity=%.1f%% threshold=%.0f%%",
                application_id,
                gatekeeper_result.semantic_similarity_pct,
                prompt_cfg.gatekeeper_threshold * 100,
            )

            await db.execute(
                text("""
                    UPDATE applications SET
                        gatekeeper_passed    = false,
                        evaluation_stage     = 1,
                        evaluation_exit_reason = :reason,
                        processing_status   = 'low_match',
                        decision            = 'low_match',
                        scored_at           = now()
                    WHERE application_id = :aid
                """),
                {"reason": gatekeeper_result.rejection_reason, "aid": application_id},
            )
            await db.execute(
                text("""
                    INSERT INTO application_scores (
                        application_id,
                        score_skills, score_experience, score_education,
                        score_certifications, score_soft_skills,
                        score_domain_knowledge, score_other,
                        final_score, weights_snapshot, ai_model,
                        local_similarity_score, skill_match_ratio,
                        matched_skills, missing_skills,
                        cv_language, gatekeeper_passed,
                        evaluation_notes, reasoning
                    ) VALUES (
                        :aid,
                        0, 0, 0, 0, 0, 0, 0,
                        0, :weights, 'gatekeeper_filtered',
                        :sim, :skill_ratio,
                        :matched, :missing,
                        :cv_lang, false,
                        :notes, :reasoning
                    )
                """),
                {
                    "aid":        application_id,
                    "weights":    json.dumps(weights),
                    "sim":        gatekeeper_result.semantic_similarity_pct,
                    "skill_ratio": gatekeeper_result.skill_match_ratio,
                    "matched":    gatekeeper_result.matched_skills,
                    "missing":    gatekeeper_result.missing_skills,
                    "cv_lang":    gatekeeper_result.cv_language,
                    "notes":      gatekeeper_result.rejection_reason,
                    "reasoning":  json.dumps({"level1_gatekeeper": gatekeeper_result.rejection_reason}),
                },
            )
            await db.commit()
            return

        # Level 1 passed — persist gatekeeper data + stage so frontend shows progress
        await db.execute(
            text("""
                UPDATE applications SET
                    gatekeeper_passed = true,
                    evaluation_stage  = 1
                WHERE application_id = :aid
            """),
            {"aid": application_id},
        )
        await db.commit()  # also commits extracted_text update from Step 2

        # ════════════════════════════════════════════════════════════════════════
        # LEVEL 2 — Lightweight LLM binary screen (cheap: short prompt, 120 tokens)
        # ════════════════════════════════════════════════════════════════════════
        level2 = await lightweight_screen_cv(
            cv_text=gatekeeper_result.cleaned_cv_text,
            job_title=criteria["job_title"],
            required_skills=required_skills,
            prompt_override=level2_prompt,
        )

        if level2["decision"] == "REJECT":
            logger.info(
                "Level 2 REJECTED application %s — %s",
                application_id, level2["reason"],
            )

            await db.execute(
                text("""
                    UPDATE applications SET
                        evaluation_stage       = 2,
                        evaluation_exit_reason = :reason,
                        processing_status      = 'scored',
                        decision               = 'rejected',
                        scored_at              = now()
                    WHERE application_id = :aid
                """),
                {"reason": level2["reason"], "aid": application_id},
            )
            await db.execute(
                text("""
                    INSERT INTO application_scores (
                        application_id,
                        score_skills, score_experience, score_education,
                        score_certifications, score_soft_skills,
                        score_domain_knowledge, score_other,
                        final_score, weights_snapshot, ai_model,
                        local_similarity_score, skill_match_ratio,
                        matched_skills, missing_skills,
                        cv_language, gatekeeper_passed,
                        evaluation_notes, reasoning
                    ) VALUES (
                        :aid,
                        0, 0, 0, 0, 0, 0, 0,
                        0, :weights, 'lightweight_screener',
                        :sim, :skill_ratio,
                        :matched, :missing,
                        :cv_lang, true,
                        :notes, :reasoning
                    )
                """),
                {
                    "aid":        application_id,
                    "weights":    json.dumps(weights),
                    "sim":        gatekeeper_result.semantic_similarity_pct,
                    "skill_ratio": gatekeeper_result.skill_match_ratio,
                    "matched":    gatekeeper_result.matched_skills,
                    "missing":    gatekeeper_result.missing_skills,
                    "cv_lang":    gatekeeper_result.cv_language,
                    "notes":      level2["reason"],
                    "reasoning":  json.dumps({"level2_screen": level2["reason"]}),
                },
            )
            await db.commit()
            return

        # Level 2 passed — update stage so frontend shows "Level 3: Full scoring..."
        await db.execute(
            text("UPDATE applications SET evaluation_stage = 2 WHERE application_id = :aid"),
            {"aid": application_id},
        )
        await db.commit()

        # ════════════════════════════════════════════════════════════════════════
        # LEVEL 3 — Full LLM scoring (complete evaluation)
        # ════════════════════════════════════════════════════════════════════════
        criteria_dict = {
            "skills":             criteria["skills"],
            "experience":         criteria["experience"],
            "education":          criteria["education"],
            "certifications":     criteria["certifications"],
            "soft_skills":        criteria["soft_skills"],
            "domain_knowledge":   criteria["domain_knowledge"],
            "other_requirements": criteria["other_requirements"],
            **weights,
        }

        gatekeeper_context = {
            "semantic_similarity_pct": gatekeeper_result.semantic_similarity_pct,
            "matched_skills":          gatekeeper_result.matched_skills,
            "missing_skills":          gatekeeper_result.missing_skills,
        }

        ai_result = await score_cv(
            cv_text=gatekeeper_result.cleaned_cv_text,
            criteria=criteria_dict,
            job_title=criteria["job_title"],
            cv_language=gatekeeper_result.cv_language,
            gatekeeper_context=gatekeeper_context,
            prompt_override=scoring_prompt,
        )

        final_score = compute_final_score(ai_result, weights)
        q_thresh, p_thresh = await get_thresholds(db, tenant_id, job_id)
        decision = determine_decision(final_score, q_thresh, p_thresh)

        extracted_name = (ai_result.get("candidate_name") or "").strip()
        if extracted_name:
            await db.execute(
                text("UPDATE applications SET candidate_name = :name WHERE application_id = :aid"),
                {"name": extracted_name, "aid": application_id},
            )

        await db.execute(
            text("""
                INSERT INTO application_scores (
                    application_id,
                    score_skills, score_experience, score_education,
                    score_certifications, score_soft_skills,
                    score_domain_knowledge, score_other,
                    final_score, weights_snapshot, ai_model,
                    strengths, gaps_identified, red_flags,
                    evaluation_notes, interview_questions,
                    reasoning, raw_ai_response,
                    local_similarity_score, skill_match_ratio,
                    matched_skills, missing_skills,
                    cv_language, gatekeeper_passed
                ) VALUES (
                    :aid,
                    :s_skills, :s_exp, :s_edu, :s_cert, :s_soft, :s_domain, :s_other,
                    :final, :weights, :model,
                    :strengths, :gaps, :red_flags,
                    :notes, :questions,
                    :reasoning, :raw,
                    :sim, :skill_ratio,
                    :matched, :missing,
                    :cv_lang, :gk_passed
                )
            """),
            {
                "aid":       application_id,
                "s_skills":  ai_result.get("score_skills", 0),
                "s_exp":     ai_result.get("score_experience", 0),
                "s_edu":     ai_result.get("score_education", 0),
                "s_cert":    ai_result.get("score_certifications", 0),
                "s_soft":    ai_result.get("score_soft_skills", 0),
                "s_domain":  ai_result.get("score_domain_knowledge", 0),
                "s_other":   ai_result.get("score_other", 0),
                "final":     final_score,
                "weights":   json.dumps(weights),
                "model":     cfg.openai_model,
                "strengths": ai_result.get("strengths", []),
                "gaps":      ai_result.get("gaps_identified", []),
                "red_flags": ai_result.get("red_flags", []),
                "notes":     ai_result.get("evaluation_notes"),
                "questions": ai_result.get("interview_questions", []),
                "reasoning": json.dumps(ai_result.get("reasoning", {}), ensure_ascii=False),
                "raw":       json.dumps(ai_result, ensure_ascii=False),
                "sim":       gatekeeper_result.semantic_similarity_pct,
                "skill_ratio": gatekeeper_result.skill_match_ratio,
                "matched":   gatekeeper_result.matched_skills,
                "missing":   gatekeeper_result.missing_skills,
                "cv_lang":   gatekeeper_result.cv_language,
                "gk_passed": gatekeeper_result.gatekeeper_passed,
            },
        )

        await db.execute(
            text("""
                UPDATE applications SET
                    decision                = :decision,
                    processing_status       = 'scored',
                    evaluation_stage        = 3,
                    qualified_threshold_used = :qt,
                    partial_threshold_used  = :pt,
                    scored_at               = now()
                WHERE application_id = :aid
            """),
            {"decision": decision, "qt": q_thresh, "pt": p_thresh, "aid": application_id},
        )
        await db.commit()

        logger.info(
            "Level 3 SCORED application %s | lang=%s | sim=%.1f%% | final=%.2f | decision=%s",
            application_id,
            gatekeeper_result.cv_language,
            gatekeeper_result.semantic_similarity_pct,
            final_score,
            decision,
        )

        # ── Confirmation email ─────────────────────────────────────────────────
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
        await db.commit()
