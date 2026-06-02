import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/candidate-saved-views", tags=["candidate-saved-views"])


class SavedViewFilters(BaseModel):
    activeView:       str | None = None
    workflowFilter:   str | None = None
    processingFilter: str | None = None
    aiResultFilter:   str | None = None
    campaignFilter:   str | None = None
    clientFilter:     str | None = None
    search:           str | None = None
    assignedFilter:   str | None = None


class CreateSavedViewRequest(BaseModel):
    name:            str = Field(..., min_length=1, max_length=100)
    filters:         SavedViewFilters
    visibility_type: str = Field("private", pattern=r"^(private|tenant_shared)$")


class UpdateSavedViewRequest(BaseModel):
    name:            str | None = Field(None, min_length=1, max_length=100)
    filters:         SavedViewFilters | None = None
    visibility_type: str | None = Field(None, pattern=r"^(private|tenant_shared)$")


def _serialize(row: dict, current_user_id: str) -> dict:
    return {
        "saved_view_id":  str(row["saved_view_id"]),
        "name":           row["name"],
        "filters":        row["filters_json"] if isinstance(row["filters_json"], dict) else json.loads(row["filters_json"]),
        "visibility_type": row.get("visibility_type", "private"),
        "created_by":     str(row["created_by"]) if row.get("created_by") else None,
        "created_by_name": row.get("created_by_name"),
        "is_own":         str(row.get("created_by") or row.get("user_id", "")) == current_user_id,
        "created_at":     row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":     row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("")
async def list_saved_views(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the user's private views + all tenant-shared views."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
                sv.saved_view_id,
                sv.name,
                sv.filters_json,
                sv.visibility_type,
                sv.created_by,
                sv.user_id,
                sv.created_at,
                sv.updated_at,
                COALESCE(u.full_name, u.email) AS created_by_name
            FROM candidate_saved_views sv
            LEFT JOIN users u ON u.user_id = sv.created_by
            WHERE sv.tenant_id = CAST(:tid AS uuid)
              AND (
                sv.user_id         = CAST(:uid AS uuid)
                OR sv.visibility_type = 'tenant_shared'
              )
            ORDER BY sv.visibility_type DESC, sv.created_at ASC
        """),
        {"uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    views = [_serialize(dict(r), current_user.user_id) for r in rows.mappings()]
    return {"saved_views": views}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_saved_view(
    body: CreateSavedViewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    visibility_type = body.visibility_type

    if visibility_type == "tenant_shared" and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create shared views.",
        )

    row = await db.execute(
        text("""
            INSERT INTO candidate_saved_views
                (tenant_id, user_id, name, filters_json, visibility_type, created_by)
            VALUES
                (CAST(:tid AS uuid), CAST(:uid AS uuid), :name,
                 CAST(:filters AS jsonb), :vis_type, CAST(:uid AS uuid))
            RETURNING saved_view_id, name, filters_json, visibility_type, created_by,
                      user_id, created_at, updated_at
        """),
        {
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "name":     body.name.strip(),
            "filters":  json.dumps(body.filters.model_dump(exclude_none=True)),
            "vis_type": visibility_type,
        },
    )
    await db.commit()
    new_row = dict(row.mappings().first())
    new_row["created_by_name"] = current_user.full_name or current_user.email
    return {"success": True, "saved_view": _serialize(new_row, current_user.user_id)}


@router.patch("/{view_id}")
async def update_saved_view(
    view_id: str,
    body: UpdateSavedViewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    # Fetch the view — owner or admin only
    existing = await db.execute(
        text("""
            SELECT saved_view_id, user_id, visibility_type FROM candidate_saved_views
            WHERE saved_view_id = CAST(:vid AS uuid)
              AND tenant_id     = CAST(:tid AS uuid)
        """),
        {"vid": view_id, "tid": current_user.tenant_id},
    )
    ex = existing.first()
    if not ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found.")

    owner_id = str(ex[1])
    if owner_id != current_user.user_id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit another user's view.")

    if body.visibility_type == "tenant_shared" and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can make views shared.")

    set_parts: list[str] = ["updated_at = now()"]
    params: dict = {"vid": view_id, "tid": current_user.tenant_id}

    if body.name is not None:
        set_parts.append("name = :name")
        params["name"] = body.name.strip()
    if body.filters is not None:
        set_parts.append("filters_json = CAST(:filters AS jsonb)")
        params["filters"] = json.dumps(body.filters.model_dump(exclude_none=True))
    if body.visibility_type is not None:
        set_parts.append("visibility_type = :vis_type")
        params["vis_type"] = body.visibility_type

    set_clause = ", ".join(set_parts)

    row = await db.execute(
        text(f"""
            UPDATE candidate_saved_views
               SET {set_clause}
             WHERE saved_view_id = CAST(:vid AS uuid)
               AND tenant_id     = CAST(:tid AS uuid)
            RETURNING saved_view_id, name, filters_json, visibility_type,
                      created_by, user_id, created_at, updated_at
        """),
        params,
    )
    await db.commit()
    updated_row = dict(row.mappings().first())
    updated_row["created_by_name"] = None
    return {"success": True, "saved_view": _serialize(updated_row, current_user.user_id)}


@router.delete("/{view_id}")
async def delete_saved_view(
    view_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    if is_admin:
        # Admin can delete any view in the tenant
        result = await db.execute(
            text("""
                DELETE FROM candidate_saved_views
                WHERE saved_view_id = CAST(:vid AS uuid)
                  AND tenant_id     = CAST(:tid AS uuid)
            """),
            {"vid": view_id, "tid": current_user.tenant_id},
        )
    else:
        # Users can only delete their own views
        result = await db.execute(
            text("""
                DELETE FROM candidate_saved_views
                WHERE saved_view_id = CAST(:vid AS uuid)
                  AND user_id       = CAST(:uid AS uuid)
                  AND tenant_id     = CAST(:tid AS uuid)
            """),
            {"vid": view_id, "uid": current_user.user_id, "tid": current_user.tenant_id},
        )

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found.")
    await db.commit()
    return {"success": True}
