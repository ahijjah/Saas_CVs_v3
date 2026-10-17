"""Disposable PostgreSQL 16 for database tests, built from db/schema.sql and then every db/migrations/*.sql in order.

Test use only. A throw-away cluster is started from the system PostgreSQL 16 binaries in a temporary directory, and every test
database is created inside it. Nothing here touches a shared or production database. Steps that fail on the current chain are
recorded in `failed` and rolled back, not hidden, so a test can tell its own migration apart from pre-existing failures."""
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


def unavailable_reason() -> str | None:
    """None when a PostgreSQL 16 server can be started here, otherwise the reason."""
    if not (PG_BIN / "initdb").exists():
        return "PostgreSQL 16 binaries not found (set PG16_BIN)"
    if os.geteuid() == 0 and shutil.which("runuser") is None:
        return "runuser is needed to run the test cluster as the postgres user"
    return None


def schema_sql() -> str:
    lines = (DB_DIR / "schema.sql").read_text(encoding="utf-8").split("\n")
    for n in range(0, 96):      # pre-existing forward references to users in the first tables of schema.sql
        lines[n] = re.sub(r"\s+REFERENCES users \(user_id\) ON DELETE SET NULL", "", lines[n])
    return "\n".join(lines)


def migration_files() -> list[pathlib.Path]:
    return sorted((DB_DIR / "migrations").glob("*.sql"))


class Cluster:
    """One PostgreSQL 16 server, owned by the test session."""

    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="req_pg_"))
        subprocess.run(["chown", "postgres", str(self.dir)], check=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self._as_pg([str(PG_BIN / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-E", "UTF8"])
        self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-o",
                     f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1", "-l", str(self.dir / "log"), "-w", "start"])

    def _as_pg(self, cmd: list[str]) -> None:
        if os.geteuid() == 0:
            cmd = ["runuser", "-u", "postgres", "--", *cmd]
        subprocess.run(cmd, check=True, capture_output=True)

    def dsn(self, db: str = "postgres") -> str:
        return f"host=127.0.0.1 port={self.port} user=postgres dbname={db}"

    def stop(self) -> None:
        try:
            self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate", "stop"])
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


class Database:
    """A database built from the real chain. `failed` lists every step that did not apply, with its error."""

    def __init__(self, cluster: Cluster, name: str, admin, conn, failed: list[str]):
        self.cluster, self.name, self.admin, self.conn, self.failed = cluster, name, admin, conn, failed

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self.admin.cursor().execute(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
            self.admin.close()


def build(cluster: Cluster) -> Database:
    name = "t_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(cluster.dsn())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
    conn = psycopg2.connect(cluster.dsn(name))
    conn.autocommit = True
    conn.set_client_encoding("UTF8")
    cur = conn.cursor()
    failed: list[str] = []
    steps = [("schema.sql", schema_sql())] + [(p.name, p.read_text(encoding="utf-8")) for p in migration_files()]
    for label, sql in steps:
        try:
            cur.execute(sql)
        except psycopg2.Error as exc:
            failed.append(f"{label}: {str(exc).splitlines()[0]}")
            cur.execute("ROLLBACK")
        cur.execute("SET search_path = cv_analyzer")
    return Database(cluster, name, admin, conn, failed)
