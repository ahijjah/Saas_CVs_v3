from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings

settings = get_settings()

# One pool per runtime login (docs/DATABASE_ROLES.md). A request never reaches the administration or worker login by choosing it
# from request content: the API pool is always cv_app. The administration pool is returned only after the caller's token is verified
# (auth.dependencies.get_platform_db) or for unauthenticated platform paths that are coded to use it (get_admin_db).


def _create_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=settings.debug,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": settings.db_schema}},
    )


engine = _create_engine(settings.database_url)
admin_engine = _create_engine(settings.admin_database_url or settings.database_url)
worker_engine = _create_engine(settings.worker_database_url or settings.database_url)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
AdminSessionLocal = async_sessionmaker(bind=admin_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
WorkerSessionLocal = async_sessionmaker(bind=worker_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Tenant request session. Always the cv_app login."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_admin_db() -> AsyncGenerator[AsyncSession, None]:
    """Unauthenticated platform paths that must read across tenants (login, invitation acceptance). Always the cv_admin login."""
    async with AdminSessionLocal() as session:
        yield session


async def assert_runtime_role(session: AsyncSession) -> None:
    """Refuse to serve with a superuser or an RLS-bypassing login. Checked once at startup for each pool."""
    if not settings.enforce_runtime_role_checks:
        return
    row = (await session.execute(text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).mappings().first()
    if row is None or row["rolsuper"] or row["rolbypassrls"]:
        raise RuntimeError(
            "database login is a superuser or bypasses row-level security; the runtime requires a non-superuser login "
            "without BYPASSRLS (docs/DATABASE_ROLES.md)")


async def set_rls_context(session: AsyncSession, tenant_id: str, role: str) -> None:
    """Set PostgreSQL session variables for tenant scoping. Policies scope tenant rows by the tenant variable only. The role variable
    is informational: no policy grants access from it, so setting it cannot widen what a session sees."""
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_role', :role, true)"),
        {"tid": tenant_id, "role": role},
    )


@asynccontextmanager
async def get_db_with_rls(tenant_id: str, role: str) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager: yields a tenant session with tenant scoping already set."""
    async with AsyncSessionLocal() as session:
        await set_rls_context(session, tenant_id, role)
        yield session
