"""
Celery task: extract AI scoring criteria for a job asynchronously.

Flow:
  1. Mark job_criteria row as 'processing'
  2. Call OpenAI via extract_job_criteria()
  3. Flatten nested result to flat arrays for scoring pipeline
  4. Validate criteria quality (sufficient content or explicitly open/broad role)
  5. Qualifying context (Architecture C P2): only when the system_config flag
     scoring_v2.qualifying_context_analysis_enabled is exactly "true" AND the main
     analysis is 'completed', run the pinned candidate_qc-1 call on the JD text.
     Any QC failure is contained (failed_technical / failed_validation audit only):
     it never fails the main extraction and never triggers a Celery retry.
  6. Write analysis_json + flat arrays + weights to DB in ONE transaction that
     locks the job_criteria row (SELECT ... FOR UPDATE) and merges qualifying
     context against the values read under that lock (a recruiter-owned object is
     never overwritten; see services.qualifying_context.persistence)
     - 'completed' if quality check passes
     - 'failed'    if all criteria arrays are empty and role is not open/broad
  7. On max-retry failure: mark 'failed' with error message

Event-loop safety
-----------------
Same pattern as cv_score.py — each task invocation creates its own NullPool
engine + sessionmaker (no shared pool, no cross-loop Future references), owns
its own event loop, and uses an isolated NullPool engine in `_mark_failed`.
"""
import asyncio
import hashlib
import json
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _normalize_description_hash(description: str) -> str:
    """SHA-256 of whitespace-normalised lowercase description text."""
    normalized = " ".join((description or "").lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()

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


QC_FLAG_KEY = "scoring_v2.qualifying_context_analysis_enabled"


async def _qc_flag_enabled(db) -> bool:
    """Direct system_config lookup. Only the exact (trimmed, lower-cased) value "true" enables QC; a missing
    row, a DB error or any other value means OFF."""
    from sqlalchemy import text
    try:
        row = await db.execute(text("SELECT value FROM system_config WHERE key = :k"), {"k": QC_FLAG_KEY})
        value = row.scalar_one_or_none()
    except Exception as exc:                      # noqa: BLE001 - flag errors always mean OFF
        logger.warning("Qualifying-context flag lookup failed (treated as OFF): %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return False
    return isinstance(value, str) and value.strip().lower() == "true"


async def _run_qc(job_id: str, description: str):
    """One pinned candidate_qc-1 run. Never raises: every exception becomes a failed_technical result."""
    from datetime import datetime, timezone
    from services.qualifying_context import persistence, runner
    try:
        result = await runner.run_qualifying_context(description)
    except Exception as exc:                      # noqa: BLE001 - incl. PromptIntegrityError
        result = persistence.technical_failure(description, f"{type(exc).__name__}: {exc}")
    generated_at = datetime.now(timezone.utc).isoformat()
    if result.ok:
        logger.info("[job:%s] Qualifying context: ok (state=%s).", job_id, result.qualifying_context.state)
    else:
        logger.warning("[job:%s] Qualifying context: %s (%s).", job_id, result.status, result.error)
    return result, generated_at


def _load_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


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
def extract_criteria_task(self, job_id: str, description: str, job_metadata: dict | None = None) -> None:
    """Background task: extract AI criteria and update job_criteria row.

    job_metadata: optional dict with title, department, experience_level,
                  location, job_type, work_mode — passed to the AI for richer context.
    """
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
        loop.run_until_complete(_extract_async(job_id, description, TaskSession, job_metadata))
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


async def _extract_async(job_id: str, description: str, Session, job_metadata: dict | None = None) -> None:
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
        qc_enabled = await _qc_flag_enabled(db)

    logger.info("[job:%s] Criteria extraction started.", job_id)

    # Resolve stage model from registry — needs a fresh session (previous one was closed above)
    _crit_reg = None
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession as _AsyncSession
        from sqlalchemy.pool import NullPool as _NullPool
        from config import get_settings as _get_settings
        from database import set_rls_context as _set_rls_context
        from services.ai_model_registry_service import resolve_stage_client as _resolve_crit
        _reg_cfg = _get_settings()
        _reg_engine = create_async_engine(
            _reg_cfg.database_url,
            poolclass=_NullPool,
            connect_args={"server_settings": {"search_path": _reg_cfg.db_schema}},
        )
        _RegSession = async_sessionmaker(_reg_engine, class_=_AsyncSession, expire_on_commit=False)
        async with _RegSession() as _db_reg:
            await _set_rls_context(_db_reg, "", "super_admin")
            _crit_reg = await _resolve_crit(_db_reg, "cv_analyzer")
        await _reg_engine.dispose()
    except Exception as _reg_exc:
        logger.warning("[job:%s] Registry lookup failed (non-critical): %s", job_id, _reg_exc)
        _crit_reg = None

    _crit_prompt = criteria_prompt
    if _crit_reg:
        _crit_prompt = {**(criteria_prompt or {}), "model": _crit_reg.model_name}

    analysis = await extract_job_criteria(
        description,
        prompt_override=_crit_prompt,
        openai_client=_crit_reg.client if _crit_reg else None,
        job_metadata=job_metadata,
    )
    flat = flatten_criteria_for_scoring(analysis)

    is_sufficient, is_open_broad, quality_error = _check_criteria_quality(analysis, flat)

    if is_sufficient:
        final_status = "completed"
        final_error = None
        extracted_at_value = datetime.now(timezone.utc)
        desc_hash_value = None   # preserve any existing failed hash via COALESCE
        failed_at_value = None
        log_suffix = "open/broad role — no specific criteria" if is_open_broad else "OK"
        logger.info("[job:%s] Criteria quality check passed (%s).", job_id, log_suffix)
    else:
        final_status = "insufficient"
        final_error = quality_error
        extracted_at_value = None
        desc_hash_value = _normalize_description_hash(description)
        failed_at_value = datetime.now(timezone.utc)
        logger.warning(
            "[job:%s] Criteria quality check failed — description insufficient.",
            job_id,
        )

    # Qualifying context: model call OUTSIDE any DB transaction; skipped when OFF or insufficient.
    from services.qualifying_context.persistence import merge_qualifying_context
    from services.qualifying_context.runner import jd_sha256
    qc_run, qc_generated_at = None, None
    if qc_enabled and final_status == "completed":
        qc_run, qc_generated_at = await _run_qc(job_id, description)

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        # Lock the row and read the CURRENT stored analysis and JD inside this transaction, so a
        # recruiter-owned qualifying_context written while the model was running always wins.
        locked = await db.execute(
            text("""
                SELECT jc.analysis_json, j.description
                FROM job_criteria jc
                JOIN jobs j ON j.job_id = jc.job_id
                WHERE jc.job_id = :jid
                FOR UPDATE OF jc
            """),
            {"jid": job_id},
        )
        current_row = locked.mappings().first()
        existing_analysis = _load_json(current_row["analysis_json"]) if current_row else None
        current_description = current_row["description"] if current_row else None
        merged = merge_qualifying_context(
            existing_analysis, analysis, qc_run, generated_at=qc_generated_at,
            current_jd_sha256=jd_sha256(current_description) if current_description is not None else None,
        )
        await db.execute(
            text("""
                UPDATE job_criteria SET
                    analysis_json              = CAST(:aj AS jsonb),
                    original_analysis_json     = COALESCE(original_analysis_json, CAST(:orig AS jsonb)),
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
                    ai_model                                = :model,
                    ai_generated_at                         = now(),
                    criteria_extraction_status              = :status,
                    criteria_extracted_at                   = COALESCE(:extracted_at, criteria_extracted_at),
                    criteria_extraction_error               = :error,
                    criteria_last_failed_description_hash   = COALESCE(:desc_hash, criteria_last_failed_description_hash),
                    criteria_last_failed_at                 = COALESCE(:failed_at, criteria_last_failed_at)
                WHERE job_id = :jid
            """),
            {
                "aj":                 json.dumps(merged.analysis, ensure_ascii=False),
                "orig":               json.dumps(merged.original, ensure_ascii=False),
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
                "desc_hash":          desc_hash_value,
                "failed_at":          failed_at_value,
                "jid":                job_id,
            },
        )
        await db.commit()

    if is_sufficient:
        logger.info("[job:%s] Criteria extraction completed.", job_id)
    else:
        logger.warning("[job:%s] Criteria extraction marked insufficient.", job_id)


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
