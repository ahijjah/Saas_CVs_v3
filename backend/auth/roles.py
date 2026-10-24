"""The tenant-role authorization matrix, in one place.

Roles are read from the user's row (see get_current_user), never from the token. Every guard here is a route-level dependency, so a
caller that is not allowed gets 403 BEFORE the request body is validated and BEFORE any resource is looked up. A caller that is allowed
still gets 404 for a missing or cross-tenant resource (row-level security is unchanged).

    admin        everything in the tenant, including users, roles, subscription and the tenant's audit logs
    hr_manager   jobs, campaigns, Requirements, applications and results, export, tenant usage
    recruiter    read jobs / campaigns / applications / results / Requirements, export; no writes
    viewer       read-only; no export
    super_admin  platform administration. Kept where it already had access; never added to tenant-only management routes.

The guards are built by `require_roles`, which keeps the allowed roles in a frozenset. Platform-only routes keep using
`require_role("super_admin")` in auth.dependencies, which is what the administration-session tests look for.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from auth.dependencies import CurrentUser, get_current_user

ADMIN = "admin"
HR_MANAGER = "hr_manager"
RECRUITER = "recruiter"
VIEWER = "viewer"
SUPER_ADMIN = "super_admin"

TENANT_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER, VIEWER})

# Tenant account management: users and roles, subscription. Admin only; a platform super_admin manages tenants from /admin.
TENANT_ADMIN_ROLES = frozenset({ADMIN})
# Creating and changing jobs, campaigns, Requirements, applications and results.
REQUIREMENTS_EDITOR_ROLES = frozenset({ADMIN, HR_MANAGER})
# The same work on routes where super_admin already had access before this matrix.
MANAGER_ROLES = REQUIREMENTS_EDITOR_ROLES | {SUPER_ADMIN}
# Reading Requirements, with category and item weights.
REQUIREMENTS_READER_ROLES = TENANT_ROLES
# Downloading the applications export. A viewer is read-only on screen and may not take the data out.
EXPORT_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER, SUPER_ADMIN})
# Saving one's own Candidate Matrix layout. The shared job and tenant layouts need REQUIREMENTS_EDITOR_ROLES; a viewer is read-only.
MATRIX_PERSONAL_ROLES = frozenset({ADMIN, HR_MANAGER, RECRUITER})
# Destructive or configuration actions that were already admin-only.
ADMIN_OR_PLATFORM_ROLES = frozenset({ADMIN, SUPER_ADMIN})
# Tenant usage figures.
USAGE_ROLES = frozenset({ADMIN, HR_MANAGER})


def require_roles(allowed: frozenset[str]):
    """A dependency that returns the current user when their role is in `allowed` and refuses with 403 otherwise."""
    allowed = frozenset(allowed)

    async def checker(current_user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if current_user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    checker.allowed_roles = allowed          # for the route-matrix tests
    return checker


def role_allowed(role: str | None, allowed: frozenset[str]) -> bool:
    return (role or "") in allowed


RequireTenantAdmin = Depends(require_roles(TENANT_ADMIN_ROLES))
RequireRequirementsEditor = Depends(require_roles(REQUIREMENTS_EDITOR_ROLES))
RequireRequirementsReader = Depends(require_roles(REQUIREMENTS_READER_ROLES))
RequireManager = Depends(require_roles(MANAGER_ROLES))
RequireExport = Depends(require_roles(EXPORT_ROLES))
RequireMatrixPersonal = Depends(require_roles(MATRIX_PERSONAL_ROLES))
RequireAdminOrPlatform = Depends(require_roles(ADMIN_OR_PLATFORM_ROLES))
RequireUsage = Depends(require_roles(USAGE_ROLES))
