"""A structural fingerprint of the cv_analyzer schema, read from the catalogue.

The fingerprint is a mapping from an object identity to a definition string. It covers what a migration can create or change:
relations, columns (type, nullability, default), constraints, indexes, triggers, functions, policies, row-level-security flags,
sequences, and the presence of seeded rows in the configuration tables. It deliberately leaves out what an operator procedure
sets afterwards (owners, privileges) and what is free text (comments).

Two uses:
  * the reference fingerprints in db/legacy/reference.json are built from a clean chain and committed;
  * the legacy upgrade reads the live fingerprint of an existing database and compares it with them, so a migration is recorded
    as applied only when the objects it produces are present with the expected definitions.

Read-only: every query is a SELECT on the catalogue or on the seeded configuration tables."""
from __future__ import annotations

import re

SCHEMA = "cv_analyzer"
# Operator tables created by the conversion and the runner. They are not part of the application schema or of any lineage.
OPERATOR_TABLES = ("schema_migrations", "schema_migration_legacy_evidence")

# (table, identity columns). Only the presence of the key is part of the fingerprint: operators change the values.
SEED_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("system_config", ("key",)),
    ("subscription_plans", ("plan_code",)),
    ("ai_prompts", ("prompt_code", "version")),
    ("ai_model_registry", ("provider", "model_name")),
    ("ai_stage_defaults", ("stage",)),
    ("platform_secrets", ("key",)),
    ("platform_secret_readable", ("key",)),
)

_RELATIONS = """
SELECT c.relname, c.relkind::text, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.classid = 'pg_class'::regclass
                  AND d.refclassid = 'pg_class'::regclass AND d.deptype = 'a')
"""
_COLUMNS = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       coalesce(pg_get_expr(d.adbin, d.adrelid), ''), a.attidentity::text, a.attgenerated::text
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
"""
_CONSTRAINTS = """
SELECT c.relname, k.conname, k.contype::text, pg_get_constraintdef(k.oid)
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(s)s
"""
_INDEXES = """
SELECT i.relname, pg_get_indexdef(x.indexrelid)
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = %(s)s
"""
_TRIGGERS = """
SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid)
FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(s)s AND NOT t.tgisinternal
"""
_FUNCTIONS = """
SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', md5(pg_get_functiondef(p.oid))
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = %(s)s AND p.prokind IN ('f', 'p')
"""
_POLICIES = """
SELECT tablename, policyname, permissive, array_to_string(roles, ','), cmd, coalesce(qual, ''), coalesce(with_check, '')
FROM pg_policies WHERE schemaname = %(s)s
"""
_SEQUENCES = """
SELECT c.relname, format_type(s.seqtypid, NULL), s.seqstart, s.seqincrement, s.seqmax, s.seqmin
FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(s)s
"""
_VIEWS = "SELECT viewname, definition FROM pg_views WHERE schemaname = %(s)s"

_SPACE = re.compile(r"\s+")


def _norm(text: object) -> str:
    return _SPACE.sub(" ", str(text)).strip()


def snapshot(cur, *, schema: str = SCHEMA) -> dict[str, str]:
    """The fingerprint of `schema` as seen by `cur`. The cursor's role needs SELECT on the seeded tables it reads.

    The definitions printed by the catalogue functions depend on the session search_path (names in the search path are printed
    unqualified), so the path is fixed for the duration and restored afterwards."""
    cur.execute("SELECT current_setting('search_path')")
    saved = cur.fetchone()[0]
    cur.execute("SELECT set_config('search_path', %s, false)", (f"{schema}, public",))
    try:
        return _snapshot(cur, schema)
    finally:
        cur.execute("SELECT set_config('search_path', %s, false)", (saved,))


def _snapshot(cur, schema: str) -> dict[str, str]:
    p = {"s": schema}
    out: dict[str, str] = {}

    def run(sql: str):
        cur.execute(sql, p)
        return cur.fetchall()

    for rel, kind, rls, force in run(_RELATIONS):
        out[f"relation|{rel}"] = kind
        if kind in ("r", "p"):
            out[f"rls|{rel}"] = f"{rls}|{force}"
    for rel, col, typ, notnull, default, identity, generated in run(_COLUMNS):
        # A serial/identity default names a sequence; the sequence is its own element, so the default text is kept as written.
        out[f"column|{rel}.{col}"] = _norm(f"{typ} notnull={notnull} default={default} identity={identity} generated={generated}")
    for rel, name, kind, definition in run(_CONSTRAINTS):
        out[f"constraint|{rel}.{name}"] = _norm(f"{kind} {definition}")
    for name, definition in run(_INDEXES):
        out[f"index|{name}"] = _norm(definition)
    for rel, name, definition in run(_TRIGGERS):
        out[f"trigger|{rel}.{name}"] = _norm(definition)
    for signature, digest in run(_FUNCTIONS):
        out[f"function|{signature}"] = digest
    for rel, name, permissive, roles, cmd, qual, check in run(_POLICIES):
        out[f"policy|{rel}.{name}"] = _norm(f"{permissive} {roles} {cmd} using={qual} check={check}")
    for name, typ, start, inc, mx, mn in run(_SEQUENCES):
        out[f"sequence|{name}"] = _norm(f"{typ} start={start} inc={inc} max={mx} min={mn}")
    for name, definition in run(_VIEWS):
        out[f"view|{name}"] = _norm(definition)

    present = {r[0] for r in run("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                 "WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p')")}
    for table, columns in SEED_TABLES:
        if table not in present:
            continue
        have = {r[0] for r in run("SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                                  f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %(s)s AND c.relname = '{table}' "
                                  "AND a.attnum > 0 AND NOT a.attisdropped")}
        if not set(columns) <= have:
            continue
        cols = ", ".join(f'"{c}"::text' for c in columns)
        cur.execute(f'SELECT {cols} FROM "{schema}"."{table}"')
        for row in cur.fetchall():
            out[f"seed|{table}|" + "|".join(row)] = "present"
    return {k: v for k, v in out.items() if not _is_operator(k)}


def _is_operator(key: str) -> bool:
    kind, _, rest = key.partition("|")
    name = rest.split(".", 1)[0]
    if kind == "index":
        return name.startswith(tuple(f"{t}_" for t in OPERATOR_TABLES))
    return name in OPERATOR_TABLES or (kind == "constraint" and name.split(".")[0] in OPERATOR_TABLES)


def diff(expected: dict[str, str], live: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """(missing, different, extra) identities of `live` against `expected`, each sorted."""
    missing = sorted(k for k in expected if k not in live)
    different = sorted(k for k in expected if k in live and expected[k] != live[k])
    extra = sorted(k for k in live if k not in expected)
    return missing, different, extra
