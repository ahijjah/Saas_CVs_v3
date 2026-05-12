import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

router = APIRouter(prefix="/admin", tags=["platform-config"])

VALID_TYPES = ("string", "number", "boolean", "json")
VALID_CATEGORIES = ("scoring", "ai", "email", "queue", "subscription", "security", "general")


def _validate_value_for_type(value: str, type_: str) -> None:
    if type_ == "number":
        try:
            float(value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Value must be a valid number",
            )
    elif type_ == "boolean":
        if value.lower() not in ("true", "false", "1", "0"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Boolean value must be one of: true, false, 1, 0",
            )
    elif type_ == "json":
        try:
            json.loads(value)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Value must be valid JSON",
            )


class CreateConfigRequest(BaseModel):
    key: str
    value: str
    type: str = "string"
    category: str = "general"
    description: str | None = None
    editable: bool = True

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"type must be one of {VALID_TYPES}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        return v

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        import re
        if not re.match(r'^[a-z0-9_]{2,100}$', v):
            raise ValueError("key must be lowercase alphanumeric with underscores only")
        return v


class UpdateConfigValueRequest(BaseModel):
    value: str


@router.get("/platform-config", dependencies=[RequireSuperAdmin])
async def get_platform_config(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    params: dict[str, Any] = {}
    where = ""
    if category:
        if category not in VALID_CATEGORIES:
            raise HTTPException(status_code=422, detail=f"Invalid category. Must be one of {VALID_CATEGORIES}")
        where = "WHERE sc.category = :category"
        params["category"] = category

    rows = await db.execute(
        text(f"""
            SELECT sc.key, sc.value, sc.type, sc.category, sc.description,
                   sc.editable, sc.updated_at, sc.updated_by,
                   u.email AS updated_by_email
            FROM system_config sc
            LEFT JOIN users u ON u.user_id = sc.updated_by
            {where}
            ORDER BY sc.category, sc.key
        """),
        params,
    )

    configs = []
    for r in rows.mappings():
        configs.append({
            "key": r["key"],
            "value": r["value"],
            "type": r["type"],
            "category": r["category"],
            "description": r["description"],
            "editable": r["editable"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "updated_by": str(r["updated_by"]) if r["updated_by"] else None,
            "updated_by_email": r["updated_by_email"],
        })

    return {"configs": configs}


@router.put("/platform-config/{key}", dependencies=[RequireSuperAdmin])
async def update_platform_config(
    key: str,
    body: UpdateConfigValueRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    row = await db.execute(
        text("SELECT key, type, editable FROM system_config WHERE key = :key"),
        {"key": key},
    )
    existing = row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config key not found")
    if not existing["editable"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This config key is read-only")

    _validate_value_for_type(body.value, existing["type"])

    await db.execute(
        text("""
            UPDATE system_config
            SET value = :value, updated_at = now(), updated_by = CAST(:uid AS uuid)
            WHERE key = :key
        """),
        {"value": body.value, "key": key, "uid": current_user.user_id},
    )
    await db.commit()

    return {"success": True, "key": key, "value": body.value}


@router.post(
    "/platform-config",
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireSuperAdmin],
)
async def create_platform_config(
    body: CreateConfigRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    existing = await db.execute(
        text("SELECT key FROM system_config WHERE key = :key"),
        {"key": body.key},
    )
    if existing.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Config key already exists")

    _validate_value_for_type(body.value, body.type)

    await db.execute(
        text("""
            INSERT INTO system_config (key, value, type, category, description, editable, updated_by, updated_at)
            VALUES (:key, :value, :type, :category, :description, :editable, CAST(:uid AS uuid), now())
        """),
        {
            "key": body.key,
            "value": body.value,
            "type": body.type,
            "category": body.category,
            "description": body.description,
            "editable": body.editable,
            "uid": current_user.user_id,
        },
    )
    await db.commit()

    return {"success": True, "key": body.key}
