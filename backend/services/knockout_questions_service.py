"""
Service: job-level knockout questions + candidate answer persistence.

passing_criteria JSONB shapes by question_type:
  yes_no:        {"passing_answers": ["yes"]}  — subset of ["yes","no"]
  single_choice: {"passing_answers": ["Option A",...]}  — subset of options
  number:        {"operator": ">=", "value": 3}  — operator in {>=,>,=,<=,<}

passing_criteria is INTERNAL and must never be exposed on public (unauthenticated)
endpoints. Use get_public_job_knockout_questions() for the candidate-facing API.
"""
import json
import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_TYPES = {"yes_no", "single_choice", "number"}
_VALID_OPERATORS = {">=", ">", "=", "<=", "<"}
_DEFAULT_MAX = 5


async def _get_max_questions(db: AsyncSession) -> int:
    row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'max_knockout_questions_per_job'")
    )
    val = row.scalar_one_or_none()
    try:
        return int(val) if val else _DEFAULT_MAX
    except (TypeError, ValueError):
        return _DEFAULT_MAX


def _validate_passing_criteria(qtype: str, criteria: dict | None, options: list | None) -> None:
    """Raise HTTPException if passing_criteria is missing or malformed for the given type."""
    if criteria is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="passing_criteria is required for all knockout questions.",
        )

    if qtype == "yes_no":
        answers = criteria.get("passing_answers")
        if not answers or not isinstance(answers, list) or len(answers) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="yes_no questions require passing_criteria.passing_answers with at least one value.",
            )
        invalid = [a for a in answers if a not in ("yes", "no")]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"yes_no passing_answers must be 'yes' and/or 'no', got: {invalid}.",
            )

    elif qtype == "single_choice":
        if not options or len(options) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="single_choice questions require at least 2 options.",
            )
        answers = criteria.get("passing_answers")
        if not answers or not isinstance(answers, list) or len(answers) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="single_choice questions require passing_criteria.passing_answers with at least one value.",
            )
        invalid = [a for a in answers if a not in options]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"passing_answers values must exist in options, invalid: {invalid}.",
            )

    elif qtype == "number":
        operator = criteria.get("operator")
        value = criteria.get("value")
        if operator not in _VALID_OPERATORS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"number questions require passing_criteria.operator, one of {sorted(_VALID_OPERATORS)}.",
            )
        if value is None or not isinstance(value, (int, float)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="number questions require passing_criteria.value as a numeric.",
            )


def _validate_question(q: dict) -> None:
    """Full structural validation for a single question dict."""
    if not isinstance(q.get("question_text"), str) or not q["question_text"].strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each knockout question must have non-empty 'question_text'.",
        )
    qtype = q.get("question_type", "yes_no")
    if qtype not in _VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"question_type must be one of {sorted(_VALID_TYPES)}, got '{qtype}'.",
        )
    options = q.get("options")
    if options is not None and not isinstance(options, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="options must be a list.",
        )
    _validate_passing_criteria(qtype, q.get("passing_criteria"), options)


# ── Public (unauthenticated) fetch — passing_criteria excluded ────────────────

async def get_public_job_knockout_questions(db: AsyncSession, job_id: str) -> list[dict]:
    """Return questions for the candidate apply page. Strips passing_criteria."""
    rows = await db.execute(
        text("""
            SELECT question_id, question_text, question_type,
                   is_required, options, display_order
            FROM job_knockout_questions
            WHERE job_id = :jid
            ORDER BY display_order, created_at
        """),
        {"jid": job_id},
    )
    return [
        {
            "question_id":   str(r["question_id"]),
            "question_text": r["question_text"],
            "question_type": r["question_type"],
            "is_required":   r["is_required"],
            "options":       r["options"],
            "display_order": r["display_order"],
        }
        for r in rows.mappings()
    ]


# ── Authenticated (internal) fetch — full, includes passing_criteria ──────────

async def get_job_knockout_questions(db: AsyncSession, job_id: str) -> list[dict]:
    """Return questions with passing_criteria for authenticated (admin/recruiter) APIs."""
    rows = await db.execute(
        text("""
            SELECT question_id, question_text, question_type,
                   is_required, options, passing_criteria, display_order
            FROM job_knockout_questions
            WHERE job_id = :jid
            ORDER BY display_order, created_at
        """),
        {"jid": job_id},
    )
    return [
        {
            "question_id":      str(r["question_id"]),
            "question_text":    r["question_text"],
            "question_type":    r["question_type"],
            "is_required":      r["is_required"],
            "options":          r["options"],
            "passing_criteria": r["passing_criteria"],
            "display_order":    r["display_order"],
        }
        for r in rows.mappings()
    ]


# ── Write ─────────────────────────────────────────────────────────────────────

async def save_job_knockout_questions(
    db: AsyncSession,
    job_id: str,
    tenant_id: str,
    questions: list[dict],
) -> None:
    """Replace all knockout questions for a job atomically."""
    max_q = await _get_max_questions(db)
    if len(questions) > max_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {max_q} knockout questions allowed per job.",
        )

    for q in questions:
        _validate_question(q)

    await db.execute(
        text("DELETE FROM job_knockout_questions WHERE job_id = :jid"),
        {"jid": job_id},
    )

    for i, q in enumerate(questions):
        opts = q.get("options")
        criteria = q.get("passing_criteria")
        await db.execute(
            text("""
                INSERT INTO job_knockout_questions
                    (job_id, tenant_id, question_text, question_type,
                     is_required, options, passing_criteria, display_order)
                VALUES (:jid, :tid, :qtext, :qtype, :required,
                        CAST(:opts AS jsonb), CAST(:criteria AS jsonb), :order)
            """),
            {
                "jid":      job_id,
                "tid":      tenant_id,
                "qtext":    q["question_text"].strip(),
                "qtype":    q.get("question_type", "yes_no"),
                "required": q.get("is_required", True),
                "opts":     json.dumps(opts) if opts is not None else None,
                "criteria": json.dumps(criteria) if criteria is not None else None,
                "order":    i,
            },
        )


async def job_has_active_knockout_questions(db: AsyncSession, job_id: str) -> bool:
    """Return True if the job has at least one knockout question configured."""
    result = await db.execute(
        text("SELECT 1 FROM job_knockout_questions WHERE job_id = :jid LIMIT 1"),
        {"jid": job_id},
    )
    return result.first() is not None


async def save_knockout_answers(
    db: AsyncSession,
    application_id: str,
    job_id: str,
    answers: list[dict],
) -> None:
    """
    Persist candidate answers. Each item: {question_id, answer_value}.

    is_disqualifying is always stored as FALSE in this phase — pass/fail
    evaluation against passing_criteria will be implemented in the
    pre-screening stage.
    """
    if not answers:
        return

    valid_qids = set()
    rows = await db.execute(
        text("SELECT question_id FROM job_knockout_questions WHERE job_id = :jid"),
        {"jid": job_id},
    )
    for r in rows:
        valid_qids.add(str(r[0]))

    for ans in answers:
        qid = str(ans.get("question_id", ""))
        val = str(ans.get("answer_value", "")).strip()
        if qid not in valid_qids:
            logger.debug("Skipping answer for unknown question_id=%s (job=%s)", qid, job_id)
            continue

        val_json = json.dumps(val)
        try:
            await db.execute(
                text("""
                    INSERT INTO application_knockout_answers
                        (application_id, question_id, answer_value, is_disqualifying)
                    VALUES (CAST(:aid AS uuid), CAST(:qid AS uuid), CAST(:val AS jsonb), FALSE)
                    ON CONFLICT (application_id, question_id) DO UPDATE
                        SET answer_value = EXCLUDED.answer_value,
                            is_disqualifying = FALSE
                """),
                {"aid": application_id, "qid": qid, "val": val_json},
            )
        except Exception as exc:
            logger.error(
                "Failed to insert knockout answer application_id=%s question_id=%s: %s",
                application_id, qid, exc,
            )
            raise


async def get_application_knockout_answers(
    db: AsyncSession,
    application_id: str,
) -> list[dict]:
    rows = await db.execute(
        text("""
            SELECT a.answer_id, a.question_id, a.answer_value, a.is_disqualifying,
                   q.question_text, q.question_type
            FROM application_knockout_answers a
            JOIN job_knockout_questions q ON q.question_id = a.question_id
            WHERE a.application_id = :aid
            ORDER BY q.display_order, q.created_at
        """),
        {"aid": application_id},
    )
    return [
        {
            "answer_id":        str(r["answer_id"]),
            "question_id":      str(r["question_id"]),
            "question_text":    r["question_text"],
            "question_type":    r["question_type"],
            "answer_value":     r["answer_value"],
            "is_disqualifying": r["is_disqualifying"],
        }
        for r in rows.mappings()
    ]
