from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import decode_access_token
from database import get_db, set_rls_context

bearer_scheme = HTTPBearer(auto_error=True)

# The non-privileged role of the identity lookup. No row-level-security policy names it, so it reads only what the tenant policy allows.
IDENTITY_LOOKUP_ROLE = "authenticate"


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
    """Resolve the bearer token to the current user record, then establish the authoritative database context.

    1. The identity lookup names the user AND the tenant the token was issued for. It does not rely on row-level security: the application
       connection may bypass it (cv_app is a superuser). It runs under the claimed tenant with the non-privileged role "authenticate", which
       has no super_admin branch in any policy, so the policies bound it too under a restricted role.
    2. The record must match the token: the same tenant (guaranteed by the query) and the same role. A token whose role or tenant no longer
       matches the record (demotion, reassignment) is refused with 401.
    3. A disabled account is refused with 403, as before.
    4. Only then is the context set from the record itself, before any business query in the request.

    Null identities: every users row has a non-null tenant (schema), so a token without a tenant claim is refused for every role. A platform
    administrator is a super_admin record in its own tenant; it is accepted only when the record and the token agree.
    """
    payload = decode_access_token(credentials.credentials)

    user_id = payload.get("sub")
    claim_tenant = payload.get("tenant_id")
    claim_role = payload.get("role")

    if not user_id or not claim_tenant or not claim_role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    try:
        UUID(str(user_id))
        UUID(str(claim_tenant))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")

    # Step 1: the explicit identity lookup, under the claimed tenant and the non-privileged context (no super_admin branch).
    await set_rls_context(db, str(claim_tenant), IDENTITY_LOOKUP_ROLE)
    row = await db.execute(
        text("""
            SELECT user_id, tenant_id, email, full_name, role, status
            FROM users
            WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"uid": str(user_id), "tid": str(claim_tenant)},
    )
    user = row.mappings().first()

    # Step 2: the token must match the record.
    if user is None or user["role"] != claim_role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token does not match the account")
    # Step 3: a disabled account is refused.
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    # Step 4: the authoritative context comes from the record, before any business query.
    await set_rls_context(db, str(user["tenant_id"]), user["role"])

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


# Roles that may change tenant data. An explicit allowlist: viewer is read-only, and any role added later is refused until it is listed here.
WRITE_ROLES = ("super_admin", "admin", "hr_manager", "recruiter")
RequireWriteRole = Depends(require_role(*WRITE_ROLES))

RequireAdmin = Depends(require_role("super_admin", "admin"))
RequireSuperAdmin = Depends(require_role("super_admin"))
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
