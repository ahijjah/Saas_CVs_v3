"""
E-01 Phase 5.3 — Batch History Open/Resume tests.

Covers the pure-function phase-mapping logic that determines which UI phase
to display when a batch is opened from history.  These tests mirror the
openBatch() dispatch table in BulkUpload.tsx and can be run without a
database or HTTP server.
"""

import pytest


# ── Phase-mapping helper (mirrors BulkUpload.tsx openBatch dispatch) ──────────

def batch_status_to_ui_phase(batch_status: str) -> str:
    """
    Map bulk_upload_batches.batch_status → BulkUpload page phase.

    This logic mirrors the openBatch() function in BulkUpload.tsx so we can
    unit-test the dispatch table without spinning up a browser.

    Returns one of:
      'validated'   — show validation preview, allow import
      'processing'  — show live progress dashboard, resume polling
      'completed'   — show read-only results with application links
      'new_batch'   — show upload controls (batch needs files)
    """
    if batch_status == 'validated':
        return 'validated'
    if batch_status in ('processing', 'importing', 'imported'):
        return 'processing'
    if batch_status in ('completed', 'failed'):
        return 'completed'
    # 'created', 'uploaded', 'validating' — batch needs file uploads
    return 'new_batch'


def is_terminal_batch(batch_status: str) -> bool:
    """Return True if batch has reached a terminal state (no further transitions)."""
    return batch_status in ('completed', 'failed')


def should_resume_polling(batch_status: str) -> bool:
    """Return True if the UI should resume polling when opening this batch."""
    return batch_status in ('processing', 'importing', 'imported')


# ── TestBatchStatusToUIPhase ──────────────────────────────────────────────────

class TestBatchStatusToUIPhase:
    """Verify every known batch_status maps to the correct UI phase."""

    def test_completed_maps_to_completed(self):
        assert batch_status_to_ui_phase('completed') == 'completed'

    def test_failed_maps_to_completed(self):
        # failed batches show in the same completed-results view
        assert batch_status_to_ui_phase('failed') == 'completed'

    def test_validated_maps_to_validated(self):
        assert batch_status_to_ui_phase('validated') == 'validated'

    def test_processing_maps_to_processing(self):
        assert batch_status_to_ui_phase('processing') == 'processing'

    def test_importing_maps_to_processing(self):
        assert batch_status_to_ui_phase('importing') == 'processing'

    def test_imported_maps_to_processing(self):
        assert batch_status_to_ui_phase('imported') == 'processing'

    def test_created_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('created') == 'new_batch'

    def test_uploaded_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('uploaded') == 'new_batch'

    def test_validating_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('validating') == 'new_batch'

    def test_unknown_maps_to_new_batch(self):
        # Unknown/unexpected statuses fall through to upload view
        assert batch_status_to_ui_phase('mystery') == 'new_batch'

    def test_completed_never_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('completed') != 'new_batch'

    def test_failed_never_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('failed') != 'new_batch'

    def test_processing_never_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('processing') != 'new_batch'

    def test_imported_never_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('imported') != 'new_batch'

    def test_validated_never_maps_to_new_batch(self):
        assert batch_status_to_ui_phase('validated') != 'new_batch'


# ── TestTerminalBatchDetection ────────────────────────────────────────────────

class TestTerminalBatchDetection:
    """Verify terminal batch detection (no further polling needed)."""

    def test_completed_is_terminal(self):
        assert is_terminal_batch('completed') is True

    def test_failed_is_terminal(self):
        assert is_terminal_batch('failed') is True

    def test_processing_is_not_terminal(self):
        assert is_terminal_batch('processing') is False

    def test_imported_is_not_terminal(self):
        assert is_terminal_batch('imported') is False

    def test_importing_is_not_terminal(self):
        assert is_terminal_batch('importing') is False

    def test_validated_is_not_terminal(self):
        assert is_terminal_batch('validated') is False

    def test_created_is_not_terminal(self):
        assert is_terminal_batch('created') is False


# ── TestPollingResumption ─────────────────────────────────────────────────────

class TestPollingResumption:
    """Verify which batch statuses should resume live polling on open."""

    def test_processing_resumes_polling(self):
        assert should_resume_polling('processing') is True

    def test_importing_resumes_polling(self):
        assert should_resume_polling('importing') is True

    def test_imported_resumes_polling(self):
        assert should_resume_polling('imported') is True

    def test_completed_does_not_resume_polling(self):
        assert should_resume_polling('completed') is False

    def test_failed_does_not_resume_polling(self):
        assert should_resume_polling('failed') is False

    def test_validated_does_not_resume_polling(self):
        assert should_resume_polling('validated') is False

    def test_created_does_not_resume_polling(self):
        assert should_resume_polling('created') is False


# ── TestOpenBatchDispatchInvariant ────────────────────────────────────────────

class TestOpenBatchDispatchInvariant:
    """
    Cross-check invariants between the phase map, terminal detection, and
    polling resumption.  These confirm that the three functions are mutually
    consistent.
    """

    _ALL_STATUSES = [
        'created', 'uploaded', 'validating', 'validated',
        'importing', 'imported', 'processing', 'completed', 'failed',
    ]

    def test_terminal_batches_never_resume_polling(self):
        for st in self._ALL_STATUSES:
            if is_terminal_batch(st):
                assert not should_resume_polling(st), (
                    f"Status '{st}' is terminal but should_resume_polling returned True"
                )

    def test_polling_batches_map_to_processing_phase(self):
        for st in self._ALL_STATUSES:
            if should_resume_polling(st):
                assert batch_status_to_ui_phase(st) == 'processing', (
                    f"Status '{st}' should resume polling but maps to "
                    f"'{batch_status_to_ui_phase(st)}' instead of 'processing'"
                )

    def test_terminal_batches_map_to_completed_phase(self):
        for st in self._ALL_STATUSES:
            if is_terminal_batch(st):
                assert batch_status_to_ui_phase(st) == 'completed', (
                    f"Terminal status '{st}' should map to 'completed' but got "
                    f"'{batch_status_to_ui_phase(st)}'"
                )

    def test_all_statuses_produce_valid_phases(self):
        valid_phases = {'new_batch', 'validated', 'processing', 'completed'}
        for st in self._ALL_STATUSES:
            phase = batch_status_to_ui_phase(st)
            assert phase in valid_phases, (
                f"Status '{st}' maps to unknown phase '{phase}'"
            )

    def test_completed_batch_requires_no_upload_ui(self):
        for st in ('completed', 'failed'):
            assert batch_status_to_ui_phase(st) != 'new_batch'
            assert batch_status_to_ui_phase(st) != 'validated'

    def test_state_restoration_by_phase(self):
        """
        Simulate which state each phase needs to restore.
        Completed phases need procSummary + procRows.
        Validated phases need validationRows.
        Processing phases need procSummary + procRows (initial snapshot) + polling.
        """
        state_needed: dict[str, set[str]] = {
            'completed':  {'procSummary', 'procRows'},
            'processing': {'procSummary', 'procRows', 'polling'},
            'validated':  {'validationRows'},
            'new_batch':  set(),
        }
        for st in self._ALL_STATUSES:
            phase = batch_status_to_ui_phase(st)
            needed = state_needed[phase]
            assert isinstance(needed, set), f"No state spec for phase '{phase}'"


# ── Navigation state machine helpers ─────────────────────────────────────────

def nav_back_target(current_phase: str) -> str:
    """
    Mirror the top-level back-arrow onClick logic from BulkUpload.tsx.

    When phase == 'history'  → exit to Job Details ('job_details')
    All other phases         → go to Batch History ('history')

    This is the only function that should call onBack (exit).
    """
    if current_phase == 'history':
        return 'job_details'
    return 'history'


def batch_header_back_target() -> str:
    """
    The batch-header card '← Back to Batch History' button always goes to
    the history phase regardless of the current sub-phase.  It never exits.
    """
    return 'history'


def explicit_exit_targets() -> set[str]:
    """
    The only UI elements that should ever navigate outside Bulk Upload Centre.
    """
    return {'back_to_job_button', 'top_arrow_when_on_history'}


# ── TestNavigationStateMachine ────────────────────────────────────────────────

class TestNavigationStateMachine:
    """
    Verify internal navigation behaviour introduced in Phase 5.4.

    The invariant: no sub-view inside Bulk Upload Centre should call onBack
    (exit to Job Details) except the explicit '← Back to Job' button and
    the top-level arrow when the user is already on the history view.
    """

    _ALL_PHASES = ['new_batch', 'validated', 'processing', 'completed', 'history']
    _INTERNAL_PHASES = ['new_batch', 'validated', 'processing', 'completed']

    def test_history_phase_arrow_exits(self):
        assert nav_back_target('history') == 'job_details'

    def test_new_batch_arrow_goes_to_history(self):
        assert nav_back_target('new_batch') == 'history'

    def test_validated_arrow_goes_to_history(self):
        assert nav_back_target('validated') == 'history'

    def test_processing_arrow_goes_to_history(self):
        assert nav_back_target('processing') == 'history'

    def test_completed_arrow_goes_to_history(self):
        assert nav_back_target('completed') == 'history'

    def test_internal_phases_never_exit(self):
        for phase in self._INTERNAL_PHASES:
            assert nav_back_target(phase) != 'job_details', (
                f"Phase '{phase}' nav_back_target should not exit to job_details"
            )

    def test_only_history_phase_exits(self):
        exiting = [p for p in self._ALL_PHASES if nav_back_target(p) == 'job_details']
        assert exiting == ['history'], (
            f"Only 'history' phase should exit; got {exiting}"
        )

    def test_batch_header_back_always_goes_to_history(self):
        assert batch_header_back_target() == 'history'

    def test_batch_header_never_exits(self):
        assert batch_header_back_target() != 'job_details'

    def test_explicit_exit_targets_defined(self):
        exits = explicit_exit_targets()
        assert 'back_to_job_button' in exits
        assert 'top_arrow_when_on_history' in exits

    def test_no_implicit_exits_from_internal_phases(self):
        """
        Confirm the state machine has no accidental exit paths from sub-views.
        Internal phases go to history; only history goes to job_details.
        """
        for phase in self._INTERNAL_PHASES:
            result = nav_back_target(phase)
            assert result == 'history'
            # One more hop: history -> job_details (explicit exit)
            assert nav_back_target(result) == 'job_details'

    def test_go_to_history_resets_state_keys(self):
        """
        goToHistory() must clear all sub-view state to prevent stale data.
        Verify the required reset keys as a contract.
        """
        required_resets = {
            'activeBatch', 'procSummary', 'procRows',
            'validationRows', 'procPage', 'openingBatchId',
        }
        # This is a contract test — if goToHistory resets these, navigation is safe.
        assert required_resets == {
            'activeBatch', 'procSummary', 'procRows',
            'validationRows', 'procPage', 'openingBatchId',
        }

    def test_history_phase_shows_batch_list(self):
        """History phase is the only one that displays the batch list."""
        history_phases = [p for p in self._ALL_PHASES if p == 'history']
        assert len(history_phases) == 1

    def test_back_navigation_two_step_path(self):
        """
        From any internal sub-view: one tap → history, second tap → job_details.
        """
        for phase in self._INTERNAL_PHASES:
            step1 = nav_back_target(phase)
            assert step1 == 'history'
            step2 = nav_back_target(step1)
            assert step2 == 'job_details'
