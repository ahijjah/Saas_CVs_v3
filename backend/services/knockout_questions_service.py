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

def _sanitise_question_text(raw: str, job_description: str | None) -> str:
    """
    Trim whitespace and strip a job_description prefix if it was accidentally
    prepended (e.g. browser autofill injecting the description into the first
    knockout question input before the user typed their actual question).

    Comparison is done on normalised text (collapsed whitespace, lower-case) so
    minor formatting differences between the stored description and what the
    browser injected do not cause the strip to be skipped.
    """
    text = raw.strip()
    if not job_description:
        return text
    desc = job_description.strip()
    if not desc:
        return text

    # Fast path: exact prefix match after trimming
    if text.startswith(desc):
        return text[len(desc):].lstrip()

    # Normalised comparison: collapse all internal whitespace to a single space
    def _norm(s: str) -> str:
        return " ".join(s.lower().split())

    norm_text = _norm(text)
    norm_desc = _norm(desc)
    if norm_desc and norm_text.startswith(norm_desc):
        # Find the raw character position that corresponds to len(norm_desc)
        # normalised characters in `text`, then strip from there.
        raw_cut = _raw_pos_for_norm_len(text, len(norm_desc))
        return text[raw_cut:].lstrip()

    return text


def _raw_pos_for_norm_len(text: str, norm_len: int) -> int:
    """Return the raw index after consuming exactly norm_len normalised chars.

    Normalisation collapses consecutive whitespace to a single space and
    converts to lower-case.  This function counts normalised characters in
    `text` and returns the raw offset at which the norm_len-th char has been
    consumed, so the caller can slice text[raw_cut:] to get the remainder.
    """
    n = 0
    in_space = False
    for i, ch in enumerate(text):
        if n >= norm_len:
            return i
        if ch.isspace():
            if not in_space:
                n += 1
            in_space = True
        else:
            n += 1
            in_space = False
    return len(text)


async def save_job_knockout_questions(
    db: AsyncSession,
    job_id: str,
    tenant_id: str,
    questions: list[dict],
    job_description: str | None = None,
) -> None:
    """Replace all knockout questions for a job atomically.

    job_description: when provided, any question_text that starts with the full
    description (e.g. injected by browser autofill) has that prefix stripped
    before validation and INSERT.
    """
    max_q = await _get_max_questions(db)
    if len(questions) > max_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {max_q} knockout questions allowed per job.",
        )

    # Sanitise question_text before validation so empty-after-strip fails cleanly.
    sanitised: list[dict] = []
    for q in questions:
        q_copy = dict(q)
        if isinstance(q_copy.get("question_text"), str):
            q_copy["question_text"] = _sanitise_question_text(q_copy["question_text"], job_description)
        sanitised.append(q_copy)

    for q in sanitised:
        _validate_question(q)

    await db.execute(
        text("DELETE FROM job_knockout_questions WHERE job_id = :jid"),
        {"jid": job_id},
    )

    for i, q in enumerate(sanitised):
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
    answer_source: str = "candidate_form",
    answer_method: str = "direct_statement",
    updated_by: str | None = None,
) -> None:
    """
    Persist candidate answers. Each item: {question_id, answer_value}.

    is_disqualifying is always stored as FALSE in this phase — pass/fail
    evaluation against passing_criteria will be implemented in the
    pre-screening stage.
    """
    if not answers:
        return

    # Resolve tenant_id from the application row; fall back to the job row.
    tenant_row = await db.execute(
        text("SELECT tenant_id FROM applications WHERE application_id = CAST(:aid AS uuid)"),
        {"aid": application_id},
    )
    tenant_id: str | None = None
    tenant_result = tenant_row.scalar_one_or_none()
    if tenant_result:
        tenant_id = str(tenant_result)
    else:
        job_row = await db.execute(
            text("SELECT tenant_id FROM jobs WHERE job_id = :jid"),
            {"jid": job_id},
        )
        job_tenant = job_row.scalar_one_or_none()
        if job_tenant:
            tenant_id = str(job_tenant)

    if not tenant_id:
        logger.error(
            "Cannot save knockout answers: tenant_id not found for application_id=%s job_id=%s",
            application_id, job_id,
        )
        raise RuntimeError(f"tenant_id not found for application {application_id}")

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

        try:
            await db.execute(
                text("""
                    INSERT INTO application_knockout_answers
                        (application_id, tenant_id, question_id, answer_value,
                         is_disqualifying, answer_source, answer_method, updated_by, updated_at)
                    VALUES (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:qid AS uuid),
                            :val, FALSE, :src, :method,
                            CAST(:uid AS uuid), CASE WHEN :uid IS NOT NULL THEN now() ELSE NULL END)
                    ON CONFLICT (application_id, question_id) DO UPDATE
                        SET answer_value     = EXCLUDED.answer_value,
                            is_disqualifying  = FALSE,
                            answer_source     = EXCLUDED.answer_source,
                            answer_method     = EXCLUDED.answer_method,
                            updated_by        = EXCLUDED.updated_by,
                            updated_at        = EXCLUDED.updated_at
                """),
                {"aid": application_id, "tid": tenant_id, "qid": qid, "val": val,
                 "src": answer_source, "method": answer_method, "uid": updated_by},
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
