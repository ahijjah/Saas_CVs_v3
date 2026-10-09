"""Candidate `requirements-v2-warning-adapter-1` (offline; NOT wired into any API, worker or UI; see README.md)."""
from parser_candidates.requirements_v2_warning_adapter_1.adapter import (  # noqa: F401
    ADAPTER_VERSION, NEEDS_CONFLICT_REVIEW, acknowledge, build_review, carry_warning_review, normalize_warnings, readiness, reconcile, status,
    validate_review, visible_warnings,
)
