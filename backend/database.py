from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


async def set_rls_context(session: AsyncSession, tenant_id: str, role: str) -> None:
    """Set PostgreSQL session variables for Row Level Security."""
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_role', :role, true)"),
        {"tid": tenant_id, "role": role},
    )


@asynccontextmanager
async def get_db_with_rls(tenant_id: str, role: str) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager: yields a session with RLS context already set."""
    async with AsyncSessionLocal() as session:
        await set_rls_context(session, tenant_id, role)
        yield session
