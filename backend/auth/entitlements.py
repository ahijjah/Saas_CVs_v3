"""Request-time enforcement of tenant entitlement for recruitment routers.

Mounted once per recruitment router in main.py (``include_router(..., dependencies=RECRUITMENT_ACCESS)``), so every route of those
routers is covered, including routes added later. Safe methods pass, so suspended and expired tenants keep reading their own data.
Any other method is refused with 403 when the tenant is not entitled, before the route runs and before anything is written.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context
from services.entitlement_service import load_entitlement

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def enforce_recruitment_access(
    request: Request,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    if request.method in SAFE_METHODS:
        return
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    entitlement = await load_entitlement(db, current_user.tenant_id)
    if not entitlement.full:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=entitlement.message,
        )


RECRUITMENT_ACCESS = [Depends(enforce_recruitment_access)]
