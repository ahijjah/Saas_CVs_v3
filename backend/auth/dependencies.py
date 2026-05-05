from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import decode_access_token
from database import get_db, set_rls_context

bearer_scheme = HTTPBearer(auto_error=True)


class CurrentUser:
    def __init__(self, user_id: str, tenant_id: str, email: str, role: str, full_name: str):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.email = email
        self.role = role
        self.full_name = full_name


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    payload = decode_access_token(credentials.credentials)

    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role")

    if not user_id or not tenant_id or not role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")

    # Set RLS context before querying
    await set_rls_context(db, tenant_id, role)

    row = await db.execute(
        text("SELECT user_id, tenant_id, email, full_name, role, status FROM users WHERE user_id = :uid"),
        {"uid": user_id},
    )
    user = row.mappings().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    return CurrentUser(
        user_id=str(user["user_id"]),
        tenant_id=str(user["tenant_id"]),
        email=user["email"],
        role=user["role"],
        full_name=user["full_name"],
    )


def require_role(*roles: str):
    async def checker(current_user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    return checker


RequireAdmin = Depends(require_role("super_admin", "admin"))
RequireSuperAdmin = Depends(require_role("super_admin"))
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
