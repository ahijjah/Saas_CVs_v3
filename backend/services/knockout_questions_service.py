"""
Service: job-level knockout questions + candidate answer persistence.
"""
import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_TYPES = {"yes_no", "single_choice", "number"}
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


def _validate_question(q: dict) -> None:
    """Raise HTTPException if a question dict is malformed."""
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
    if qtype == "single_choice":
        opts = q.get("options")
        if not opts or not isinstance(opts, list) or len(opts) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="single_choice questions require at least 2 options.",
            )
        if q.get("disqualifying_answer") and q["disqualifying_answer"] not in opts:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="disqualifying_answer must be one of the provided options.",
            )
    if qtype == "yes_no" and q.get("disqualifying_answer"):
        if q["disqualifying_answer"] not in ("yes", "no"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="For yes_no questions, disqualifying_answer must be 'yes' or 'no'.",
            )
    # number type: no options required; disqualifying_answer not validated in V1


async def get_job_knockout_questions(db: AsyncSession, job_id: str) -> list[dict]:
    rows = await db.execute(
        text("""
            SELECT question_id, job_id, question_text, question_type,
                   is_required, disqualifying_answer, options, display_order
            FROM job_knockout_questions
            WHERE job_id = :jid
            ORDER BY display_order, created_at
        """),
        {"jid": job_id},
    )
    return [
        {
            "question_id":          str(r["question_id"]),
            "question_text":        r["question_text"],
            "question_type":        r["question_type"],
            "is_required":          r["is_required"],
            "disqualifying_answer": r["disqualifying_answer"],
            "options":              r["options"],
            "display_order":        r["display_order"],
        }
        for r in rows.mappings()
    ]


async def save_job_knockout_questions(
    db: AsyncSession,
    job_id: str,
    tenant_id: str,
    questions: list[dict],
) -> None:
    """Replace all knockout questions for a job atomically."""
    import json

    max_q = await _get_max_questions(db)
    if len(questions) > max_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {max_q} knockout questions allowed per job.",
        )

    for q in questions:
        _validate_question(q)

    # Delete existing and re-insert
    await db.execute(
        text("DELETE FROM job_knockout_questions WHERE job_id = :jid"),
        {"jid": job_id},
    )

    for i, q in enumerate(questions):
        opts = q.get("options")
        opts_json = json.dumps(opts) if opts else None
        await db.execute(
            text("""
                INSERT INTO job_knockout_questions
                    (job_id, tenant_id, question_text, question_type,
                     is_required, disqualifying_answer, options, display_order)
                VALUES (:jid, :tid, :qtext, :qtype, :required, :disq, CAST(:opts AS jsonb), :order)
            """),
            {
                "jid":     job_id,
                "tid":     tenant_id,
                "qtext":   q["question_text"].strip(),
                "qtype":   q.get("question_type", "yes_no"),
                "required": q.get("is_required", True),
                "disq":    q.get("disqualifying_answer"),
                "opts":    opts_json,
                "order":   i,
            },
        )


async def save_knockout_answers(
    db: AsyncSession,
    application_id: str,
    job_id: str,
    answers: list[dict],
) -> None:
    """
    Persist candidate answers.  Each answer: {question_id, answer_value}.
    Marks is_disqualifying based on the question's disqualifying_answer.
    """
    if not answers:
        return

    # Load questions to check disqualifying logic
    rows = await db.execute(
        text("""
            SELECT question_id, disqualifying_answer, is_required
            FROM job_knockout_questions WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    q_map: dict[str, dict] = {
        str(r["question_id"]): {
            "disqualifying_answer": r["disqualifying_answer"],
            "is_required": r["is_required"],
        }
        for r in rows.mappings()
    }

    for ans in answers:
        qid = str(ans.get("question_id", ""))
        val = str(ans.get("answer_value", "")).strip()
        if qid not in q_map:
            continue  # skip answers to unknown/deleted questions

        q_meta = q_map[qid]
        is_disq = bool(
            q_meta["disqualifying_answer"]
            and val.lower() == q_meta["disqualifying_answer"].lower()
        )

        await db.execute(
            text("""
                INSERT INTO application_knockout_answers
                    (application_id, question_id, answer_value, is_disqualifying)
                VALUES (:aid, CAST(:qid AS uuid), :val, :disq)
                ON CONFLICT (application_id, question_id) DO UPDATE
                    SET answer_value = EXCLUDED.answer_value,
                        is_disqualifying = EXCLUDED.is_disqualifying
            """),
            {
                "aid":  application_id,
                "qid":  qid,
                "val":  val,
                "disq": is_disq,
            },
        )


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
            "answer_id":       str(r["answer_id"]),
            "question_id":     str(r["question_id"]),
            "question_text":   r["question_text"],
            "question_type":   r["question_type"],
            "answer_value":    r["answer_value"],
            "is_disqualifying": r["is_disqualifying"],
        }
        for r in rows.mappings()
    ]
