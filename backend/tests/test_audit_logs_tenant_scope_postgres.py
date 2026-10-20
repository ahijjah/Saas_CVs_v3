"""GET /admin/audit-logs on a real PostgreSQL 16 (the disposable migration chain), reading through the administration login.

Two tenants each have audit rows. A tenant admin sees only their own tenant's rows and the total counts only those; naming the other
tenant is refused; a super admin sees both."""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUser, get_current_user
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import audit_logs as A
from runtime_engines import async_engine

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest_asyncio.fixture()
async def engine(built):
    engine = async_engine(built, "cv_admin")
    yield engine
    await engine.dispose()


@pytest.fixture()
def tenants(built):
    cur = built.conn.cursor()
    out = {}
    for label, rows in (("a", 3), ("b", 2)):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                    (f"tenant {label} {tag}", f"{label}-{tag}.example"))
        tenant = str(cur.fetchone()[0])
        for n in range(rows):
            cur.execute("INSERT INTO audit_logs (tenant_id, user_email, action, resource_type) VALUES (%s, %s, %s, 'job')",
                        (tenant, f"{label}@example.test", f"scope.test.{tag}.{n}"))
        out[label] = {"tenant": tenant, "tag": tag, "rows": rows}
    return out


def _client(engine, tenant, role):
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(str(uuid.uuid4()), tenant, "u@example.test", role, "U")

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[A.get_audit_db] = session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_a_tenant_admin_sees_only_their_own_tenants_records(engine, tenants):
    a, b = tenants["a"], tenants["b"]
    async with _client(engine, a["tenant"], "admin") as client:
        response = await client.get("/admin/audit-logs", params={"action": f"scope.test.{a['tag']}"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == a["rows"] and len(body["logs"]) == a["rows"]
        assert {log["tenant_id"] for log in body["logs"]} == {a["tenant"]}
        # the other tenant's rows are not reachable by any filter
        other = await client.get("/admin/audit-logs", params={"action": f"scope.test.{b['tag']}"})
        assert other.status_code == 200 and other.json()["total"] == 0 and other.json()["logs"] == []


async def test_a_tenant_admin_cannot_name_another_tenant(engine, tenants):
    a, b = tenants["a"], tenants["b"]
    async with _client(engine, a["tenant"], "admin") as client:
        response = await client.get("/admin/audit-logs", params={"tenant_id": b["tenant"]})
    assert response.status_code == 403


async def test_a_tenant_admin_without_any_filter_still_sees_no_foreign_rows(engine, tenants):
    a = tenants["a"]
    async with _client(engine, a["tenant"], "admin") as client:
        response = await client.get("/admin/audit-logs")
    assert response.status_code == 200
    assert {log["tenant_id"] for log in response.json()["logs"]} <= {a["tenant"]}


async def test_a_super_admin_still_sees_both_tenants(engine, tenants):
    a, b = tenants["a"], tenants["b"]
    async with _client(engine, a["tenant"], "super_admin") as client:
        for tenant in (a, b):
            response = await client.get("/admin/audit-logs", params={"tenant_id": tenant["tenant"]})
            assert response.status_code == 200 and response.json()["total"] == tenant["rows"]
