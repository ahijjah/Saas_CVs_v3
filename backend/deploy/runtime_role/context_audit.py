"""Static audit of tenant-context handling (read-only; reads the source only). A REVIEW AID, not a proof.

1. Routes that run SQL but take no authenticated user (no CurrentUserDep / current_user parameter): their tenant context is whatever the code sets
   (super_admin in login, public intake and workers) or none. They rely on the superuser bypass today if they set none.
2. Functions that COMMIT and then run SQL without setting the context again: after the commit the transaction-local context is gone, so
   tenant tables return no rows under a restricted role. A helper that re-sets the context (for example services/requirements_api.py `_load`)
   is named explicitly so the reviewer can see it.

Usage:  python deploy/runtime_role/context_audit.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SCAN = ("routers", "services", "workers", "auth", "database.py")
ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
RLS_RE = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:cv_analyzer\.)?(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)
TABLE_RE = re.compile(r"(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+(?:cv_analyzer\.)?(\w+)", re.I)
SQL_CALLS = {"execute", "exec_driver_sql"}
CONTEXT_SETTERS = {"set_rls_context", "set_request_context", "get_db_with_rls"}


def rls_tables() -> set[str]:
    """Tables with ROW LEVEL SECURITY enabled, from db/schema.sql and every migration (static; the live check is inventory.sql)."""
    names: set[str] = set()
    for p in [BACKEND / "db" / "schema.sql", *sorted((BACKEND / "db" / "migrations").glob("*.sql"))]:
        names.update(m.group(1).lower() for m in RLS_RE.finditer(p.read_text(encoding="utf-8", errors="replace")))
    return names


def tables_after(fn: ast.AST, line: int) -> set[str]:
    """Tables named in SQL text (string constants) that sit after `line` in the function."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and getattr(n, "lineno", 0) > line:
            out.update(m.group(1).lower() for m in TABLE_RE.finditer(n.value))
    return out


def _files():
    for entry in SCAN:
        base = BACKEND / entry
        for f in ([base] if base.is_file() else sorted(base.rglob("*.py"))):
            if "__pycache__" not in f.parts:
                yield f


def _calls(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Attribute):
                out.add(fn.attr)
            elif isinstance(fn, ast.Name):
                out.add(fn.id)
    return out


def _is_route(fn: ast.AST) -> bool:
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_METHODS:
            return True
    return False


def _has_guard(fn: ast.AST) -> bool:
    """A route-level guard (dependencies=[RequireX]) : the Require* guards resolve get_current_user, which sets the tenant context."""
    for d in getattr(fn, "decorator_list", []):
        for kw in getattr(d, "keywords", []):
            if kw.arg == "dependencies" and any(isinstance(n, ast.Name) and n.id.startswith("Require") for n in ast.walk(kw.value)):
                return True
    return False


def _has_user_param(fn: ast.AST) -> bool:
    names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    anns = [ast.unparse(a.annotation) for a in fn.args.args + fn.args.kwonlyargs if a.annotation is not None]
    return any(n in ("current_user", "user", "current_user_dep") for n in names) or any("CurrentUser" in a for a in anns)


def audit() -> tuple[list[str], list[str]]:
    routes, commits = [], []
    rls = rls_tables()
    for f in _files():
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        rel = f.relative_to(BACKEND)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]:
            calls = _calls(fn)
            if _is_route(fn) and (calls & SQL_CALLS or "text" in calls) and not _has_user_param(fn) and not _has_guard(fn) \
                    and not (calls & CONTEXT_SETTERS):
                routes.append(f"{rel}:{fn.lineno} {fn.name}")
            commit_lines = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "commit")
            if commit_lines:
                first = commit_lines[0]
                later_sql = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                   and n.func.attr in SQL_CALLS and n.lineno > first)
                resets = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in CONTEXT_SETTERS
                                or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in CONTEXT_SETTERS))
                helpers = sorted({c for c in _calls(fn) if c.startswith("_load") or c in ("build_view", "recovery_status", "pipeline_context")})
                if later_sql and not any(r > first for r in resets):
                    touched = sorted(tables_after(fn, first) & rls)
                    commits.append(f"{rel}:{fn.name} commit@{first} sql-after@{later_sql[0]} helpers={helpers or '-'} "
                                   f"rls-tables-after-commit={touched or 'none'}")
    return routes, commits


def main() -> int:
    routes, commits = audit()
    print("# 1. routes that run SQL without an authenticated user and without setting the context in the function")
    for r in routes:
        print(f"  {r}")
    print(f"  ({len(routes)} routes; their context is set by code (login, public intake, workers) or absent)")
    print("# 2. functions that commit and run SQL afterwards without setting the context again (review). The database.py listener re-applies the")
    print("#    context recorded by set_rls_context on the session at every new transaction, so a function whose session already called")
    print("#    set_rls_context is covered; the rows below need the runtime-role test (tests/test_runtime_affected_paths.py) or a re-set.")
    for c in commits:
        print(f"  {c}")
    print(f"  ({len(commits)} functions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
