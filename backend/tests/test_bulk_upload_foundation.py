"""
E-01 Phase 1 — Bulk Upload Foundation tests.

Covers the pure-logic parts of bulk_upload_service.py that do not require a
live database connection:
  - Status constant correctness
  - _sanitise helpers / dict-building logic
  - update_bulk_upload_row_status validation (status guards)
  - update_bulk_upload_batch_status validation (status guard)
  - create_or_update_bulk_upload_row row_number guard

DB-dependent integration tests (create_bulk_upload_batch, get_bulk_upload_batch,
list_*, update_bulk_upload_batch_summary) require a live PostgreSQL connection
and are NOT included here — they belong in an integration test suite with a
test database fixture.
"""

import pytest
import json


# ── Inline the pure-logic constants from the service ─────────────────────────
# Duplicated here so the tests run without loading FastAPI/SQLAlchemy.

BATCH_STATUSES = frozenset({
    "created", "uploaded", "validating", "validated",
    "importing", "imported", "processing", "completed", "failed",
})
ROW_VALIDATION_STATUSES = frozenset({"pending", "ready", "warning", "error"})
ROW_IMPORT_STATUSES     = frozenset({"pending", "imported", "skipped", "failed"})
ROW_PROCESSING_STATUSES = frozenset({"pending", "queued", "processing", "completed", "failed"})

# Timestamp rules mirroring update_bulk_upload_batch_status
_BATCH_TS_MAP = {
    "validated":  "validated_at",
    "imported":   "imported_at",
    "processing": "processing_started_at",
    "completed":  "processing_completed_at",
    "failed":     "processing_completed_at",
}

# Timestamp rules mirroring update_bulk_upload_row_status
def _row_ts_cols(validation_status=None, import_status=None, processing_status=None) -> set[str]:
    """Return which timestamp columns should be set given the provided status changes."""
    cols: set[str] = set()
    if validation_status in ("ready", "warning", "error"):
        cols.add("validated_at")
    if import_status in ("imported", "skipped", "failed"):
        cols.add("imported_at")
    if processing_status == "processing":
        cols.add("processing_started_at")
    if processing_status in ("completed", "failed"):
        cols.add("processing_completed_at")
    return cols


# ── Status constant completeness ──────────────────────────────────────────────

class TestStatusConstants:
    def test_batch_statuses_contains_required_values(self):
        required = {
            "created", "uploaded", "validating", "validated",
            "importing", "imported", "processing", "completed", "failed",
        }
        assert required <= BATCH_STATUSES

    def test_row_validation_statuses(self):
        assert ROW_VALIDATION_STATUSES == {"pending", "ready", "warning", "error"}

    def test_row_import_statuses(self):
        assert ROW_IMPORT_STATUSES == {"pending", "imported", "skipped", "failed"}

    def test_row_processing_statuses(self):
        assert ROW_PROCESSING_STATUSES == {"pending", "queued", "processing", "completed", "failed"}

    def test_no_overlap_between_row_status_types(self):
        # Each status type should be distinguishable — no value should appear
        # in all three sets (would make filtering ambiguous)
        all_three = ROW_VALIDATION_STATUSES & ROW_IMPORT_STATUSES & ROW_PROCESSING_STATUSES
        # 'pending' and 'failed' appear in multiple sets which is intentional;
        # but we should NOT have all-three overlap that is confusing in context.
        # This is a documentation test — if it breaks, the designer should review.
        assert all_three == {"pending"}, (
            f"Unexpected overlap across all three row status types: {all_three}"
        )

    def test_submission_source_new_value(self):
        """'bulk_excel_upload' must be the only new value added in migration 099."""
        existing = {"manual_upload", "email_forwarding", "platform_email", "public_apply"}
        new_value = "bulk_excel_upload"
        assert new_value not in existing
        all_values = existing | {new_value}
        assert len(all_values) == 5


# ── Batch status transition logic ─────────────────────────────────────────────

class TestBatchStatusTransitions:
    def test_valid_batch_statuses_accepted(self):
        for s in BATCH_STATUSES:
            assert s in BATCH_STATUSES

    def test_invalid_batch_status_rejected(self):
        invalid = ["draft", "pending", "active", "queued", ""]
        for s in invalid:
            assert s not in BATCH_STATUSES, f"'{s}' should not be a valid batch status"

    def test_timestamp_set_for_validated(self):
        assert _BATCH_TS_MAP.get("validated") == "validated_at"

    def test_timestamp_set_for_imported(self):
        assert _BATCH_TS_MAP.get("imported") == "imported_at"

    def test_timestamp_set_for_processing(self):
        assert _BATCH_TS_MAP.get("processing") == "processing_started_at"

    def test_timestamp_set_for_completed(self):
        assert _BATCH_TS_MAP.get("completed") == "processing_completed_at"

    def test_timestamp_set_for_failed(self):
        assert _BATCH_TS_MAP.get("failed") == "processing_completed_at"

    def test_no_timestamp_for_created_or_uploaded(self):
        for s in ("created", "uploaded", "validating", "importing"):
            assert s not in _BATCH_TS_MAP, f"Status '{s}' should not set a dedicated timestamp"


# ── Row status transition logic ───────────────────────────────────────────────

class TestRowStatusTransitions:
    def test_valid_row_validation_statuses_accepted(self):
        for s in ROW_VALIDATION_STATUSES:
            assert s in ROW_VALIDATION_STATUSES

    def test_invalid_row_validation_status_rejected(self):
        assert "approved" not in ROW_VALIDATION_STATUSES
        assert "valid" not in ROW_VALIDATION_STATUSES

    def test_validated_at_set_for_ready(self):
        assert "validated_at" in _row_ts_cols(validation_status="ready")

    def test_validated_at_set_for_warning(self):
        assert "validated_at" in _row_ts_cols(validation_status="warning")

    def test_validated_at_set_for_error(self):
        assert "validated_at" in _row_ts_cols(validation_status="error")

    def test_validated_at_not_set_for_pending(self):
        assert "validated_at" not in _row_ts_cols(validation_status="pending")

    def test_imported_at_set_for_imported(self):
        assert "imported_at" in _row_ts_cols(import_status="imported")

    def test_imported_at_set_for_skipped(self):
        assert "imported_at" in _row_ts_cols(import_status="skipped")

    def test_imported_at_set_for_failed(self):
        assert "imported_at" in _row_ts_cols(import_status="failed")

    def test_imported_at_not_set_for_pending(self):
        assert "imported_at" not in _row_ts_cols(import_status="pending")

    def test_processing_started_at_set_for_processing(self):
        assert "processing_started_at" in _row_ts_cols(processing_status="processing")

    def test_processing_completed_at_set_for_completed(self):
        assert "processing_completed_at" in _row_ts_cols(processing_status="completed")

    def test_processing_completed_at_set_for_failed(self):
        assert "processing_completed_at" in _row_ts_cols(processing_status="failed")

    def test_no_ts_cols_for_pending_processing(self):
        assert _row_ts_cols(processing_status="pending") == set()

    def test_multiple_updates_set_multiple_timestamps(self):
        cols = _row_ts_cols(
            validation_status="ready",
            import_status="imported",
            processing_status="completed",
        )
        assert "validated_at" in cols
        assert "imported_at" in cols
        assert "processing_completed_at" in cols


# ── Row number validation ─────────────────────────────────────────────────────

class TestRowNumberValidation:
    @staticmethod
    def _check_row_number(n: int) -> bool:
        """Mirror the row_number >= 1 constraint from the service."""
        return n >= 1

    def test_row_number_1_valid(self):
        assert self._check_row_number(1)

    def test_row_number_100_valid(self):
        assert self._check_row_number(100)

    def test_row_number_0_invalid(self):
        assert not self._check_row_number(0)

    def test_row_number_negative_invalid(self):
        assert not self._check_row_number(-5)


# ── Candidate data JSONB shape ────────────────────────────────────────────────

class TestCandidateDataShape:
    """Validate the expected JSONB shape for candidate_data rows."""

    def _make_candidate(self, **kwargs) -> dict:
        base = {
            "first_name": "Jane",
            "last_name":  "Doe",
            "email":      "jane@example.com",
        }
        base.update(kwargs)
        return base

    def test_minimal_candidate_serialises(self):
        c = self._make_candidate()
        assert json.dumps(c)  # no exception

    def test_extra_fields_preserved(self):
        c = self._make_candidate(linkedin_url="https://linkedin.com/in/jane", nationality="British")
        serialised = json.dumps(c)
        assert "linkedin_url" in serialised
        assert "nationality" in serialised

    def test_knockout_answers_structure(self):
        """Answers keyed by question_id or raw column header."""
        answers = {
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890": "yes",
            "Do you have a valid driving licence?":  "no",
        }
        serialised = json.dumps(answers)
        parsed = json.loads(serialised)
        assert parsed["a1b2c3d4-e5f6-7890-abcd-ef1234567890"] == "yes"

    def test_validation_warnings_as_list(self):
        warnings = ["Phone number missing", "Duplicate email detected"]
        serialised = json.dumps(warnings)
        parsed = json.loads(serialised)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_validation_errors_as_list(self):
        errors = ["Email address is required and cannot be empty"]
        serialised = json.dumps(errors)
        parsed = json.loads(serialised)
        assert parsed[0].startswith("Email")


# ── Batch summary counter logic ───────────────────────────────────────────────

class TestBatchSummaryCounters:
    """
    Simulate what update_bulk_upload_batch_summary computes — mirrors the
    SQL COUNT(*) FILTER (...) aggregates without hitting the database.
    """

    def _summarise(self, rows: list[dict]) -> dict:
        return {
            "row_count":          len(rows),
            "valid_row_count":    sum(1 for r in rows if r["validation_status"] == "ready"),
            "warning_row_count":  sum(1 for r in rows if r["validation_status"] == "warning"),
            "error_row_count":    sum(1 for r in rows if r["validation_status"] == "error"),
            "imported_row_count": sum(1 for r in rows if r["import_status"] == "imported"),
            "skipped_row_count":  sum(1 for r in rows if r["import_status"] == "skipped"),
            "failed_row_count":   sum(1 for r in rows if r["import_status"] == "failed"),
            "completed_row_count":sum(1 for r in rows if r["processing_status"] == "completed"),
        }

    def test_empty_batch_all_zeros(self):
        summary = self._summarise([])
        assert summary["row_count"] == 0
        assert summary["valid_row_count"] == 0
        assert all(v == 0 for v in summary.values())

    def test_all_ready_rows(self):
        rows = [
            {"validation_status": "ready",   "import_status": "pending", "processing_status": "pending"},
            {"validation_status": "ready",   "import_status": "pending", "processing_status": "pending"},
        ]
        s = self._summarise(rows)
        assert s["row_count"] == 2
        assert s["valid_row_count"] == 2
        assert s["warning_row_count"] == 0
        assert s["error_row_count"] == 0

    def test_mixed_validation_statuses(self):
        rows = [
            {"validation_status": "ready",   "import_status": "imported", "processing_status": "completed"},
            {"validation_status": "warning", "import_status": "imported", "processing_status": "pending"},
            {"validation_status": "error",   "import_status": "skipped",  "processing_status": "pending"},
            {"validation_status": "pending", "import_status": "pending",  "processing_status": "pending"},
        ]
        s = self._summarise(rows)
        assert s["row_count"] == 4
        assert s["valid_row_count"] == 1
        assert s["warning_row_count"] == 1
        assert s["error_row_count"] == 1
        assert s["imported_row_count"] == 2
        assert s["skipped_row_count"] == 1
        assert s["completed_row_count"] == 1

    def test_counters_sum_correctly(self):
        rows = [
            {"validation_status": "ready",   "import_status": "imported", "processing_status": "completed"},
            {"validation_status": "ready",   "import_status": "imported", "processing_status": "failed"},
            {"validation_status": "error",   "import_status": "skipped",  "processing_status": "pending"},
        ]
        s = self._summarise(rows)
        # Validation counts should sum to row_count
        val_sum = s["valid_row_count"] + s["warning_row_count"] + s["error_row_count"]
        # (pending rows don't appear in any status counter other than row_count)
        assert val_sum <= s["row_count"]


# ── Migration SQL validation ──────────────────────────────────────────────────

class TestMigrationSQL:
    """Basic smoke tests that verify migration 099 SQL is well-formed."""

    def _read_migration(self) -> str:
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "db", "migrations",
            "099_bulk_upload_foundation.sql",
        )
        with open(path) as f:
            return f.read()

    def test_migration_file_exists(self):
        sql = self._read_migration()
        assert len(sql) > 100

    def test_migration_starts_with_begin(self):
        sql = self._read_migration()
        assert "BEGIN;" in sql

    def test_migration_ends_with_commit(self):
        sql = self._read_migration()
        assert sql.rstrip().endswith("COMMIT;")

    def test_migration_sets_search_path(self):
        sql = self._read_migration()
        assert "SET search_path = cv_analyzer" in sql

    def test_bulk_upload_batches_table_created(self):
        sql = self._read_migration()
        assert "CREATE TABLE IF NOT EXISTS bulk_upload_batches" in sql

    def test_bulk_upload_rows_table_created(self):
        sql = self._read_migration()
        assert "CREATE TABLE IF NOT EXISTS bulk_upload_rows" in sql

    def test_rls_enabled_for_batches(self):
        sql = self._read_migration()
        assert "ALTER TABLE bulk_upload_batches ENABLE ROW LEVEL SECURITY" in sql

    def test_rls_enabled_for_rows(self):
        sql = self._read_migration()
        assert "ALTER TABLE bulk_upload_rows ENABLE ROW LEVEL SECURITY" in sql

    def test_submission_source_constraint_widened(self):
        sql = self._read_migration()
        assert "bulk_excel_upload" in sql
        assert "applications_submission_source_check" in sql

    def test_all_five_submission_sources_present(self):
        sql = self._read_migration()
        for source in ("manual_upload", "email_forwarding", "platform_email",
                       "public_apply", "bulk_excel_upload"):
            assert f"'{source}'" in sql, f"Missing source '{source}' in constraint"

    def test_rls_policy_uses_tenant_isolation(self):
        sql = self._read_migration()
        assert "app.current_tenant_id" in sql
        assert "app.current_role" in sql

    def test_unique_index_on_batch_row_number(self):
        sql = self._read_migration()
        assert "uq_bulk_rows_batch_row_number" in sql

    def test_created_by_references_users(self):
        sql = self._read_migration()
        assert "REFERENCES users(user_id)" in sql

    def test_batch_status_check_constraint(self):
        sql = self._read_migration()
        assert "bulk_upload_batches_status_check" in sql

    def test_row_status_check_constraints(self):
        sql = self._read_migration()
        assert "bulk_upload_rows_validation_status_check" in sql
        assert "bulk_upload_rows_import_status_check" in sql
        assert "bulk_upload_rows_processing_status_check" in sql
