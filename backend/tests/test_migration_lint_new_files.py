"""Migration lint and operator-template checks.

The lint applies only to migrations numbered 095 and later. Earlier files are historical: they are checked for byte-identity
against the recorded checksums instead, and they are never linted, so that legacy content cannot create false failures."""
from __future__ import annotations

import json
import pathlib
import re

import pytest

import realdb_helper as H
from db.runner import runner
from db.runner.ledger import LEDGER_DDL
from db.runner.manifest import MANIFEST
from db.runner.sqltext import mask_comments

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND / "db" / "migrations"
OPS = BACKEND / "db" / "ops"
LEGACY = json.loads((BACKEND / "db" / "runner" / "legacy_checksums.json").read_text(encoding="utf-8"))

NEW_FROM = "095"
DB0_FILES = ("095_repair_010_platform_control.sql", "096_repair_070_sla_policy_index.sql",
             "097_repair_087_knockout_answer_tenant.sql", "098_application_scores_requirements_revision.sql")
# Hardening migrations: functions and policies are allowed, and each is reviewed by name. DB0 files keep their stricter rules.
HARDENING_FILES = ("099_platform_secret_accessors.sql", "100_runtime_rls_identity.sql")

FORBIDDEN_IN_NEW_MIGRATIONS = {
    "role creation": re.compile(r"\bCREATE\s+(ROLE|USER)\b", re.I),
    "login change": re.compile(r"\b(NO)?LOGIN\b", re.I),
    "grant all": re.compile(r"\bGRANT\s+ALL\b", re.I),
}

# SECURITY DEFINER is allowed only in the reviewed files below, and only where each such function sets a fixed search_path.
SECURITY_DEFINER_ALLOWED = frozenset({"099_platform_secret_accessors.sql"})


def _new_migrations() -> list[pathlib.Path]:
    return sorted(p for p in MIGRATIONS.glob("*.sql") if p.name[:3] >= NEW_FROM)


def test_legacy_migrations_are_byte_identical_to_their_recorded_checksums():
    import hashlib
    for name, recorded in LEGACY.items():
        assert hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest() == recorded, name


def test_recorded_legacy_set_is_exactly_the_files_before_095():
    assert sorted(LEGACY) == sorted(p.name for p in MIGRATIONS.glob("*.sql") if p.name[:3] < NEW_FROM)


def test_manifest_lists_every_migration_file_exactly_once():
    names = [e.file for e in MANIFEST]
    assert len(names) == len(set(names))
    assert sorted(names) == sorted(p.name for p in MIGRATIONS.glob("*.sql"))


def test_lint_scope_is_exactly_the_files_numbered_095_and_later():
    assert [p.name for p in _new_migrations()] == sorted(DB0_FILES + HARDENING_FILES)


@pytest.mark.parametrize("label", list(FORBIDDEN_IN_NEW_MIGRATIONS))
def test_new_migrations_contain_no_forbidden_construct(label):
    pattern = FORBIDDEN_IN_NEW_MIGRATIONS[label]
    for path in _new_migrations():
        code = mask_comments(path.read_text(encoding="utf-8"))
        assert not pattern.search(code), f"{path.name}: {label}"


def test_db0_migrations_grant_and_revoke_nothing():
    for name in DB0_FILES:
        code = mask_comments((MIGRATIONS / name).read_text(encoding="utf-8"))
        assert not re.search(r"\b(GRANT|REVOKE)\b", code, re.I), name


def test_db0_migrations_create_no_function_and_no_policy():
    for name in DB0_FILES:
        code = mask_comments((MIGRATIONS / name).read_text(encoding="utf-8"))
        assert not re.search(r"\b(CREATE\s+(OR\s+REPLACE\s+)?FUNCTION|CREATE\s+POLICY|ENABLE\s+ROW\s+LEVEL)\b", code, re.I), name


def test_ledger_ddl_in_the_runner_matches_the_ops_template():
    template = (OPS / "0001_roles_and_ledger.sql.template").read_text(encoding="utf-8")
    start = template.index("CREATE TABLE cv_analyzer.schema_migrations")
    end = template.index(");", start) + 2
    normalise = lambda s: re.sub(r"\s+", " ", s).strip().rstrip(";")
    assert normalise(template[start:end]) == normalise(LEDGER_DDL)


@pytest.mark.parametrize("name", ["0001_roles_and_ledger.sql.template", "0002_ownership_transfer.sql.template",
                                  "0003_ledger_baseline.sql.template"])
def test_operator_templates_contain_no_password_and_no_reassign_owned(name):
    """Only executable text is checked: the templates explain in comments why REASSIGN OWNED is never emitted."""
    text = (OPS / name).read_text(encoding="utf-8")
    code = mask_comments(text)
    assert not re.search(r"PASSWORD\s+'", code)
    assert "REASSIGN" not in code.upper()


def test_operator_templates_are_not_migrations():
    assert not any(p.suffix == ".sql" for p in OPS.iterdir())
    assert all(p.name.endswith(".template") for p in OPS.iterdir())


def test_bootstrap_template_creates_no_runtime_role():
    text = (OPS / "0001_roles_and_ledger.sql.template").read_text(encoding="utf-8")
    created = re.findall(r"CREATE\s+ROLE\s+(\w+)", text)
    assert created == ["cv_migrator", "cv_migration_operator"]


def test_bootstrap_template_runs_on_a_database_without_a_ledger_and_yields_owned_ledger():
    from test_db0_migration_runner import empty_database
    template = (OPS / "0001_roles_and_ledger.sql.template").read_text(encoding="utf-8")
    script = "\n".join(line for line in template.splitlines() if not line.startswith("\\set"))
    script = script.replace(":'migration_operator_password'", "'disposable-test-only'")
    with empty_database() as conn:
        conn.cursor().execute(script)
        cur = conn.cursor()
        cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'cv_analyzer.schema_migrations'::regclass")
        assert cur.fetchone()[0] == "cv_migrator"
        cur.execute("SELECT rolinherit, rolcanlogin FROM pg_roles WHERE rolname = 'cv_migration_operator'")
        assert cur.fetchone() == (False, True)
        cur.execute("SELECT rolcanlogin, rolsuper FROM pg_roles WHERE rolname = 'cv_migrator'")
        assert cur.fetchone() == (False, False)


def test_generated_ownership_statements_cover_every_cv_analyzer_object_and_nothing_is_reassigned():
    from scripts.generate_ownership_transfer import generate
    with H.built_chain() as db:
        cur = db.conn.cursor()
        cur.execute("CREATE ROLE cv_migrator NOLOGIN")
        lines = generate(db.conn, "cv_migrator")
        statements = [line for line in lines if not line.startswith("--")]
        assert not any("REASSIGN" in line.upper() for line in lines)
        # The owner is always emitted as a quoted identifier (psycopg2.sql.Identifier).
        assert statements[0] == 'ALTER SCHEMA cv_analyzer OWNER TO "cv_migrator";'
        assert any(line.startswith('ALTER TABLE cv_analyzer.schema_migrations OWNER TO "cv_migrator";') for line in lines)
        # Apply the statements in a transaction that is then rolled back: every relation must end up owned by the new role.
        cur.execute("BEGIN")
        for line in statements:
            cur.execute(line.split("  --")[0])
        cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','p','S','v','m')
                         AND pg_get_userbyid(c.relowner) <> 'cv_migrator'""")
        assert cur.fetchone()[0] == 0
        cur.execute("ROLLBACK")


def test_generator_refuses_an_owner_name_that_is_not_a_plain_identifier():
    from scripts.generate_ownership_transfer import GenerationError, generate
    with H.built_chain() as db:
        with pytest.raises(GenerationError):
            generate(db.conn, "cv_migrator; DROP SCHEMA cv_analyzer")


def test_runner_rejects_an_owner_role_name_that_is_not_a_plain_identifier(tmp_path):
    from test_db0_migration_runner import _entries, _write_dir, empty_database
    files = {"001_a.sql": "SELECT 1;"}
    d = _write_dir(tmp_path, files)
    with empty_database() as conn:
        with pytest.raises(runner.ManifestError):
            runner.run(conn, allow_fresh=True, directory=d, entries=_entries(files), owner_role="x; drop")


def test_security_definer_is_used_only_in_reviewed_files_with_a_fixed_search_path():
    for path in _new_migrations():
        body = mask_comments(path.read_text(encoding="utf-8"))
        if not re.search(r"SECURITY\s+DEFINER", body, re.I):
            continue
        assert path.name in SECURITY_DEFINER_ALLOWED, f"{path.name} uses SECURITY DEFINER but is not a reviewed file"
        functions = re.split(r"CREATE\s+OR\s+REPLACE\s+FUNCTION", body, flags=re.I)[1:]
        assert functions, f"{path.name}: SECURITY DEFINER outside a function definition"
        for fn in functions:
            if re.search(r"SECURITY\s+DEFINER", fn, re.I):
                assert re.search(r"SET\s+search_path\s*=", fn, re.I), f"{path.name}: SECURITY DEFINER without SET search_path"
