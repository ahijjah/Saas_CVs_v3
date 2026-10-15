"""Access gate and resume for AI extraction work that has been queued but not started.

Three kinds of work, each refused BEFORE any model call while the tenant has no current access (services/entitlement_service.py):
  * requirements-v2 initial extraction: the attempt is 'pending' with its token. A paused attempt stays exactly so (no claim, no token change,
    no failure), and resume dispatches it again. The claim (services/requirements_v2_extraction.claim_attempt) takes it from pending exactly once.
  * legacy criteria extraction (not v2): the job is 'pending'. The claim is conditional on pending or failed (or processing for a Celery retry of
    the same run), so a duplicate delivery does not run a second model call.
  * re-analysis candidates (migration 109): 'requested' with no decision. The worker appends a 'started' event under the job lock before its
    model call (migration 114), so a duplicate delivery is refused.

Resume is idempotent: it only dispatches work that is still pending, and each dispatch is safe to repeat because the claim is single-use.
Nothing here changes an analysis version's data; the append-only log only gains 'started' events.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.entitlement_service import Entitlement, load_entitlement

logger = logging.getLogger(__name__)


async def job_entitlement(db: AsyncSession, job_id: str) -> Entitlement:
    """The entitlement of the tenant that owns the job. A job that does not exist is refused (fail-closed)."""
    tenant = (await db.execute(
        text("SELECT tenant_id::text FROM jobs WHERE job_id = CAST(:jid AS uuid)"), {"jid": str(job_id)},
    )).scalar_one_or_none()
    if tenant is None:
        return Entitlement(state="blocked", subscription_status=None)
    return await load_entitlement(db, tenant)


_JOB_CONTEXT = "j.description, j.title, j.department, j.experience_level, j.location, j.job_type, j.work_mode"


def job_meta(row) -> dict:
    """The context the enqueue sends (creation and retry send exactly these fields)."""
    return {"title": row["title"], "department": row["department"], "experience_level": row["experience_level"], "location": row["location"],
            "job_type": row["job_type"], "work_mode": row["work_mode"]}


async def resume_paused_extraction(db: AsyncSession, tenant_id: str) -> dict:
    """Dispatch the still-pending extraction work of one tenant, if the tenant is entitled now. Returns the counts dispatched by kind.
    A failed dispatch is counted in "failed" and never raised; the work stays pending. Each dispatch is idempotent (the claims refuse a second run)."""
    empty = {"v2": 0, "legacy": 0, "candidates": 0, "failed": 0}
    entitlement = await load_entitlement(db, tenant_id)
    if not entitlement.full:
        return empty

    v2_rows = (await db.execute(text(f"""
        SELECT jc.job_id::text AS job_id, jc.requirements_extraction_token::text AS token, {_JOB_CONTEXT}
        FROM job_criteria jc JOIN jobs j ON j.job_id = jc.job_id
        WHERE j.tenant_id = CAST(:tid AS uuid)
          AND jc.requirements_schema_version = 2
          AND jc.criteria_extraction_status = 'pending'
          AND jc.requirements_extraction_token IS NOT NULL
          AND jc.original_analysis_json IS NULL
        ORDER BY j.created_at
    """), {"tid": tenant_id})).mappings().all()

    legacy_rows = (await db.execute(text(f"""
        SELECT jc.job_id::text AS job_id, {_JOB_CONTEXT}
        FROM job_criteria jc JOIN jobs j ON j.job_id = jc.job_id
        WHERE j.tenant_id = CAST(:tid AS uuid)
          AND COALESCE(jc.requirements_schema_version, 1) <> 2
          AND jc.criteria_extraction_status = 'pending'
        ORDER BY j.created_at
    """), {"tid": tenant_id})).mappings().all()

    candidate_rows = (await db.execute(text(f"""
        SELECT r.job_id::text AS job_id, r.version_id::text AS version_id, r.attempt_token::text AS token, {_JOB_CONTEXT}
        FROM requirements_analysis_log r JOIN jobs j ON j.job_id = r.job_id
        WHERE j.tenant_id = CAST(:tid AS uuid)
          AND r.kind = 'candidate' AND r.event = 'requested'
          AND NOT EXISTS (SELECT 1 FROM requirements_analysis_log d
                          WHERE d.version_id = r.version_id
                            AND d.event IN ('started', 'completed', 'rejected', 'applied', 'discarded', 'superseded'))
        ORDER BY r.log_id
    """), {"tid": tenant_id})).mappings().all()

    from workers.requirements_v2_extraction_worker import extract_requirements_v2_task, extract_candidate_task  # noqa: PLC0415
    from workers.criteria_worker import extract_criteria_task  # noqa: PLC0415

    # A dispatch that fails (the broker is down) leaves its work exactly as it is: still pending (or requested), with its token. It is not
    # failed and it is not lost: the next provisioning dispatches it again. The failure is counted and logged, and the caller still completes.
    counts = {"v2": 0, "legacy": 0, "candidates": 0, "failed": 0}
    plan = [("v2", extract_requirements_v2_task, row, lambda r: (r["job_id"], r["token"], r["description"], job_meta(r))) for row in v2_rows]
    plan += [("legacy", extract_criteria_task, row, lambda r: (r["job_id"], r["description"], job_meta(r))) for row in legacy_rows]
    plan += [("candidates", extract_candidate_task, row,
              lambda r: (r["job_id"], r["version_id"], r["token"], r["description"], job_meta(r))) for row in candidate_rows]
    for kind, task, row, args in plan:
        try:
            task.delay(*args(row))
            counts[kind] += 1
        except Exception as exc:  # noqa: BLE001 - the work stays pending; the failure is reported, not raised
            counts["failed"] += 1
            logger.error("tenant %s: could not dispatch paused %s work for job %s: %s", tenant_id, kind, row["job_id"], exc)
    if counts["v2"] or counts["legacy"] or counts["candidates"] or counts["failed"]:
        logger.info("tenant %s: resumed paused extraction work %s", tenant_id, counts)
    return counts
