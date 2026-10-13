"""Stands in for the optional `pgserver` package in the requirements-v2 API and migration suites, using the system PostgreSQL 16 cluster that
tests/realdb_helper.py starts (the same real server, the same disposable-cluster semantics). It provides only what those suites call:
get_server(path) -> an object with get_uri(name=None) (a socket URI, `postgresql://postgres:@/<db>?host=<socket dir>`) and cleanup().
Nothing here is mocked. Raises ImportError when no PostgreSQL 16 can be started, so the suites still skip exactly as before."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import realdb_helper as rh  # noqa: E402

_reason = rh.available()
if _reason:
    raise ImportError(_reason)


class _Server:
    def __init__(self):
        self._cluster = rh.Cluster()

    def get_uri(self, name: str | None = None) -> str:
        return f"postgresql://postgres:@/{name or 'postgres'}?host={self._cluster.dir}&port={self._cluster.port}"

    def cleanup(self) -> None:
        self._cluster.stop()


def get_server(path: str | None = None) -> _Server:
    """`path` is accepted for compatibility with pgserver and ignored: the cluster lives in its own temporary directory."""
    return _Server()
