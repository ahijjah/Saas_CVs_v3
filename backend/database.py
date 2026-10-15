from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={"server_settings": {"search_path": settings.db_schema}},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


_RLS_SQL = text("SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_role', :role, true)")


def _reapply_rls_context(session, transaction, connection):  # noqa: ARG001 - SQLAlchemy listener signature
    """Every new transaction of a session starts with the session's tenant context. set_config(..., true) lasts only to the end of a
    transaction, so without this a commit inside a request would leave the rest of the request with NO tenant, and a pooled connection
    would be handed on with the previous one's context if the session did not set one. The context is recorded on the session, so it
    survives commits; a session that never recorded one stays fail-closed (no tenant)."""
    ctx = session.info.get("rls")
    if ctx is not None:
        connection.execute(_RLS_SQL, {"tid": ctx[0], "role": ctx[1]})


event.listen(Session, "after_begin", _reapply_rls_context)


async def set_rls_context(session: AsyncSession, tenant_id: str, role: str) -> None:
    """Set the tenant context for this session and every transaction it starts from now on (see _reapply_rls_context)."""
    session.info["rls"] = (tenant_id, role)
    await session.execute(_RLS_SQL, {"tid": tenant_id, "role": role})


@asynccontextmanager
async def get_db_with_rls(tenant_id: str, role: str) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager: yields a session with RLS context already set."""
    async with AsyncSessionLocal() as session:
        await set_rls_context(session, tenant_id, role)
        yield session
