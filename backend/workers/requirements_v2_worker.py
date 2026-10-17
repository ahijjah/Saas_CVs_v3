"""
Celery task: persist a Requirements V2 document for a newly analysed job.

Only rows with requirements_schema_version = 2 are claimed here. Legacy rows (NULL marker) stay with criteria_worker, which never
claims a v2 row, so the two paths cannot write the same job.

Flow:
  1. Claim: pending -> processing, v2 rows only (compare-and-set). The claim token is the row's new updated_at. The claim also
     returns the retired item ids, which a new id must never reuse.
  2. Request: one call with the pinned criteria_extraction_v2 request (see requirements_v2/extraction).
  3. Parse: parse_response builds the draft. Ids are a function of the description and the position, so a retry of the same
     description yields the same ids.
  4. Validate: the draft must have no parse error, pass the structure contract, use no retired id, and total 0 or 100 in weight.
     Anything else is an attempt failure, and nothing is written.
  5. Persist: one UPDATE writes the document, its original, the weight columns, the revision and the status. It is guarded by
     this claim's token and by "no document yet", and the transaction commits only after it succeeds.

Retry: an attempt that raises releases the claim to pending. The final attempt marks the row failed with the fixed worker message.
A cancelled attempt raises CancelledError, which is not caught here: its transaction rolls back and nothing is written.
"""
import asyncio
import hashlib
import itertools
import json
import logging

from services.criteria_status import WORKER_FAILED_MESSAGE
from services.requirements_v2.contract import CATEGORIES, SCHEMA_VERSION, WEIGHT_COLUMN, collect_item_ids
from services.requirements_v2.extraction import build_request, parse_response
from services.requirements_v2.validation import validate_draft, validate_structure
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_ITEM_ID_PREFIX = "req_"


class InvalidModelOutput(Exception):
    """The model output cannot be stored as a document. The code is for the log; no model text is stored."""


class RequirementsExtractionFailed(Exception):
    """Raised by the task after its final attempt failed. The message is the fixed server message, never the model or driver text."""


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
    """The one document that may be stored, or InvalidModelOutput. Uses the contract validators; adds only the retired-id and
    weight-total checks that the validators report as review rather than error."""
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


async def _request_extraction(description: str):
    """One chat-completion call with the pinned request. Returns (content, finish_reason, model). Tests replace this seam."""
    from services.ai_service import _get_client

    request = build_request(description)
    response = await _get_client().chat.completions.create(**request)
    choice = response.choices[0]
    return choice.message.content, choice.finish_reason, request["model"]


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

_MARK_FAILED = """
    UPDATE job_criteria SET criteria_extraction_status = 'failed', criteria_extraction_error = :err
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


def _persist_params(result, *, job_id: str, token, model: str) -> dict:
    doc = result.analysis["requirements"]
    return {
        "jid": job_id,
        "token": token,
        "analysis": json.dumps(result.analysis, ensure_ascii=False),
        "original": json.dumps({"requirements": result.original}, ensure_ascii=False),
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
        try:
            loop.run_until_complete(_mark_failed(job_id, claim.get("token"), TaskSession))
        except Exception as mark_exc:
            logger.error("[job:%s] _mark_failed itself failed: %s", job_id, mark_exc)
        raise RequirementsExtractionFailed(WORKER_FAILED_MESSAGE) from None
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

    content, finish_reason, model = await _request_extraction(description)
    result = parse_response(content, description, finish_reason,
                            id_factory=stable_id_factory(description, reserved=retired))
    checked_document(result, retired=retired)

    params = _persist_params(result, job_id=job_id, token=token, model=model)
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        written = (await db.execute(text(_PERSIST), params)).first()
        if written is None:
            await db.rollback()
            logger.warning("[job:%s] Requirements result discarded: this attempt no longer holds the claim.", job_id)
            return
        await db.commit()
    logger.info("[job:%s] Requirements v2 extraction completed.", job_id)


async def _release_claim(job_id: str, token, Session) -> None:
    from database import set_rls_context
    from sqlalchemy import text

    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await db.execute(text(_RELEASE), {"jid": job_id, "token": token})
        await db.commit()


async def _mark_failed(job_id: str, token, Session) -> bool:
    """Final failure: processing -> failed with the fixed worker message, for the claim that holds `token` only."""
    from database import set_rls_context
    from sqlalchemy import text

    if token is None:
        return False
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        result = await db.execute(text(_MARK_FAILED), {"err": WORKER_FAILED_MESSAGE, "jid": job_id, "token": token})
        await db.commit()
    marked = result.rowcount == 1
    if marked:
        logger.error("[job:%s] Requirements v2 extraction permanently failed.", job_id)
    return marked
