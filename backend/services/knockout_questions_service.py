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
import re

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_TYPES = {"yes_no", "single_choice", "number"}
_VALID_OPERATORS = {">=", ">", "=", "<=", "<"}
_DEFAULT_MAX = 5
GATE_FAILURE_ACTIONS = frozenset({"not_eligible", "hold_for_review"})
_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_PATTERN.match(value))


async def _get_max_questions(db: AsyncSession) -> int:
    row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'max_knockout_questions_per_job'")
    )
    val = row.scalar_one_or_none()
    try:
        return int(val) if val else _DEFAULT_MAX
    except (TypeError, ValueError):
        return _DEFAULT_MAX


def passing_criteria_problem(qtype: str, criteria: dict | None, options: list | None) -> str | None:
    """Why passing_criteria is missing or malformed for the question type, or None when it is valid. Pure; shared by the question
    editor and by the check that a requirement gate is linked to a usable question."""
    if criteria is None or not isinstance(criteria, dict):
        return "passing_criteria is required for all knockout questions."
    if qtype == "yes_no":
        answers = criteria.get("passing_answers")
        if not answers or not isinstance(answers, list):
            return "yes_no questions require passing_criteria.passing_answers with at least one value."
        invalid = [a for a in answers if a not in ("yes", "no")]
        if invalid:
            return f"yes_no passing_answers must be 'yes' and/or 'no', got: {invalid}."
    elif qtype == "single_choice":
        if not options or not isinstance(options, list) or len(options) < 2:
            return "single_choice questions require at least 2 options."
        answers = criteria.get("passing_answers")
        if not answers or not isinstance(answers, list):
            return "single_choice questions require passing_criteria.passing_answers with at least one value."
        invalid = [a for a in answers if a not in options]
        if invalid:
            return f"passing_answers values must exist in options, invalid: {invalid}."
    elif qtype == "number":
        operator = criteria.get("operator")
        value = criteria.get("value")
        if operator not in _VALID_OPERATORS:
            return f"number questions require passing_criteria.operator, one of {sorted(_VALID_OPERATORS)}."
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return "number questions require passing_criteria.value as a numeric."
    else:
        return f"question_type must be one of {sorted(_VALID_TYPES)}, got '{qtype}'."
    return None


def _validate_passing_criteria(qtype: str, criteria: dict | None, options: list | None) -> None:
    """Raise HTTPException if passing_criteria is missing or malformed for the given type."""
    problem = passing_criteria_problem(qtype, criteria, options)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)


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
    # Eligibility gate controls. Absent means advisory, exactly as before. A gate question has already passed the passing-criteria
    # check above, which every question must pass.
    gate = q.get("is_eligibility_gate", False)
    if not isinstance(gate, bool):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="is_eligibility_gate must be true or false.")
    action = q.get("gate_failure_action", "not_eligible")
    if action not in GATE_FAILURE_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"gate_failure_action must be one of {sorted(GATE_FAILURE_ACTIONS)}.")
    question_id = q.get("question_id")
    if question_id is not None and not _is_uuid(question_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="question_id must be a question id.")


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


# ── Links from Job Analysis requirements (a requirement gate names the question that verifies it) ──────────────────

async def linked_requirements(db: AsyncSession, job_id: str) -> dict[str, dict]:
    """{question id (lower case): {item_id, text, failure_action}} for the confirmed requirement gates of the job that name a question.
    Empty when the job has no Requirements v2 document or the document cannot be read. The link is stored once, in the document."""
    from services.requirements_v2 import gates as gate_rules
    from services.requirements_v2.validation import validate_structure
    row = await db.execute(text("SELECT analysis_json FROM job_criteria WHERE job_id = :jid"), {"jid": job_id})
    analysis = row.scalar_one_or_none()
    doc = analysis.get("requirements") if isinstance(analysis, dict) else None
    if not isinstance(doc, dict) or validate_structure(doc):
        return {}
    return {g["linked_question_id"].lower(): {"item_id": g["item_id"], "text": g["text"], "failure_action": g["failure_action"]}
            for g in gate_rules.confirmed_gates(doc) if g["linked_question_id"]}


# ── Authenticated (internal) fetch — full, includes passing_criteria ──────────

async def get_job_knockout_questions(db: AsyncSession, job_id: str) -> list[dict]:
    """Return questions with passing_criteria for authenticated (admin/recruiter) APIs."""
    rows = await db.execute(
        text("""
            SELECT question_id, question_text, question_type,
                   is_required, options, passing_criteria, display_order,
                   is_eligibility_gate, gate_failure_action
            FROM job_knockout_questions
            WHERE job_id = :jid
            ORDER BY display_order, created_at
        """),
        {"jid": job_id},
    )
    links = await linked_requirements(db, job_id)
    out = []
    for r in rows.mappings():
        qid = str(r["question_id"])
        link = links.get(qid.lower())
        out.append({
            "question_id":      qid,
            "question_text":    r["question_text"],
            "question_type":    r["question_type"],
            "is_required":      r["is_required"],          # "Response Required": the candidate must answer. It is not eligibility.
            "options":          r["options"],
            "passing_criteria": r["passing_criteria"],
            "display_order":    r["display_order"],
            "is_eligibility_gate": r["is_eligibility_gate"],
            "gate_failure_action": r["gate_failure_action"],
            # read-only: the failure action that governs when the question verifies a requirement gate, and the requirement itself
            "linked_requirement": None if link is None else {"item_id": link["item_id"], "text": link["text"],
                                                              "failure_action": link["failure_action"]},
        })
    return out


# ── Write ─────────────────────────────────────────────────────────────────────

async def save_job_knockout_questions(
    db: AsyncSession,
    job_id: str,
    tenant_id: str,
    questions: list[dict],
) -> None:
    """Replace the knockout questions for a job atomically.

    A question that names its question_id in the request keeps that id (and so its candidate answers and any requirement gate that is
    linked to it). A question without an id is created, and every question the request does not name is removed, exactly as before.
    A question that a confirmed requirement gate is linked to cannot be removed, and cannot stop being an eligibility gate, until the
    gate is cleared or re-linked in Job Analysis."""
    max_q = await _get_max_questions(db)
    if len(questions) > max_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {max_q} knockout questions allowed per job.",
        )

    for q in questions:
        _validate_question(q)

    kept_ids = [str(q["question_id"]).lower() for q in questions if q.get("question_id")]
    if len(kept_ids) != len(set(kept_ids)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A question id appears twice.")
    existing = {str(r[0]).lower(): r[1] for r in (await db.execute(
        text("SELECT question_id, is_eligibility_gate FROM job_knockout_questions WHERE job_id = :jid"), {"jid": job_id}))}
    unknown = [qid for qid in kept_ids if qid not in existing]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A question id does not belong to this job.")
    by_id = {str(q["question_id"]).lower(): q for q in questions if q.get("question_id")}
    for qid, link in (await linked_requirements(db, job_id)).items():
        kept = by_id.get(qid)
        if kept is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "code": "question_linked_to_gate",
                "message": "A requirement gate in Job Analysis is linked to this question. Clear or re-link the gate before removing it."})
        if not kept.get("is_eligibility_gate", False):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "code": "question_linked_to_gate",
                "message": "A requirement gate in Job Analysis is linked to this question, so it stays an eligibility gate."})

    for qid in existing:
        if qid not in by_id:
            await db.execute(text("DELETE FROM job_knockout_questions WHERE question_id = CAST(:qid AS uuid) AND job_id = :jid"),
                             {"qid": qid, "jid": job_id})

    for i, q in enumerate(questions):
        opts = q.get("options")
        criteria = q.get("passing_criteria")
        params = {
            "jid":      job_id,
            "tid":      tenant_id,
            "qtext":    q["question_text"].strip(),
            "qtype":    q.get("question_type", "yes_no"),
            "required": q.get("is_required", True),
            "opts":     json.dumps(opts) if opts is not None else None,
            "criteria": json.dumps(criteria) if criteria is not None else None,
            "order":    i,
            "gate":     bool(q.get("is_eligibility_gate", False)),
            "action":   q.get("gate_failure_action", "not_eligible"),
        }
        if q.get("question_id"):
            await db.execute(
                text("""
                    UPDATE job_knockout_questions SET
                        question_text = :qtext, question_type = :qtype, is_required = :required,
                        options = CAST(:opts AS jsonb), passing_criteria = CAST(:criteria AS jsonb), display_order = :order,
                        is_eligibility_gate = :gate, gate_failure_action = :action
                    WHERE question_id = CAST(:qid AS uuid) AND job_id = :jid
                """),
                {**params, "qid": str(q["question_id"])},
            )
            continue
        await db.execute(
            text("""
                INSERT INTO job_knockout_questions
                    (job_id, tenant_id, question_text, question_type,
                     is_required, options, passing_criteria, display_order,
                     is_eligibility_gate, gate_failure_action)
                VALUES (:jid, :tid, :qtext, :qtype, :required,
                        CAST(:opts AS jsonb), CAST(:criteria AS jsonb), :order,
                        :gate, :action)
            """),
            params,
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
