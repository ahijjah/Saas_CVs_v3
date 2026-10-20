"""Legacy criteria status (L1), offline: the fixed messages, the status shown to the API, the intake blocking set, and the reachability
scans that keep every legacy writer guarded and every applications INSERT naming its submission_source. No database."""
from __future__ import annotations

import pathlib
import re

import pytest

from services import criteria_status as CS

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _sources(suffix: str):
    """Backend sources to scan. SQL comment lines are removed, so prose about a statement is never read as a statement."""
    for path in BACKEND.rglob(f"*{suffix}"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel or rel.startswith("venv"):
            continue
        text = path.read_text(encoding="utf-8")
        if suffix == ".sql":
            text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("--"))
        yield rel, text


# A legacy writer carries the predicate literally, or as the {LEGACY_ONLY_PREDICATE} placeholder of an f-string whose value is
# this constant. Either form is a guard; a statement with neither is an unguarded writer.
GUARD_FORMS = (CS.LEGACY_ONLY_PREDICATE, "{LEGACY_ONLY_PREDICATE}")


def _guarded(window: str) -> bool:
    return any(form in window for form in GUARD_FORMS)


def _sql_window(source: str, start: int) -> str:
    """The SQL from `start` to the end of its statement: the closing triple quote or the closing call parenthesis."""
    ends = [i for i in (source.find('"""', start), source.find('")', start)) if i != -1]
    return source[start:min(ends)]


# ---- the fixed messages and the stored states ----

def test_the_three_fixed_messages_are_exact():
    assert CS.INSUFFICIENT_MESSAGE == (
        "The job description does not contain enough specific requirements. Update it and retry.")
    assert CS.ENQUEUE_FAILED_MESSAGE == "Criteria extraction could not be started. Retry later."
    assert CS.WORKER_FAILED_MESSAGE == "Criteria extraction failed. Retry later."


def test_the_stale_threshold_is_thirty_minutes():
    assert CS.STALE_PROCESSING_MINUTES == 30


# ---- the status the API reports, derived without a write ----

@pytest.mark.parametrize("stored", ["pending", "processing", "completed"])
def test_a_stored_status_other_than_failed_is_reported_unchanged(stored):
    assert CS.display_status(stored, None, 0, 3) == stored


def test_failed_with_the_insufficient_message_is_reported_as_insufficient():
    assert CS.display_status("failed", CS.INSUFFICIENT_MESSAGE, 0, 3) == "insufficient"


def test_failed_with_another_message_is_reported_as_failed():
    assert CS.display_status("failed", CS.WORKER_FAILED_MESSAGE, 1, 3) == "failed"
    assert CS.display_status("failed", CS.ENQUEUE_FAILED_MESSAGE, 0, 3) == "failed"


def test_failed_at_the_retry_cap_is_reported_as_blocked_and_takes_precedence():
    assert CS.display_status("failed", CS.WORKER_FAILED_MESSAGE, 3, 3) == "blocked"
    assert CS.display_status("failed", CS.INSUFFICIENT_MESSAGE, 3, 3) == "blocked"


def test_the_blocking_set_refuses_failed_and_the_in_flight_statuses():
    assert {"pending", "processing", "failed"} <= CS.CRITERIA_BLOCKING_STATUSES
    assert "completed" not in CS.CRITERIA_BLOCKING_STATUSES


def test_intake_uses_the_shared_blocking_set_and_no_private_copy():
    source = (BACKEND / "services" / "application_intake_service.py").read_text(encoding="utf-8")
    assert "_INTAKE_BLOCKING_STATUSES = CRITERIA_BLOCKING_STATUSES" in source
    assert 'frozenset({"pending", "processing"' not in source


# ---- reachability: every legacy UPDATE of job_criteria is guarded ----

def test_every_update_of_job_criteria_carries_the_legacy_predicate_or_is_the_v2_writer():
    seen = 0
    for rel, source in _sources(".py"):
        for match in re.finditer(r"UPDATE\s+job_criteria\b", source):
            seen += 1
            window = _sql_window(source, match.start())
            if rel == "workers/requirements_v2_worker.py":
                # The requirements-v2 worker writes only its own rows: every statement carries the v2 marker, never the legacy one.
                assert "requirements_schema_version = 2" in window, rel
                continue
            if rel == "services/requirements_edit.py":
                # The requirements-v2 writer guards on its own version (the other direction). It is not a legacy writer.
                assert "requirements_schema_version = :schema_version" in window, rel
                continue
            if _guarded(window) or "REQUIREMENTS_V2_PREDICATE" in window or "requirements_schema_version = 2" in window:
                continue                                # legacy-guarded, or a v2-only statement (it cannot touch a legacy row)
            raise AssertionError(f"{rel}: UPDATE job_criteria without the legacy predicate")
    # The scan must see the real writers (worker, the legacy PUT routes, the retry route, the requirements-v2 writer).
    assert seen >= 9, seen


def test_the_scan_detects_an_unguarded_legacy_writer():
    # Self-check of the scanner: an unguarded statement is found, and a guarded one is not reported.
    unguarded = 'text("UPDATE job_criteria SET criteria_extraction_status = \'x\' WHERE job_id = :jid")'
    guarded = 'text(f"""UPDATE job_criteria SET x = 1 WHERE job_id = :jid AND {LEGACY_ONLY_PREDICATE}""")'
    placeholder = "{LEGACY_ONLY_PREDICATE}"
    assert not _guarded(_sql_window(unguarded, re.search(r"UPDATE\s+job_criteria\b", unguarded).start()))
    assert _guarded(_sql_window(guarded, re.search(r"UPDATE\s+job_criteria\b", guarded).start()))
    assert placeholder in guarded


# ---- applications INSERTs and score-pending ----

def test_every_insert_into_applications_names_submission_source():
    seen = 0
    for suffix in (".py", ".sql"):
        for rel, source in _sources(suffix):
            for match in re.finditer(r"INSERT\s+INTO\s+(?:cv_analyzer\.)?applications\b", source):
                seen += 1
                columns = source[match.start(): source.index("VALUES", match.start())]
                assert "submission_source" in columns, f"{rel}: INSERT INTO applications omits submission_source"
    assert seen == 2, seen


def test_score_pending_claims_only_manual_uploads():
    source = (BACKEND / "routers" / "applications.py").read_text(encoding="utf-8")
    claim = source[source.index("WITH claimed AS ("): source.index("RETURNING application_id", source.index("WITH claimed AS ("))]
    assert "AND submission_source = 'manual_upload'" in claim
    assert "candidate_reuse" not in claim


def test_the_copy_route_names_candidate_reuse_and_the_criteria_gate():
    route = (BACKEND / "routers" / "candidate_tags.py").read_text(encoding="utf-8")
    service = (BACKEND / "services" / "application_reuse_service.py").read_text(encoding="utf-8")
    assert "'candidate_reuse', 'awaiting_review', FALSE" in service
    assert "reuse_cv_for_job(" in route
    assert 'current_user.role not in REUSE_ROLES' in route
    # the locked, authoritative checks live in the service, inside the writing transaction
    assert 'target["status"] != "active"' in service
    assert "CRITERIA_BLOCKING_STATUSES" in service
    assert "FOR UPDATE" in service and "FOR SHARE" in service
    assert 'REUSE_ROLES = REQUIREMENTS_EDITOR_ROLES' in route


def test_the_retry_route_checks_the_exact_role_before_any_sql():
    source = (BACKEND / "routers" / "jobs.py").read_text(encoding="utf-8")
    body = source[source.index("async def retry_criteria_extraction"):]
    guard = body.index("current_user.role not in CRITERIA_RETRY_ROLES")
    assert guard < body.index("set_rls_context")
    assert guard < body.index("text(f\"\"\"")
    assert 'CRITERIA_RETRY_ROLES = REQUIREMENTS_EDITOR_ROLES' in source


# ---- the migration text ----

def test_migration_094_widens_the_check_and_drops_the_default_without_a_column():
    sql = (BACKEND / "db" / "migrations" / "094_candidate_reuse_submission_source.sql").read_text(encoding="utf-8")
    assert "'candidate_reuse'" in sql
    assert "ALTER COLUMN submission_source DROP DEFAULT" in sql
    assert "ADD COLUMN" not in sql.upper()
    assert "SET DEFAULT" not in sql.upper()
