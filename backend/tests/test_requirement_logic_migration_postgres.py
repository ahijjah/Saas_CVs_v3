"""Migration 105 on a real PostgreSQL 16: additive and idempotent, the new labels accepted and nothing else, existing evaluations untouched, and a
rollback that removes exactly what 105 added and loses no result."""
from __future__ import annotations

import pathlib

import psycopg2
import pytest

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from test_candidate_matrix_migration_postgres import _evaluation
from test_item_scoring_migration_postgres import _run, _seed

BACKEND = pathlib.Path(__file__).resolve().parent.parent
UP = (BACKEND / "db/migrations/105_requirement_logic_evaluations.sql").read_text(encoding="utf-8")
DOWN = (BACKEND / "db/rollback/105_requirement_logic_evaluations.down.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


def test_the_migration_is_idempotent_and_every_new_column_is_null_for_existing_rows(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    _evaluation(built, tenant, app, run, "req_old", "Downgraded to unclear: no quotation from the CV could be verified (the model said met).", reason="no_verified_evidence")
    cur = built.conn.cursor()
    cur.execute(UP)
    cur.execute(UP)
    cur.execute("SELECT matched_option, option_results, review_reason FROM application_item_evaluations WHERE item_id = 'req_old'")
    assert cur.fetchone() == (None, None, "no_verified_evidence")


def test_the_two_new_labels_are_accepted_and_nothing_else(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    for label in ("conditional_alternative", "ambiguous_requirement"):
        _evaluation(built, tenant, app, run, f"req_{label}", "x", reason=label)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _evaluation(built, tenant, app, run, "req_bad", "x", reason="guess")


def test_option_results_must_be_a_bounded_json_array(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    eid = _evaluation(built, tenant, app, run, "req_opts", "x")
    cur = built.conn.cursor()
    cur.execute("UPDATE application_item_evaluations SET matched_option = 'A', option_results = '[{\"option_id\": \"o1\"}]' WHERE evaluation_id = %s", (eid,))
    for bad in ('{"a": 1}', '"text"', '5'):
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("UPDATE application_item_evaluations SET option_results = %s WHERE evaluation_id = %s", (bad, eid))


def test_rollback_removes_exactly_what_105_added_and_keeps_every_result(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    eid = _evaluation(built, tenant, app, run, "req_keep", "x", reason="conditional_alternative")
    cur = built.conn.cursor()
    cur.execute("UPDATE application_item_evaluations SET matched_option = 'A', option_results = '[]' WHERE evaluation_id = %s", (eid,))
    cur.execute(DOWN)
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'application_item_evaluations' AND column_name IN ('matched_option', 'option_results')")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT result, review_reason FROM application_item_evaluations WHERE evaluation_id = %s", (eid,))
    assert cur.fetchone() == ("unclear", None)                                              # the result stays, only the new label is lost
    with pytest.raises(psycopg2.errors.CheckViolation):
        _evaluation(built, tenant, app, run, "req_after", "x", reason="conditional_alternative")
    cur.execute(DOWN)                                                                        # a second run changes nothing
    cur.execute(UP)                                                                          # and the upgrade can be applied again
    cur.execute("SELECT matched_option FROM application_item_evaluations WHERE evaluation_id = %s", (eid,))
    assert cur.fetchone() == (None,)
