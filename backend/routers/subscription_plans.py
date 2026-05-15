import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

router = APIRouter(tags=["subscription-plans"])

_PLAN_CODE_RE = re.compile(r'^[a-z0-9_-]{2,50}$')


class PlanRequest(BaseModel):
    plan_code: str
    plan_name: str
    description: str | None = None
    monthly_price: float = 0.0
    yearly_price: float = 0.0
    currency: str = "USD"
    trial_days: int = 14
    max_campaigns: int = 5
    max_processed_cvs_per_month: int = 500
    max_users: int = 3
    display_order: int = 0

    @field_validator("plan_code")
    @classmethod
    def validate_plan_code(cls, v: str) -> str:
        if not _PLAN_CODE_RE.match(v):
            raise ValueError("plan_code must be lowercase alphanumeric with _ or - only (2-50 chars)")
        return v

    @field_validator("monthly_price", "yearly_price")
    @classmethod
    def validate_non_negative_price(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Price must be non-negative")
        return v

    @field_validator("max_campaigns", "max_processed_cvs_per_month", "max_users")
    @classmethod
    def validate_positive_limits(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Limits must be positive integers")
        return v

    @field_validator("trial_days")
    @classmethod
    def validate_trial_days(cls, v: int) -> int:
        if v < 0:
            raise ValueError("trial_days must be non-negative")
        return v


class FeaturePatchRequest(BaseModel):
    value_boolean: bool | None = None
    value_number: float | None = None
    value_text: str | None = None


class SubscribeRequest(BaseModel):
    plan_code: str


def _row_to_plan(r: Any, features: list[dict]) -> dict[str, Any]:
    return {
        "plan_id": str(r["plan_id"]),
        "plan_code": r["plan_code"],
        "plan_name": r["plan_name"],
        "description": r["description"],
        "monthly_price": float(r["monthly_price"]),
        "yearly_price": float(r["yearly_price"]),
        "currency": r["currency"],
        "trial_days": r["trial_days"],
        "max_campaigns": r["max_campaigns"],
        "max_processed_cvs_per_month": r["max_processed_cvs_per_month"],
        "max_users": r["max_users"],
        "status": r["status"],
        "display_order": r["display_order"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        "updated_by_email": r["updated_by_email"],
        "features": features,
    }


def _row_to_feature(r: Any) -> dict[str, Any]:
    return {
        "feature_id": str(r["feature_id"]),
        "feature_key": r["feature_key"],
        "feature_name": r["feature_name"],
        "description": r["description"],
        "value_type": r["value_type"],
        "value_boolean": r["value_boolean"],
        "value_number": float(r["value_number"]) if r["value_number"] is not None else None,
        "value_text": r["value_text"],
        "display_order": r["display_order"],
    }


async def _load_features_by_plan(db: AsyncSession, plan_ids: list[str]) -> dict[str, list[dict]]:
    """Return {plan_id_str: [feature_dict, ...]} for all given plan IDs."""
    if not plan_ids:
        return {}
    result = await db.execute(
        text("""
            SELECT feature_id, plan_id, feature_key, feature_name, description,
                   value_type, value_boolean, value_number, value_text, display_order
            FROM plan_features
            WHERE plan_id = ANY(CAST(:ids AS uuid[]))
            ORDER BY display_order, feature_key
        """),
        {"ids": plan_ids},
    )
    by_plan: dict[str, list[dict]] = {pid: [] for pid in plan_ids}
    for row in result.mappings():
        pid = str(row["plan_id"])
        if pid in by_plan:
            by_plan[pid].append(_row_to_feature(row))
    return by_plan


# ── Super Admin: plan CRUD ─────────────────────────────────────────────────────

@router.get("/admin/subscription-plans", dependencies=[RequireSuperAdmin])
async def list_subscription_plans(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    rows = await db.execute(text("""
        SELECT sp.*, u.email AS updated_by_email
        FROM subscription_plans sp
        LEFT JOIN users u ON u.user_id = sp.updated_by
        ORDER BY sp.display_order, sp.created_at
    """))
    plan_rows = list(rows.mappings())
    plan_ids = [str(r["plan_id"]) for r in plan_rows]
    features_by_plan = await _load_features_by_plan(db, plan_ids)

    return {"plans": [_row_to_plan(r, features_by_plan.get(str(r["plan_id"]), [])) for r in plan_rows]}


@router.post(
    "/admin/subscription-plans",
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireSuperAdmin],
)
async def create_subscription_plan(
    body: PlanRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    dup = await db.execute(
        text("SELECT plan_id FROM subscription_plans WHERE plan_code = :code"),
        {"code": body.plan_code},
    )
    if dup.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan code already exists")

    result = await db.execute(
        text("""
            INSERT INTO subscription_plans (
                plan_code, plan_name, description, monthly_price, yearly_price, currency,
                trial_days, max_campaigns, max_processed_cvs_per_month, max_users,
                display_order, status, updated_by
            ) VALUES (
                :plan_code, :plan_name, :description, :monthly_price, :yearly_price, :currency,
                :trial_days, :max_campaigns, :max_cvs, :max_users,
                :display_order, 'active', CAST(:uid AS uuid)
            ) RETURNING plan_id
        """),
        _plan_params(body, current_user.user_id),
    )
    plan_id = str(result.scalar_one())
    await db.commit()

    return {"success": True, "plan_id": plan_id}


@router.put("/admin/subscription-plans/{plan_id}", dependencies=[RequireSuperAdmin])
async def update_subscription_plan(
    plan_id: str,
    body: PlanRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    dup = await db.execute(
        text("""
            SELECT plan_id FROM subscription_plans
            WHERE plan_code = :code AND plan_id != CAST(:pid AS uuid)
        """),
        {"code": body.plan_code, "pid": plan_id},
    )
    if dup.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan code already used by another plan")

    params = _plan_params(body, current_user.user_id)
    params["pid"] = plan_id

    result = await db.execute(
        text("""
            UPDATE subscription_plans SET
                plan_code = :plan_code, plan_name = :plan_name, description = :description,
                monthly_price = :monthly_price, yearly_price = :yearly_price, currency = :currency,
                trial_days = :trial_days, max_campaigns = :max_campaigns,
                max_processed_cvs_per_month = :max_cvs, max_users = :max_users,
                display_order = :display_order, updated_by = CAST(:uid AS uuid)
            WHERE plan_id = CAST(:pid AS uuid)
            RETURNING plan_id
        """),
        params,
    )
    if not result.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    await db.commit()
    return {"success": True}


@router.patch(
    "/admin/subscription-plans/{plan_id}/features/{feature_key}",
    dependencies=[RequireSuperAdmin],
)
async def patch_plan_feature(
    plan_id: str,
    feature_key: str,
    body: FeaturePatchRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update a single feature value for a plan."""
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    row = await db.execute(
        text("""
            SELECT feature_id, value_type
            FROM plan_features
            WHERE plan_id = CAST(:pid AS uuid) AND feature_key = :fkey
        """),
        {"pid": plan_id, "fkey": feature_key},
    )
    feat = row.mappings().first()
    if not feat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature not found for this plan")

    vtype = feat["value_type"]
    if vtype == "boolean" and body.value_boolean is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="value_boolean required for boolean feature")
    if vtype == "number" and body.value_number is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="value_number required for number feature")
    if vtype == "text" and body.value_text is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="value_text required for text feature")

    await db.execute(
        text("""
            UPDATE plan_features
            SET value_boolean = :vb,
                value_number  = :vn,
                value_text    = :vt
            WHERE plan_id = CAST(:pid AS uuid) AND feature_key = :fkey
        """),
        {
            "pid":  plan_id,
            "fkey": feature_key,
            "vb":   body.value_boolean,
            "vn":   body.value_number,
            "vt":   body.value_text,
        },
    )
    await db.commit()
    return {"success": True}


@router.delete("/admin/subscription-plans/{plan_id}", dependencies=[RequireSuperAdmin])
async def deactivate_or_delete_subscription_plan(
    plan_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    plan_row = await db.execute(
        text("SELECT plan_code FROM subscription_plans WHERE plan_id = CAST(:pid AS uuid)"),
        {"pid": plan_id},
    )
    plan = plan_row.mappings().first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    assigned = await db.execute(
        text("SELECT COUNT(*) FROM tenants WHERE plan = :code AND status = 'active'"),
        {"code": plan["plan_code"]},
    )
    active_tenant_count = assigned.scalar_one()

    if active_tenant_count > 0:
        await db.execute(
            text("""
                UPDATE subscription_plans
                SET status = 'inactive', updated_by = CAST(:uid AS uuid)
                WHERE plan_id = CAST(:pid AS uuid)
            """),
            {"uid": current_user.user_id, "pid": plan_id},
        )
        await db.commit()
        return {
            "success": True,
            "action": "deactivated",
            "message": f"Plan has {active_tenant_count} active tenant(s) — deactivated instead of deleted",
        }

    await db.execute(
        text("DELETE FROM subscription_plans WHERE plan_id = CAST(:pid AS uuid)"),
        {"pid": plan_id},
    )
    await db.commit()
    return {"success": True, "action": "deleted"}


# ── Tenant-facing: view available plans & subscribe ────────────────────────────

@router.get("/subscription/plans")
async def list_public_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return active plans with features — no auth required (used for plan comparison page)."""
    await set_rls_context(db, "", "super_admin")

    rows = await db.execute(text("""
        SELECT plan_id, plan_code, plan_name, description,
               monthly_price, yearly_price, currency, trial_days,
               max_campaigns, max_processed_cvs_per_month, max_users,
               display_order
        FROM subscription_plans
        WHERE status = 'active'
        ORDER BY display_order, created_at
    """))
    plan_rows = list(rows.mappings())
    plan_ids = [str(r["plan_id"]) for r in plan_rows]
    features_by_plan = await _load_features_by_plan(db, plan_ids)

    return {
        "plans": [
            {
                "plan_id": str(r["plan_id"]),
                "plan_code": r["plan_code"],
                "plan_name": r["plan_name"],
                "description": r["description"],
                "monthly_price": float(r["monthly_price"]),
                "yearly_price": float(r["yearly_price"]),
                "currency": r["currency"],
                "trial_days": r["trial_days"],
                "max_campaigns": r["max_campaigns"],
                "max_processed_cvs_per_month": r["max_processed_cvs_per_month"],
                "max_users": r["max_users"],
                "display_order": r["display_order"],
                "features": features_by_plan.get(str(r["plan_id"]), []),
            }
            for r in plan_rows
        ]
    }


@router.post("/subscription/subscribe")
async def subscribe_to_plan(
    body: SubscribeRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Allow a tenant admin to select / change their subscription plan."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only tenant admins can change subscription plans")

    plan_code: str = body.plan_code

    await set_rls_context(db, current_user.tenant_id, "super_admin")

    plan_row = await db.execute(
        text("SELECT plan_id, plan_code FROM subscription_plans WHERE plan_code = :code AND status = 'active'"),
        {"code": plan_code},
    )
    plan = plan_row.mappings().first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found or inactive")

    await db.execute(
        text("""
            UPDATE tenants
            SET plan = :plan_code,
                subscription_status = 'active',
                subscription_started_at = NOW()
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"plan_code": plan_code, "tid": current_user.tenant_id},
    )
    await db.commit()
    return {"success": True, "plan_code": plan_code}


def _plan_params(body: PlanRequest, user_id: str) -> dict[str, Any]:
    return {
        "plan_code": body.plan_code,
        "plan_name": body.plan_name,
        "description": body.description,
        "monthly_price": body.monthly_price,
        "yearly_price": body.yearly_price,
        "currency": body.currency,
        "trial_days": body.trial_days,
        "max_campaigns": body.max_campaigns,
        "max_cvs": body.max_processed_cvs_per_month,
        "max_users": body.max_users,
        "display_order": body.display_order,
        "uid": user_id,
    }
