"""Proof of the V2 failed-description retry rule (routers/jobs.py: retry_blocked_by_description, the claim SQL, the refusal).

The rule: a rejected description (REQUIREMENTS_REJECTED) is retried only when its stored hash is a well-formed SHA-256 digest that
differs from the current normalised description. Every other failure stays retryable. The database check runs the real claim SQL as
the tenant login, for every stored-value shape, so a NULL, empty, malformed or uppercase value cannot open the claim.
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import realdb_helper as H  # noqa: E402
from routers import jobs as J  # noqa: E402
from services import criteria_status as CS  # noqa: E402
from workers.criteria_worker import _normalize_description_hash  # noqa: E402
from security_fixtures import connect  # noqa: E402
from test_requirements_v2_routing_postgres import JD, _job, _tenant  # noqa: E402

REJECTED = CS.REQUIREMENTS_REJECTED_MESSAGE
INSUFFICIENT = CS.INSUFFICIENT_MESSAGE
WORKER_FAILED = CS.WORKER_FAILED_MESSAGE
CURRENT = _normalize_description_hash(JD)
OTHER = _normalize_description_hash(JD + " A different requirement the rejected version did not state.")


@pytest.fixture(scope="module")
def built():
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


# ---- A. the rule and the normaliser, as pure functions ----

@pytest.mark.parametrize("error, stored, blocked", [
    (REJECTED, None, True),                 # never recorded: not evidence of a change
    (REJECTED, "", True),                   # empty
    (REJECTED, "deadbeef", True),           # malformed: too short
    (REJECTED, CURRENT.upper(), True),      # uppercase digest: not produced by the normaliser
    (REJECTED, CURRENT[:63], True),         # 63 characters
    (REJECTED, CURRENT + "0", True),        # 65 characters
    (REJECTED, "g" * 64, True),             # not hexadecimal
    (REJECTED, " " + OTHER, True),          # leading space: not a digest
    (REJECTED, CURRENT, True),              # the same description: unchanged
    (REJECTED, OTHER, False),               # a well-formed digest of a different description: changed
    (INSUFFICIENT, CURRENT, True),          # legacy: the same description is refused
    (INSUFFICIENT, OTHER, False),
    (INSUFFICIENT, None, False),            # legacy: no stored value does not refuse, as before
    (WORKER_FAILED, CURRENT, False),        # transient failures stay retryable whatever the stored hash
    (WORKER_FAILED, None, False),
    (WORKER_FAILED, "", False),
    (None, CURRENT, False),                 # no failure recorded
])
def test_the_rule_for_each_failure_and_stored_value(error, stored, blocked):
    assert J.retry_blocked_by_description(error, stored, CURRENT) is blocked


def test_the_normaliser_ignores_case_and_whitespace_and_nothing_else():
    assert _normalize_description_hash("  Senior\tPython\n developer  ") == _normalize_description_hash("senior python developer")
    assert _normalize_description_hash("Senior Python developer") != _normalize_description_hash("Senior Python developers")
    assert _normalize_description_hash("a, b") != _normalize_description_hash("a b")
    assert _normalize_description_hash("") == hashlib.sha256(b"").hexdigest()
    assert _normalize_description_hash(None) == hashlib.sha256(b"").hexdigest()


def test_the_normaliser_output_is_always_a_well_formed_digest():
    for text in ("", "x", "Ünïcödé  ", "tab\tand\nnewline", "x" * 10000):
        assert J._SHA256_HEX.match(_normalize_description_hash(text))


def test_a_cosmetic_change_is_the_same_description_for_the_rule():
    cosmetic = _normalize_description_hash(JD.upper().replace(" ", "   "))
    assert cosmetic == CURRENT
    assert J.retry_blocked_by_description(REJECTED, CURRENT, cosmetic) is True


# ---- B. the real claim SQL, as the tenant login, for every stored-value shape ----



def _claim(built, tenant, job, description_hash: str):
    """Run the claim as cv_app with the tenant set, exactly as the route binds it (SQLAlchemy named parameters). The transaction is
    rolled back, so the row is unchanged afterwards. Returns the claimed retry count, or None when the claim matched no row."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    engine = create_engine(f"postgresql+psycopg2://cv_app:{built.runtime_passwords['cv_app']}@127.0.0.1:{built.cluster.port}/{built.name}",
                           poolclass=NullPool, connect_args={"options": "-csearch_path=cv_analyzer,public"})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": tenant["tenant"]})
            row = conn.execute(text(J._CLAIM_RETRY_V2_SQL), {
                "jid": job, "tid": tenant["tenant"], "max_retries": 3, "cancelled_message": CS.EXTRACTION_CANCELLED_MESSAGE,
                "rejected_message": REJECTED, "description_hash": description_hash}).first()
            conn.rollback()
            return row[0] if row else None
    finally:
        engine.dispose()


def _set_failure(built, job, error, stored):
    cur = built.conn.cursor()
    cur.execute("UPDATE job_criteria SET criteria_extraction_status = 'failed', criteria_extraction_error = %s,"
                " criteria_last_failed_description_hash = %s WHERE job_id = %s", (error, stored, job))


@pytest.mark.parametrize("label, stored, claimable", [
    ("null", None, False),
    ("empty", "", False),
    ("malformed", "deadbeef", False),
    ("uppercase digest of the current description", CURRENT.upper(), False),
    ("64 non-hex characters", "g" * 64, False),
    ("63 characters of the current digest", CURRENT[:63], False),
    ("65 characters", CURRENT + "0", False),
    ("leading space before a valid other digest", " " + OTHER, False),
    ("the current description's digest", CURRENT, False),
    ("a different well-formed digest", OTHER, True),
])
def test_a_rejected_row_is_claimed_only_when_the_stored_digest_proves_a_change(built, label, stored, claimable):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2)
    _set_failure(built, job, REJECTED, stored)
    result = _claim(built, tenant, job, CURRENT)
    assert (result is not None) is claimable, label
    if claimable:
        assert result == 1
    else:
        cur = built.conn.cursor()
        cur.execute("SELECT criteria_extraction_status, criteria_extraction_retry_count FROM job_criteria WHERE job_id = %s", (job,))
        assert cur.fetchone() == ("failed", 0), f"{label}: a refused claim changed the row"


@pytest.mark.parametrize("stored", [None, "", "deadbeef", CURRENT, OTHER])
def test_a_transient_failure_is_claimable_whatever_its_stored_digest(built, stored):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2)
    _set_failure(built, job, WORKER_FAILED, stored)
    assert _claim(built, tenant, job, CURRENT) == 1


def test_the_claim_and_the_python_rule_agree_for_every_shape(built):
    """The SQL and retry_blocked_by_description must give the same answer, or the derived status and the claim disagree."""
    shapes = [None, "", "deadbeef", CURRENT.upper(), "g" * 64, CURRENT[:63], CURRENT + "0", " " + OTHER, CURRENT, OTHER]
    for stored in shapes:
        tenant = _tenant(built)
        job = _job(built, tenant, marker=2)
        _set_failure(built, job, REJECTED, stored)
        claimed = _claim(built, tenant, job, CURRENT) is not None
        assert claimed is (not J.retry_blocked_by_description(REJECTED, stored, CURRENT)), repr(stored)


def test_a_changed_description_permits_the_retry_and_the_old_text_stays_refused(built):
    """Stored digest of the rejected text. The current text changed: permitted. Changed back to the rejected text: refused again."""
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2)
    _set_failure(built, job, REJECTED, CURRENT)
    assert _claim(built, tenant, job, OTHER) == 1
    # The claim rolled back, so the row still holds CURRENT. Record the other text as the rejected one, then retry the current text.
    _set_failure(built, job, REJECTED, OTHER)
    assert _claim(built, tenant, job, CURRENT) == 1
    _set_failure(built, job, REJECTED, CURRENT)
    assert _claim(built, tenant, job, CURRENT) is None


def test_the_derived_status_blocks_exactly_what_the_claim_refuses(built):
    """The API shows retry_blocked_reason = description_unchanged only where the claim refuses."""
    for stored in (None, "deadbeef", CURRENT, OTHER):
        blocked = J.retry_blocked_by_description(REJECTED, stored, CURRENT)
        tenant = _tenant(built)
        job = _job(built, tenant, marker=2)
        _set_failure(built, job, REJECTED, stored)
        assert (_claim(built, tenant, job, CURRENT) is None) is blocked, repr(stored)


def test_the_retry_refusal_message_matches_the_refused_state(built):
    """A refused rejected description gets the 'update the description' answer, not a conflict, whatever the stored value."""
    state = {"criteria_extraction_status": "failed", "criteria_extraction_error": REJECTED,
             "criteria_extraction_retry_count": 0, "criteria_last_failed_description_hash": None}
    refusal = J._retry_refusal(state, 3, CURRENT)
    assert refusal.status_code == 400
    assert "update the job description" in refusal.detail.lower()
    state["criteria_last_failed_description_hash"] = OTHER
    assert J._retry_refusal(state, 3, CURRENT).status_code == 409
