"""
E-02 Phase 3 — Module-level enforcement dependencies.

Two FastAPI dependencies that gate entire API surfaces behind the
AI_RECRUITMENT and ATS_MANAGEMENT module flags.

Design:
  * super_admin always bypasses module checks (admin visibility requirement)
  * Missing DB row → default enabled (backward compatibility guarantee)
  * Returns 403 with a human-readable message (not a server error)
  * Logging of access denials at INFO level for auditability

Usage:
  # At router level (protects all routes in the router):
  router = APIRouter(prefix="/analytics", dependencies=[RequireATSManagement])

  # At individual endpoint level:
  @router.patch("/workflow-status", dependencies=[RequireATSManagement])
  async def update_workflow_status(...): ...
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from constants.features import AI_RECRUITMENT, ATS_MANAGEMENT
from database import get_db
from services.feature_service import is_feature_enabled

logger = logging.getLogger(__name__)

_DbDep = Annotated[AsyncSession, Depends(get_db)]


async def _check_module(
    module_code: str,
    module_label: str,
    current_user,
    db: AsyncSession,
) -> None:
    """Check one module flag. Raises 403 if disabled; logs the denial."""
    # super_admin bypasses all module checks
    if current_user.role == "super_admin":
        return

    enabled = await is_feature_enabled(db, current_user.tenant_id, module_code)
    if not enabled:
        logger.info(
            "module_guard: access denied — %s disabled for tenant %s (user %s)",
            module_code, current_user.tenant_id, current_user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{module_label} is not enabled for this tenant. "
                "Contact your administrator to enable this module."
            ),
        )


async def require_ai_recruitment(
    current_user: CurrentUserDep,
    db: _DbDep,
) -> None:
    """FastAPI dependency — raises 403 if AI_RECRUITMENT module is disabled."""
    await _check_module(AI_RECRUITMENT, "AI Recruitment", current_user, db)


async def require_ats_management(
    current_user: CurrentUserDep,
    db: _DbDep,
) -> None:
    """FastAPI dependency — raises 403 if ATS_MANAGEMENT module is disabled."""
    await _check_module(ATS_MANAGEMENT, "ATS Management", current_user, db)


RequireAIRecruitment = Depends(require_ai_recruitment)
RequireATSManagement = Depends(require_ats_management)
