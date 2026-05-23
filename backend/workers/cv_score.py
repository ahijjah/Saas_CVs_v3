"""
Celery task: score a CV through the 2-level evaluation pipeline.

Pipeline:
  1. File read + DOCX→PDF conversion
  2. PDF text extraction (PyMuPDF)
  3. Level 1 — Local Gatekeeper (semantic similarity + bilingual skill matching)
     → Below threshold: mark scored/rejected, evaluation_stage=1, skip LLM (cost saving)
  4. Level 3 — Full LLM scoring (GPT-4o bilingual)
     → Produces final score (ceiling integer), decision, score_details, candidate contacts
  5. Optional — AI comparison run (secondary scorer, if job toggle enabled)
  6. Write application_scores + update applications table
  7. Send confirmation email per job toggle settings

Event-loop safety
-----------------
Celery fork workers inherit the parent process's event loop state.  Using the
module-level `asyncio.run()` on a shared `AsyncEngine` (connection pool) causes
two classic errors:

  * "got Future <Future ...> attached to a different loop"
  * "Event loop is closed"

Fixes applied here:
  * Each task invocation creates its own NullPool engine + sessionmaker and
    passes them into ``_score_cv_async``.  NullPool never pools connections,
    so there are no cross-loop Future references between tasks or the FastAPI
    process.  The engine is disposed in the outer ``finally`` after the loop
    closes.
  * Each task invocation owns its own event loop via ``asyncio.new_event_loop()``.
    The loop is explicitly closed in ``finally``.
  * `_mark_failed()` uses an *isolated* NullPool engine so it never touches
    another task's engine, making it safe to call from any loop context.
  * The outer try/except in `_score_cv_async` calls `_mark_failed()` only
    *after* the SQLAlchemy session context-manager has fully exited (rollback
    + close already done), so there is no interference between sessions.
  * `_scoring_committed` flag prevents falsely marking an already-scored
    application as failed if a non-critical post-scoring step raises.
"""
import asyncio
import json
import logging
from pathlib import Path

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Event-loop helpers ────────────────────────────────────────────────────────

def _run_in_fresh_loop(coro):
    """
    Run *coro* on a brand-new event loop that is completely isolated from any
    previously-used loop.  Always closes the loop in ``finally``.

    Used for the max-retries failure path where the task's primary loop may
    already be closed or in an error state.
    """
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


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="workers.cv_score.score_cv_task",
)
def score_cv_task(
    self,
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_path: str,
    mime_type: str,
    scoring_overrides: dict | None = None,
):
    """Score a CV file through the evaluation pipeline."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from config import get_settings

    cfg = get_settings()
    # Task-local NullPool engine: never shares connections with other tasks or
    # the FastAPI process pool, so there are no cross-loop Future references.
    task_engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    TaskSession = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)

    # Create an isolated event loop for this task invocation.  Celery fork
    # workers inherit the parent's loop state; a fresh loop prevents
    # "Future attached to different loop" / "Event loop is closed" errors.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _score_cv_async(
                application_id, job_id, tenant_id, file_path, mime_type,
                scoring_overrides or {},
                TaskSession,
            )
        )
    except Exception as exc:
        attempt = self.request.retries + 1
        logger.error(
            "[%s] score_cv_task failed (attempt %d/%d): %s",
            application_id, attempt, self.max_retries + 1, exc,
            exc_info=True,
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                "[%s] Max retries exceeded — marking application failed", application_id
            )
            _run_in_fresh_loop(
                _mark_failed(
                    application_id,
                    f"Max retries exceeded after {self.max_retries + 1} attempts. "
                    f"Last error: {exc}",
                )
            )
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        task_engine.dispose()


# ── Gatekeeper decision helper ────────────────────────────────────────────────

def _evaluate_gatekeeper_decision(gatekeeper_result, prompt_cfg) -> str:
    """
    Evaluate the gatekeeper result against the configurable bilingual
    dual-threshold auto-reject policy.

    Returns one of:
      'auto_reject_disabled'   — master switch or gatekeeper_enabled is off
      'clear_mismatch_rejected' — BOTH similarity AND skill_ratio below their
                                  language-specific floors → reject locally
      'uncertain_sent_to_ai'   — original gatekeeper would reject but only ONE
                                  threshold breached → forward to AI
      'passed_to_ai'           — gatekeeper passed normally (no rejection)

    Does NOT mutate gatekeeper_result; callers handle state changes.
    """
    if not prompt_cfg.gatekeeper_enabled or not prompt_cfg.gatekeeper_auto_reject_enabled:
        return "auto_reject_disabled"

    sim        = gatekeeper_result.semantic_similarity   # 0.0–1.0
    skill_pct  = gatekeeper_result.skill_match_ratio     # 0–100
    lang       = gatekeeper_result.cv_language           # 'en' | 'ar' | 'mixed' | other

    if lang == "en":
        sim_floor   = prompt_cfg.gatekeeper_english_similarity_reject_below
        skill_floor = prompt_cfg.gatekeeper_english_skill_ratio_reject_below
    else:
        sim_floor   = prompt_cfg.gatekeeper_non_english_similarity_reject_below
        skill_floor = prompt_cfg.gatekeeper_non_english_skill_ratio_reject_below

    if sim < sim_floor and skill_pct < skill_floor:
        return "clear_mismatch_rejected"

    # Original gatekeeper would have rejected but the stricter dual-threshold
    # didn't fire → uncertain case, let AI decide.
    if not gatekeeper_result.gatekeeper_passed:
        return "uncertain_sent_to_ai"

    return "passed_to_ai"


# ── Main async pipeline ───────────────────────────────────────────────────────

async def _score_cv_async(
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_path: str,
    mime_type: str,
    scoring_overrides: dict,
    Session,
) -> None:
    from config import get_settings
    from database import set_rls_context
    from services.ai_service import (
        compute_final_score,
        determine_decision,
        load_active_prompt,
        score_cv,
    )
    from services.docx_service import convert_docx_to_pdf
    from services.email_service import send_cv_received_email
    from services.llm_provider import get_comparison_client_async
    from services.duplicate_detection import (
        compute_normalized_text_hash,
        check_exact_content_duplicate,
    )
    from services.local_processor import run_gatekeeper
    from services.pdf_service import extract_text_from_pdf
    from services.prompt_config import load_prompt_config
    from services.threshold_service import get_thresholds
    from sqlalchemy import text

    cfg = get_settings()

    logger.info("[%s] START scoring pipeline", application_id)

    # _scoring_committed is set True only after the final application_scores
    # INSERT + applications UPDATE commit.  Used to guard _mark_failed so we
    # never overwrite an already-scored result if a post-scoring step raises.
    _scoring_committed = False

    try:
        async with Session() as db:
            await set_rls_context(db, tenant_id, "super_admin")

            # ── Mark processing ───────────────────────────────────────────────
            logger.info("[%s] status → processing", application_id)
            await db.execute(
                text(
                    "UPDATE applications SET processing_status = 'processing' "
                    "WHERE application_id = :aid"
                ),
                {"aid": application_id},
            )
            await db.commit()

            # ── Step 1: File read + optional DOCX→PDF conversion ─────────────
            logger.info("[%s] Reading file: %s", application_id, file_path)
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"CV file not found: {file_path}")
            file_bytes = path.read_bytes()

            if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                logger.info("[%s] Converting DOCX → PDF", application_id)
                file_bytes = await convert_docx_to_pdf(file_bytes)

            # ── Step 2: Text extraction ───────────────────────────────────────
            logger.info("[%s] Extracting PDF text", application_id)
            raw_cv_text = extract_text_from_pdf(file_bytes)

            # Compute normalized hash immediately after extraction — used for
            # exact content duplicate detection in Step 2b below.
            normalized_text_hash = compute_normalized_text_hash(raw_cv_text)

            await db.execute(
                text("""
                    UPDATE application_files
                    SET extracted_text        = :text,
                        extraction_status     = 'done',
                        normalized_text_hash  = :nhash
                    WHERE application_id = :aid
                """),
                {"text": raw_cv_text, "nhash": normalized_text_hash, "aid": application_id},
            )

            # ── Load scoring config early — needed for quality gate + gatekeeper
            prompt_cfg = await load_prompt_config(db, tenant_id, job_id, overrides=scoring_overrides)
            scoring_prompt = await load_active_prompt(db, "cv_scoring")

            # ── Step 2a: Extraction quality gate ──────────────────────────────
            _text_len = len((raw_cv_text or "").strip())
            if _text_len < prompt_cfg.min_extracted_text_chars:
                _exit_reason = (
                    f"Extracted CV text is too short or unreadable "
                    f"({_text_len} chars extracted, minimum required: "
                    f"{prompt_cfg.min_extracted_text_chars})"
                )
                logger.warning("[%s] Text quality gate FAILED: %s", application_id, _exit_reason)
                await db.execute(
                    text("""
                        UPDATE applications SET
                            processing_status      = 'failed',
                            evaluation_exit_reason = :reason,
                            scored_at              = now()
                        WHERE application_id = :aid
                    """),
                    {"reason": _exit_reason, "aid": application_id},
                )
                await db.execute(
                    text("""
                        UPDATE application_files SET extraction_status = 'failed'
                        WHERE application_id = :aid
                    """),
                    {"aid": application_id},
                )
                await db.commit()
                _scoring_committed = True
                return

            # ── Step 2b: Exact content duplicate pre-screening ────────────────
            # Uses normalized_text_hash for O(1) lookup — format-agnostic
            # (same CV exported as PDF vs DOCX produces the same hash).
            # Applies uniformly to ALL intake methods; no application is deleted.
            logger.info("[%s] Exact content duplicate pre-screening", application_id)
            content_dup = await check_exact_content_duplicate(
                db=db,
                application_id=application_id,
                job_id=job_id,
                tenant_id=tenant_id,
                normalized_hash=normalized_text_hash,
            )
            if content_dup:
                ref_id = content_dup["application_id"]
                logger.info(
                    "[%s] Exact content duplicate detected — ref=%s stopping scoring",
                    application_id, ref_id,
                )
                await db.execute(
                    text("""
                        UPDATE applications SET
                            duplicate_status                   = 'exact_duplicate',
                            duplicate_reference_application_id = :ref_id,
                            duplicate_reason                   = 'normalized_text_hash',
                            duplicate_checked_at               = now(),
                            evaluation_stage                   = 1,
                            evaluation_exit_reason             = :reason,
                            processing_status                  = 'scored',
                            decision                           = 'rejected',
                            scored_at                          = now()
                        WHERE application_id = :aid
                    """),
                    {
                        "ref_id": ref_id,
                        "reason": (
                            f"Exact content duplicate of application "
                            f"{ref_id[:8]}… — identical CV content already on file."
                        ),
                        "aid": application_id,
                    },
                )
                await db.commit()
                _scoring_committed = True
                return

            # ── Step 3: Fetch job criteria + scoring config ───────────────────
            logger.info("[%s] Fetching job criteria", application_id)
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

            weights = {
                "weight_skills":           criteria["weight_skills"],
                "weight_experience":       criteria["weight_experience"],
                "weight_education":        criteria["weight_education"],
                "weight_certifications":   criteria["weight_certifications"],
                "weight_soft_skills":      criteria["weight_soft_skills"],
                "weight_domain_knowledge": criteria["weight_domain_knowledge"],
                "weight_other":            criteria["weight_other"],
            }
            # Only apply per-dimension overrides when scoring_overrides explicitly
            # supplies them (e.g. a one-off re-score request).  prompt_cfg.weights
            # (system-profile defaults) must NOT override job-specific weights.
            _WEIGHT_KEYS = [
                "weight_skills", "weight_experience", "weight_education",
                "weight_certifications", "weight_soft_skills",
                "weight_domain_knowledge", "weight_other",
            ]
            for _wk in _WEIGHT_KEYS:
                if scoring_overrides.get(_wk) is not None and isinstance(scoring_overrides[_wk], int):
                    weights[_wk] = scoring_overrides[_wk]

            logger.info(
                "[%s] Effective scoring weights: %s",
                application_id, weights,
            )

            required_skills = (
                list(criteria.get("skills") or []) + list(criteria.get("certifications") or [])
            )

            # ════════════════════════════════════════════════════════════════
            # LEVEL 1 — Local Gatekeeper
            # All parameters sourced from system_config via prompt_cfg.
            # ════════════════════════════════════════════════════════════════
            gk_params = {
                "semantic_threshold":    prompt_cfg.gatekeeper_threshold,
                "skill_fuzzy_threshold": prompt_cfg.skill_fuzzy_threshold,
                "min_skill_ratio":       prompt_cfg.min_skill_ratio,
            }
            logger.info(
                "[%s] Gatekeeper params: enabled=%s sim_threshold=%.2f "
                "skill_fuzzy=%.0f min_skill_ratio=%.0f",
                application_id,
                prompt_cfg.gatekeeper_enabled,
                gk_params["semantic_threshold"],
                gk_params["skill_fuzzy_threshold"],
                gk_params["min_skill_ratio"],
            )

            gatekeeper_result = run_gatekeeper(
                cv_text=raw_cv_text,
                job_description=criteria["job_description"],
                required_skills=required_skills,
                semantic_threshold=gk_params["semantic_threshold"],
                skill_threshold=gk_params["skill_fuzzy_threshold"],
                min_skill_ratio=gk_params["min_skill_ratio"],
            )

            # ── Bilingual dual-threshold auto-reject policy ───────────────────
            # Decision zones:
            #   clear_mismatch_rejected  — both sim AND skill_ratio below their floors → reject
            #   uncertain_sent_to_ai     — gatekeeper would reject but only one threshold breached
            #   passed_to_ai             — gatekeeper passed normally
            #   auto_reject_disabled     — master switch or gatekeeper_enabled is off
            decision_zone = _evaluate_gatekeeper_decision(gatekeeper_result, prompt_cfg)
            logger.info(
                "[%s] Gatekeeper zone=%s lang=%s sim=%.1f%% skill_ratio=%.1f%%",
                application_id, decision_zone, gatekeeper_result.cv_language,
                gatekeeper_result.semantic_similarity_pct, gatekeeper_result.skill_match_ratio,
            )

            bypass_reason: str | None = None

            if decision_zone == "auto_reject_disabled":
                gatekeeper_result.gatekeeper_passed = True
                bypass_reason = (
                    "Gatekeeper auto-reject disabled by system configuration; "
                    "passed to AI scoring."
                )

            elif decision_zone == "uncertain_sent_to_ai":
                gatekeeper_result.gatekeeper_passed = True
                bypass_reason = (
                    f"Gatekeeper uncertain zone (lang={gatekeeper_result.cv_language}): "
                    f"sim={gatekeeper_result.semantic_similarity_pct:.1f}% "
                    f"skill_ratio={gatekeeper_result.skill_match_ratio:.1f}%; "
                    f"passing to AI scoring."
                )

            elif decision_zone == "clear_mismatch_rejected":
                _exit_reason = (
                    f"[{decision_zone}] "
                    + (gatekeeper_result.rejection_reason or "CV does not match job requirements.")
                )
                _reasoning_payload = {
                    "level1_gatekeeper": {
                        "decision_zone":            decision_zone,
                        "semantic_similarity_pct":  gatekeeper_result.semantic_similarity_pct,
                        "skill_match_ratio":         gatekeeper_result.skill_match_ratio,
                        "matched_skills":            gatekeeper_result.matched_skills,
                        "missing_skills":            gatekeeper_result.missing_skills,
                        "cv_language":               gatekeeper_result.cv_language,
                        "rejection_reason":          gatekeeper_result.rejection_reason,
                    }
                }
                logger.info(
                    "[%s] Gatekeeper REJECTED [clear_mismatch] lang=%s sim=%.1f%% skill_ratio=%.1f%%",
                    application_id, gatekeeper_result.cv_language,
                    gatekeeper_result.semantic_similarity_pct, gatekeeper_result.skill_match_ratio,
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
                    {"reason": _exit_reason, "aid": application_id},
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
                        "notes":      _exit_reason,
                        "reasoning":  json.dumps(_reasoning_payload, ensure_ascii=False),
                    },
                )
                await db.commit()
                _scoring_committed = True
                return

            # Level 1 passed (or bypassed) — persist gatekeeper data + stage
            level1_exit_reason = bypass_reason
            await db.execute(
                text("""
                    UPDATE applications SET
                        gatekeeper_passed = true,
                        evaluation_stage  = 1
                        {bypass_clause}
                    WHERE application_id = :aid
                """.replace(
                    "{bypass_clause}",
                    ", evaluation_exit_reason = :exit_reason" if level1_exit_reason else "",
                )),
                {"aid": application_id, **({"exit_reason": level1_exit_reason} if level1_exit_reason else {})},
            )
            await db.commit()
            logger.info(
                "[%s] Gatekeeper PASSED sim=%.1f%% skill_ratio=%.1f%% "
                "matched=%s missing=%s",
                application_id,
                gatekeeper_result.semantic_similarity_pct,
                gatekeeper_result.skill_match_ratio,
                gatekeeper_result.matched_skills,
                gatekeeper_result.missing_skills,
            )

            # ════════════════════════════════════════════════════════════════
            # LEVEL 3 — Full LLM scoring
            # ════════════════════════════════════════════════════════════════
            logger.info("[%s] Starting L3 AI scoring", application_id)
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

            # ── Scoring committed — from here on failures are non-critical ────
            _scoring_committed = True
            logger.info(
                "[%s] L3 SCORED lang=%s sim=%.1f%% final=%d decision=%s",
                application_id,
                gatekeeper_result.cv_language,
                gatekeeper_result.semantic_similarity_pct,
                final_score,
                decision,
            )

            # ── Post-scoring: optional AI comparison ──────────────────────────
            # Job-level flag takes priority; falls back to system default.
            _job_comparison_flag = criteria.get("enable_ai_comparison")
            _run_comparison = (
                _job_comparison_flag
                if _job_comparison_flag is not None
                else prompt_cfg.enable_ai_comparison_default
            )
            if _run_comparison:
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
                            "[%s] Comparison score: provider=%s final=%d",
                            application_id, comparison_client.provider, comp_final,
                        )
                except Exception as comp_exc:
                    logger.warning(
                        "[%s] AI comparison failed (non-critical): %s",
                        application_id, comp_exc,
                    )

            # ── Post-scoring: confirmation email ──────────────────────────────
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
                    cv_email = (
                        app_data["confirmation_email_recipient"]
                        or app_data["candidate_email_from_cv"]
                        or app_data["candidate_email"]
                    )
                    if source == "manual_upload":
                        if cv_email and criteria.get("send_confirmation_to_cv_email_for_upload", False):
                            await send_cv_received_email(
                                to_email=cv_email,
                                candidate_name=app_data["candidate_name"],
                                job_title=criteria["job_title"],
                            )
                    elif source == "public_apply":
                        if cv_email:
                            await send_cv_received_email(
                                to_email=cv_email,
                                candidate_name=app_data["candidate_name"],
                                job_title=criteria["job_title"],
                            )
                    # email_forwarding / platform_email: confirmation sent at intake
                    # time by intake_notification_service — do not duplicate here.
            except Exception as email_exc:
                logger.warning(
                    "[%s] Confirmation email failed (non-critical): %s",
                    application_id, email_exc,
                )

    except Exception as exc:
        # Session context manager has already rolled back and closed the
        # connection by the time we reach here — safe to open a new session.
        logger.error(
            "[%s] Pipeline failed (scoring_committed=%s): %s",
            application_id, _scoring_committed, exc,
            exc_info=True,
        )
        if not _scoring_committed:
            await _mark_failed(application_id, str(exc))
        raise


# ── Duplicate-to-log conversion ───────────────────────────────────────────────

async def _convert_manual_dup_to_log(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    mime_type: str,
    file_path: str,
    app_data: dict,
    dup_info: dict,
) -> None:
    """
    Move a manual-upload application that has high_content_similarity into
    duplicate_application_logs and delete the transient application record.

    Called from Step 2b of the scoring pipeline.  *db* is already open with
    RLS set.  Raises on any failure — the caller handles cleanup.
    """
    import uuid as _uuid
    from pathlib import Path as _Path
    from config import get_settings as _get_settings
    from sqlalchemy import text

    cfg = _get_settings()
    logger.info(
        "[%s] _convert_manual_dup_to_log: ref=%s",
        application_id,
        dup_info.get("duplicate_reference_application_id"),
    )

    file_row = await db.execute(
        text("""
            SELECT file_path, original_name, mime_type, file_size_bytes
            FROM application_files WHERE application_id = :aid LIMIT 1
        """),
        {"aid": application_id},
    )
    file_meta = file_row.mappings().first()

    dup_file_path: str | None = None
    dup_orig_name: str | None = None
    dup_content_type: str | None = None
    dup_file_size: int | None = None

    if file_meta and file_meta["file_path"]:
        src = _Path(cfg.files_base_path) / file_meta["file_path"]
        ext = _Path(file_meta["file_path"]).suffix.lstrip(".")
        log_id_for_file = str(_uuid.uuid4())
        dup_dir = (
            _Path(cfg.files_base_path)
            / "tenants" / tenant_id / "jobs" / job_id / "duplicates"
        )
        dup_dir.mkdir(parents=True, exist_ok=True)
        dst = dup_dir / f"{log_id_for_file}.{ext}"
        if src.exists():
            import shutil
            shutil.move(str(src), str(dst))
            dup_file_path = str(dst.relative_to(cfg.files_base_path))
        dup_orig_name = file_meta["original_name"]
        dup_content_type = file_meta["mime_type"]
        dup_file_size = file_meta["file_size_bytes"]
    else:
        log_id_for_file = str(_uuid.uuid4())

    ref_id = (
        str(dup_info["duplicate_reference_application_id"])
        if dup_info["duplicate_reference_application_id"] else None
    )
    sim_score = (
        float(dup_info["duplicate_similarity_score"])
        if dup_info["duplicate_similarity_score"] is not None else None
    )

    await db.execute(
        text("""
            INSERT INTO duplicate_application_logs
                (log_id, tenant_id, job_id,
                 duplicate_email, duplicate_name,
                 attachment_hash, received_at,
                 original_application_id,
                 raw_filename, notes, source,
                 submitted_by_user_id, submitted_by_name, submitted_by_email,
                 duplicate_file_path, duplicate_original_filename,
                 duplicate_content_type, duplicate_file_size_bytes,
                 duplicate_reason, duplicate_similarity_score)
            VALUES
                (:log_id, :tenant_id, :job_id,
                 :email, :name,
                 NULL, NOW(),
                 :orig_id,
                 :filename, :notes, 'manual_upload',
                 :uploader_id, :uploader_name, :uploader_email,
                 :dup_file_path, :dup_orig_name,
                 :dup_content_type, :dup_file_size,
                 'high_content_similarity', :similarity_score)
        """),
        {
            "log_id":          log_id_for_file,
            "tenant_id":       tenant_id,
            "job_id":          job_id,
            "email":           app_data["candidate_email"],
            "name":            app_data["candidate_name"],
            "orig_id":         ref_id,
            "filename":        dup_orig_name,
            "notes": (
                f"Manual upload duplicate detected during scoring — "
                f"content similarity {sim_score:.1f}% ≥ 90% threshold."
                if sim_score else
                "Manual upload duplicate detected during scoring."
            ),
            "uploader_id":     str(app_data["submitted_by_user_id"]) if app_data["submitted_by_user_id"] else None,
            "uploader_name":   app_data["submitted_by_name"],
            "uploader_email":  app_data["submitted_by_email"],
            "dup_file_path":   dup_file_path,
            "dup_orig_name":   dup_orig_name,
            "dup_content_type": dup_content_type,
            "dup_file_size":   dup_file_size,
            "similarity_score": sim_score,
        },
    )

    # Delete files row first (no ON DELETE CASCADE), then the application.
    await db.execute(
        text("DELETE FROM application_files WHERE application_id = :aid"),
        {"aid": application_id},
    )
    await db.execute(
        text("DELETE FROM applications WHERE application_id = :aid"),
        {"aid": application_id},
    )
    await db.commit()

    logger.info(
        "[%s] Duplicate converted to log: ref=%s sim=%.1f",
        application_id, ref_id, sim_score or 0,
    )


# ── Failure marker ────────────────────────────────────────────────────────────

async def _mark_failed(application_id: str, error: str) -> None:
    """
    Mark an application as failed using an *isolated* NullPool engine.

    Isolation guarantees:
      * Never touches the shared module-level connection pool.
      * Safe to call from any event loop (fresh loop after max retries,
        or the current loop from an exception handler).
      * Only updates rows that are still in queued/processing — will not
        overwrite an already-scored application if a post-scoring step raises.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from config import get_settings
    from sqlalchemy import text

    cfg = get_settings()

    fail_engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    try:
        Session = async_sessionmaker(
            fail_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with Session() as db:
            # Set RLS to super_admin with empty tenant to access any row.
            await db.execute(
                text(
                    "SELECT set_config('app.current_tenant_id', '', true), "
                    "       set_config('app.current_role', 'super_admin', true)"
                )
            )
            result = await db.execute(
                text("""
                    UPDATE applications SET
                        processing_status      = 'failed',
                        evaluation_exit_reason = :err,
                        scored_at              = now()
                    WHERE application_id = :aid
                    AND   processing_status IN ('queued', 'processing')
                """),
                {"err": error[:1000], "aid": application_id},
            )
            await db.execute(
                text("""
                    UPDATE application_files SET extraction_status = 'failed'
                    WHERE application_id = :aid
                    AND   extraction_status = 'pending'
                """),
                {"aid": application_id},
            )
            await db.commit()
        rows_updated = result.rowcount if result else 0
        logger.info(
            "[%s] _mark_failed: rows_updated=%d reason=%.200s",
            application_id, rows_updated, error,
        )
    except Exception as mark_exc:
        logger.error(
            "[%s] _mark_failed itself failed: %s",
            application_id, mark_exc,
            exc_info=True,
        )
    finally:
        fail_engine.dispose()
