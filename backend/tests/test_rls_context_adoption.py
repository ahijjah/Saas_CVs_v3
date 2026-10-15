"""The adopted tenant-context helper (database.set_rls_context + the after_begin listener), on a disposable PostgreSQL 16 built from the full chain,
connecting as the restricted runtime role. Covers: post-commit queries, every new transaction of a session, missing context (fail closed for reads
and writes), a pooled connection that must not carry one session's context into the next, changing the tenant mid-session, and the super_admin
context of the background paths. The raw set_config that the code used before is included to show why the helper records the context on the session.
"""
import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import realdb_helper as rh  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent
GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1 = "00000000-0000-0000-0000-0000000000a1"
T2 = "00000000-0000-0000-0000-0000000000a2"
U1 = "00000000-0000-0000-0000-0000000000b1"
U2 = "00000000-0000-0000-0000-0000000000b2"
JOB1 = "00000000-0000-0000-0000-0000000000c1"
JOB2 = "00000000-0000-0000-0000-0000000000c2"
pytestmark = pytest.mark.skipif(rh.available() is not None, reason=rh.available() or "")


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def db(cluster):
    d = cluster.build()
    cur = d.conn.cursor()
    cur.execute(f"INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
                f"('{T1}','T1','t1.example','active','active','organization'),('{T2}','T2','t2.example','active','active','organization')")
    for uid, tid in ((U1, T1), (U2, T2)):
        cur.execute(f"INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES "
                    f"('{uid}','{tid}','{uid[-4:]}@x.example','x','U','admin','active')")
    for jid, tid, uid in ((JOB1, T1, U1), (JOB2, T2, U2)):
        cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'Role','Synthetic')", (jid, tid, uid))
    cur.execute(GRANTS.read_text(encoding="utf-8"))
    yield d
    d.drop()


@pytest.fixture
def sessions(db):
    """Sessions of the real helper module, bound to ONE pooled connection (pool_size=1), as the runtime role."""
    sys.path.insert(0, str(BACKEND))
    import database  # noqa: F401  (installs the after_begin listener, as the application does at import)
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    engine = create_async_engine(f"postgresql+asyncpg://cv_runtime@127.0.0.1:{db.cluster.port}/{db.name}", pool_size=1, max_overflow=0,
                                 connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    S = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield database, S
    asyncio.run(engine.dispose())


def run(coro):
    return asyncio.run(coro)


async def count(s, where=""):
    from sqlalchemy import text
    return (await s.execute(text(f"SELECT count(*) FROM jobs {where}"))).scalar()


def test_the_tenant_context_survives_commits_inside_one_request(db, sessions):
    database, S = sessions

    async def go():
        async with S() as s:
            await database.set_rls_context(s, T1, "admin")
            assert await count(s) == 1
            await s.commit()
            assert await count(s) == 1                                    # after the commit: the same tenant again
            await s.rollback()
            assert await count(s) == 1
    run(go())


def test_the_context_is_restored_in_every_new_transaction_of_the_session(db, sessions):
    database, S = sessions

    async def go():
        async with S() as s:
            await database.set_rls_context(s, T1, "admin")
            for _ in range(3):
                await s.commit()
                assert await count(s) == 1
    run(go())


def test_a_session_that_never_sets_a_context_sees_nothing_and_writes_nothing(db, sessions):
    import sqlalchemy
    database, S = sessions

    async def go():
        async with S() as s:
            assert await count(s) == 0
            with pytest.raises(sqlalchemy.exc.DBAPIError):
                await s.execute(sqlalchemy.text("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES "
                                                "(gen_random_uuid(), :t, :u, 'x', 'y')"), {"t": T1, "u": U1})
            await s.rollback()
    run(go())


def test_a_pooled_connection_does_not_carry_a_context_into_the_next_session(db, sessions):
    database, S = sessions

    async def go():
        async with S() as a:                                              # request 1: tenant 1, committed
            await database.set_rls_context(a, T1, "admin")
            assert await count(a) == 1
            await a.commit()
        async with S() as b:                                              # request 2 on the same single pooled connection: no context
            assert await count(b) == 0
        async with S() as c:                                              # request 3: tenant 2 only
            await database.set_rls_context(c, T2, "admin")
            assert await count(c) == 1
            assert await count(c, f"WHERE tenant_id = '{T1}'") == 0
        async with S() as d:                                              # request 4: after tenant 2, still no context
            assert await count(d) == 0
    run(go())


def test_a_rolled_back_session_leaves_nothing_behind_on_the_pooled_connection(db, sessions):
    database, S = sessions

    async def go():
        async with S() as a:
            await database.set_rls_context(a, T2, "admin")
            await a.rollback()
        async with S() as b:
            assert await count(b) == 0
    run(go())


def test_changing_the_tenant_in_one_session_takes_effect_for_the_next_transaction(db, sessions):
    database, S = sessions

    async def go():
        async with S() as s:
            await database.set_rls_context(s, T1, "admin")
            assert await count(s) == 1
            await s.commit()
            await database.set_rls_context(s, T2, "admin")
            assert await count(s, f"WHERE tenant_id = '{T2}'") == 1
            assert await count(s, f"WHERE tenant_id = '{T1}'") == 0
            await s.commit()
    run(go())


def test_the_super_admin_context_of_the_background_paths_survives_commits(db, sessions):
    """Workers, login and public intake set app.current_role = 'super_admin' through the helper. Under the policies this sees every tenant, and
    it keeps doing so after a commit. (Whether a session may set it at all is the open decision in the privileged-operations proposal.)"""
    database, S = sessions

    async def go():
        async with S() as s:
            await database.set_rls_context(s, "", "super_admin")
            assert await count(s) == 2
            await s.commit()
            assert await count(s) == 2
    run(go())


def test_a_raw_set_config_is_lost_at_commit_which_is_why_the_context_is_recorded(db, sessions):
    """The pattern the code used before the helper: a plain set_config. It is correct only until the first commit."""
    from sqlalchemy import text
    database, S = sessions

    async def go():
        async with S() as s:
            await s.execute(text("SELECT set_config('app.current_tenant_id', :t, true), set_config('app.current_role', 'admin', true)"), {"t": T1})
            assert await count(s) == 1
            await s.commit()
            assert await count(s) == 0
    run(go())
