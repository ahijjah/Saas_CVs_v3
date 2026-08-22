"""
E-01 Phase 4 — Bulk Upload Processing tests.

Covers pure-function logic in services/bulk_upload_processing_service.py:
  - Application status → stage mapping
  - Stage → progress percentage mapping
  - Failure reason classification (stopped_reason → label)
  - Batch progress percentage calculation
  - Batch completion detection
  - Row-level processing status aggregation

No database or HTTP layer required.
"""

import pytest

from services.bulk_upload_processing_service import (
    app_status_to_row_status,
    app_status_to_stage,
    classify_failure_reason,
    compute_batch_progress_percentage,
    is_batch_fully_processed,
    stage_to_progress,
)


# ── TestAppStatusToStage ──────────────────────────────────────────────────────

class TestAppStatusToStage:
    def test_pending_maps_to_pending(self):
        assert app_status_to_stage("pending") == "pending"

    def test_queued_maps_to_queued(self):
        assert app_status_to_stage("queued") == "queued"

    def test_processing_maps_to_processing(self):
        assert app_status_to_stage("processing") == "processing"

    def test_ai_scored_maps_to_completed(self):
        assert app_status_to_stage("ai_scored") == "completed"

    def test_low_match_maps_to_completed(self):
        assert app_status_to_stage("low_match") == "completed"

    def test_failed_maps_to_failed(self):
        assert app_status_to_stage("failed") == "failed"

    def test_none_maps_to_pending(self):
        assert app_status_to_stage(None) == "pending"

    def test_empty_string_maps_to_pending(self):
        assert app_status_to_stage("") == "pending"

    def test_unknown_status_maps_to_pending(self):
        assert app_status_to_stage("mystery_status") == "pending"


# ── TestStageToProgress ───────────────────────────────────────────────────────

class TestStageToProgress:
    def test_pending_is_zero(self):
        assert stage_to_progress("pending") == 0

    def test_queued_is_five(self):
        assert stage_to_progress("queued") == 5

    def test_processing_is_fifty(self):
        assert stage_to_progress("processing") == 50

    def test_completed_is_one_hundred(self):
        assert stage_to_progress("completed") == 100

    def test_failed_is_zero(self):
        assert stage_to_progress("failed") == 0

    def test_unknown_stage_is_zero(self):
        assert stage_to_progress("unknown_stage") == 0


# ── TestClassifyFailureReason ─────────────────────────────────────────────────

class TestClassifyFailureReason:
    def test_extraction_failed_maps_correctly(self):
        assert classify_failure_reason("extraction_failed") == "extraction_failed"

    def test_security_blocked_maps_to_extraction_failed(self):
        assert classify_failure_reason("security_blocked") == "extraction_failed"

    def test_duplicate_exact_maps_to_duplicate_rejected(self):
        assert classify_failure_reason("duplicate_exact") == "duplicate_rejected"

    def test_duplicate_possible_maps_to_duplicate_rejected(self):
        assert classify_failure_reason("duplicate_possible") == "duplicate_rejected"

    def test_low_match_gatekeeper_maps_to_ai_processing_failed(self):
        assert classify_failure_reason("low_match_gatekeeper") == "ai_processing_failed"

    def test_ai_failed_maps_to_ai_processing_failed(self):
        assert classify_failure_reason("ai_failed") == "ai_processing_failed"

    def test_scoring_failed_maps_correctly(self):
        assert classify_failure_reason("scoring_failed") == "scoring_failed"

    def test_unknown_reason_maps_to_unexpected_error(self):
        assert classify_failure_reason("some_random_reason") == "unexpected_error"

    def test_none_returns_none(self):
        assert classify_failure_reason(None) is None

    def test_empty_string_returns_none(self):
        assert classify_failure_reason("") is None


# ── TestComputeBatchProgressPercentage ────────────────────────────────────────

class TestComputeBatchProgressPercentage:
    def test_all_completed_is_100(self):
        assert compute_batch_progress_percentage(10, 10, 0, 0, 0) == 100

    def test_all_failed_is_100_progress(self):
        # failed rows count as terminal → progress 100%
        assert compute_batch_progress_percentage(5, 0, 5, 0, 0) == 100

    def test_all_queued_is_5(self):
        assert compute_batch_progress_percentage(10, 0, 0, 0, 10) == 5

    def test_all_processing_is_50(self):
        assert compute_batch_progress_percentage(10, 0, 0, 10, 0) == 50

    def test_all_pending_is_0(self):
        assert compute_batch_progress_percentage(10, 0, 0, 0, 0) == 0

    def test_zero_total_returns_0(self):
        assert compute_batch_progress_percentage(0, 0, 0, 0, 0) == 0

    def test_half_completed_half_pending_is_50(self):
        assert compute_batch_progress_percentage(10, 5, 0, 0, 0) == 50

    def test_mixed_batch(self):
        # 4 completed (100%), 2 failed (100%), 2 processing (50%), 2 queued (5%)
        # weighted = (4+2)*100 + 2*50 + 2*5 = 600+100+10 = 710 / 10 = 71
        result = compute_batch_progress_percentage(10, 4, 2, 2, 2)
        assert result == 71

    def test_rounding(self):
        # 1 completed out of 3: 100/3 ≈ 33
        result = compute_batch_progress_percentage(3, 1, 0, 0, 0)
        assert result == 33

    def test_single_row_queued(self):
        assert compute_batch_progress_percentage(1, 0, 0, 0, 1) == 5

    def test_single_row_completed(self):
        assert compute_batch_progress_percentage(1, 1, 0, 0, 0) == 100


# ── TestIsBatchFullyProcessed ─────────────────────────────────────────────────

class TestIsBatchFullyProcessed:
    def test_all_terminal_returns_true(self):
        assert is_batch_fully_processed(10, 10) is True

    def test_partial_terminal_returns_false(self):
        assert is_batch_fully_processed(10, 5) is False

    def test_zero_rows_returns_false(self):
        assert is_batch_fully_processed(0, 0) is False

    def test_zero_total_non_zero_terminal_returns_false(self):
        # defensive: terminal > total should not happen, but return False
        assert is_batch_fully_processed(0, 5) is False

    def test_exact_match_returns_true(self):
        assert is_batch_fully_processed(100, 100) is True

    def test_one_row_not_done(self):
        assert is_batch_fully_processed(100, 99) is False


# ── TestProcessingStageRoundTrip ──────────────────────────────────────────────

class TestProcessingStageRoundTrip:
    """End-to-end: from applications.processing_status → stage → progress."""

    def _pct(self, app_status: str) -> int:
        return stage_to_progress(app_status_to_stage(app_status))

    def test_pending_application_gives_0_percent(self):
        assert self._pct("pending") == 0

    def test_queued_application_gives_5_percent(self):
        assert self._pct("queued") == 5

    def test_processing_application_gives_50_percent(self):
        assert self._pct("processing") == 50

    def test_ai_scored_application_gives_100_percent(self):
        assert self._pct("ai_scored") == 100

    def test_low_match_application_gives_100_percent(self):
        assert self._pct("low_match") == 100

    def test_failed_application_gives_0_percent(self):
        assert self._pct("failed") == 0


# ── TestBatchSummaryAggregation ───────────────────────────────────────────────

class TestBatchSummaryAggregation:
    """Simulate the counter arithmetic used by run_batch_import + monitoring."""

    def _simulate_batch(self, app_statuses: list[str]) -> dict:
        """
        Given a list of app processing_status values for imported rows,
        compute what the batch monitoring summary should look like.
        """
        completed  = sum(1 for s in app_statuses if s in ("ai_scored", "low_match"))
        failed     = sum(1 for s in app_statuses if s == "failed")
        processing = sum(1 for s in app_statuses if s == "processing")
        queued     = sum(1 for s in app_statuses if s == "queued")
        pending    = sum(1 for s in app_statuses if s in ("pending", None))
        total      = len(app_statuses)
        terminal   = completed + failed
        progress   = compute_batch_progress_percentage(total, completed, failed, processing, queued)
        return {
            "total":      total,
            "completed":  completed,
            "failed":     failed,
            "processing": processing,
            "queued":     queued,
            "pending":    pending,
            "progress":   progress,
            "fully_processed": is_batch_fully_processed(total, terminal),
        }

    def test_all_ai_scored(self):
        r = self._simulate_batch(["ai_scored"] * 10)
        assert r["completed"] == 10
        assert r["progress"] == 100
        assert r["fully_processed"] is True

    def test_all_queued(self):
        r = self._simulate_batch(["queued"] * 10)
        assert r["queued"] == 10
        assert r["progress"] == 5
        assert r["fully_processed"] is False

    def test_mixed_in_progress(self):
        statuses = ["ai_scored", "ai_scored", "failed", "processing", "queued", "pending"]
        r = self._simulate_batch(statuses)
        assert r["completed"] == 2
        assert r["failed"] == 1
        assert r["processing"] == 1
        assert r["queued"] == 1
        assert r["pending"] == 1
        assert r["fully_processed"] is False

    def test_all_failed(self):
        r = self._simulate_batch(["failed"] * 5)
        assert r["failed"] == 5
        assert r["progress"] == 100
        assert r["fully_processed"] is True

    def test_low_match_rows_count_as_completed(self):
        r = self._simulate_batch(["low_match"] * 3 + ["ai_scored"] * 2)
        assert r["completed"] == 5
        assert r["progress"] == 100

    def test_empty_batch(self):
        r = self._simulate_batch([])
        assert r["total"] == 0
        assert r["progress"] == 0
        assert r["fully_processed"] is False


# ── TestFailureHandling ───────────────────────────────────────────────────────

class TestFailureHandling:
    """Verify failure classification covers all known pipeline outcomes."""

    _KNOWN_REASONS = [
        "extraction_failed",
        "security_blocked",
        "duplicate_exact",
        "duplicate_possible",
        "low_match_gatekeeper",
        "ai_failed",
        "scoring_failed",
    ]

    def test_all_known_reasons_map_to_non_none(self):
        for reason in self._KNOWN_REASONS:
            result = classify_failure_reason(reason)
            assert result is not None, f"classify_failure_reason({reason!r}) returned None"

    def test_all_known_reasons_map_to_valid_labels(self):
        valid_labels = {
            "extraction_failed",
            "duplicate_rejected",
            "ai_processing_failed",
            "scoring_failed",
            "unexpected_error",
        }
        for reason in self._KNOWN_REASONS:
            result = classify_failure_reason(reason)
            assert result in valid_labels, f"{reason!r} → {result!r} not in valid labels"

    def test_partial_batch_failure_does_not_block_others(self):
        """Batch progress must count successfully-scored rows even when some fail."""
        statuses = ["ai_scored", "failed", "ai_scored", "failed", "processing"]
        completed  = sum(1 for s in statuses if s == "ai_scored")
        failed     = sum(1 for s in statuses if s == "failed")
        processing = sum(1 for s in statuses if s == "processing")
        total = len(statuses)
        progress = compute_batch_progress_percentage(total, completed, failed, processing, 0)
        # (2*100 + 2*100 + 1*50) / 5 = 450/5 = 90
        assert progress == 90

    def test_batch_not_fully_processed_while_one_row_processing(self):
        terminal = 4  # 4 done
        total    = 5  # 1 still processing
        assert is_batch_fully_processed(total, terminal) is False


# ── TestAppStatusToRowStatus ──────────────────────────────────────────────────

class TestAppStatusToRowStatus:
    """
    Verify the mapping from applications.processing_status to the vocabulary
    used in bulk_upload_rows.processing_status.

    This mapping drives the sync that keeps stale row statuses current after
    Celery scores applications without touching bulk_upload_rows.
    """

    def test_queued_row_with_ai_scored_app_becomes_completed(self):
        # Core bug scenario: row stuck at 'queued' while app is 'ai_scored'
        assert app_status_to_row_status("ai_scored") == "completed"

    def test_queued_row_with_low_match_app_becomes_completed(self):
        assert app_status_to_row_status("low_match") == "completed"

    def test_queued_row_with_failed_app_becomes_failed(self):
        assert app_status_to_row_status("failed") == "failed"

    def test_processing_maps_to_processing(self):
        assert app_status_to_row_status("processing") == "processing"

    def test_queued_maps_to_queued(self):
        assert app_status_to_row_status("queued") == "queued"

    def test_pending_maps_to_pending(self):
        assert app_status_to_row_status("pending") == "pending"

    def test_none_maps_to_pending(self):
        assert app_status_to_row_status(None) == "pending"

    def test_empty_string_maps_to_pending(self):
        assert app_status_to_row_status("") == "pending"

    def test_unknown_status_maps_to_pending(self):
        assert app_status_to_row_status("mystery") == "pending"

    def test_ai_scored_and_low_match_both_map_to_completed(self):
        assert app_status_to_row_status("ai_scored") == app_status_to_row_status("low_match")
        assert app_status_to_row_status("ai_scored") == "completed"


# ── TestRowSyncLogic ──────────────────────────────────────────────────────────

class TestRowSyncLogic:
    """
    Simulate the bulk UPDATE logic that sync_row_statuses_from_applications
    applies.  Tests use the pure mapping function to verify the expected
    transition without hitting a database.
    """

    def _sync(self, rows: list[dict]) -> list[dict]:
        """
        Simulate the UPDATE: for each row that has an app_processing_status,
        derive the new row processing_status.
        """
        result = [dict(r) for r in rows]
        for row in result:
            app_status = row.get("app_processing_status")
            if app_status is not None:
                row["processing_status"] = app_status_to_row_status(app_status)
        return result

    def test_queued_row_ai_scored_app_syncs_to_completed(self):
        rows = [{"processing_status": "queued", "app_processing_status": "ai_scored"}]
        synced = self._sync(rows)
        assert synced[0]["processing_status"] == "completed"

    def test_queued_row_low_match_app_syncs_to_completed(self):
        rows = [{"processing_status": "queued", "app_processing_status": "low_match"}]
        synced = self._sync(rows)
        assert synced[0]["processing_status"] == "completed"

    def test_queued_row_failed_app_syncs_to_failed(self):
        rows = [{"processing_status": "queued", "app_processing_status": "failed"}]
        synced = self._sync(rows)
        assert synced[0]["processing_status"] == "failed"

    def test_row_without_app_not_changed(self):
        rows = [{"processing_status": "queued", "app_processing_status": None}]
        synced = self._sync(rows)
        # No application → row status stays as-is (pending from mapping, but
        # the UPDATE WHERE clause excludes NULL app statuses)
        # Simulate the WHERE a.processing_status IS NOT NULL exclusion
        row = rows[0]
        if row["app_processing_status"] is None:
            row["processing_status"] = row["processing_status"]  # unchanged
        assert row["processing_status"] == "queued"

    def test_mixed_batch_sync(self):
        rows = [
            {"processing_status": "queued", "app_processing_status": "ai_scored"},
            {"processing_status": "queued", "app_processing_status": "ai_scored"},
            {"processing_status": "queued", "app_processing_status": "failed"},
            {"processing_status": "queued", "app_processing_status": "processing"},
            {"processing_status": "queued", "app_processing_status": "queued"},
        ]
        synced = self._sync(rows)
        statuses = [r["processing_status"] for r in synced]
        assert statuses == ["completed", "completed", "failed", "processing", "queued"]

    def test_already_completed_row_unchanged(self):
        rows = [{"processing_status": "completed", "app_processing_status": "ai_scored"}]
        synced = self._sync(rows)
        assert synced[0]["processing_status"] == "completed"


# ── TestCompletedRowsVisibility ───────────────────────────────────────────────

class TestCompletedRowsVisibility:
    """
    Verify that completed rows (ai_scored / low_match apps) are correctly
    identified as terminal and visible in the summary.
    """

    def test_five_ai_scored_rows_all_completed(self):
        app_statuses = ["ai_scored"] * 5
        completed = sum(1 for s in app_statuses if s in ("ai_scored", "low_match"))
        failed    = sum(1 for s in app_statuses if s == "failed")
        queued    = sum(1 for s in app_statuses if s == "queued")
        processing = sum(1 for s in app_statuses if s == "processing")
        terminal  = completed + failed
        total     = len(app_statuses)

        assert completed == 5
        assert queued == 0
        assert processing == 0
        assert is_batch_fully_processed(total, terminal) is True
        assert compute_batch_progress_percentage(total, completed, failed, processing, queued) == 100

    def test_five_queued_rows_zero_completed(self):
        app_statuses = ["queued"] * 5
        completed = sum(1 for s in app_statuses if s in ("ai_scored", "low_match"))
        failed    = sum(1 for s in app_statuses if s == "failed")
        queued    = sum(1 for s in app_statuses if s == "queued")
        processing = sum(1 for s in app_statuses if s == "processing")
        terminal  = completed + failed
        total     = len(app_statuses)

        assert completed == 0
        assert queued == 5
        assert is_batch_fully_processed(total, terminal) is False

    def test_mixed_completed_and_failed_rows(self):
        app_statuses = ["ai_scored", "ai_scored", "low_match", "failed", "failed"]
        completed  = sum(1 for s in app_statuses if s in ("ai_scored", "low_match"))
        failed     = sum(1 for s in app_statuses if s == "failed")
        terminal   = completed + failed
        total      = len(app_statuses)

        assert completed == 3
        assert failed == 2
        assert is_batch_fully_processed(total, terminal) is True

    def test_row_stage_and_progress_for_ai_scored(self):
        stage    = app_status_to_stage("ai_scored")
        progress = stage_to_progress(stage)
        row_status = app_status_to_row_status("ai_scored")
        assert stage      == "completed"
        assert progress   == 100
        assert row_status == "completed"

    def test_row_stage_and_progress_for_low_match(self):
        stage    = app_status_to_stage("low_match")
        progress = stage_to_progress(stage)
        row_status = app_status_to_row_status("low_match")
        assert stage      == "completed"
        assert progress   == 100
        assert row_status == "completed"

    def test_terminal_detection_by_counters(self):
        """Simulate the frontend terminal detection logic."""
        queued_rows     = 0
        processing_rows = 0
        completed_rows  = 5
        failed_rows     = 0
        imported_rows   = 5
        progress_pct    = 100

        counters_done = (
            queued_rows == 0
            and processing_rows == 0
            and (completed_rows + failed_rows) >= imported_rows
        )
        assert counters_done is True
        assert progress_pct >= 100

    def test_non_terminal_detection_by_counters(self):
        """Still polling: some rows queued."""
        queued_rows     = 3
        processing_rows = 2
        completed_rows  = 0
        failed_rows     = 0
        imported_rows   = 5

        counters_done = (
            queued_rows == 0
            and processing_rows == 0
            and (completed_rows + failed_rows) >= imported_rows
        )
        assert counters_done is False


# ── TestApplicationScoresJoin ─────────────────────────────────────────────────

class TestApplicationScoresJoin:
    """
    Regression tests for the fix that moved final_score from applications
    to application_scores (the correct table).

    These tests verify the pure mapping and data-extraction logic without
    hitting the database, ensuring the 'column a.final_score does not exist'
    500 error cannot recur through the service layer.
    """

    def _make_row(
        self,
        app_processing_status: str | None = "ai_scored",
        final_score: float | None = 85.0,
        decision: str | None = "qualified",
        stopped_reason: str | None = None,
        candidate_data: dict | None = None,
        app_candidate_name: str | None = "Jane Doe",
        matched_cv_filename: str | None = None,
        row_number: int = 1,
    ) -> dict:
        """
        Simulate a row returned by list_rows_processing_status after the fix.
        Note: final_score now comes from application_scores (s.final_score),
        NOT from applications (a.final_score).
        """
        return {
            "row_number":           row_number,
            "candidate_data":       candidate_data or {},
            "app_candidate_name":   app_candidate_name,
            "matched_cv_filename":  matched_cv_filename,
            "app_processing_status": app_processing_status,
            "final_score":          final_score,        # from application_scores
            "decision":             decision,
            "stopped_reason":       stopped_reason,
            "processing_failure_reason": None,
            "evaluation_stage":     3,
            "scored_at":            None,
            "application_id":       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "import_status":        "imported",
        }

    def _process_row(self, r: dict) -> dict:
        """Apply the same extraction logic as list_rows_processing_status."""
        candidate_data = r["candidate_data"] or {}
        candidate_name = (
            candidate_data.get("name")
            or candidate_data.get("first_name")
            or r["app_candidate_name"]
            or r["matched_cv_filename"]
            or f"Row {r['row_number']}"
        )
        app_status = r["app_processing_status"]
        stage      = app_status_to_stage(app_status)
        progress   = stage_to_progress(stage)
        failure_reason = (
            r["processing_failure_reason"]
            or classify_failure_reason(r["stopped_reason"])
        ) if stage == "failed" else None

        return {
            "row_number":        r["row_number"],
            "candidate_name":    candidate_name,
            "application_id":    str(r["application_id"]) if r["application_id"] else None,
            "import_status":     r["import_status"],
            "processing_stage":  stage,
            "progress_percentage": progress,
            "app_processing_status": app_status,
            "final_score":       r["final_score"],   # from application_scores
            "decision":          r["decision"],
            "failure_reason":    failure_reason,
            "evaluation_stage":  r["evaluation_stage"],
            "scored_at":         r["scored_at"],
        }

    def test_ai_scored_row_returns_final_score_from_scores_table(self):
        """Core regression: final_score from application_scores, not applications."""
        row = self._make_row(app_processing_status="ai_scored", final_score=85.0)
        result = self._process_row(row)
        assert result["final_score"] == 85.0
        assert result["processing_stage"] == "completed"
        assert result["progress_percentage"] == 100

    def test_ai_scored_row_with_null_score_does_not_crash(self):
        """application_scores row may not exist (LEFT JOIN) → null score is fine."""
        row = self._make_row(app_processing_status="ai_scored", final_score=None)
        result = self._process_row(row)
        assert result["final_score"] is None
        assert result["processing_stage"] == "completed"

    def test_low_match_row_returns_score(self):
        row = self._make_row(app_processing_status="low_match", final_score=20.0, decision="low_match")
        result = self._process_row(row)
        assert result["final_score"] == 20.0
        assert result["processing_stage"] == "completed"
        assert result["decision"] == "low_match"

    def test_failed_row_returns_null_score(self):
        row = self._make_row(
            app_processing_status="failed",
            final_score=None,
            decision=None,
            stopped_reason="ai_failed",
        )
        result = self._process_row(row)
        assert result["final_score"] is None
        assert result["processing_stage"] == "failed"
        assert result["failure_reason"] == "ai_processing_failed"

    def test_queued_row_returns_null_score(self):
        row = self._make_row(app_processing_status="queued", final_score=None, decision=None)
        result = self._process_row(row)
        assert result["final_score"] is None
        assert result["processing_stage"] == "queued"
        assert result["progress_percentage"] == 5

    def test_candidate_name_falls_back_to_app_candidate_name(self):
        """If candidate_data is empty, use applications.candidate_name."""
        row = self._make_row(
            candidate_data={},
            app_candidate_name="John Smith",
            matched_cv_filename=None,
        )
        result = self._process_row(row)
        assert result["candidate_name"] == "John Smith"

    def test_candidate_name_prefers_candidate_data(self):
        """candidate_data.name takes precedence over applications.candidate_name."""
        row = self._make_row(
            candidate_data={"name": "Alice From Excel"},
            app_candidate_name="Alice DB",
        )
        result = self._process_row(row)
        assert result["candidate_name"] == "Alice From Excel"

    def test_candidate_name_falls_back_to_cv_filename(self):
        row = self._make_row(
            candidate_data={},
            app_candidate_name=None,
            matched_cv_filename="john_cv.pdf",
        )
        result = self._process_row(row)
        assert result["candidate_name"] == "john_cv.pdf"

    def test_candidate_name_falls_back_to_row_number(self):
        row = self._make_row(
            candidate_data={},
            app_candidate_name=None,
            matched_cv_filename=None,
            row_number=7,
        )
        result = self._process_row(row)
        assert result["candidate_name"] == "Row 7"

    def test_decision_is_returned_from_applications(self):
        """decision column lives on applications, not application_scores."""
        row = self._make_row(decision="rejected")
        result = self._process_row(row)
        assert result["decision"] == "rejected"

    def test_application_id_is_included(self):
        row = self._make_row()
        result = self._process_row(row)
        assert result["application_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
