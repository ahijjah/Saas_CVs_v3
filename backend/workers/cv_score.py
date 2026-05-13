"""
Celery task: score a CV through the 3-level evaluation pipeline.

Pipeline:
  1. File read + DOCX→PDF conversion
  2. PDF text extraction (PyMuPDF)
  3. Level 1 — Local Gatekeeper (semantic similarity + bilingual skill matching)
     → Below threshold: mark scored/rejected, evaluation_stage=1, skip LLM (cost saving)
  4. Level 2 — Lightweight LLM binary screen (PASS/REJECT, ~10x cheaper than full)
     → REJECT: mark scored/rejected, evaluation_stage=2, skip full scoring
  5. Level 3 — Full LLM scoring (GPT-4o bilingual, output in English)
     → Produces final score (ceiling integer), decision, score_details, candidate contacts
  6. Optional — AI comparison run (DeepSeek secondary scorer, if job toggle enabled)
  7. Write application_scores + update applications table
  8. Send confirmation email per job toggle settings

evaluation_stage in the applications table is updated after each level so the
frontend can show live progress during polling.
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
    from services.llm_provider import get_comparison_client_async
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
                SELECT jc.*, j.title AS job_title, j.description AS job_description,
                       j.enable_ai_comparison,
                       j.send_confirmation_to_cv_email_for_upload,
                       j.send_confirmation_to_cv_email_for_forwarding,
                       j.send_confirmation_to_sender_for_forwarding,
                       j.send_confirmation_to_cv_email_for_platform_email
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

        # Load active DB prompts; each returns code+version for audit references
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
                        gatekeeper_passed      = false,
                        evaluation_stage       = 1,
                        evaluation_exit_reason = :reason,
                        processing_status      = 'scored',
                        decision               = 'rejected',
                        scored_at              = now()
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
                        evaluation_notes, reasoning,
                        scoring_provider
                    ) VALUES (
                        :aid,
                        0, 0, 0, 0, 0, 0, 0,
                        0, :weights, 'gatekeeper_filtered',
                        :sim, :skill_ratio,
                        :matched, :missing,
                        :cv_lang, false,
                        :notes, :reasoning,
                        'local'
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

        # Level 1 passed — persist gatekeeper data + stage
        await db.execute(
            text("""
                UPDATE applications SET
                    gatekeeper_passed = true,
                    evaluation_stage  = 1
                WHERE application_id = :aid
            """),
            {"aid": application_id},
        )
        await db.commit()

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
                        evaluation_notes, reasoning,
                        level2_prompt_code, level2_prompt_version,
                        scoring_provider
                    ) VALUES (
                        :aid,
                        0, 0, 0, 0, 0, 0, 0,
                        0, :weights, 'lightweight_screener',
                        :sim, :skill_ratio,
                        :matched, :missing,
                        :cv_lang, true,
                        :notes, :reasoning,
                        :l2_code, :l2_ver,
                        'openai'
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
                    "l2_code":    (level2_prompt or {}).get("prompt_code"),
                    "l2_ver":     (level2_prompt or {}).get("version"),
                },
            )
            await db.commit()
            return

        # Level 2 passed — update stage
        await db.execute(
            text("UPDATE applications SET evaluation_stage = 2 WHERE application_id = :aid"),
            {"aid": application_id},
        )
        await db.commit()

        # ════════════════════════════════════════════════════════════════════════
        # LEVEL 3 — Full LLM scoring
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

        # Update candidate info from AI extraction
        extracted_name  = (ai_result.get("candidate_name")  or "").strip()
        extracted_email = (ai_result.get("candidate_email") or "").strip()
        extracted_phone = (ai_result.get("candidate_phone") or "").strip()

        update_parts: list[str] = []
        update_params: dict = {"aid": application_id}

        if extracted_name:
            update_parts.append("candidate_name = :cname")
            update_params["cname"] = extracted_name
        if extracted_email:
            update_parts.append("candidate_email_from_cv = :cv_email")
            update_params["cv_email"] = extracted_email
        if extracted_phone:
            update_parts.append("candidate_phone_from_cv = :cv_phone")
            update_params["cv_phone"] = extracted_phone

        if update_parts:
            await db.execute(
                text(f"UPDATE applications SET {', '.join(update_parts)} WHERE application_id = :aid"),
                update_params,
            )

        score_details = ai_result.get("score_details") or {}

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
                    cv_language, gatekeeper_passed,
                    score_details,
                    level2_prompt_code, level2_prompt_version,
                    scoring_prompt_code, scoring_prompt_version,
                    scoring_provider
                ) VALUES (
                    :aid,
                    :s_skills, :s_exp, :s_edu, :s_cert, :s_soft, :s_domain, :s_other,
                    :final, :weights, :model,
                    :strengths, :gaps, :red_flags,
                    :notes, :questions,
                    :reasoning, :raw,
                    :sim, :skill_ratio,
                    :matched, :missing,
                    :cv_lang, :gk_passed,
                    :score_details,
                    :l2_code, :l2_ver,
                    :sc_code, :sc_ver,
                    'openai'
                )
            """),
            {
                "aid":        application_id,
                "s_skills":   ai_result.get("score_skills", 0),
                "s_exp":      ai_result.get("score_experience", 0),
                "s_edu":      ai_result.get("score_education", 0),
                "s_cert":     ai_result.get("score_certifications", 0),
                "s_soft":     ai_result.get("score_soft_skills", 0),
                "s_domain":   ai_result.get("score_domain_knowledge", 0),
                "s_other":    ai_result.get("score_other", 0),
                "final":      final_score,
                "weights":    json.dumps(weights),
                "model":      (scoring_prompt or {}).get("model") or cfg.openai_model,
                "strengths":  ai_result.get("strengths", []),
                "gaps":       ai_result.get("gaps_identified", []),
                "red_flags":  ai_result.get("red_flags", []),
                "notes":      ai_result.get("evaluation_notes"),
                "questions":  ai_result.get("interview_questions", []),
                "reasoning":  json.dumps(ai_result.get("reasoning", {}), ensure_ascii=False),
                "raw":        json.dumps(ai_result, ensure_ascii=False),
                "sim":        gatekeeper_result.semantic_similarity_pct,
                "skill_ratio": gatekeeper_result.skill_match_ratio,
                "matched":    gatekeeper_result.matched_skills,
                "missing":    gatekeeper_result.missing_skills,
                "cv_lang":    gatekeeper_result.cv_language,
                "gk_passed":  gatekeeper_result.gatekeeper_passed,
                "score_details": json.dumps(score_details, ensure_ascii=False),
                "l2_code":    (level2_prompt or {}).get("prompt_code"),
                "l2_ver":     (level2_prompt or {}).get("version"),
                "sc_code":    (scoring_prompt or {}).get("prompt_code"),
                "sc_ver":     (scoring_prompt or {}).get("version"),
            },
        )

        await db.execute(
            text("""
                UPDATE applications SET
                    decision                 = :decision,
                    processing_status        = 'scored',
                    evaluation_stage         = 3,
                    qualified_threshold_used = :qt,
                    partial_threshold_used   = :pt,
                    scored_at                = now()
                WHERE application_id = :aid
            """),
            {"decision": decision, "qt": q_thresh, "pt": p_thresh, "aid": application_id},
        )
        await db.commit()

        logger.info(
            "Level 3 SCORED application %s | lang=%s | sim=%.1f%% | final=%d | decision=%s",
            application_id,
            gatekeeper_result.cv_language,
            gatekeeper_result.semantic_similarity_pct,
            final_score,
            decision,
        )

        # ════════════════════════════════════════════════════════════════════════
        # OPTIONAL — AI comparison (secondary LLM provider)
        # ════════════════════════════════════════════════════════════════════════
        if criteria.get("enable_ai_comparison"):
            try:
                comparison_client = await get_comparison_client_async(db)
                if comparison_client is not None:
                    comp_result = await score_cv(
                        cv_text=gatekeeper_result.cleaned_cv_text,
                        criteria=criteria_dict,
                        job_title=criteria["job_title"],
                        cv_language=gatekeeper_result.cv_language,
                        gatekeeper_context=gatekeeper_context,
                        prompt_override=scoring_prompt,
                        openai_client=comparison_client.client,
                    )
                    comp_final = compute_final_score(comp_result, weights)
                    comp_score_details = comp_result.get("score_details") or {}

                    await db.execute(
                        text("""
                            INSERT INTO application_score_comparisons (
                                application_id, provider, model, final_score,
                                score_skills, score_experience, score_education,
                                score_certifications, score_soft_skills,
                                score_domain_knowledge, score_other,
                                score_details, weights_snapshot,
                                evaluation_notes, strengths, gaps_identified,
                                scoring_prompt_code, scoring_prompt_version,
                                raw_response
                            ) VALUES (
                                :aid, :provider, :model, :final,
                                :s_skills, :s_exp, :s_edu, :s_cert, :s_soft, :s_domain, :s_other,
                                :score_details, :weights,
                                :notes, :strengths, :gaps,
                                :sc_code, :sc_ver,
                                :raw
                            )
                        """),
                        {
                            "aid":          application_id,
                            "provider":     comparison_client.provider,
                            "model":        comparison_client.model,
                            "final":        comp_final,
                            "s_skills":     comp_result.get("score_skills", 0),
                            "s_exp":        comp_result.get("score_experience", 0),
                            "s_edu":        comp_result.get("score_education", 0),
                            "s_cert":       comp_result.get("score_certifications", 0),
                            "s_soft":       comp_result.get("score_soft_skills", 0),
                            "s_domain":     comp_result.get("score_domain_knowledge", 0),
                            "s_other":      comp_result.get("score_other", 0),
                            "score_details": json.dumps(comp_score_details, ensure_ascii=False),
                            "weights":      json.dumps(weights),
                            "notes":        comp_result.get("evaluation_notes"),
                            "strengths":    comp_result.get("strengths", []),
                            "gaps":         comp_result.get("gaps_identified", []),
                            "sc_code":      (scoring_prompt or {}).get("prompt_code"),
                            "sc_ver":       (scoring_prompt or {}).get("version"),
                            "raw":          json.dumps(comp_result, ensure_ascii=False),
                        },
                    )
                    await db.commit()
                    logger.info(
                        "Comparison score for application %s: provider=%s final=%d",
                        application_id, comparison_client.provider, comp_final,
                    )
            except Exception as exc:
                logger.warning("AI comparison scoring failed for %s: %s", application_id, exc)

        # ── Confirmation email (source-aware routing) ──────────────────────────
        try:
            app_row = await db.execute(
                text("""
                    SELECT candidate_email, candidate_email_from_cv,
                           candidate_name, confirmation_email_recipient,
                           submission_source, email_sender_address
                    FROM applications WHERE application_id = :aid
                """),
                {"aid": application_id},
            )
            app_data = app_row.mappings().first()
            if app_data:
                source = app_data["submission_source"] or "manual_upload"

                # CV owner email: explicit override → CV-extracted → submitted
                cv_email = (
                    app_data["confirmation_email_recipient"]
                    or app_data["candidate_email_from_cv"]
                    or app_data["candidate_email"]
                )
                sender_email = app_data["email_sender_address"]

                if source == "manual_upload":
                    # Only send to CV email if the toggle is on for uploads
                    if cv_email and criteria.get("send_confirmation_to_cv_email_for_upload", False):
                        await send_cv_received_email(
                            to_email=cv_email,
                            candidate_name=app_data["candidate_name"],
                            job_title=criteria["job_title"],
                        )

                elif source == "email_forwarding":
                    # Two independent sends: to CV owner and/or to the forwarding sender
                    if cv_email and criteria.get("send_confirmation_to_cv_email_for_forwarding", False):
                        await send_cv_received_email(
                            to_email=cv_email,
                            candidate_name=app_data["candidate_name"],
                            job_title=criteria["job_title"],
                        )
                    if sender_email and criteria.get("send_confirmation_to_sender_for_forwarding", True):
                        await send_cv_received_email(
                            to_email=sender_email,
                            candidate_name=app_data["candidate_name"],
                            job_title=criteria["job_title"],
                        )

                elif source == "platform_email":
                    if cv_email and criteria.get("send_confirmation_to_cv_email_for_platform_email", True):
                        await send_cv_received_email(
                            to_email=cv_email,
                            candidate_name=app_data["candidate_name"],
                            job_title=criteria["job_title"],
                        )

        except Exception as exc:
            logger.warning("Confirmation email failed for application %s: %s", application_id, exc)


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
