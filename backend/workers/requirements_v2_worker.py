"""
Celery task: persist a Requirements V2 document for a newly analysed job.

Only rows with requirements_schema_version = 2 are claimed here. Legacy rows (NULL marker) stay with criteria_worker, which never
claims a v2 row, so the two paths cannot write the same job.

Flow:
  1. Claim: pending -> processing, v2 rows only (compare-and-set). The claim token is the row's new updated_at. The claim also
     returns the retired item ids, which a new id must never reuse.
  2. Model: the stage's model is resolved through the model registry (cv_analyzer). When no stage default is configured the row is
     marked failed with the fixed configuration message and is not retried.
  3. Request and parse: the pinned criteria_extraction_v2 request, then parse_response. Ids are a function of the description and the
     position, so a retry of the same description yields the same ids.
  4. Validate: the draft must have no parse error, pass the draft contract, reuse no retired id, and total 0 or 100 in weight.
  5. Finalise: the structural review is attached by the canonical detector from the expectations derived from the document, and the
     classification and structure review blocks are always present. The final structure is checked again.
  6. Persist: one UPDATE writes the document, its original, the weights, the revision and the status. It is guarded by this claim's
     token and by "no document yet", and the transaction commits only after it succeeds.

Retry: an attempt that raises releases the claim to pending. The final attempt marks the row failed with the fixed worker message.
A cancelled attempt raises CancelledError, which is not caught here: its transaction rolls back and nothing is written.
"""
import asyncio
import hashlib
import itertools
import json
import logging
from datetime import datetime, timezone

from services.criteria_status import (
    EXTRACTION_CANCELLED_MESSAGE, MODEL_NOT_CONFIGURED_MESSAGE, REQUIREMENTS_REJECTED_MESSAGE, WORKER_FAILED_MESSAGE,
)
from services.requirements_v2.acknowledgment import REVIEW_KEY, empty_review
from services.requirements_v2.contract import CATEGORIES, ITEM_ID_RE, SCHEMA_VERSION, WEIGHT_COLUMN, collect_item_ids
from services.requirements_v2.extraction import build_request, parse_response
from services.requirements_v2.structural_detection import attach
from services.requirements_v2.structural_expectations import ALTERNATIVES_TOGETHER, has_token, jd_sha256, validate_expectations
from services.requirements_v2.structural_review import READINESS_INVALID, structural_status
from services.requirements_v2.structure import STRUCTURE_KEY, empty_block
from services.requirements_v2.validation import validate_draft, validate_structure
from workers.celery_app import celery_app
from workers.criteria_worker import _normalize_description_hash

logger = logging.getLogger(__name__)

_ITEM_ID_PREFIX = "req_"
STRUCTURAL_EXPECTATIONS_KEY = "structural_expectations"
STAGE = "cv_analyzer"


class InvalidModelOutput(Exception):
    """The model output cannot be stored as a document. The code is for the log; no model text is stored."""


class ModelNotConfigured(Exception):
    """No model is configured for the stage. The attempt is not retried: an operator must configure the stage."""


class RequirementsExtractionFailed(Exception):
    """Raised by the task after its final attempt failed. The message is a fixed server message, never model or driver text."""


def stable_id_factory(description: str, reserved=()):
    """Item ids for one description: the k-th id depends only on the description and k, so a retry gives the same ids as a clean
    run. An id already used in the document, or in `reserved` (retired ids), is skipped."""
    seed = hashlib.sha256((description or "").encode("utf-8")).hexdigest()
    reserved_ids = set(reserved)
    counter = itertools.count()

    def next_id(used):
        taken = set(used) | reserved_ids
        while True:
            candidate = _ITEM_ID_PREFIX + hashlib.sha256(f"{seed}:{next(counter)}".encode("utf-8")).hexdigest()[:12]
            if candidate not in taken:
                return candidate
    return next_id


def checked_document(result, *, retired=()) -> dict:
    """The draft document, or InvalidModelOutput. Uses the contract validators; adds the retired-id and weight-total checks that the
    validators report as review rather than error."""
    if not result.ok:
        raise InvalidModelOutput(result.errors[0].code)
    doc = result.requirements
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise InvalidModelOutput("schema_version")
    if validate_structure(doc):
        raise InvalidModelOutput("structure")
    if validate_draft(doc).errors:
        raise InvalidModelOutput("draft_contract")
    if collect_item_ids(doc) & set(retired):
        raise InvalidModelOutput("retired_id_reused")
    total = sum(doc["categories"][c]["weight"] for c in CATEGORIES)
    if total not in (0, 100):
        raise InvalidModelOutput("weights_total")
    return doc


def expectations_from_extraction(doc: dict, description: str) -> dict:
    """The structural expectation baseline for one extraction, built on the server from the document the model returned.

    For each source wording that two or more items quote, the expectation's tokens are the alternatives the items declared that occur
    as words in that wording. A wording is expected to hold one item with all of its alternatives, so a split across items is what the
    detector reports. Nothing else is inferred, and the block is validated against the description before it is returned. No client
    input reaches this function."""
    groups: dict[str, list[dict]] = {}
    for category in CATEGORIES:
        for item in doc["categories"][category]["items"]:
            if item.get("source_text"):
                groups.setdefault(item["source_text"], []).append(item)
    expectations = []
    for source, items in groups.items():
        if len(items) < 2 or description.count(source) != 1:
            continue
        declared: list[str] = []
        for item in items:
            for alternative in item.get("alternatives") or []:
                if alternative not in declared:
                    declared.append(alternative)
        tokens = [t for t in declared if has_token(source, t)]
        if len(tokens) < 2:
            continue
        start = description.index(source)
        digest = hashlib.sha256(f"{source}\n{'|'.join(tokens)}".encode("utf-8")).hexdigest()[:12]
        expectations.append({"id": f"exp_{digest}", "kind": ALTERNATIVES_TOGETHER, "source_text": source,
                             "source_start": start, "source_end": start + len(source), "tokens": tokens})
    block = {"schema_version": 1, "jd_sha256": jd_sha256(description), "expectations": expectations}
    validate_expectations(block, description)              # the canonical contract; a builder bug fails loudly here
    return block


def finalise_document(doc: dict, description: str) -> tuple[dict, dict]:
    """The document to store and the expectation baseline it was checked against. The structural review comes from the canonical
    detector, the server-owned blocks are always present, and the final structure is checked again. Raises InvalidModelOutput when
    the final structure is not usable."""
    expectations = expectations_from_extraction(doc, description)
    doc = attach(doc, description, expectations)
    doc.setdefault(REVIEW_KEY, empty_review())
    doc.setdefault(STRUCTURE_KEY, empty_block())
    if validate_structure(doc):
        raise InvalidModelOutput("structure_after_blocks")
    if any(issue.code == READINESS_INVALID for issue in structural_status(doc, description).issues):
        raise InvalidModelOutput("structural_review_invalid")
    return doc, expectations


async def _resolve_model(Session):
    """The stage's model from the registry, or None when the stage is not configured. Resolution never raises on its own; a missing
    stage default is reported as None and handled by the caller."""
    from database import set_rls_context
    from services.ai_model_registry_service import resolve_stage_client

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        return await resolve_stage_client(db, STAGE)


async def _request_extraction(description: str, resolved):
    """One chat-completion call with the pinned request, through the resolved client. Returns (content, finish_reason).
    Tests replace this seam."""
    request = build_request(description, model=resolved.model_name)
    response = await resolved.client.chat.completions.create(**request)
    choice = response.choices[0]
    return choice.message.content, choice.finish_reason


_CLAIM = """
    UPDATE job_criteria SET criteria_extraction_status = 'processing'
    WHERE job_id = :jid AND criteria_extraction_status = 'pending' AND requirements_schema_version = 2
    RETURNING updated_at, requirements_retired_item_ids::text AS retired
"""

_RELEASE = """
    UPDATE job_criteria SET criteria_extraction_status = 'pending'
    WHERE job_id = :jid AND criteria_extraction_status = 'processing' AND updated_at = :token
      AND requirements_schema_version = 2
    RETURNING updated_at
"""

# A cancelled or interrupted attempt that has not committed. Only the claim that holds the token, on a processing row that has no
# document, is returned to pending. A row that another claim or a committed document owns is never matched.
_RELEASE_CANCELLED = """
    UPDATE job_criteria SET criteria_extraction_status = 'pending', criteria_extraction_error = :message
    WHERE job_id = :jid AND criteria_extraction_status = 'processing' AND updated_at = :token
      AND requirements_schema_version = 2 AND analysis_json IS NULL
    RETURNING updated_at
"""

# The status, the fixed message and the normalized description hash change in one statement, guarded by the claim token. A stale claim
# matches no row, so it can neither change the status nor overwrite a newer hash.
_MARK_FAILED = """
    UPDATE job_criteria SET criteria_extraction_status = 'failed', criteria_extraction_error = :err,
        criteria_last_failed_description_hash = :desc_hash, criteria_last_failed_at = now()
    WHERE job_id = :jid AND criteria_extraction_status = 'processing' AND updated_at = :token
      AND requirements_schema_version = 2
"""

# One statement: the document, original, weights, revision and status change together. A document is written only when none exists,
# so a stored document is never replaced, and only by the claim that holds the token.
_PERSIST = """
    UPDATE job_criteria SET
        analysis_json = CAST(:analysis AS jsonb),
        original_analysis_json = CAST(:original AS jsonb),
        weight_skills = :weight_skills, weight_experience = :weight_experience, weight_education = :weight_education,
        weight_certifications = :weight_certifications, weight_soft_skills = :weight_soft_skills,
        weight_domain_knowledge = :weight_domain_knowledge, weight_other = :weight_other,
        requirements_revision = requirements_revision + 1,
        ai_model = :model, ai_generated_at = now(),
        criteria_extraction_status = 'completed', criteria_extracted_at = now(), criteria_extraction_error = NULL,
        updated_at = now()
    WHERE job_id = :jid AND criteria_extraction_status = 'processing' AND updated_at = :token
      AND requirements_schema_version = 2 AND analysis_json IS NULL
    RETURNING requirements_revision
"""


def _persist_params(analysis: dict, original: dict, expectations: dict, doc: dict, *, job_id: str, token, model: str) -> dict:
    return {
        "jid": job_id,
        "token": token,
        "analysis": json.dumps(analysis, ensure_ascii=False),
        # The baseline holds the approved expectations next to the original analysis: the structural review is checked against it.
        "original": json.dumps({"requirements": original, STRUCTURAL_EXPECTATIONS_KEY: expectations}, ensure_ascii=False),
        **{WEIGHT_COLUMN[c]: doc["categories"][c]["weight"] for c in CATEGORIES},
        "model": model,
    }


def _task_engine_and_session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from config import get_settings

    cfg = get_settings()
    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="workers.requirements_v2_worker.extract_requirements_v2_task",
)
def extract_requirements_v2_task(self, job_id: str, description: str) -> None:
    """Background task: extract and persist a Requirements V2 document for a v2 pending row."""
    task_engine, TaskSession = _task_engine_and_session()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    claim: dict = {}
    try:
        loop.run_until_complete(_extract_async(job_id, description, TaskSession, claim))
    except ModelNotConfigured:
        # Not retryable: mark the row failed with the configuration message, so the failure is visible and the retry is an operator action.
        logger.error("[job:%s] No criteria model is configured for stage %s.", job_id, STAGE)
        try:
            loop.run_until_complete(_mark_failed(job_id, claim.get("token"), TaskSession, MODEL_NOT_CONFIGURED_MESSAGE, description))
        except Exception as mark_exc:
            logger.error("[job:%s] _mark_failed itself failed: %s", job_id, mark_exc)
        raise RequirementsExtractionFailed(MODEL_NOT_CONFIGURED_MESSAGE) from None
    except Exception as exc:
        logger.error("extract_requirements_v2_task failed for job %s (attempt %d/%d): %s",
                     job_id, self.request.retries + 1, self.max_retries + 1, exc)
        if self.request.retries < self.max_retries:
            # Release this claim so the retried attempt can claim the row again.
            if claim.get("token") is not None:
                try:
                    loop.run_until_complete(_release_claim(job_id, claim["token"], TaskSession))
                except Exception as release_exc:
                    logger.error("[job:%s] Could not release the claim before retry: %s", job_id, release_exc)
            raise self.retry(exc=exc)
        # Invalid model output for this description is deterministic; anything else is treated as transient and stays retryable.
        final_message = REQUIREMENTS_REJECTED_MESSAGE if isinstance(exc, InvalidModelOutput) else WORKER_FAILED_MESSAGE
        try:
            loop.run_until_complete(_mark_failed(job_id, claim.get("token"), TaskSession, final_message, description))
        except Exception as mark_exc:
            logger.error("[job:%s] _mark_failed itself failed: %s", job_id, mark_exc)
        raise RequirementsExtractionFailed(final_message) from None
    finally:
        try:
            if not loop.is_closed():
                loop.run_until_complete(task_engine.dispose())
                loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)


async def _claim(db, job_id: str):
    """pending -> processing for a v2 row. Returns (token, retired ids), or None when the row is not a pending v2 row."""
    from sqlalchemy import text

    row = (await db.execute(text(_CLAIM), {"jid": job_id})).first()
    if row is None:
        return None
    return row.updated_at, json.loads(row.retired)


async def _extract_async(job_id: str, description: str, Session, claim: dict) -> None:
    from database import set_rls_context
    from sqlalchemy import text

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        claimed = await _claim(db, job_id)
        await db.commit()
    if claimed is None:
        logger.info("[job:%s] Requirements v2 extraction not started: the row is not a pending v2 row.", job_id)
        return
    token, retired = claimed
    claim["token"] = token
    logger.info("[job:%s] Requirements v2 extraction started.", job_id)
    try:
        await _attempt(job_id, description, Session, claim, token, retired, text, set_rls_context)
    except asyncio.CancelledError:
        # Cancelled before the document committed: this claim returns its row to pending. The release matches the claim token and a
        # processing row with no document, so a committed document, or another claim, is never touched. Ordinary failures keep the
        # task's own release and final-failure handling.
        if not claim.get("persisted"):
            await _release_cancelled(job_id, token, Session)
        raise


async def _attempt(job_id, description, Session, claim, token, retired, text, set_rls_context) -> None:
    resolved = await _resolve_model(Session)
    if resolved is None:
        raise ModelNotConfigured(STAGE)

    content, finish_reason = await _request_extraction(description, resolved)
    result = parse_response(content, description, finish_reason,
                            id_factory=stable_id_factory(description, reserved=retired))
    doc, expectations = finalise_document(checked_document(result, retired=retired), description)
    analysis = result.analysis
    analysis["requirements"] = doc

    params = _persist_params(analysis, result.original, expectations, doc, job_id=job_id, token=token, model=resolved.model_name)
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        written = (await db.execute(text(_PERSIST), params)).first()
        if written is None:
            await db.rollback()
            logger.warning("[job:%s] Requirements result discarded: this attempt no longer holds the claim.", job_id)
            return
        await db.commit()
    claim["persisted"] = True
    logger.info("[job:%s] Requirements v2 extraction completed.", job_id)


async def _release_cancelled(job_id: str, token, Session) -> None:
    """Return this claim's row to pending in a clean transaction. Never raises: a cancelled attempt must still exit cleanly, and the
    row's state is then decided by the token-guarded statement alone."""
    from database import set_rls_context
    from sqlalchemy import text

    try:
        async with Session() as db:
            await set_rls_context(db, "", "super_admin")
            released = (await db.execute(text(_RELEASE_CANCELLED), {"jid": job_id, "token": token,
                                                                     "message": EXTRACTION_CANCELLED_MESSAGE})).first()
            await db.commit()
        if released is not None:
            logger.warning("[job:%s] Requirements v2 extraction cancelled; the row is pending again.", job_id)
    except BaseException as exc:                              # noqa: BLE001 - cleanup must not mask the cancellation
        logger.error("[job:%s] Cancellation cleanup did not complete: %s", job_id, exc)


async def _release_claim(job_id: str, token, Session) -> None:
    from database import set_rls_context
    from sqlalchemy import text

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(text(_RELEASE), {"jid": job_id, "token": token})
        await db.commit()


async def _mark_failed(job_id: str, token, Session, message: str, description: str) -> bool:
    """Final failure: processing -> failed with a fixed message and the normalized description hash, for the claim that holds `token`."""
    from database import set_rls_context
    from sqlalchemy import text

    if token is None:
        return False
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        result = await db.execute(text(_MARK_FAILED), {
            "err": message, "jid": job_id, "token": token, "desc_hash": _normalize_description_hash(description),
        })
        await db.commit()
    marked = result.rowcount == 1
    if marked:
        logger.error("[job:%s] Requirements v2 extraction permanently failed.", job_id)
    return marked
