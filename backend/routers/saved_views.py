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


class CreateSavedViewRequest(BaseModel):
    name:    str = Field(..., min_length=1, max_length=100)
    filters: SavedViewFilters


class UpdateSavedViewRequest(BaseModel):
    name:    str | None = Field(None, min_length=1, max_length=100)
    filters: SavedViewFilters | None = None


def _serialize(row: dict) -> dict:
    return {
        "saved_view_id": str(row["saved_view_id"]),
        "name":          row["name"],
        "filters":       row["filters_json"] if isinstance(row["filters_json"], dict) else json.loads(row["filters_json"]),
        "created_at":    row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":    row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("")
async def list_saved_views(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT saved_view_id, name, filters_json, created_at, updated_at
            FROM candidate_saved_views
            WHERE user_id   = CAST(:uid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
            ORDER BY created_at ASC
        """),
        {"uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    return {"saved_views": [_serialize(dict(r)) for r in rows.mappings()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_saved_view(
    body: CreateSavedViewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            INSERT INTO candidate_saved_views (tenant_id, user_id, name, filters_json)
            VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), :name, CAST(:filters AS jsonb))
            RETURNING saved_view_id, name, filters_json, created_at, updated_at
        """),
        {
            "tid":     current_user.tenant_id,
            "uid":     current_user.user_id,
            "name":    body.name.strip(),
            "filters": json.dumps(body.filters.model_dump(exclude_none=True)),
        },
    )
    await db.commit()
    return {"success": True, "saved_view": _serialize(dict(row.mappings().first()))}


@router.patch("/{view_id}")
async def update_saved_view(
    view_id: str,
    body: UpdateSavedViewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    existing = await db.execute(
        text("""
            SELECT saved_view_id FROM candidate_saved_views
            WHERE saved_view_id = CAST(:vid AS uuid)
              AND user_id       = CAST(:uid AS uuid)
              AND tenant_id     = CAST(:tid AS uuid)
        """),
        {"vid": view_id, "uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    if not existing.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found.")

    set_parts: list[str] = ["updated_at = now()"]
    params: dict = {"vid": view_id, "uid": current_user.user_id, "tid": current_user.tenant_id}

    if body.name is not None:
        set_parts.append("name = :name")
        params["name"] = body.name.strip()
    if body.filters is not None:
        set_parts.append("filters_json = CAST(:filters AS jsonb)")
        params["filters"] = json.dumps(body.filters.model_dump(exclude_none=True))

    set_clause = ", ".join(set_parts)

    row = await db.execute(
        text(f"""
            UPDATE candidate_saved_views
            SET {set_clause}
            WHERE saved_view_id = CAST(:vid AS uuid)
              AND user_id       = CAST(:uid AS uuid)
              AND tenant_id     = CAST(:tid AS uuid)
            RETURNING saved_view_id, name, filters_json, created_at, updated_at
        """),
        params,
    )
    await db.commit()
    return {"success": True, "saved_view": _serialize(dict(row.mappings().first()))}


@router.delete("/{view_id}")
async def delete_saved_view(
    view_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

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
