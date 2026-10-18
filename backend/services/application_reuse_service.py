"""Copy-to-job: give a candidate's CV files to a new application on another job.

Why a physical copy. Deleting an application (routers/applications.py, the delete route) removes the physical file of the first
application_files row it deletes. A reuse that shared the file_path would lose its CV when the source application is deleted, so the
copy gets its own physical file, written under the target job with the storage convention used at intake:

    tenants/<tenant_id>/jobs/<target_job_id>/<new_application_id>.<ext>

A second file of the same application gets "<new_application_id>-<n>.<ext>", so two files never share a path.

Atomicity. Every decisive check runs inside the one transaction that inserts the application and its file rows, after locks taken in
a fixed order (the lock sequence below). The service never trusts the route's earlier checks. It commits once. Every unsuccessful exit
first removes the copies written so far (synchronously, before any awaited cleanup), then rolls back, then re-raises the original.

Lock sequence (every reuse takes them in this order; nothing else in the application takes them in another order):

    1. target jobs row              FOR UPDATE   (tenant-scoped; serialises reuses, and any status change, onto this job)
    2. target job_criteria row      FOR UPDATE   (criteria cannot change between validation and commit)
    3. source applications row      FOR SHARE    (cannot be deleted until this transaction ends)
    4. source application_files     FOR SHARE    (cannot be removed until this transaction ends)
    5. duplicate-candidate check    under lock 1 (only one reuse can create the candidate on the target job)

Not covered: a process that dies between the copy and the commit leaves its copies on disk. They are unreferenced, not deleted.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.criteria_status import CRITERIA_BLOCKING_STATUSES

logger = logging.getLogger(__name__)


class ReuseError(Exception):
    """Base for refusals and failures of a reuse. Nothing is created when one of these is raised."""


class ReuseTargetNotFound(ReuseError):
    """The target job is not in the tenant."""


class ReuseTargetInactive(ReuseError):
    """The target job is not active."""


class ReuseTargetCriteriaIncomplete(ReuseError):
    """The target job's criteria analysis is not complete."""


class ReuseSourceNotFound(ReuseError):
    """The source application is not in the tenant."""


class ReuseSameJob(ReuseError):
    """The source application is already on the target job."""


class ReuseSourceHasNoFiles(ReuseError):
    """The source application has no CV file, so there is nothing to reuse."""


class ReuseSourceFileMissing(ReuseError):
    """A source CV file is referenced by the database but missing from storage."""


class ReuseCandidateAlreadyOnJob(ReuseError):
    """The candidate already has an application on the target job."""


_TARGET_JOB_SQL = text("""
    SELECT job_id, status FROM jobs
    WHERE job_id = CAST(:job_id AS uuid) AND tenant_id = CAST(:tid AS uuid)
    FOR UPDATE
""")

_TARGET_CRITERIA_SQL = text("""
    SELECT criteria_extraction_status FROM job_criteria
    WHERE job_id = CAST(:job_id AS uuid)
    FOR UPDATE
""")

_SOURCE_SQL = text("""
    SELECT a.application_id, a.job_id, a.candidate_name, a.candidate_email, a.candidate_phone
    FROM applications a
    JOIN jobs j ON j.job_id = a.job_id
    WHERE a.application_id = CAST(:app_id AS uuid)
      AND j.tenant_id = CAST(:tid AS uuid)
    FOR SHARE OF a
""")

_SOURCE_FILES_SQL = text("""
    SELECT af.original_name, af.mime_type, af.file_path, af.file_size_bytes,
           af.extracted_text, af.extraction_status
    FROM application_files af
    WHERE af.application_id = CAST(:app_id AS uuid)
      AND af.tenant_id = CAST(:tid AS uuid)
    ORDER BY af.created_at, af.file_id
""")
# No row lock on the files: PostgreSQL requires UPDATE privilege for every row lock, and the runtime role has none on application_files.
# The source application row is locked FOR SHARE (_SOURCE_SQL), and the only delete of a file row runs inside deleting that application,
# which must first take the same row for update. The copy therefore commits before any source file can be removed.

_DUPLICATE_SQL = text("""
    SELECT 1 FROM applications
    WHERE candidate_email = :email AND job_id = CAST(:job_id AS uuid)
    LIMIT 1
""")

_INSERT_APPLICATION_SQL = text("""
    INSERT INTO applications (
      application_id, job_id, tenant_id, candidate_name, candidate_email, candidate_phone,
      submission_source, workflow_status, is_talent_pool
    ) VALUES (
      CAST(:app_id AS uuid), CAST(:job_id AS uuid), CAST(:tid AS uuid), :name, :email, :phone,
      'candidate_reuse', 'awaiting_review', FALSE
    )
""")

_INSERT_FILE_SQL = text("""
    INSERT INTO application_files (
      application_id, tenant_id, original_name, mime_type, file_path,
      file_size_bytes, extracted_text, extraction_status
    ) VALUES (
      CAST(:app_id AS uuid), CAST(:tid AS uuid), :orig, :mime, :path,
      :size, :text, :status
    )
""")


def _copy_name(new_application_id: str, index: int, suffix: str) -> str:
    return f"{new_application_id}{suffix}" if index == 0 else f"{new_application_id}-{index}{suffix}"


def _copy_file(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)


async def _copy_settled(source: Path, destination: Path) -> None:
    """Copy in a worker thread, so the event loop is not blocked.

    A thread cannot be stopped. If the awaiting task is cancelled, this waits for the thread to finish before it re-raises the
    cancellation, so the caller's cleanup sees every file the copy produced and none appears after the cleanup.
    """
    task = asyncio.ensure_future(asyncio.to_thread(_copy_file, source, destination))
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError()
    task.result()


async def _commit_settled(db: AsyncSession) -> bool:
    """Commit in its own task, so a cancellation of the caller cannot cancel the commit itself.

    Returns True when the caller was cancelled while the commit ran and the commit then succeeded: the transaction is committed, and
    the caller must keep its files. Returns False when the commit succeeded without cancellation. A commit failure is raised; when
    the caller was also cancelled, the cancellation is raised instead, once the commit has settled.
    """
    task = asyncio.ensure_future(db.commit())
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            cancelled = True
    try:
        task.result()
    except BaseException:
        if cancelled:
            raise asyncio.CancelledError()
        raise
    return cancelled


def _remove_copies(copied: list[Path]) -> None:
    """Synchronous on purpose: runs before any awaited cleanup, so a cancellation cannot interrupt it."""
    for destination in copied:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.error("Could not remove an unreferenced CV copy: %s", destination)


async def _insert_file_row(db: AsyncSession, *, new_application_id: str, tenant_id: str, row, file_path: str) -> None:
    await db.execute(_INSERT_FILE_SQL, {
        "app_id": new_application_id,
        "tid": tenant_id,
        "orig": row["original_name"],
        "mime": row["mime_type"],
        "path": file_path,
        "size": row["file_size_bytes"],
        "text": row["extracted_text"],
        "status": row["extraction_status"],
    })


async def reuse_cv_for_job(
    db: AsyncSession,
    *,
    source_application_id: str,
    target_job_id: str,
    tenant_id: str,
    files_base_path: str,
) -> tuple[str, str]:
    """Create a candidate_reuse application on the target job with its own CV copies, in one transaction.

    Returns (new_application_id, candidate_name). Every refusal is a ReuseError raised before anything is committed. The caller owns
    no transaction: this function commits on success and rolls back on every other exit.
    """
    base = Path(files_base_path)
    copied: list[Path] = []
    committed = False
    try:
        # 1. target job: tenant-scoped, locked. Serialises reuses onto this job and any status change of it.
        target = (await db.execute(_TARGET_JOB_SQL, {"job_id": target_job_id, "tid": tenant_id})).mappings().first()
        if target is None:
            raise ReuseTargetNotFound("Target job not found")
        if target["status"] != "active":
            raise ReuseTargetInactive("Target job is not active.")

        # 2. target criteria: locked, so the status holds until commit. A missing row counts as pending, as at intake.
        criteria_status = (await db.execute(_TARGET_CRITERIA_SQL, {"job_id": target_job_id})).scalar_one_or_none() or "pending"
        if criteria_status in CRITERIA_BLOCKING_STATUSES:
            raise ReuseTargetCriteriaIncomplete("The target job's criteria analysis is not complete.")

        # 3. source application: tenant-scoped, shared lock. A delete of it waits for this transaction.
        source = (await db.execute(_SOURCE_SQL, {"app_id": source_application_id, "tid": tenant_id})).mappings().first()
        if source is None:
            raise ReuseSourceNotFound("Source application not found")
        if str(source["job_id"]) == str(target_job_id):
            raise ReuseSameJob("The candidate is already on this job.")

        # 4. source files: shared lock, and each physical file must still exist.
        files = (await db.execute(_SOURCE_FILES_SQL, {"app_id": source_application_id, "tid": tenant_id})).mappings().all()
        if not files:
            raise ReuseSourceHasNoFiles("The source application has no CV file to reuse.")
        for row in files:
            if not (base / row["file_path"]).is_file():
                raise ReuseSourceFileMissing("The source CV file is missing from storage and cannot be reused.")

        # 5. duplicate candidate, under the target-job lock: two reuses of one candidate cannot both pass this check.
        duplicate = (await db.execute(_DUPLICATE_SQL, {"email": source["candidate_email"], "job_id": target_job_id})).first()
        if duplicate is not None:
            raise ReuseCandidateAlreadyOnJob("Candidate already applied to this job")

        # 6. copies first (each path recorded before its copy starts, so a partial write is also removed), then the rows, one commit.
        new_application_id = str(uuid.uuid4())
        planned: list[tuple[object, str]] = []
        for index, row in enumerate(files):
            suffix = Path(row["file_path"]).suffix
            relative = f"tenants/{tenant_id}/jobs/{target_job_id}/{_copy_name(new_application_id, index, suffix)}"
            destination = base / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copied.append(destination)
            await _copy_settled(base / row["file_path"], destination)
            planned.append((row, relative))

        await db.execute(_INSERT_APPLICATION_SQL, {
            "app_id": new_application_id,
            "job_id": target_job_id,
            "tid": tenant_id,
            "name": source["candidate_name"],
            "email": source["candidate_email"],
            "phone": source["candidate_phone"],
        })
        for row, relative in planned:
            await _insert_file_row(db, new_application_id=new_application_id, tenant_id=tenant_id, row=row, file_path=relative)
        cancelled_during_commit = await _commit_settled(db)
        committed = True
        if cancelled_during_commit:
            raise asyncio.CancelledError()
    except BaseException:
        # Only an uncommitted transaction is undone. Files first, synchronously; then the awaited rollback; then the original
        # exception is re-raised unchanged. After a successful commit the files and rows must stay together.
        if not committed:
            _remove_copies(copied)
            try:
                await db.rollback()
            except BaseException:
                logger.exception("Rollback after a failed CV reuse did not complete; the session is discarded by its owner.")
        raise
    return new_application_id, source["candidate_name"]
