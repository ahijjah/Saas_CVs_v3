"""Static inventory of the table privileges the application code needs (read-only; reads the source, connects to nothing).

For every table of the cv_analyzer schema it lists the statement verbs found in the backend source: SELECT (FROM / JOIN), INSERT (INTO),
UPDATE and DELETE. It is a starting point for the grants, checked against the live schema by inventory.sql and tested in
tests/test_runtime_role_plan.py. Heuristic: dynamic SQL (f-strings with table names built at run time) is reported separately.

Usage:  python deploy/runtime_role/usage_inventory.py [--schema-sql db/schema.sql] [--migrations db/migrations]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import defaultdict

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("services", "routers", "workers", "auth", "database.py", "main.py", "config.py")
CREATE_RE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(?:cv_analyzer\.)?(\w+)", re.I)
TABLE = r"(?:cv_analyzer\.)?(\w+)"
PATTERNS = {
    "SELECT": re.compile(r"\b(?:FROM|JOIN)\s+" + TABLE, re.I),
    "INSERT": re.compile(r"\bINSERT\s+INTO\s+" + TABLE, re.I),
    "UPDATE": re.compile(r"\bUPDATE\s+" + TABLE + r"\s+SET\b", re.I),
    "DELETE": re.compile(r"\bDELETE\s+FROM\s+" + TABLE, re.I),
}
DYNAMIC = re.compile(r"(?:FROM|JOIN|INTO|UPDATE)\s+\{", re.I)


def schema_tables(schema_sql: pathlib.Path, migrations: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for p in [schema_sql, *sorted(migrations.glob("*.sql"))]:
        names.update(m.group(1).lower() for m in CREATE_RE.finditer(p.read_text(encoding="utf-8", errors="replace")))
    return names


def scan(tables: set[str]) -> tuple[dict[str, set[str]], list[str]]:
    usage: dict[str, set[str]] = defaultdict(set)
    dynamic: list[str] = []
    for entry in SOURCE_DIRS:
        base = BACKEND / entry
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for f in files:
            if "tests" in f.parts or "__pycache__" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for verb, pat in PATTERNS.items():
                for m in pat.finditer(text):
                    name = m.group(1).lower()
                    if name in tables:
                        usage[name].add(verb)
            if DYNAMIC.search(text):
                dynamic.append(str(f.relative_to(BACKEND)))
    return usage, sorted(set(dynamic))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema-sql", default=str(BACKEND / "db" / "schema.sql"))
    ap.add_argument("--migrations", default=str(BACKEND / "db" / "migrations"))
    args = ap.parse_args(argv)
    tables = schema_tables(pathlib.Path(args.schema_sql), pathlib.Path(args.migrations))
    usage, dynamic = scan(tables)
    print("# table -> statement verbs found in source")
    for name in sorted(usage):
        print(f"{name}\t{','.join(sorted(usage[name]))}")
    print(f"# tables with no statement found in source (granted only if an operator confirms a need): {len(tables - set(usage))}")
    print("# files that build SQL with a table name at run time (review by hand):")
    for f in dynamic:
        print(f"#   {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
