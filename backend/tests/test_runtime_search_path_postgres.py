"""Every runtime login resolves the application schema without qualification, after a rollback and on every new connection.

The schema is resolved by the role default (db/ops/0004) and by the explicit search path every engine passes. Neither depends on a
SET in an earlier session. A missing path shows up as "relation does not exist" (42P01); a path that resolves but is not granted shows
up as "permission denied" (42501). The checks below tell those apart, so a missing path cannot pass as a missing grant.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from runtime_engines import async_engine, sync_connection


@pytest.fixture(scope="module")
def built():
    import realdb_helper as H
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


ASYNC_ROLES_WITH_TABLE_ACCESS = ("cv_app", "cv_worker", "cv_admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ASYNC_ROLES_WITH_TABLE_ACCESS)
async def test_the_role_resolves_jobs_and_job_criteria_unqualified_after_a_rollback(built, role):
    engine = async_engine(built, role)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM no_such_relation_for_rollback"))  # a failed statement ends the transaction
    except Exception:
        pass
    try:
        async with engine.connect() as conn:
            await conn.rollback()
            assert (await conn.execute(text("SELECT count(*) FROM jobs"))).scalar_one() >= 0
            assert (await conn.execute(text("SELECT count(*) FROM job_criteria"))).scalar_one() >= 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ASYNC_ROLES_WITH_TABLE_ACCESS)
async def test_the_role_resolves_the_tables_on_every_new_pooled_connection(built, role):
    engine = async_engine(built, role)   # NullPool: each checkout below is a new physical connection
    try:
        for _ in range(3):
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT count(*) FROM jobs"))).scalar_one() >= 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_secret_reader_resolves_its_getter_unqualified_and_is_refused_only_by_the_allowlist(built):
    engine = async_engine(built, "cv_secrets")
    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SELECT get_platform_secret('SMTP_HOST')"))).scalar_one_or_none()
            assert value is None or isinstance(value, str)
            await conn.rollback()
            with pytest.raises(Exception) as err:
                await conn.execute(text("SELECT get_platform_secret('DB_PASSWORD')"))
            assert "not readable through the accessor" in str(err.value)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_secret_reader_sees_the_schema_but_holds_no_table_privilege(built):
    """cv_secrets resolves jobs (the schema path works) and is then refused by the missing grant, which is permission denied,
    not relation does not exist."""
    engine = async_engine(built, "cv_secrets")
    try:
        async with engine.connect() as conn:
            with pytest.raises(Exception) as err:
                await conn.execute(text("SELECT count(*) FROM jobs"))
            message = str(err.value).lower()
            assert "permission denied" in message
            assert "does not exist" not in message
    finally:
        await engine.dispose()


def test_a_synchronous_connection_resolves_the_schema_after_rollback_for_every_login(built):
    for role in ("cv_app", "cv_worker", "cv_admin"):
        conn = sync_connection(built, role)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM no_such_relation_for_rollback")
        except Exception:
            conn.rollback()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM jobs")
        assert cur.fetchone()[0] >= 0
        conn.rollback()
        cur.execute("SELECT count(*) FROM job_criteria")
        assert cur.fetchone()[0] >= 0
        conn.close()


@pytest.mark.asyncio
async def test_the_operator_resolves_the_schema_and_can_only_become_the_migrator(built):
    """cv_migration_operator is a login with no table privileges of its own. It may SET ROLE to cv_migrator and to nothing else."""
    import psycopg2
    operator_password = built.runtime_passwords["cv_migration_operator"]
    conn = psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user="cv_migration_operator", password=operator_password,
                            dbname=built.name, options="-c search_path=cv_analyzer,public")
    try:
        cur = conn.cursor()
        cur.execute("SET ROLE cv_migrator")
        cur.execute("SELECT current_user")
        assert cur.fetchone()[0] == "cv_migrator"
        conn.rollback()
        cur.execute("SET ROLE cv_app")
    except psycopg2.Error as exc:
        assert exc.pgcode == "42501", exc
    finally:
        conn.close()
