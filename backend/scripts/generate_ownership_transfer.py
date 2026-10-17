"""Generate explicit ALTER ... OWNER TO statements for the cv_analyzer schema. Read-only.

    MIGRATION_DATABASE_URL=<bootstrap superuser URL> python -m scripts.generate_ownership_transfer --target-owner cv_migrator

The output lists one statement per verified object in cv_analyzer and nothing outside it. Object kinds this generator does
not handle stop it: nothing is printed and the exit status is 1. REASSIGN OWNED is never emitted, because it can move
objects outside the schema. The statements are reviewed and run by an operator; this script does not run them."""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import psycopg2

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Relation kinds handled explicitly. Indexes and TOAST tables follow their table and are not listed.
_RELATION_KINDS = {"r": "TABLE", "p": "TABLE", "S": "SEQUENCE", "v": "VIEW", "m": "MATERIALIZED VIEW"}

_RELATIONS = """
    SELECT c.relkind, format('%I.%I', n.nspname, c.relname), pg_get_userbyid(c.relowner)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'cv_analyzer'
      AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
      AND NOT EXISTS (SELECT 1 FROM pg_depend d
                      WHERE d.objid = c.oid AND d.classid = 'pg_class'::regclass
                        AND d.refclassid = 'pg_class'::regclass AND d.deptype = 'a')
    ORDER BY c.relkind, c.relname
"""

_ROUTINES = """
    SELECT p.prokind, format('%I.%I(%s)', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)),
           pg_get_userbyid(p.proowner)
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'cv_analyzer'
    ORDER BY p.proname, p.oid
"""

# Enum, domain and range types are not owned through ALTER ... OWNER in this generator. Their presence stops it.
_UNHANDLED_TYPES = """
    SELECT count(*)
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname = 'cv_analyzer' AND t.typtype IN ('e', 'd', 'r', 'm')
"""


class GenerationError(RuntimeError):
    pass


def generate(conn, target_owner: str) -> list[str]:
    if not _ROLE_NAME.match(target_owner):
        raise GenerationError(f"owner role {target_owner!r} is not a plain role identifier")
    previous = conn.autocommit
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_UNHANDLED_TYPES)
        if cur.fetchone()[0]:
            raise GenerationError("cv_analyzer contains enum, domain or range types; ownership for them is not generated")

        lines = [f"-- generated for target owner {target_owner}; review before running",
                 f"ALTER SCHEMA cv_analyzer OWNER TO {target_owner};"]
        cur.execute(_RELATIONS)
        for kind, name, owner in cur.fetchall():
            keyword = _RELATION_KINDS[kind]
            lines.append(f"ALTER {keyword} {name} OWNER TO {target_owner};  -- was {owner}")
        cur.execute(_ROUTINES)
        for kind, signature, owner in cur.fetchall():
            keyword = "PROCEDURE" if kind == "p" else "FUNCTION"
            lines.append(f"ALTER {keyword} {signature} OWNER TO {target_owner};  -- was {owner}")
        return lines
    finally:
        conn.rollback()
        conn.autocommit = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ownership statements for cv_analyzer.")
    parser.add_argument("--target-owner", default="cv_migrator")
    args = parser.parse_args(argv)
    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not url:
        print("MIGRATION_DATABASE_URL is not set", file=sys.stderr)
        return 1
    conn = psycopg2.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        lines = generate(conn, args.target_owner)
    except GenerationError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
