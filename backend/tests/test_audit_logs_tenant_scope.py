"""GET /admin/audit-logs: a tenant admin reads only their own tenant; a super admin keeps the platform-wide view.

The administration session is replaced by one that records every statement and its parameters, so the tests see exactly which tenant
predicate the route sent. A role that may not read the log never opens the session at all."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from auth.dependencies import CurrentUser, get_current_user
from routers import audit_logs as A

pytestmark = pytest.mark.asyncio

OWN = "11111111-1111-4111-8111-111111111111"
OTHER = "99999999-9999-4999-8999-999999999999"


class _Rows:
    def mappings(self): return []
    def scalar_one(self): return 0


class Recorder:
    def __init__(self): self.calls = []
    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return _Rows()


def _client(role, recorder, opened):
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", OWN, "u@example.test", role, "U")

    async def session():
        opened.append(role)
        yield recorder

    app.dependency_overrides[A.get_audit_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _get(role, **params):
    recorder, opened = Recorder(), []
    async with _client(role, recorder, opened) as client:
        response = await client.get("/admin/audit-logs", params=params)
    return response, recorder, opened


@pytest.mark.parametrize("role", ["hr_manager", "recruiter", "viewer", "owner", ""])
async def test_other_roles_are_refused_and_the_administration_session_is_not_opened(role):
    response, recorder, opened = await _get(role)
    assert response.status_code == 403
    assert recorder.calls == []


async def test_the_dependency_is_refused_before_a_session_is_made(monkeypatch):
    """The real get_audit_db: a role that may not read the log never reaches the pool."""
    made = []
    monkeypatch.setattr(A, "AdminSessionLocal", lambda: made.append(1))
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", OWN, "u@example.test", "recruiter", "U")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/admin/audit-logs")).status_code == 403
    assert made == []


async def test_a_tenant_admin_is_always_scoped_to_their_own_tenant():
    response, recorder, _ = await _get("admin")
    assert response.status_code == 200
    assert recorder.calls, "no query was run"
    for statement, params in recorder.calls:
        assert "al.tenant_id = :filter_tenant_id" in statement
        assert params["filter_tenant_id"] == OWN


async def test_a_tenant_admin_naming_their_own_tenant_is_allowed_and_naming_another_is_refused():
    ok, recorder, _ = await _get("admin", tenant_id=OWN)
    assert ok.status_code == 200 and all(p["filter_tenant_id"] == OWN for _, p in recorder.calls)
    for other in (OTHER, OTHER.upper(), "not-a-tenant", "%"):
        response, rec, _ = await _get("admin", tenant_id=other)
        assert response.status_code == 403
        assert rec.calls == [], "a refused request must not query the log"


async def test_a_tenant_admin_cannot_widen_the_scope_with_other_filters():
    response, recorder, _ = await _get("admin", action="x' OR 1=1 --", user_email="%", resource_type="job")
    assert response.status_code == 200
    for statement, params in recorder.calls:
        assert "al.tenant_id = :filter_tenant_id" in statement and params["filter_tenant_id"] == OWN
        assert "OR 1=1" not in statement                     # the values are bound parameters, never text


async def test_a_super_admin_keeps_the_platform_wide_view_and_the_tenant_filter():
    response, recorder, _ = await _get("super_admin")
    assert response.status_code == 200
    assert all("filter_tenant_id" not in statement for statement, _ in recorder.calls)
    response, recorder, _ = await _get("super_admin", tenant_id=OTHER)
    assert response.status_code == 200
    assert all(params.get("filter_tenant_id") == OTHER for _, params in recorder.calls)
