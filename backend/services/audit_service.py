"""
Generic audit service — writes structured audit log entries.

Usage:
    await log_action(db, tenant_id, user, "create_job", "job", job_id, {"title": "..."})

All entries are written with fire-and-forget semantics — if the insert fails
(e.g. a transient DB error) it logs a warning and never raises, so auditing
never blocks the primary operation.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def log_action(
    db: AsyncSession,
    tenant_id: str | None,
    user_id: str | None,
    user_email: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Insert one audit log row. Never raises on failure."""
    try:
        await db.execute(
            text("""
                INSERT INTO audit_logs
                    (tenant_id, user_id, user_email, action,
                     resource_type, resource_id, details, ip_address)
                VALUES
                    (:tenant_id, :user_id, :user_email, :action,
                     :resource_type, :resource_id, :details, :ip_address)
            """),
            {
                "tenant_id":     tenant_id or None,
                "user_id":       user_id or None,
                "user_email":    user_email,
                "action":        action,
                "resource_type": resource_type,
                "resource_id":   str(resource_id) if resource_id else None,
                "details":       json.dumps(details) if details else None,
                "ip_address":    ip_address,
            },
        )
    except Exception as exc:
        logger.warning("audit_service: failed to log action '%s': %s", action, exc)
