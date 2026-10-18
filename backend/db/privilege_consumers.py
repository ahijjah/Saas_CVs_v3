"""Production consumers of every runtime privilege, derived from the code that runs under each login.

A consumer is a production module that a login's code path can reach by import and that refers to the object. The entry points are:

  * cv_app: every router that does not use a platform dependency, and main (the API process);
  * cv_admin: every router that declares get_platform_db or get_admin_db (platform administration and unauthenticated platform flows);
  * cv_worker: every module under workers/;
  * cv_secrets: services/runtime_config.py, the only module that reads secret values.

This is an over-approximation: reachability shows that a path exists, not that every verb is needed. Verb minimality is reviewed when
the matrix changes. The manifest records the consumers so that a grant with no consumer, or a consumer that no longer exists, fails.

    PYTHONPATH=backend python -m db.privilege_consumers > backend/db/privilege_manifest.json
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

from db import runtime_grants as G

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PLATFORM_DEPENDENCIES = ("get_platform_db", "get_admin_db")
SECRET_READER_MODULE = "services.runtime_config"
PATH_BY_ROLE = {
    G.APP: "request",
    G.WORKER: "worker",
    G.ADMIN: "platform administration (verified super_admin) or an unauthenticated platform flow",
    G.SECRETS: "secret reader (runtime configuration)",
}


def modules() -> dict[str, pathlib.Path]:
    found: dict[str, pathlib.Path] = {}
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in ("tests", "scripts", "db") or "__pycache__" in relative.parts:
            continue
        found[".".join(relative.with_suffix("").parts)] = path
    return found


def _imports(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def _resolve(name: str, known: dict[str, pathlib.Path]) -> str | None:
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return None


def _uses_platform_dependency(path: pathlib.Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return any(re.search(rf"\b{name}\b", text) for name in PLATFORM_DEPENDENCIES)


def entry_points(known: dict[str, pathlib.Path]) -> dict[str, list[str]]:
    admin_routers = [m for m, p in known.items() if m.startswith("routers.") and _uses_platform_dependency(p)]
    app_routers = [m for m, p in known.items() if m.startswith("routers.") and m not in admin_routers]
    return {
        G.APP: app_routers + ["main"],
        G.ADMIN: admin_routers,
        G.WORKER: [m for m in known if m.startswith("workers.")],
        G.SECRETS: [SECRET_READER_MODULE],
    }


def closure(entries: list[str], known: dict[str, pathlib.Path], excluded: frozenset[str] = frozenset()) -> set[str]:
    """Modules reachable by import from entries. excluded modules are not entered: the API process imports the administration
    routers, but their handlers run under cv_admin, not cv_app."""
    seen: set[str] = set()
    stack = list(entries)
    while stack:
        module = stack.pop()
        if module in seen or module not in known or module in excluded:
            continue
        seen.add(module)
        for imported in _imports(known[module]):
            resolved = _resolve(imported, known)
            if resolved and resolved not in seen:
                stack.append(resolved)
    return seen


def consumers_of(obj: str, role: str, known: dict[str, pathlib.Path], reach: dict[str, set[str]]) -> list[str]:
    """Modules reachable from role's entry points whose source refers to obj as a whole word."""
    pattern = re.compile(rf"\b{re.escape(obj)}\b")
    return sorted(m for m in reach[role] if pattern.search(known[m].read_text(encoding="utf-8")))


def build() -> dict:
    known = modules()
    entries = entry_points(known)
    admin_routers = frozenset(entries[G.ADMIN])
    reach = {role: closure(entries[role], known, excluded=admin_routers if role == G.APP else frozenset()) for role in entries}
    cells = []
    for table in sorted(G.MATRIX):
        for role in (G.APP, G.WORKER, G.ADMIN, G.SECRETS):
            verbs = G.MATRIX[table].get(role)
            if not verbs:
                continue
            cells.append({
                "kind": "table", "object": table, "role": role,
                "privileges": [G.VERB_PRIVILEGES[v] for v in ("S", "I", "U", "D") if v in verbs],
                "path": PATH_BY_ROLE[role],
                "consumers": consumers_of(table, role, known, reach),
            })
    columns = []
    for role, names in G.PLATFORM_SECRETS_COLUMN_GRANTS.items():
        columns.append({
            "kind": "column", "object": "platform_secrets", "columns": list(names), "role": role, "privileges": ["SELECT"],
            "path": PATH_BY_ROLE[role],
            "consumers": consumers_of("platform_secrets", role, known, reach),
        })
    functions = []
    for signature, role in G.FUNCTION_EXECUTE.items():
        name = signature.split("(")[0]
        consumers = consumers_of(name, role, known, reach)
        functions.append({"kind": "function", "object": signature, "role": role, "privileges": ["EXECUTE"],
                          "path": PATH_BY_ROLE[role], "consumers": consumers})
    sequences = [{
        "kind": "sequence", "object": "job_code_seq", "role": G.APP, "privileges": ["USAGE"], "path": PATH_BY_ROLE[G.APP],
        # The sequence is the only one in the schema. Only the job-creation path draws from it; the worker and the administration
        # login never do, so they hold no grant on it.
        "consumers": consumers_of("job_code_seq", G.APP, known, reach),
    }]
    schemas = [{
        "kind": "schema", "object": "cv_analyzer", "role": role, "privileges": ["USAGE"], "path": PATH_BY_ROLE[role],
        # Every login resolves unqualified names through the role default and the engine option (database.py, db/ops/0004).
        "consumers": ["database.py"],
    } for role in (G.APP, G.WORKER, G.ADMIN, G.SECRETS)]
    return {"schema": 1, "source": "backend/db/runtime_grants.py", "generated_by": "python -m db.privilege_consumers",
            "cells": cells, "columns": columns, "functions": functions, "sequences": sequences, "schemas": schemas}


def main() -> int:
    sys.stdout.write(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
