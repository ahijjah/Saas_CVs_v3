"""Disposable PostgreSQL 16 for database tests, built from db/schema.sql and then every db/migrations/*.sql in order.

Test use only. A throw-away cluster is started from the system PostgreSQL 16 binaries in a temporary directory, and every test
database is created inside it. Nothing here touches a shared or production database. Steps that fail on the current chain are
recorded in `failed` and rolled back, not hidden, so a test can tell its own migration apart from pre-existing failures."""
from __future__ import annotations

import os
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from db.runner import runner  # noqa: E402

DB_DIR = BACKEND / "db"
PG_BIN = pathlib.Path(os.environ.get("PG16_BIN", "/usr/lib/postgresql/16/bin"))

# The chain builds with zero failures. Migrations 010, 070 and 087 are superseded by replacements 095, 096 and 097 through
# db/runner/manifest.py, so they are never executed. Any failure now fails the validation.
EXPECTED_PRE_EXISTING_FAILURES: frozenset[str] = frozenset()
REQUIRED_MIGRATIONS: frozenset[str] = frozenset({"093_requirements_v2_storage_schema.sql"})


@dataclass(frozen=True)
class StepFailure:
    file: str          # "schema.sql" or the migration file name
    error: str         # the first line of the database error


class ChainValidationError(AssertionError):
    """The migration chain does not match the expected outcome."""


def check_chain(failures: Sequence[StepFailure], *, expected: frozenset[str] = EXPECTED_PRE_EXISTING_FAILURES,
                required: frozenset[str] = REQUIRED_MIGRATIONS) -> None:
    """Raise ChainValidationError unless the failing steps are exactly `expected`, schema.sql succeeded, and no required migration failed."""
    problems: list[str] = []
    names = [f.file for f in failures]
    if "schema.sql" in names:
        problems.append(f"schema.sql failed: {[f.error for f in failures if f.file == 'schema.sql']}")
    for name in sorted(required & set(names)):
        problems.append(f"required migration {name} failed: {[f.error for f in failures if f.file == name]}")
    unexpected = sorted(set(names) - expected)
    if unexpected:
        problems.append(f"unexpected failures: {[(f.file, f.error) for f in failures if f.file in unexpected]}")
    missing = sorted(expected - set(names))
    if missing:
        problems.append(f"expected failures no longer fail: {missing}")
    if problems:
        raise ChainValidationError("migration chain does not match the expected outcome: " + "; ".join(problems))


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
        try:
            subprocess.run(["chown", "postgres", str(self.dir)], check=True)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            self._as_pg([str(PG_BIN / "initdb"), "-D", str(self.dir / "data"), "-A", "trust", "-E", "UTF8"])
            self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-o",
                         f"-p {self.port} -k {self.dir} -c listen_addresses=127.0.0.1", "-l", str(self.dir / "log"), "-w", "start"])
        except BaseException:
            self.stop()                 # a half-started cluster is stopped and removed before the error propagates
            raise

    def _as_pg(self, cmd: list[str]) -> None:
        if os.geteuid() == 0:
            cmd = ["runuser", "-u", "postgres", "--", *cmd]
        subprocess.run(cmd, check=True, capture_output=True)

    def dsn(self, db: str = "postgres") -> str:
        return f"host=127.0.0.1 port={self.port} user=postgres dbname={db}"

    def stop(self) -> None:
        """Safe to call on a cluster that never started: the server may not be running, so the stop error is ignored."""
        try:
            if (self.dir / "data" / "postmaster.pid").exists():
                self._as_pg([str(PG_BIN / "pg_ctl"), "-D", str(self.dir / "data"), "-m", "immediate", "stop"])
        except subprocess.CalledProcessError:
            pass
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


class Database:
    """A database built from the real chain. `failures` records every step that did not apply, with its error."""

    def __init__(self, cluster: Cluster, name: str, admin, conn, failures: list[StepFailure]):
        self.cluster, self.name, self.admin, self.conn, self.failures = cluster, name, admin, conn, failures
        self.runtime_passwords: dict[str, str] = {}

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self.admin.cursor().execute(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
            self.admin.close()


def build(cluster: Cluster) -> Database:
    """schema.sql, then the migration runner over an empty database. The runner stops at the first failing file, which is
    recorded here so the validation can report it."""
    name = "t_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(cluster.dsn())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
    conn = psycopg2.connect(cluster.dsn(name))
    conn.autocommit = True
    conn.set_client_encoding("UTF8")
    cur = conn.cursor()
    failures: list[StepFailure] = []
    try:
        cur.execute(schema_sql())
    except psycopg2.Error as exc:
        failures.append(StepFailure("schema.sql", str(exc).splitlines()[0]))
    if not failures:
        try:
            runner.run(conn, allow_fresh=True)
        except runner.MigrationError as exc:
            failures.append(StepFailure(exc.file or "migrations", str(exc).splitlines()[0]))
    cur.execute("SET search_path = cv_analyzer")
    database = Database(cluster, name, admin, conn, failures)
    if not failures:
        provision_runtime_logins(database)
    return database


RUNTIME_LOGIN_ROLES = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")


def provision_runtime_logins(database: "Database") -> dict[str, str]:
    """Apply the operator scripts 0004 (logins and attributes) and 0005 (generated privileges) with per-database passwords, exactly as
    an operator does. Tests connect as these logins. The passwords exist only in the returned dict and the test process."""
    passwords = {role: secrets.token_urlsafe(18) for role in RUNTIME_LOGIN_ROLES}
    variables: list[str] = []
    for role in RUNTIME_LOGIN_ROLES:
        variables += ["-v", f"{role}_password={passwords[role]}"]
    base = ["psql", "-h", "127.0.0.1", "-p", str(database.cluster.port), "-U", "postgres", "-d", database.name, "-X", "-q",
            "-v", "ON_ERROR_STOP=1"]
    for script in ("0004_runtime_roles.sql.template", "0005_runtime_privileges.sql.template"):
        proc = subprocess.run(base + variables + ["-f", str(DB_DIR / "ops" / script)], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"{script} failed on the test database: {proc.stderr.strip()}")
    database.runtime_passwords = passwords
    return passwords


@contextmanager
def built_chain() -> Iterator[Database]:
    """The chain built on a disposable cluster. The cluster is stopped even when construction or the caller's body fails."""
    reason = unavailable_reason()
    if reason:
        raise RuntimeError(reason)
    cluster = Cluster()
    try:
        db = build(cluster)
        try:
            yield db
        finally:
            db.close()
    finally:
        cluster.stop()
