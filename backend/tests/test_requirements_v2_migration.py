"""Migration 106 (requirements-v2 safeguards): static checks always run; the constraint behavior is exercised on a real
PostgreSQL when the optional `pgserver` package is installed (it is not a project dependency; those tests skip
otherwise). The migration is NEVER applied to a real environment by these tests: they build synthetic tables in a
throw-away database."""
from __future__ import annotations

import importlib
import pathlib
import re
import sys
import tempfile
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = BACKEND / "db" / "migrations"
MIGRATION = MIGRATIONS / "106_requirements_v2_safeguards.sql"
SCHEMA = BACKEND / "db" / "schema.sql"


def sql_without_comments(path: pathlib.Path) -> str:
    return "\n".join(line.split("--")[0] for line in path.read_text(encoding="utf-8").splitlines())


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def balanced(text: str, start: int) -> str:
    """The text inside the parenthesis opening at text[start] == '('."""
    depth = 0
    for i in range(start, len(text)):
        depth += text[i] == "("
        depth -= text[i] == ")"
        if depth == 0:
            return text[start + 1:i]
    raise AssertionError("unbalanced")


def legacy_weights_expression() -> str:
    schema = SCHEMA.read_text(encoding="utf-8")
    i = schema.index("CONSTRAINT weights_sum_100 CHECK")
    return balanced(schema, schema.index("(", i))


def stopped_reasons(path: pathlib.Path) -> set[str]:
    sql = sql_without_comments(path)
    i = sql.index("CHECK (stopped_reason IN (")
    block = balanced(sql, sql.index("(", sql.index("IN", i)))
    return set(re.findall(r"'([a-z_]+)'", block))


# ══ static checks (always run) ════════════════════════════════════════════════

class TestMigrationText:

    def test_exists_and_is_numbered_after_the_latest_migration(self):
        numbers = sorted(int(p.name[:3]) for p in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        # 107 (editing-API revision columns) is the only migration allowed after 106
        assert MIGRATION.exists() and numbers.count(106) == 1 and numbers[numbers.index(106):] in ([106], [106, 107])

    def test_is_transactional_and_idempotent_in_style(self):
        sql = sql_without_comments(MIGRATION)
        assert sql.strip().startswith("BEGIN;") and sql.strip().endswith("COMMIT;")
        assert "ADD COLUMN IF NOT EXISTS requirements_schema_version SMALLINT NULL" in squash(sql)
        assert squash(sql).count("DROP CONSTRAINT IF EXISTS") == 3

    def test_changes_schema_only_no_data_and_no_destructive_statements(self):
        sql = squash(sql_without_comments(MIGRATION)).upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "DROP TABLE", "DROP COLUMN", "DEFAULT"):
            assert forbidden not in sql, forbidden

    def test_the_marker_has_no_default_and_only_allows_null_or_two(self):
        sql = squash(sql_without_comments(MIGRATION))
        assert "requirements_schema_version IS NULL OR requirements_schema_version = 2" in sql

    def test_legacy_branch_is_exactly_the_existing_weight_rule(self):
        legacy = squash(legacy_weights_expression())
        assert squash(sql_without_comments(MIGRATION)).count(legacy) >= 1 or all(
            term in squash(sql_without_comments(MIGRATION))
            for term in ("weight_skills + weight_experience + weight_education + weight_certifications",
                         "weight_soft_skills + weight_domain_knowledge + weight_other = 100"))
        assert "= 100" in legacy and "weight_other" in legacy

    def test_v2_branch_is_null_safe(self):
        sql = squash(sql_without_comments(MIGRATION))
        assert "requirements_schema_version IS NOT NULL AND" in sql

    def test_stopped_reason_list_is_a_strict_superset_of_migration_104(self):
        old = stopped_reasons(MIGRATIONS / "104_scoring_method.sql")
        new = stopped_reasons(MIGRATION)
        assert new == old | {"evaluation_unsupported"} and "evaluation_unsupported" not in old

    def test_stopped_reason_matches_the_value_the_code_writes(self):
        from services.requirements_guard import STOPPED_REASON
        assert STOPPED_REASON in stopped_reasons(MIGRATION)


# ══ constraint behavior on a real PostgreSQL (optional) ═══════════════════════

@pytest.fixture(scope="module")
def pg_server():
    pgserver = pytest.importorskip("pgserver")
    pytest.importorskip("psycopg2")
    server = pgserver.get_server(tempfile.mkdtemp(prefix="req_v2_pg_"))
    yield server
    server.cleanup()


def _synthetic_schema_sql() -> str:
    """Minimal job_criteria / applications fixtures that reuse the REAL constraint definitions from schema.sql and
    migration 104, so the test fails if those drift."""
    reasons_104 = ", ".join(f"'{r}'" for r in sorted(stopped_reasons(MIGRATIONS / "104_scoring_method.sql")))
    return f"""
        CREATE SCHEMA cv_analyzer;
        SET search_path = cv_analyzer;
        CREATE TABLE job_criteria (
            criteria_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
            weight_skills SMALLINT NOT NULL DEFAULT 30,
            weight_experience SMALLINT NOT NULL DEFAULT 25,
            weight_education SMALLINT NOT NULL DEFAULT 15,
            weight_certifications SMALLINT NOT NULL DEFAULT 10,
            weight_soft_skills SMALLINT NOT NULL DEFAULT 10,
            weight_domain_knowledge SMALLINT NOT NULL DEFAULT 5,
            weight_other SMALLINT NOT NULL DEFAULT 5,
            analysis_json JSONB,
            criteria_extraction_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            CONSTRAINT weights_sum_100 CHECK ({legacy_weights_expression()})
        );
        CREATE TABLE applications (
            application_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id UUID NOT NULL,
            stopped_reason VARCHAR(50),
            CONSTRAINT applications_stopped_reason_check CHECK (stopped_reason IN ({reasons_104}))
        );
        CREATE TABLE application_scores (
            application_id UUID NOT NULL,
            llm_match_results_json JSONB,
            det_final_score INTEGER,
            weights_snapshot JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """


class Db:
    def __init__(self, conn):
        self.conn = conn

    def run(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def rejects(self, sql: str, params=None) -> bool:
        import psycopg2
        try:
            self.run(sql, params)
        except psycopg2.errors.CheckViolation:
            return True
        return False

    def apply_migration(self):
        self.run(MIGRATION.read_text(encoding="utf-8"))
        self.run("SET search_path = cv_analyzer")


@pytest.fixture
def db(pg_server):
    import psycopg2
    name = "t_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(pg_server.get_uri())
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    conn = psycopg2.connect(pg_server.get_uri(name))
    conn.autocommit = True
    d = Db(conn)
    d.run(_synthetic_schema_sql())
    yield d
    conn.close()
    admin.cursor().execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


INSERT_WEIGHTS = ("INSERT INTO job_criteria (weight_skills, weight_experience, weight_education, weight_certifications, "
                  "weight_soft_skills, weight_domain_knowledge, weight_other{extra}) VALUES "
                  "(%s, %s, %s, %s, %s, %s, %s{extra_val})")


def insert(db: Db, weights, marker="__omit__"):
    if marker == "__omit__":
        sql = INSERT_WEIGHTS.format(extra="", extra_val="")
        return db.run(sql, weights)
    sql = INSERT_WEIGHTS.format(extra=", requirements_schema_version", extra_val=", %s")
    return db.run(sql, [*weights, marker])


ZERO = (0, 0, 0, 0, 0, 0, 0)
HUNDRED = (30, 25, 15, 10, 10, 5, 5)
FIFTY = (10, 10, 10, 10, 5, 5, 0)
OVER = (30, 25, 15, 10, 10, 5, 6)
UNDER = (30, 25, 15, 10, 10, 5, 4)


class TestConstraintsOnPostgres:

    def test_fixture_reproduces_the_existing_legacy_rule(self, db):
        insert(db, HUNDRED)
        for bad in (ZERO, OVER, UNDER):
            assert db.rejects(INSERT_WEIGHTS.format(extra="", extra_val=""), bad), bad

    def test_existing_rows_are_untouched_and_marker_is_nullable_without_default(self, db):
        insert(db, HUNDRED)
        insert(db, (20, 20, 20, 20, 10, 5, 5))
        before = db.run("SELECT criteria_id, job_id, weight_skills, weight_other FROM job_criteria ORDER BY 1")
        db.apply_migration()
        assert db.run("SELECT criteria_id, job_id, weight_skills, weight_other FROM job_criteria ORDER BY 1") == before
        assert db.run("SELECT count(*) FROM job_criteria WHERE requirements_schema_version IS NOT NULL") == [(0,)]
        col = db.run("SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                     "WHERE table_schema='cv_analyzer' AND table_name='job_criteria' "
                     "AND column_name='requirements_schema_version'")
        assert col == [("smallint", "YES", None)]

    def test_legacy_rows_keep_the_exact_100_rule_including_all_zero_with_null_marker(self, db):
        db.apply_migration()
        insert(db, HUNDRED)                                           # marker omitted -> NULL
        insert(db, HUNDRED, None)
        for bad in (ZERO, OVER, UNDER, FIFTY):
            assert db.rejects(INSERT_WEIGHTS.format(extra="", extra_val=""), bad), f"legacy accepted {bad}"
            assert insert_rejected_with_marker(db, bad, None), f"explicit NULL marker accepted {bad}"

    def test_v2_rows_accept_100_for_weighted_and_0_for_preferred_only_or_incomplete(self, db):
        db.apply_migration()
        insert(db, HUNDRED, 2)
        insert(db, ZERO, 2)
        assert db.run("SELECT count(*) FROM job_criteria WHERE requirements_schema_version = 2") == [(2,)]

    def test_v2_rows_reject_any_other_total(self, db):
        db.apply_migration()
        for bad in (FIFTY, OVER, UNDER):
            assert insert_rejected_with_marker(db, bad, 2), bad

    def test_only_null_or_2_are_valid_markers(self, db):
        db.apply_migration()
        for marker in (1, 3, 0, -1):
            assert insert_rejected_with_marker(db, HUNDRED, marker), marker
            assert insert_rejected_with_marker(db, ZERO, marker), marker

    def test_updating_a_legacy_row_to_all_zero_weights_is_still_rejected(self, db):
        db.apply_migration()
        insert(db, HUNDRED)
        assert db.rejects("UPDATE job_criteria SET weight_skills=0, weight_experience=0, weight_education=0, "
                          "weight_certifications=0, weight_soft_skills=0, weight_domain_knowledge=0, weight_other=0")

    def test_a_v2_row_can_move_between_weighted_and_empty_states(self, db):
        db.apply_migration()
        insert(db, ZERO, 2)
        db.run("UPDATE job_criteria SET weight_skills=60, weight_experience=40 WHERE requirements_schema_version = 2")
        db.run("UPDATE job_criteria SET weight_skills=0, weight_experience=0 WHERE requirements_schema_version = 2")

    def test_stopped_reason_accepts_the_new_value_and_every_old_one_but_nothing_else(self, db):
        db.apply_migration()
        for reason in stopped_reasons(MIGRATION) | {None}:
            db.run("INSERT INTO applications (job_id, stopped_reason) VALUES (gen_random_uuid(), %s)", (reason,))
        assert db.rejects("INSERT INTO applications (job_id, stopped_reason) VALUES (gen_random_uuid(), 'bogus')")

    def test_before_the_migration_the_new_stopped_reason_is_rejected(self, db):
        assert db.rejects("INSERT INTO applications (job_id, stopped_reason) "
                          "VALUES (gen_random_uuid(), 'evaluation_unsupported')")

    def test_migration_is_idempotent(self, db):
        insert(db, HUNDRED)
        db.apply_migration()
        db.apply_migration()
        insert(db, ZERO, 2)
        assert db.run("SELECT count(*) FROM job_criteria") == [(2,)]

    def test_a_failure_inside_the_migration_leaves_nothing_half_applied(self, db):
        """The migration is one transaction: when a statement fails (here: re-adding the weight rule over a legacy
        row that totals 101), the already-run ALTER ... ADD COLUMN is rolled back with it."""
        insert(db, HUNDRED)
        db.run("ALTER TABLE job_criteria DROP CONSTRAINT weights_sum_100")
        db.run("UPDATE job_criteria SET weight_skills = 31")                   # that row now totals 101
        import psycopg2
        with pytest.raises(psycopg2.Error):
            db.apply_migration()
        db.run("ROLLBACK")                       # what psql does when the script stops on the error
        db.run("SET search_path = cv_analyzer")
        cols = db.run("SELECT count(*) FROM information_schema.columns WHERE table_schema='cv_analyzer' "
                      "AND table_name='job_criteria' AND column_name='requirements_schema_version'")
        assert cols == [(0,)]                                                 # the column was rolled back too


def insert_rejected_with_marker(db: Db, weights, marker) -> bool:
    sql = INSERT_WEIGHTS.format(extra=", requirements_schema_version", extra_val=", %s")
    return db.rejects(sql, [*weights, marker])


# ══ the guard's SQL against a real PostgreSQL, before and after the migration ═

class TestGuardSqlOnPostgres:

    def _seed(self, db: Db):
        """legacy job, job whose analysis carries a v2 block (no marker column needed), plus applications and scores."""
        legacy, shape_v2 = uuid.uuid4(), uuid.uuid4()
        db.run("INSERT INTO job_criteria (job_id, analysis_json) VALUES (%s, %s::jsonb)",
               (str(legacy), '{"skills": {"required": []}}'))
        db.run("INSERT INTO job_criteria (job_id, analysis_json) VALUES (%s, %s::jsonb)",
               (str(shape_v2), '{"requirements": {"schema_version": 2, "categories": {}}}'))
        ids = {}
        for label, job in (("legacy", legacy), ("shape_v2", shape_v2)):
            app = uuid.uuid4()
            ids[label] = str(app)
            db.run("INSERT INTO applications (application_id, job_id) VALUES (%s, %s)", (str(app), str(job)))
            db.run("INSERT INTO application_scores (application_id, llm_match_results_json, weights_snapshot) "
                   "VALUES (%s, '{}'::jsonb, '{}'::jsonb)", (str(app),))
        return ids, legacy, shape_v2

    def test_marker_lookup_returns_null_before_the_column_exists_and_the_value_after(self, db):
        from services.requirements_guard import MARKER_SQL
        job = uuid.uuid4()
        db.run("INSERT INTO job_criteria (job_id) VALUES (%s)", (str(job),))
        assert db.run(f"SELECT {MARKER_SQL} FROM job_criteria jc") == [(None,)]            # no column yet: no error
        db.apply_migration()
        assert db.run(f"SELECT {MARKER_SQL} FROM job_criteria jc") == [(None,)]
        db.run("UPDATE job_criteria SET requirements_schema_version = 2")
        assert db.run(f"SELECT {MARKER_SQL} FROM job_criteria jc") == [("2",)]

    @pytest.mark.parametrize("migrated", [False, True])
    def test_backfill_selects_only_legacy_job_rows(self, db, migrated):
        from psycopg2.extras import RealDictCursor
        from types import SimpleNamespace
        sys.path.insert(0, str(BACKEND / "scripts"))
        try:
            backfill = importlib.import_module("backfill_deterministic_scores")
        finally:
            sys.path.remove(str(BACKEND / "scripts"))
        ids, legacy, shape_v2 = self._seed(db)
        if migrated:
            db.apply_migration()
            marker_app = uuid.uuid4()
            marker_job = uuid.uuid4()
            db.run("INSERT INTO job_criteria (job_id, requirements_schema_version, weight_skills, weight_experience, "
                   "weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other) "
                   "VALUES (%s, 2, 0, 0, 0, 0, 0, 0, 0)", (str(marker_job),))
            db.run("INSERT INTO applications (application_id, job_id) VALUES (%s, %s)", (str(marker_app), str(marker_job)))
            db.run("INSERT INTO application_scores (application_id, llm_match_results_json, weights_snapshot) "
                   "VALUES (%s, '{}'::jsonb, '{}'::jsonb)", (str(marker_app),))
        conn = SimpleNamespace(cursor=lambda: db.conn.cursor(cursor_factory=RealDictCursor))

        pending = [str(i) for i in backfill._fetch_pending_ids(conn, None)]
        assert pending == [ids["legacy"]]
        assert backfill._count_pending(conn) == 1
        assert backfill._fetch_row(conn, ids["legacy"]) is not None
        assert backfill._fetch_row(conn, ids["shape_v2"]) is None
        assert backfill._count_requirements_v2_skipped(conn) == (2 if migrated else 1)
