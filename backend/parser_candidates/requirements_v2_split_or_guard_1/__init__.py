"""Candidate safeguard `requirements-v2-split-or-guard-1` (offline; NOT wired into any API, worker or UI; see README.md)."""
from parser_candidates.requirements_v2_split_or_guard_1.guard import (  # noqa: F401
    NEEDS_SPLIT_OR_REVIEW, SPLIT_OR_VERSION, carry_split_or_review, composed_readiness, detect, inspect_result, open_issues, reconcile,
    validate_review,
)
