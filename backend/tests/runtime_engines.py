"""Canonical connections for tests, one per runtime role.

Every connection names the role the production path uses and resolves the application schema through an explicit search path, as
the engines do (database.py, runtime_config.py). The role also carries the same default, set by db/ops/0004, so a new pooled
connection or a transaction rollback never loses it. Tests must not create roles or grants of their own: they take the login
passwords that realdb_helper provisioned for the built database.
"""
from __future__ import annotations

import psycopg2
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

SEARCH_PATH = "cv_analyzer,public"
ASYNC_ROLES = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")


def _password(built, role: str) -> str:
    try:
        return built.runtime_passwords[role]
    except KeyError as exc:
        raise RuntimeError(f"{role} has no provisioned login on this test database") from exc


def async_url(built, role: str) -> str:
    """The asyncpg URL of role on this database, for settings that take a URL."""
    return f"postgresql+asyncpg://{role}:{_password(built, role)}@127.0.0.1:{built.cluster.port}/{built.name}"


def async_engine(built, role: str):
    """A new asyncpg engine for role. NullPool: every checkout is a new connection, so the role default is what is tested."""
    return create_async_engine(async_url(built, role), poolclass=NullPool,
                               connect_args={"server_settings": {"search_path": SEARCH_PATH}})


def sync_connection(built, role: str):
    """A psycopg2 connection for role, with the same search path passed as a connection option."""
    return psycopg2.connect(host="127.0.0.1", port=built.cluster.port, user=role, password=_password(built, role),
                            dbname=built.name, options=f"-c search_path={SEARCH_PATH}")
