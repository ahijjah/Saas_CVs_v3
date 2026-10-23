"""The tenant-role authorization matrix, checked on the mounted application for every affected route.

Each route is called as each of the five roles with the real routers and dependency guards. Only the authenticated user and the
database session are replaced, and the session is an empty fake that counts statements. So:

  * a role that may not use a route gets 403 and the handler never reaches the database (the check runs before the body is validated
    and before any resource is looked up);
  * a role that may use it passes the guard: the answer is whatever the handler says about a missing resource or a bad body (404 /
    422 / 200), never 403.

A coverage test fails when a new write route is mounted without a decision here.
"""
from __future__ import annotations

import re

import httpx
import pytest
from fastapi.routing import APIRoute

import main
from auth.dependencies import CurrentUser, get_current_user
from database import get_db

pytestmark = pytest.mark.asyncio

ROLES = ("super_admin", "admin", "hr_manager", "recruiter", "viewer")
TENANT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
OBJ = "33333333-3333-4333-8333-333333333333"


class _Rows:
    def first(self): return None
    def one_or_none(self): return None
    def all(self): return []
    def __iter__(self): return iter([])
    def fetchall(self): return []
    def fetchone(self): return None
    def scalars(self): return self
    def keys(self): return []


class _Result(_Rows):
    rowcount = 0
    def mappings(self): return _Rows()
    def scalar_one_or_none(self): return None
    def scalar(self): return None
    def scalar_one(self): raise LookupError("no row")


class CountingDB:
    """An empty database that records every statement."""
    def __init__(self): self.statements = []
    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))
        return _Result()
    async def commit(self): pass
    async def rollback(self): pass
    async def flush(self): pass
    async def close(self): pass
    async def refresh(self, *args, **kwargs): pass
    def add(self, *args, **kwargs): pass


# The approved matrix, written out here on purpose: it must not be derived from auth/roles.py, or it could not catch a change there.
MANAGER_ROLES = frozenset({"super_admin", "admin", "hr_manager"})
REQUIREMENTS_EDITOR_ROLES = frozenset({"admin", "hr_manager"})
REQUIREMENTS_READER_ROLES = frozenset({"admin", "hr_manager", "recruiter", "viewer"})
EXPORT_ROLES = frozenset({"super_admin", "admin", "hr_manager", "recruiter"})
ADMIN_OR_PLATFORM_ROLES = frozenset({"super_admin", "admin"})
TENANT_ADMIN_ROLES = frozenset({"admin"})
USAGE_ROLES = frozenset({"admin", "hr_manager"})

# (method, path) -> the roles allowed through the guard. Everything else must be 403 with no database access.
MANAGERS = MANAGER_ROLES
MATRIX: dict[tuple[str, str], frozenset[str]] = {
    # jobs
    ("POST", "/jobs"): MANAGERS,
    ("PUT", "/jobs/{id}/ingestion"): MANAGERS,
    ("PUT", "/jobs/{id}/settings"): MANAGERS,
    ("PUT", "/jobs/{id}"): REQUIREMENTS_EDITOR_ROLES,
    ("PUT", "/jobs/{id}/criteria"): REQUIREMENTS_EDITOR_ROLES,
    ("PUT", "/jobs/{id}/criteria/content"): REQUIREMENTS_EDITOR_ROLES,
    ("POST", "/jobs/{id}/criteria/retry"): REQUIREMENTS_EDITOR_ROLES,
    # requirements
    ("GET", "/jobs/{id}/requirements"): REQUIREMENTS_READER_ROLES,
    ("POST", "/jobs/{id}/requirements/edits"): REQUIREMENTS_EDITOR_ROLES,
    # gates / knockout criteria: every tenant role reads the Gate Summary; only an admin or an HR manager records, overrides or re-runs
    ("GET", "/applications/{id}/gates"): REQUIREMENTS_READER_ROLES,
    ("POST", "/applications/{id}/gates/evaluate"): REQUIREMENTS_EDITOR_ROLES,
    ("POST", "/applications/{id}/gates/{id}/override"): REQUIREMENTS_EDITOR_ROLES,
    ("DELETE", "/applications/{id}/gates/{id}/override"): REQUIREMENTS_EDITOR_ROLES,
    ("POST", "/applications/{id}/gates/{id}/result"): REQUIREMENTS_EDITOR_ROLES,
    # item-level scoring: every tenant role reads the breakdown; only an admin or an HR manager resolves an unclear item or rescores
    ("GET", "/applications/{id}/item-scoring"): REQUIREMENTS_READER_ROLES,
    ("POST", "/applications/{id}/item-scoring/items/{id}/resolve"): REQUIREMENTS_EDITOR_ROLES,
    ("POST", "/applications/{id}/item-scoring/rescore"): REQUIREMENTS_EDITOR_ROLES,
    # campaigns
    ("POST", "/campaigns"): MANAGERS,
    ("PATCH", "/campaigns/{id}"): MANAGERS,
    ("POST", "/campaigns/{id}/status"): MANAGERS,
    ("DELETE", "/campaigns/{id}"): ADMIN_OR_PLATFORM_ROLES,
    # applications
    ("GET", "/applications/export"): EXPORT_ROLES,
    ("POST", "/applications/upload"): MANAGERS,
    ("POST", "/applications/score-pending"): MANAGERS,
    ("POST", "/applications/reset-stuck"): MANAGERS,
    ("PATCH", "/applications/{id}/workflow-status"): MANAGERS,
    ("PATCH", "/applications/bulk-workflow-status"): MANAGERS,
    ("PATCH", "/applications/{id}/assignment"): MANAGERS,
    ("PATCH", "/applications/bulk-assignment"): MANAGERS,
    ("PATCH", "/applications/{id}/recruiter-notes"): MANAGERS,
    ("PATCH", "/applications/{id}/knockout-answers"): MANAGERS,
    ("POST", "/applications/{id}/knockout-analysis"): MANAGERS,
    ("POST", "/applications/{id}/screening-validation"): MANAGERS,
    ("PATCH", "/applications/{id}/knockout-suggestions/accept"): MANAGERS,
    ("PATCH", "/applications/{id}/knockout-suggestions/ignore"): MANAGERS,
    ("DELETE", "/applications/{id}"): MANAGERS,
    ("POST", "/applications/{id}/tags"): MANAGERS,
    ("DELETE", "/applications/{id}/tags/{id}"): MANAGERS,
    ("POST", "/applications/{id}/talent-pool"): MANAGERS,
    ("POST", "/applications/{id}/reuse-to-job"): REQUIREMENTS_EDITOR_ROLES,
    ("POST", "/tags/"): MANAGERS,
    ("DELETE", "/tags/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("POST", "/applications/{id}/comments"): MANAGERS,
    ("PATCH", "/applications/{id}/comments/{id}"): MANAGERS,
    ("DELETE", "/applications/{id}/comments/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("POST", "/applications/{id}/interviews"): MANAGERS,
    ("PATCH", "/applications/{id}/interviews/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("DELETE", "/applications/{id}/interviews/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("POST", "/applications/{id}/interviews/{id}/feedback"): MANAGERS,
    ("PATCH", "/applications/{id}/interviews/{id}/feedback/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("POST", "/applications/{id}/approvals/initiate"): MANAGERS,
    ("POST", "/applications/{id}/approvals"): ADMIN_OR_PLATFORM_ROLES,
    ("PATCH", "/applications/{id}/approvals/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("DELETE", "/applications/{id}/approvals/{id}"): ADMIN_OR_PLATFORM_ROLES,
    ("POST", "/applications/{id}/communications/log"): MANAGERS,
    ("POST", "/applications/{id}/communications/send"): MANAGERS,
    ("PATCH", "/applications/{id}/preferred-contact"): MANAGERS,
    ("PATCH", "/applications/{id}/communications/{id}"): MANAGERS,
    ("POST", "/applications/{id}/communications/{id}/send"): MANAGERS,
    ("DELETE", "/applications/{id}/communications/{id}"): MANAGERS,
    # tenant account management: tenant admin only
    ("GET", "/tenant/users"): TENANT_ADMIN_ROLES,
    ("POST", "/tenant/users"): TENANT_ADMIN_ROLES,
    ("PATCH", "/tenant/users/{id}/status"): TENANT_ADMIN_ROLES,
    ("PATCH", "/tenant/users/{id}"): TENANT_ADMIN_ROLES,
    ("POST", "/tenant/subscription/activate-trial"): TENANT_ADMIN_ROLES,
    ("POST", "/tenant/subscription/select-plan"): TENANT_ADMIN_ROLES,
    ("POST", "/tenant/subscription/simulate-payment"): TENANT_ADMIN_ROLES,
    ("POST", "/subscription/subscribe"): TENANT_ADMIN_ROLES,
    ("GET", "/tenant/usage"): USAGE_ROLES,
}

# Write routes that this matrix deliberately does not cover, with the reason. A new write route must be added to MATRIX or here.
EXEMPT_PREFIXES = {
    "/auth/": "authentication and the caller's own profile",
    "/applications/public": "unauthenticated public application form",
    "/admin/": "platform administration: super_admin guard, covered by the administration-session tests",
    "/client-organizations": "agency client organisations: their own admin / write helpers",
    "/candidate-saved-views": "a user's personal saved filters",
    "/communication/templates": "admin-only configuration, guarded in the handler",
    "/communication/automation-rules": "admin-only configuration, guarded in the handler",
    "/tenant/workflow-policies": "admin-only configuration, guarded in the handler",
}


def _canonical(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{id}", path)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", OBJ, path)


@pytest.fixture()
def call():
    state = {"role": None}
    db = CountingDB()
    main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(USER, TENANT, "u@example.test", state["role"], "U")

    async def fake_db():
        yield db

    main.app.dependency_overrides[get_db] = fake_db
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)

    async def run(role, method, path):
        state["role"] = role
        db.statements.clear()
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.request(method, _concrete(path), json={} if method in ("POST", "PUT", "PATCH") else None,
                                            headers={"Authorization": "Bearer x"})
        return response.status_code, list(db.statements)

    yield run
    main.app.dependency_overrides.pop(get_current_user, None)
    main.app.dependency_overrides.pop(get_db, None)


CASES = [(method, path, role) for (method, path) in MATRIX for role in ROLES]


@pytest.mark.parametrize("method,path,role", CASES, ids=[f"{m} {p} as {r}" for m, p, r in CASES])
async def test_each_role_gets_exactly_the_access_the_matrix_gives_it(call, method, path, role):
    status, statements = await call(role, method, path)
    if role in MATRIX[(method, path)]:
        assert status != 403, f"{role} must pass the guard on {method} {path}"
    else:
        assert status == 403, f"{role} must be refused on {method} {path}, got {status}"
        # The refusal came from the dependency guard: no handler code ran, so nothing was looked up.
        assert statements == [], f"{role} reached the database on {method} {path}: {statements}"


@pytest.mark.parametrize("role", ["admin", "hr_manager", "recruiter", "viewer"])
async def test_an_allowed_role_sees_a_missing_job_as_404_not_403(call, role):
    status, _ = await call(role, "GET", "/jobs/{id}/requirements")
    assert status == 404


async def test_the_matrix_names_every_mounted_route_it_claims_to_cover():
    mounted = {(m, _canonical(r.path)) for r in main.app.routes if isinstance(r, APIRoute) for m in r.methods}
    missing = [key for key in MATRIX if key not in mounted]
    assert not missing, f"the matrix lists routes that are not mounted: {missing}"


async def test_every_write_route_has_a_decision():
    undecided = []
    for route in main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            path = _canonical(route.path)
            if (method, path) in MATRIX or any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
                continue
            undecided.append(f"{method} {route.path}")
    assert not undecided, f"write routes without a role decision (add them to MATRIX or EXEMPT_PREFIXES): {undecided}"


async def test_viewers_and_recruiters_cannot_change_a_requirements_document(call):
    for role in ("recruiter", "viewer"):
        status, statements = await call(role, "POST", "/jobs/{id}/requirements/edits")
        assert status == 403 and statements == []


async def test_hr_manager_and_admin_reach_the_requirements_edit_handler(call):
    for role in ("admin", "hr_manager"):
        status, _ = await call(role, "POST", "/jobs/{id}/requirements/edits")
        assert status == 422            # past the guard, into body validation (an empty body)


async def test_viewer_cannot_export_and_the_other_tenant_roles_can(call):
    assert (await call("viewer", "GET", "/applications/export"))[0] == 403
    for role in ("admin", "hr_manager", "recruiter"):
        assert (await call(role, "GET", "/applications/export"))[0] == 200
