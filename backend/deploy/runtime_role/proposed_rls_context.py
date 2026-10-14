"""PROPOSAL (not wired into the application): re-apply the row-level-security context at the start of EVERY transaction.

Today `set_rls_context` runs `set_config(..., true)` once, inside whatever transaction is open. That setting ends with the transaction. A commit
inside a request therefore leaves the rest of the request with NO tenant (fail closed: no rows, and writes are refused), and a pooled connection
is handed back with no context. The superuser / BYPASSRLS application role hides this today. A restricted role makes it visible.

The fix stores the context on the session and re-applies it in `after_begin`, which SQLAlchemy runs whenever a new transaction starts on that
session, including after a commit. Missing context stays fail-closed: nothing is set, so tenant tables return no rows.

Usage (when adopted):  database.py calls `install_rls_context()` once; request code calls `set_request_context(session, tenant, role)` where it
calls `set_rls_context` today. Nothing else changes.
"""
from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

_SET = text("SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_role', :role, true)")


def _reapply(session, transaction, connection):                 # noqa: ARG001 - SQLAlchemy listener signature
    ctx = session.info.get("rls")
    if ctx is not None:
        connection.execute(_SET, {"tid": ctx[0], "role": ctx[1]})


_installed = False


def install_rls_context() -> None:
    """Register the listener once for every Session (sync and the sync side of AsyncSession)."""
    global _installed
    if not _installed:
        event.listen(Session, "after_begin", _reapply)
        _installed = True


async def set_request_context(session, tenant_id: str, role: str) -> None:
    """Record the context on the session (it survives commits) and apply it to the current transaction now."""
    session.info["rls"] = (tenant_id, role)
    await session.execute(_SET, {"tid": tenant_id, "role": role})
