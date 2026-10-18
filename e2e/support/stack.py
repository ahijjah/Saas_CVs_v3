"""The disposable stack for the E2E suite: PostgreSQL 16 (built from the migration chain), the backend (uvicorn) and the frontend
(Vite dev server). Each server runs in its own process group on a port chosen at runtime. Teardown stops every process group and
verifies that nothing survived; a failure during setup or during the tests still runs teardown.

Test-only switches (read from the environment, never set by production code):
  E2E_PID_RECORD      file that receives "<name> <pid>" lines for every process started (the cleanup check reads it)
  E2E_INJECT_FAILURE  "after-start": raise once every service is up (proves teardown runs after a failure)
  E2E_CLEANUP_MUTATION "no-kill": teardown skips stopping processes (proves the cleanup check detects survivors)
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from realdb_helper import built_chain, unavailable_reason  # backend/tests is on sys.path (see conftest.py)

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VITE = REPO / "node_modules" / ".bin" / "vite"
TEARDOWN_TIMEOUT = 15


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


RUNTIME_ROLES = ("cv_app", "cv_worker", "cv_admin", "cv_secrets")
OPS_DIR = REPO / "backend" / "db" / "ops"


def _url(db, role: str, password: str, driver: str) -> str:
    return f"{driver}://{role}:{password}@127.0.0.1:{db.cluster.port}/{db.name}"


def _provision_runtime_roles(db, passwords: dict[str, str]) -> None:
    """Apply the operator scripts exactly as an operator does, with this run's passwords. A failure stops the run."""
    variables: list[str] = []
    for role in RUNTIME_ROLES:
        variables += ["-v", f"{role}_password={passwords[role]}"]
    base = ["psql", "-h", "127.0.0.1", "-p", str(db.cluster.port), "-U", "postgres", "-d", db.name, "-X", "-q",
            "-v", "ON_ERROR_STOP=1"]
    for script in ("0004_runtime_roles.sql.template", "0005_runtime_privileges.sql.template"):
        proc = subprocess.run(base + variables + ["-f", str(OPS_DIR / script)], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"operator script {script} failed: {proc.stderr.strip()}")


@dataclass
class Service:
    name: str
    process: subprocess.Popen
    log: Path


@dataclass
class Stack:
    backend_base: str          # http://127.0.0.1:<port> of the API
    frontend_base: str         # http://127.0.0.1:<port> of the UI
    ui_api_origin: str         # the origin the UI is compiled with (config.ts); the browser's API calls are routed to backend_base
    dsn: str                   # SQLAlchemy URL for the backend
    psycopg_dsn: str           # libpq URL for test assertions
    users: dict[str, dict] = field(default_factory=dict)
    logs: list[Path] = field(default_factory=list)


def _record(name: str, pid: int) -> None:
    record = os.environ.get("E2E_PID_RECORD")
    if record:
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(f"{name} {pid}\n")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        status = Path(f"/proc/{pid}/status").read_text().split("State:")[1].split()[0]
        return status != "Z"       # a zombie has exited; it is not a survivor
    except (OSError, IndexError):
        return False


def _wait_gone(pid: int, timeout: float) -> bool:
    """True once the process has exited. pg_ctl stop returns before the postmaster exits, so a single check races it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return not _alive(pid)


def _stop_group(service: Service) -> None:
    pgid = service.process.pid            # start_new_session=True makes the child the leader of its own group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        service.process.wait(timeout=TEARDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        service.process.wait(timeout=TEARDOWN_TIMEOUT)


def _start(name: str, cmd: list[str], env: dict, cwd: Path, log: Path) -> Service:
    handle = open(log, "wb")
    process = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    handle.close()
    _record(name, process.pid)
    return Service(name, process, log)


def _wait_http(url: str, service: Service, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise RuntimeError(f"{service.name} exited with {service.process.returncode}; see {service.log}")
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except urllib.error.HTTPError:
            return                       # any HTTP answer means the server is listening
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{service.name} did not answer {url} within {timeout}s; see {service.log}")


def _seed(db) -> dict[str, dict]:
    """Tenant and one active user per role. Passwords are generated here, held in memory for this run only, and stored as the
    application's own hash. Nothing is written to disk."""
    from auth.password import hash_password
    cur = db.conn.cursor()
    tag = uuid.uuid4().hex[:6]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"E2E {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    users = {"tenant": tenant}
    for role in ("admin", "recruiter", "viewer"):
        email = f"{role}-{tag}@e2e-{tag}.com"
        password = secrets.token_urlsafe(18)
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status, must_change_password)"
                    " VALUES (%s, %s, %s, %s, %s, 'active', false) RETURNING user_id",
                    (tenant, email, hash_password(password), role.title(), role))
        users[role] = {"email": email, "password": password, "user_id": str(cur.fetchone()[0])}
    return users


def _postgres_pid(cluster_dir: Path) -> int:
    return int((cluster_dir / "data" / "postmaster.pid").read_text().split("\n", 1)[0])


@contextmanager
def running_stack(run_dir: Path):
    """Build the cluster, start backend and frontend, yield a Stack, and always tear everything down afterwards."""
    reason = unavailable_reason()
    if reason:
        raise RuntimeError(reason)
    frontend_port, backend_port = free_port(), free_port()
    origin = f"http://127.0.0.1:{frontend_port}"
    files_root = run_dir / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    services: list[Service] = []
    postgres_pid = None
    try:
        with built_chain() as db:
            postgres_pid = _postgres_pid(db.cluster.dir)
            _record("postgres", postgres_pid)
            users = _seed(db)
            # The application runs on the hardened runtime logins, exactly as an operator would provision them: the operator scripts
            # 0004 and 0005 with per-run passwords. Nothing in the run uses the cluster superuser after fixture setup.
            passwords = {role: secrets.token_urlsafe(18) for role in RUNTIME_ROLES}
            _provision_runtime_roles(db, passwords)
            dsn = _url(db, "cv_app", passwords["cv_app"], "postgresql+asyncpg")
            admin_dsn = _url(db, "cv_admin", passwords["cv_admin"], "postgresql+asyncpg")
            worker_dsn = _url(db, "cv_worker", passwords["cv_worker"], "postgresql+asyncpg")
            secrets_dsn = _url(db, "cv_secrets", passwords["cv_secrets"], "postgresql+asyncpg")
            # Test-side inspection reads through cv_admin, not the superuser (the grants allow it the rows the checks need).
            psycopg_dsn = _url(db, "cv_admin", passwords["cv_admin"], "postgresql")
            secret = secrets.token_urlsafe(32)
            try:
                backend_env = {**os.environ, "DATABASE_URL": dsn, "ADMIN_DATABASE_URL": admin_dsn,
                               "WORKER_DATABASE_URL": worker_dsn, "SECRETS_DATABASE_URL": secrets_dsn,
                               "DB_SCHEMA": "cv_analyzer", "JWT_SECRET": secret}
                backend = _start("backend", [sys.executable, str(HERE / "backend_app.py"), "--port", str(backend_port),
                                             "--origin", origin, "--files-root", str(files_root)],
                                 backend_env, REPO, run_dir / "backend.log")
                services.append(backend)
                _wait_http(f"http://127.0.0.1:{backend_port}/openapi.json", backend, 120)

                frontend_env = {**os.environ, "BROWSER": "none", "CI": "1"}
                frontend = _start("frontend", [str(VITE), "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                                  frontend_env, REPO, run_dir / "frontend.log")
                services.append(frontend)
                _wait_http(origin, frontend, 180)

                if os.environ.get("E2E_INJECT_FAILURE") == "after-start":
                    raise RuntimeError("injected failure after start (cleanup check)")

                yield Stack(backend_base=f"http://127.0.0.1:{backend_port}", frontend_base=origin,
                            ui_api_origin="http://72.62.31.221:8000", dsn=dsn, psycopg_dsn=psycopg_dsn,
                            users=users,
                            logs=[s.log for s in services])
            finally:
                teardown(services)
    finally:
        survivors = [s.name for s in services if s.process.poll() is None]
        if postgres_pid and not _wait_gone(postgres_pid, TEARDOWN_TIMEOUT):
            survivors.append("postgres")
        if survivors:
            raise RuntimeError(f"E2E teardown left processes running: {survivors}")


def teardown(services: list[Service]) -> None:
    if os.environ.get("E2E_CLEANUP_MUTATION") == "no-kill":
        return                                    # mutation: teardown skips stopping processes
    for service in reversed(services):
        _stop_group(service)
