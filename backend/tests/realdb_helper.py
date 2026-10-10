"""Disposable PostgreSQL 16 built from the project's REAL db/schema.sql and db/migrations/*.sql (test use only).

A throw-away cluster is started from the system PostgreSQL 16 binaries (as the `postgres` OS user, in a temporary directory) and every test database is
created inside it. Nothing here touches the VPS, production or any shared database. Migrations that already fail on top of schema.sql in this codebase
(pre-existing; listed in `failed`) are skipped with a rollback."""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import uuid

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parent.parent
DB_DIR = BACKEND / "db"
PG_BIN = pathlib.Path(os.environ.get("PG16_BIN", "/usr/lib/postgresql/16/bin"))


def available() -> str | None:
    """None when a real PostgreSQL 16 server can be started here, else the reason."""
    if not (PG_BIN / "initdb").exists():
        return "system PostgreSQL 16 binaries not found (set PG16_BIN)"
    if shutil.which("runuser") is None and os.geteuid() == 0:
        return "runuser not available (needed to run the cluster as the postgres user)"
    return None


def _schema_sql() -> str:
    lines = (DB_DIR / "schema.sql").read_text(encoding="utf-8").split("\n")
    for n in range(0, 96):                       # forward references to users in the first tables (pre-existing)
        lines[n] = re.sub(r"\s+REFERENCES users \(user_id\) ON DELETE SET NULL", "", lines[n])
    return "\n".join(lines)


def migration_files(upto: str | None = None) -> list[pathlib.Path]:
    return [p for p in sorted((DB_DIR / "migrations").glob("*.sql")) if upto is None or p.name <= upto]


class Cluster:
    """One PostgreSQL 16 server for a test session."""

    def __init__(self):
        reason = available()
        if reason:
            raise RuntimeError(reason)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="req_pg_"))
        subprocess.run(["chown", "postgres", str(self.dir)], check=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self._as_pg([str(PG_BIN / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-E", "UTF8"])
        self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-o",
                     f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1", "-l", str(self.dir / "log"), "-w", "start"])

    def _as_pg(self, cmd):
        if os.geteuid() == 0:
            cmd = ["runuser", "-u", "postgres", "--", *cmd]
        subprocess.run(cmd, check=True, capture_output=True)

    def dsn(self, db="postgres") -> str:
        return f"host=127.0.0.1 port={self.port} user=postgres dbname={db}"

    def build(self, *, upto: str | None = None) -> "RealDb":
        name = "t_" + uuid.uuid4().hex[:10]
        admin = psycopg2.connect(self.dsn())
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
        conn = psycopg2.connect(self.dsn(name))
        conn.autocommit = True
        conn.set_client_encoding("UTF8")
        cur = conn.cursor()
        failed: list[str] = []
        steps = [("schema.sql", _schema_sql())] + [(p.name, p.read_text(encoding="utf-8")) for p in migration_files(upto)] + [
            ("seed.sql", (DB_DIR / "seed.sql").read_text(encoding="utf-8"))]
        for label, sql in steps:
            try:
                cur.execute(sql)
            except Exception as exc:                 # noqa: BLE001
                failed.append(f"{label}: {str(exc).splitlines()[0]}")
                cur.execute("ROLLBACK")
            cur.execute("SET search_path = cv_analyzer")
        return RealDb(self, name, admin, conn, failed)

    def stop(self):
        try:
            self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate", "stop"])
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


class RealDb:
    def __init__(self, cluster, name, admin, conn, failed):
        self.cluster, self.name, self.admin, self.conn, self.failed = cluster, name, admin, conn, failed
        self.async_url = f"postgresql+asyncpg://postgres@127.0.0.1:{cluster.port}/{name}"

    def q(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute("SET search_path = cv_analyzer")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None

    def drop(self):
        try:
            self.conn.close()
        finally:
            self.admin.cursor().execute(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
            self.admin.close()
