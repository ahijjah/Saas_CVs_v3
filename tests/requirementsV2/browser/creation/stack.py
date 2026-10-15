"""The disposable stack used by the full-application verifications: PostgreSQL 16 with the project's schema and migrations, Redis, the real Celery
worker, the real FastAPI application (uvicorn) and the built single-page application on its own origin. Verification-only plumbing: the model
transport is the fake in fake_provider_runtime.py, and no provider client can be constructed. Use as a context manager.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent.parent
BACKEND = REPO / "backend"
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

import realdb_helper as rh  # noqa: E402

PASSWORD = "Passw0rd!x"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def wait_log(path: pathlib.Path, needle: str, timeout: float = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and needle in path.read_text(encoding="utf-8", errors="ignore"):
            return True
        time.sleep(0.5)
    return False


TW_CONFIG = HERE.parent / "tailwind.full.cjs"       # the same content paths and colour tokens as index.html's inline config
TW_INPUT = HERE.parent / "full.css"


def _offline_styles(dist: pathlib.Path) -> None:
    """The CDN script of index.html cannot be loaded offline: compile the stylesheet from the project's own sources and link it instead."""
    subprocess.run(["npx", "--prefix", str(REPO), "tailwindcss", "-c", str(TW_CONFIG), "-i", str(TW_INPUT), "-o", str(dist / "tw-full.css"), "--minify"],
                   cwd=str(TW_CONFIG.parent), check=True, capture_output=True, timeout=600)     # content globs resolve from this folder
    index = (dist / "index.html").read_text(encoding="utf-8")
    index = re.sub(r'<script src="https://cdn\.tailwindcss\.com"></script>', "", index)
    index = re.sub(r'<script type="importmap">.*?</script>', "", index, flags=re.S)
    index = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)[^>]*>', "", index)
    index = re.sub(r"<script>\s*tailwind\.config.*?</script>", "", index, flags=re.S)
    index = index.replace("</head>", '<link rel="stylesheet" href="/tw-full.css"></head>')
    (dist / "index.html").write_text(index, encoding="utf-8")


def _spa_server(dist: pathlib.Path, port: int):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(dist), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.translate_path(self.path)
            if not os.path.exists(path) or (os.path.isdir(path) and not os.path.exists(os.path.join(path, "index.html"))):
                self.path = "/index.html"                    # single-page application: deep links fall back to index.html
            return super().do_GET()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@dataclass
class Stack:
    out: pathlib.Path
    db: object
    api: str
    spa: str
    tenant: str
    procs: list = field(default_factory=list)
    cluster: object = None

    def post_json(self, path: str, body: dict, token: str | None = None):
        req = urllib.request.Request(self.api + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "null")

    def login_token(self, email: str) -> str:
        status, body = self.post_json("/auth/login", {"email": email, "password": PASSWORD})
        assert status == 200, (status, body)
        return body["token"]


def start(out: pathlib.Path, *, seed, spa_dist_name: str = "dist") -> Stack:
    """Build the database, seed it with `seed(db, tenant_id)`, start Redis, the Celery worker, the API, and serve the built SPA.
    The caller closes the stack with close(). Every process writes its log under `out`."""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cluster = rh.Cluster()
    db = cluster.build()
    procs: list = []
    stack = Stack(out=out, db=db, api="", spa="", tenant="", procs=procs, cluster=cluster)
    try:
        os.environ.setdefault("JWT_SECRET", "stack-verification-secret")
        tenant = "11111111-1111-1111-1111-111111111111"
        stack.tenant = tenant
        seed(db, tenant)
        redis_port, api_port, spa_port = free_port(), free_port(), free_port()
        procs.append(subprocess.Popen(["redis-server", "--port", str(redis_port), "--save", "", "--appendonly", "no", "--daemonize", "no"],
                                      stdout=(out / "redis.log").open("w"), stderr=subprocess.STDOUT))
        spa_dist = out / spa_dist_name
        build = subprocess.run(["npx", "vite", "build", "--outDir", str(spa_dist), "--emptyOutDir"], cwd=str(REPO), capture_output=True, text=True,
                               env={**os.environ, "VITE_API_BASE": f"http://127.0.0.1:{api_port}"}, timeout=900)
        if build.returncode != 0:
            raise RuntimeError("vite build failed:\n" + build.stderr[-2000:])
        _offline_styles(spa_dist)
        env = {**os.environ, "REQ_V2_BACKEND": str(BACKEND), "FAKE_LOG": str(out / "model_calls.jsonl"), "FAKE_STATE": str(out / "fail_once"),
               "DATABASE_URL": db.async_url, "JWT_SECRET": os.environ["JWT_SECRET"],
               "CELERY_BROKER_URL": f"redis://127.0.0.1:{redis_port}/0", "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{redis_port}/1",
               "CORS_ORIGINS": json.dumps([f"http://127.0.0.1:{spa_port}"]), "PYTHONIOENCODING": "utf-8",
               "PYTHONPATH": str(HERE)}
        api_log, worker_log = out / "api.log", out / "worker.log"
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "fake_api_app:app", "--host", "127.0.0.1", "--port", str(api_port),
                                       "--log-level", "warning"], cwd=str(BACKEND), env=env, stdout=api_log.open("w"), stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen([sys.executable, "-m", "celery", "-A", "fake_celery_app", "worker", "--pool=solo", "--loglevel=WARNING",
                                       "--without-heartbeat", "--without-gossip", "--without-mingle"], cwd=str(BACKEND), env=env,
                                      stdout=worker_log.open("w"), stderr=subprocess.STDOUT))
        stack.spa_server = _spa_server(spa_dist, spa_port)
        if not wait_http(f"http://127.0.0.1:{api_port}/docs"):
            raise RuntimeError("the API did not start: see api.log")
        if not wait_log(worker_log, "[queues]"):
            raise RuntimeError("the Celery worker did not start: see worker.log")
        stack.api = f"http://127.0.0.1:{api_port}"
        stack.spa = f"http://127.0.0.1:{spa_port}"
        return stack
    except Exception:
        stack.close()
        raise


def close(stack: Stack) -> None:
    for proc in reversed(stack.procs):
        proc.terminate()
    for proc in stack.procs:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    try:
        stack.db.drop()
    finally:
        if stack.cluster is not None:
            stack.cluster.stop()


Stack.close = close
