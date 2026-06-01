from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

app_router = APIRouter(prefix="/applications", tags=["candidates"])
tag_router = APIRouter(prefix="/tags", tags=["candidates"])


# ── Pydantic Models ────────────────────────────────────────────────────────


class CandidateTag(BaseModel):
    tag_id: str
    tag_name: str
    color: str | None


class TagCreateRequest(BaseModel):
    tag_name: str
    color: str | None = None


class TagAddRequest(BaseModel):
    tag_ids: list[str]


class TalentPoolRequest(BaseModel):
    is_talent_pool: bool


class ReuseToJobRequest(BaseModel):
    target_job_id: str


# ── Application Tag Endpoints ──────────────────────────────────────────────


@app_router.get("/{application_id}/tags")
async def get_application_tags(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get all tags for a candidate (application)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT t.tag_id, t.tag_name, t.color
            FROM application_candidate_tags act
            JOIN candidate_tags t ON t.tag_id = act.tag_id
            WHERE act.application_id = CAST(:app_id AS uuid)
            ORDER BY t.tag_name
        """),
        {"app_id": application_id},
    )

    tags = [CandidateTag(
        tag_id=str(r["tag_id"]),
        tag_name=r["tag_name"],
        color=r["color"],
    ) for r in rows.mappings()]

    return {"tags": tags}


@app_router.post("/{application_id}/tags")
async def add_tags_to_application(
    application_id: str,
    body: TagAddRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add one or more tags to a candidate."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if not body.tag_ids:
        raise HTTPException(status_code=400, detail="tag_ids cannot be empty")

    tag_ids = [str(tid) for tid in body.tag_ids]

    # Verify application exists and belongs to this tenant
    app_check = await db.execute(
        text("""
            SELECT a.application_id FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    if not app_check.scalars().first():
        raise HTTPException(status_code=404, detail="Application not found")

    # Verify all tags exist and belong to this tenant.
    # Use per-element parameters — asyncpg cannot bind a Python list into ARRAY[].
    tag_params: dict = {"tid": current_user.tenant_id}
    tag_placeholders: list[str] = []
    for i, tid in enumerate(tag_ids):
        key = f"tag_id_{i}"
        tag_params[key] = tid
        tag_placeholders.append(f"CAST(:{key} AS uuid)")

    tag_check = await db.execute(
        text(f"""
            SELECT COUNT(*) FROM candidate_tags
            WHERE tenant_id = CAST(:tid AS uuid)
              AND tag_id IN ({', '.join(tag_placeholders)})
        """),
        tag_params,
    )
    if (tag_check.scalar() or 0) != len(tag_ids):
        raise HTTPException(status_code=400, detail="One or more tags not found")

    # Insert tags one at a time — ON CONFLICT DO NOTHING handles duplicates.
    for tid in tag_ids:
        await db.execute(
            text("""
                INSERT INTO application_candidate_tags (application_id, tag_id)
                VALUES (CAST(:app_id AS uuid), CAST(:tag_id AS uuid))
                ON CONFLICT DO NOTHING
            """),
            {"app_id": application_id, "tag_id": tid},
        )

    await db.commit()
    return {"status": "tags added"}


@app_router.delete("/{application_id}/tags/{tag_id}")
async def remove_tag_from_application(
    application_id: str,
    tag_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove a tag from a candidate."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify application belongs to this tenant
    app_check = await db.execute(
        text("""
            SELECT a.application_id FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    if not app_check.scalars().first():
        raise HTTPException(status_code=404, detail="Application not found")

    result = await db.execute(
        text("""
            DELETE FROM application_candidate_tags
            WHERE application_id = CAST(:app_id AS uuid)
              AND tag_id = CAST(:tag_id AS uuid)
            RETURNING id
        """),
        {"app_id": application_id, "tag_id": tag_id},
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Tag not associated with this candidate")

    await db.commit()
    return {"status": "tag removed"}


# ── Talent Pool Endpoints ──────────────────────────────────────────────────


@app_router.post("/{application_id}/talent-pool")
async def toggle_talent_pool(
    application_id: str,
    body: TalentPoolRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add or remove a candidate from talent pool."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("""
            UPDATE applications
            SET is_talent_pool = :is_tp
            WHERE application_id = CAST(:app_id AS uuid)
              AND job_id IN (
                SELECT job_id FROM jobs WHERE tenant_id = CAST(:tid AS uuid)
              )
            RETURNING application_id
        """),
        {
            "app_id": application_id,
            "is_tp": body.is_talent_pool,
            "tid": current_user.tenant_id,
        },
    )

    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Application not found")

    await db.commit()
    return {"status": "talent pool status updated"}


@app_router.get("/talent-pool")
async def list_talent_pool(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 50,
):
    """List talent pool candidates for the tenant."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
              a.application_id,
              a.candidate_name,
              a.candidate_email,
              a.workflow_status,
              a.assigned_user_id,
              a.created_at,
              j.title as job_title,
              j.job_id,
              u.full_name as assigned_user_name
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN users u ON u.user_id = a.assigned_user_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND a.is_talent_pool = TRUE
            ORDER BY a.created_at DESC
            LIMIT :limit OFFSET :skip
        """),
        {
            "tid": current_user.tenant_id,
            "limit": limit,
            "skip": skip,
        },
    )

    candidates = [dict(r) for r in rows.mappings()]

    # Get total count
    count_result = await db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND a.is_talent_pool = TRUE
        """),
        {"tid": current_user.tenant_id},
    )
    total = count_result.scalar() or 0

    return {
        "candidates": candidates,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@app_router.post("/{application_id}/reuse-to-job")
async def reuse_candidate_to_job(
    application_id: str,
    body: ReuseToJobRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new application for another job using an existing candidate."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify source application exists and belongs to this tenant
    source = await db.execute(
        text("""
            SELECT a.application_id, a.candidate_name, a.candidate_email,
                   a.candidate_phone, a.source_job_id, a.source_page_url,
                   a.external_id, a.cv_file_path
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    src_app = source.mappings().first()
    if not src_app:
        raise HTTPException(status_code=404, detail="Source application not found")

    # Verify target job exists and belongs to this tenant
    job_check = await db.execute(
        text("""
            SELECT job_id FROM jobs
            WHERE job_id = CAST(:job_id AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
        """),
        {"job_id": body.target_job_id, "tid": current_user.tenant_id},
    )
    if not job_check.scalars().first():
        raise HTTPException(status_code=404, detail="Target job not found")

    # Check if candidate already applied to this job
    existing = await db.execute(
        text("""
            SELECT application_id FROM applications
            WHERE candidate_email = :email
              AND job_id = CAST(:job_id AS uuid)
            LIMIT 1
        """),
        {"email": src_app["candidate_email"], "job_id": body.target_job_id},
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Candidate already applied to this job")

    # Create new application
    result = await db.execute(
        text("""
            INSERT INTO applications (
              job_id, candidate_name, candidate_email, candidate_phone,
              source_job_id, source_page_url, external_id, cv_file_path,
              workflow_status, is_talent_pool
            ) VALUES (
              CAST(:job_id AS uuid), :name, :email, :phone,
              :source_job, :source_url, :ext_id, :cv_path,
              'awaiting_review', FALSE
            )
            RETURNING application_id
        """),
        {
            "job_id": body.target_job_id,
            "name": src_app["candidate_name"],
            "email": src_app["candidate_email"],
            "phone": src_app["candidate_phone"],
            "source_job": src_app["source_job_id"],
            "source_url": src_app["source_page_url"],
            "ext_id": src_app["external_id"],
            "cv_path": src_app["cv_file_path"],
        },
    )
    new_app_id = result.scalar()
    await db.commit()

    return {
        "new_application_id": str(new_app_id),
        "candidate_name": src_app["candidate_name"],
        "message": "Candidate added to new job",
    }


# ── Tag Management Endpoints ───────────────────────────────────────────────


@tag_router.get("/")
async def list_tenant_tags(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = None,
):
    """List all tags for the tenant, optionally filtered by name."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    query = "SELECT tag_id, tag_name, color FROM candidate_tags WHERE tenant_id = CAST(:tid AS uuid)"
    params = {"tid": current_user.tenant_id}

    if search:
        query += " AND tag_name ILIKE :search"
        params["search"] = f"%{search}%"

    query += " ORDER BY tag_name"

    rows = await db.execute(text(query), params)
    tags = [CandidateTag(
        tag_id=str(r["tag_id"]),
        tag_name=r["tag_name"],
        color=r["color"],
    ) for r in rows.mappings()]

    return {"tags": tags}


@tag_router.post("/")
async def create_tag(
    body: TagCreateRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new tag for the tenant."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if not body.tag_name or not body.tag_name.strip():
        raise HTTPException(status_code=400, detail="tag_name cannot be empty")

    tag_name = body.tag_name.strip()

    try:
        result = await db.execute(
            text("""
                INSERT INTO candidate_tags (tenant_id, tag_name, color, created_by)
                VALUES (CAST(:tid AS uuid), :name, :color, CAST(:user_id AS uuid))
                RETURNING tag_id, tag_name, color
            """),
            {
                "tid": current_user.tenant_id,
                "name": tag_name,
                "color": body.color,
                "user_id": current_user.user_id,
            },
        )
        row = result.mappings().first()
        await db.commit()

        return CandidateTag(
            tag_id=str(row["tag_id"]),
            tag_name=row["tag_name"],
            color=row["color"],
        )
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Tag '{tag_name}' already exists for this tenant",
            )
        raise HTTPException(status_code=400, detail="Failed to create tag")


@tag_router.delete("/{tag_id}")
async def delete_tag(
    tag_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a tag (only if not in use or user is admin)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Check if tag belongs to this tenant
    tag_check = await db.execute(
        text("""
            SELECT tag_id FROM candidate_tags
            WHERE tag_id = CAST(:tag_id AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
        """),
        {"tag_id": tag_id, "tid": current_user.tenant_id},
    )
    if not tag_check.scalars().first():
        raise HTTPException(status_code=404, detail="Tag not found")

    # Check if tag is in use
    usage_check = await db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM application_candidate_tags
            WHERE tag_id = CAST(:tag_id AS uuid)
        """),
        {"tag_id": tag_id},
    )
    if usage_check.scalar() > 0 and current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Cannot delete tags in use")

    result = await db.execute(
        text("""
            DELETE FROM candidate_tags
            WHERE tag_id = CAST(:tag_id AS uuid)
            RETURNING tag_id
        """),
        {"tag_id": tag_id},
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.commit()
    return {"status": "tag deleted"}
