"""Requirements-v2 storage schema (migration 093) on a real PostgreSQL 16, built from the full current migration chain.

Covers the schema version marker, the revision counter, the retired-item-id array, the version-aware weight rule, and tenant
isolation under the existing job_criteria row-level security. No routes, services, workers or settings are involved."""
from __future__ import annotations

import uuid

import psycopg2
import psycopg2.errors
import pytest

from realdb_helper import (EXPECTED_PRE_EXISTING_FAILURES, Database, built_chain, check_chain, unavailable_reason)

MIGRATION = "093_requirements_v2_storage_schema.sql"
PROBE_ROLE = "cv_storage_probe"


@pytest.fixture(scope="module")
def db():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as built:
        check_chain(built.failures)                      # the failing steps are exactly the known set; 093 and schema.sql succeed
        cur = built.conn.cursor()
        cur.execute(f"CREATE ROLE {PROBE_ROLE} NOLOGIN")
        cur.execute(f"GRANT USAGE ON SCHEMA cv_analyzer TO {PROBE_ROLE}")
        cur.execute(f"GRANT SELECT, UPDATE ON cv_analyzer.job_criteria, cv_analyzer.jobs TO {PROBE_ROLE}")
        yield built


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


@pytest.fixture()
def jobs(db):
    """Two tenants, each with one user and one job. Each job has its default job_criteria row (weights total 100, marker NULL)."""
    cur = db.conn.cursor()
    out = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]           # unique per test: the fixture is function-scoped
        tenant = _one(cur, "INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                      (f"tenant {label} {tag}", f"{label}-{tag}.example"))[0]
        user = _one(cur, "INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                         "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                    (tenant, f"{label}-{tag}@example.test"))[0]
        job = _one(cur, "INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd') RETURNING job_id",
                   (tenant, user))[0]
        cur.execute("INSERT INTO job_criteria (job_id) VALUES (%s)", (job,))
        out[label] = {"tenant": tenant, "job": job}
    return out


def _set_weights(cur, job, total, marker=None, column="weight_skills"):
    """Set a total by putting it in one weight and zeroing the others (the seven weight columns are all NOT NULL)."""
    cols = ["weight_skills", "weight_experience", "weight_education", "weight_certifications",
            "weight_soft_skills", "weight_domain_knowledge", "weight_other"]
    values = {c: (total if c == column else 0) for c in cols}
    assignments = ", ".join(f"{c} = %s" for c in cols) + ", requirements_schema_version = %s"
    cur.execute(f"UPDATE job_criteria SET {assignments} WHERE job_id = %s", [values[c] for c in cols] + [marker, job])


def _rejected(cur, fn) -> bool:
    """True when `fn` is refused by a CHECK constraint. The statement runs in autocommit, so a refusal leaves nothing behind."""
    try:
        fn()
    except psycopg2.errors.CheckViolation:
        return True
    return False


# ---- migration applied on the complete chain ----

def test_the_real_chain_fails_only_at_the_known_steps(db):
    """Exactly the expected pre-existing failures, and nothing else: schema.sql and migration 093 both succeeded."""
    assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES


def test_migration_093_is_applied_on_the_complete_chain(db):
    assert not [f for f in db.failures if f.file.startswith("093_")], db.failures
    cur = db.conn.cursor()
    assert _one(cur, "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'cv_analyzer'"
                     " AND table_name = 'job_criteria' AND column_name IN ('requirements_schema_version',"
                     " 'requirements_revision', 'requirements_retired_item_ids')")[0] == 3


# ---- schema version marker ----

@pytest.mark.parametrize("marker", [None, 2])
def test_allowed_schema_versions_are_null_and_two(db, jobs, marker):
    cur = db.conn.cursor()
    _set_weights(cur, jobs["a"]["job"], 100, marker=marker)
    assert _one(cur, "SELECT requirements_schema_version FROM job_criteria WHERE job_id = %s", (jobs["a"]["job"],))[0] == marker


@pytest.mark.parametrize("marker", [0, 1, 3])
def test_other_schema_versions_are_rejected(db, jobs, marker):
    cur = db.conn.cursor()
    assert _rejected(cur, lambda: _set_weights(cur, jobs["a"]["job"], 100, marker=marker))


# ---- legacy weight behaviour is unchanged ----

@pytest.mark.parametrize("total", [100])
def test_legacy_total_of_one_hundred_is_accepted(db, jobs, total):
    cur = db.conn.cursor()
    _set_weights(cur, jobs["a"]["job"], total, marker=None)


@pytest.mark.parametrize("total", [0, 1, 99, 101])
def test_legacy_totals_other_than_one_hundred_are_rejected(db, jobs, total):
    cur = db.conn.cursor()
    assert _rejected(cur, lambda: _set_weights(cur, jobs["a"]["job"], total, marker=None))


# ---- version 2 weight totals ----

@pytest.mark.parametrize("total", [100, 0])
def test_v2_totals_of_one_hundred_and_zero_are_accepted(db, jobs, total):
    cur = db.conn.cursor()
    _set_weights(cur, jobs["a"]["job"], total, marker=2)


@pytest.mark.parametrize("total", [1, 99, 101])
def test_v2_invalid_totals_are_rejected(db, jobs, total):
    cur = db.conn.cursor()
    assert _rejected(cur, lambda: _set_weights(cur, jobs["a"]["job"], total, marker=2))


# ---- revision and retired ids ----

def test_revision_zero_is_accepted_and_negative_is_rejected(db, jobs):
    cur = db.conn.cursor()
    job = jobs["a"]["job"]
    cur.execute("UPDATE job_criteria SET requirements_revision = 0 WHERE job_id = %s", (job,))
    assert _rejected(cur, lambda: cur.execute("UPDATE job_criteria SET requirements_revision = -1 WHERE job_id = %s", (job,)))


def test_retired_item_ids_default_to_an_empty_array_and_must_be_an_array(db, jobs):
    cur = db.conn.cursor()
    job = jobs["a"]["job"]
    assert _one(cur, "SELECT requirements_retired_item_ids FROM job_criteria WHERE job_id = %s", (job,))[0] == []
    cur.execute("UPDATE job_criteria SET requirements_retired_item_ids = %s::jsonb WHERE job_id = %s", ('["req_1", "req_2"]', job))
    for bad in ('{"req_1": true}', '"req_1"', "null", "7"):
        assert _rejected(cur, lambda b=bad: cur.execute(
            "UPDATE job_criteria SET requirements_retired_item_ids = %s::jsonb WHERE job_id = %s", (b, job))), bad


# ---- tenant isolation under the existing row-level security ----

def test_tenant_isolation_still_applies_to_the_new_columns(db, jobs):
    """A non-owner role sees and changes only its own tenant's criteria rows, as before."""
    cur = db.conn.cursor()
    own, other = jobs["a"], jobs["b"]
    cur.execute("UPDATE job_criteria SET requirements_revision = 5 WHERE job_id = %s", (other["job"],))
    cur.execute(f"SET ROLE {PROBE_ROLE}")
    try:
        cur.execute("SELECT set_config('app.current_role', 'admin', false), set_config('app.current_tenant_id', %s, false)",
                    (str(own["tenant"]),))
        cur.execute("SELECT count(*) FROM job_criteria WHERE job_id = %s", (other["job"],))
        assert cur.fetchone()[0] == 0, "another tenant's criteria row must be invisible"
        cur.execute("UPDATE job_criteria SET requirements_revision = 9 WHERE job_id = %s", (other["job"],))
        assert cur.rowcount == 0, "another tenant's criteria row must not be updatable"
        cur.execute("SELECT count(*) FROM job_criteria WHERE job_id = %s", (own["job"],))
        assert cur.fetchone()[0] == 1
    finally:
        cur.execute("RESET ROLE")
        cur.execute("SELECT set_config('app.current_role', '', false), set_config('app.current_tenant_id', '', false)")
    assert _one(cur, "SELECT requirements_revision FROM job_criteria WHERE job_id = %s", (other["job"],))[0] == 5


def test_the_existing_tenant_policy_on_job_criteria_is_unchanged(db):
    cur = db.conn.cursor()
    assert _one(cur, "SELECT count(*) FROM pg_policies WHERE schemaname = 'cv_analyzer' AND tablename = 'job_criteria'"
                     " AND policyname = 'tenant_isolation_job_criteria'")[0] == 1
    assert _one(cur, "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                     " WHERE n.nspname = 'cv_analyzer' AND c.relname = 'job_criteria'")[0] is True
