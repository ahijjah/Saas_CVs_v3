import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.module_guards import RequireATSManagement
from database import get_db, set_rls_context

router = APIRouter(tags=["comments"], dependencies=[RequireATSManagement])

_MENTION_RE = re.compile(r"@(\w[\w.\- ]*)")


def _parse_mentions(text_: str) -> list[str]:
    """Extract @mention strings from comment text."""
    return _MENTION_RE.findall(text_)


def _serialize_comment(row: dict) -> dict:
    mentions = _parse_mentions(row["comment_text"])
    return {
        "comment_id":    str(row["comment_id"]),
        "application_id": str(row["application_id"]),
        "user_id":       str(row["user_id"]),
        "author_name":   row["author_name"] or row["author_email"] or "Unknown",
        "author_email":  row["author_email"],
        "comment_text":  row["comment_text"],
        "mentions":      mentions,
        "created_at":    row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":    row["updated_at"].isoformat() if row["updated_at"] else None,
        "is_own":        False,  # overridden by caller
    }


class CreateCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=2000)


class UpdateCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=2000)


@router.get("/applications/{application_id}/comments")
async def list_comments(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    tenant_filter = "" if is_super_admin else " AND cc.tenant_id = CAST(:tid AS uuid)"

    params: dict = {"aid": application_id}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    rows = await db.execute(
        text(f"""
            SELECT
                cc.comment_id,
                cc.application_id,
                cc.user_id,
                cc.comment_text,
                cc.created_at,
                cc.updated_at,
                COALESCE(u.full_name, u.email) AS author_name,
                u.email AS author_email
            FROM candidate_comments cc
            JOIN users u ON u.user_id = cc.user_id
            WHERE cc.application_id = CAST(:aid AS uuid)
              {tenant_filter}
            ORDER BY cc.created_at ASC
        """),
        params,
    )

    comments = []
    for r in rows.mappings():
        c = _serialize_comment(dict(r))
        c["is_own"] = str(r["user_id"]) == current_user.user_id
        comments.append(c)

    return {"comments": comments}


@router.post("/applications/{application_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    application_id: str,
    body: CreateCommentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"

    verify_params: dict = {"aid": application_id}
    if not is_super_admin:
        verify_params["tid"] = current_user.tenant_id

    # Verify the application belongs to this tenant
    exists = await db.execute(
        text(f"""
            SELECT 1 FROM applications
            WHERE application_id = CAST(:aid AS uuid)
              {tenant_filter}
        """),
        verify_params,
    )
    if not exists.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")

    row = await db.execute(
        text("""
            INSERT INTO candidate_comments
                (application_id, tenant_id, user_id, comment_text)
            VALUES
                (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:uid AS uuid), :text)
            RETURNING comment_id, application_id, user_id, comment_text, created_at, updated_at
        """),
        {
            "aid":  application_id,
            "tid":  current_user.tenant_id,
            "uid":  current_user.user_id,
            "text": body.comment_text.strip(),
        },
    )
    await db.commit()

    new_row = dict(row.mappings().first())
    new_row["author_name"]  = current_user.full_name or current_user.email
    new_row["author_email"] = current_user.email

    comment = _serialize_comment(new_row)
    comment["is_own"] = True

    return {"success": True, "comment": comment}


@router.patch("/applications/{application_id}/comments/{comment_id}")
async def update_comment(
    application_id: str,
    comment_id: str,
    body: UpdateCommentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    # Build conditional WHERE clause for SELECT
    select_where_parts = ["comment_id = CAST(:cid AS uuid)", "application_id = CAST(:aid AS uuid)"]
    select_params: dict = {"cid": comment_id, "aid": application_id}

    # Only enforce tenant_id filter if not super_admin
    if not is_super_admin:
        select_where_parts.append("tenant_id = CAST(:tid AS uuid)")
        select_params["tid"] = current_user.tenant_id

    # Only enforce user_id filter if not admin
    if not is_admin:
        select_where_parts.append("user_id = CAST(:uid AS uuid)")
        select_params["uid"] = current_user.user_id

    select_where = " AND ".join(select_where_parts)

    existing = await db.execute(
        text(f"""
            SELECT comment_id FROM candidate_comments
            WHERE {select_where}
        """),
        select_params,
    )
    if not existing.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found or not yours.")

    # Build UPDATE query
    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"
    update_params: dict = {"cid": comment_id, "text": body.comment_text.strip()}
    if not is_super_admin:
        update_params["tid"] = current_user.tenant_id

    row = await db.execute(
        text(f"""
            UPDATE candidate_comments
               SET comment_text = :text,
                   updated_at   = now()
             WHERE comment_id = CAST(:cid AS uuid)
               {tenant_filter}
            RETURNING comment_id, application_id, user_id, comment_text, created_at, updated_at
        """),
        update_params,
    )
    await db.commit()

    updated_row = dict(row.mappings().first())
    updated_row["author_name"]  = current_user.full_name or current_user.email
    updated_row["author_email"] = current_user.email

    comment = _serialize_comment(updated_row)
    comment["is_own"] = True

    return {"success": True, "comment": comment}


@router.delete("/applications/{application_id}/comments/{comment_id}")
async def delete_comment(
    application_id: str,
    comment_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"

    if is_admin:
        # Admins can delete any comment in their tenant (or all tenants if super_admin)
        delete_params: dict = {"cid": comment_id, "aid": application_id}
        if not is_super_admin:
            delete_params["tid"] = current_user.tenant_id

        result = await db.execute(
            text(f"""
                DELETE FROM candidate_comments
                WHERE comment_id     = CAST(:cid AS uuid)
                  AND application_id = CAST(:aid AS uuid)
                  {tenant_filter}
            """),
            delete_params,
        )
    else:
        # Others can only delete their own comments
        delete_params: dict = {"cid": comment_id, "aid": application_id, "uid": current_user.user_id}
        if not is_super_admin:
            delete_params["tid"] = current_user.tenant_id

        result = await db.execute(
            text(f"""
                DELETE FROM candidate_comments
                WHERE comment_id     = CAST(:cid AS uuid)
                  AND application_id = CAST(:aid AS uuid)
                  AND user_id        = CAST(:uid AS uuid)
                  {tenant_filter}
            """),
            delete_params,
        )

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found or not yours.")

    await db.commit()
    return {"success": True}
