"""Copy-to-job: give a candidate's CV files to a new application on another job.

Why a physical copy. Deleting an application (routers/applications.py, the delete route) removes the physical file of the first
application_files row it deletes. A reuse that shared the file_path would lose its CV when the source application is deleted, so the
copy gets its own physical file, written under the target job with the storage convention used at intake:

    tenants/<tenant_id>/jobs/<target_job_id>/<new_application_id>.<ext>

A second file of the same application gets "<new_application_id>-<n>.<ext>", so two files never share a path.

Atomicity. Every physical copy is written first. The application row and every application_files row are then written in one
transaction, committed once. If any step fails, the transaction is rolled back and every copy written so far is removed, so no
committed application exists without its files, and no file is left behind for a failed reuse.

Not covered: a process that dies between the copy and the commit leaves its copies on disk. They are unreferenced, not deleted.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReuseError(Exception):
    """Base for refusals and failures of a reuse. Nothing is created when one of these is raised."""


class ReuseSourceNotFound(ReuseError):
    """The source application is not in the tenant."""


class ReuseSourceHasNoFiles(ReuseError):
    """The source application has no CV file, so there is nothing to reuse."""


class ReuseSourceFileMissing(ReuseError):
    """A source CV file is referenced by the database but missing from storage."""


_SOURCE_SQL = text("""
    SELECT a.application_id, a.candidate_name, a.candidate_email, a.candidate_phone
    FROM applications a
    JOIN jobs j ON j.job_id = a.job_id
    WHERE a.application_id = CAST(:app_id AS uuid)
      AND j.tenant_id = CAST(:tid AS uuid)
""")

_SOURCE_FILES_SQL = text("""
    SELECT af.original_name, af.mime_type, af.file_path, af.file_size_bytes,
           af.extracted_text, af.extraction_status
    FROM application_files af
    WHERE af.application_id = CAST(:app_id AS uuid)
      AND af.tenant_id = CAST(:tid AS uuid)
    ORDER BY af.created_at, af.file_id
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
) -> str:
    """Create a candidate_reuse application on the target job with its own copies of the source CV files. Returns its id.

    The caller has already checked the target job (tenant, active, criteria) and the duplicate candidate. This function refuses
    before any write when the source is missing, has no file, or a referenced file is missing from storage.
    """
    source = (await db.execute(_SOURCE_SQL, {"app_id": source_application_id, "tid": tenant_id})).mappings().first()
    if source is None:
        raise ReuseSourceNotFound("Source application not found")
    files = (await db.execute(_SOURCE_FILES_SQL, {"app_id": source_application_id, "tid": tenant_id})).mappings().all()
    if not files:
        raise ReuseSourceHasNoFiles("The source application has no CV file to reuse.")

    base = Path(files_base_path)
    for row in files:
        if not (base / row["file_path"]).is_file():
            raise ReuseSourceFileMissing("The source CV file is missing from storage and cannot be reused.")

    new_application_id = str(uuid.uuid4())
    copied: list[Path] = []
    try:
        planned: list[tuple[object, str]] = []
        for index, row in enumerate(files):
            suffix = Path(row["file_path"]).suffix
            relative = f"tenants/{tenant_id}/jobs/{target_job_id}/{_copy_name(new_application_id, index, suffix)}"
            destination = base / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / row["file_path"], destination)
            copied.append(destination)
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
        await db.commit()
    except BaseException:
        await db.rollback()
        for destination in copied:
            destination.unlink(missing_ok=True)
        raise
    return new_application_id
