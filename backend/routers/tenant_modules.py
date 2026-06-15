"""
E-02 Phase 3 — Tenant module status endpoint for the frontend.

Lightweight endpoint called once after login to determine which product
modules are enabled for the current tenant. Result is cached client-side
in FeatureContext — no repeated calls during a session.

GET /tenant/modules
  Returns: {ai_recruitment: bool, ats_management: bool}

Available to all authenticated users (not super_admin only), since every
tenant user needs to know their own module configuration.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from constants.features import AI_RECRUITMENT, ATS_MANAGEMENT
from database import get_db
from services.feature_service import is_feature_enabled

router = APIRouter(prefix="/tenant", tags=["tenant-modules"])


@router.get("/modules")
async def get_tenant_modules(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Return which product modules are enabled for the current tenant.

    super_admin always receives all modules as enabled (admin bypass).
    Defaults to True on any DB error (fail-open for backward compatibility).
    """
    if current_user.role == "super_admin":
        return {"ai_recruitment": True, "ats_management": True}

    ai_enabled, ats_enabled = await _load_modules(
        db, current_user.tenant_id
    )
    return {
        "ai_recruitment":  ai_enabled,
        "ats_management":  ats_enabled,
    }


async def _load_modules(db: AsyncSession, tenant_id: str) -> tuple[bool, bool]:
    """Fetch both module flags concurrently (sequential, but two fast cache-hits)."""
    ai  = await is_feature_enabled(db, tenant_id, AI_RECRUITMENT)
    ats = await is_feature_enabled(db, tenant_id, ATS_MANAGEMENT)
    return ai, ats
